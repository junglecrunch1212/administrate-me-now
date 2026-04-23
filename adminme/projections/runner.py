"""
ProjectionRunner — discovers, dispatches, rebuilds projections.

Per SYSTEM_INVARIANTS.md §2 and ADMINISTRATEME_BUILD.md §L3.

The runner owns one SQLCipher connection per registered projection and one
bus subscription per projection (``subscriber_id = f"projection:{name}"``).
On each delivered event, the bound callback opens a transaction on the
projection's DB, calls ``projection.apply(envelope, conn)``, commits, and
returns. Checkpoint persistence is owned by the bus (prompt 03) — if the
callback raises, the bus does not advance the checkpoint and the event is
re-delivered. Projections must be idempotent for this to be safe.

Rebuild: ``await runner.rebuild(name)`` drops the projection's DB file,
recreates it from ``schema.sql``, resets the bus checkpoint for
``projection:{name}`` to ``NULL``, and replays the whole event log through
``apply()``. The bus continues to deliver live events during/after rebuild
because the subscriber resumes from the freshly-reset checkpoint; there is
no quiesce window.

Per §15/D15 all DB paths come from ``InstanceConfig.projection_db_path()``.
No path literals in this module.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal, cast

import sqlcipher3

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import InstanceConfig
from adminme.projections.base import Projection

_log = logging.getLogger(__name__)


def _format_key(encryption_key: bytes) -> str:
    """SQLCipher PRAGMA key expects a quoted hex blob for raw keys."""
    if not isinstance(encryption_key, (bytes, bytearray)):
        raise TypeError("encryption_key must be bytes")
    if len(encryption_key) != 32:
        raise ValueError(f"encryption_key must be 32 bytes, got {len(encryption_key)}")
    return f"x'{bytes(encryption_key).hex()}'"


class ProjectionRunner:
    """Owns the projection DB connections and bus subscriptions."""

    def __init__(
        self,
        bus: EventBus,
        log: EventLog,
        instance_config: InstanceConfig,
        *,
        encryption_key: bytes,
    ) -> None:
        self._bus = bus
        self._log = log
        self._config = instance_config
        self._key_pragma = _format_key(encryption_key)
        self._projections: dict[str, Projection] = {}
        self._conns: dict[str, sqlcipher3.Connection] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._started = False
        self._config.projections_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, projection: Projection) -> None:
        """Register a projection. Must be called before start()."""
        if self._started:
            raise RuntimeError("register() must be called before start()")
        if projection.name in self._projections:
            raise ValueError(f"projection already registered: {projection.name}")
        self._projections[projection.name] = projection

    def projection_names(self) -> list[str]:
        return sorted(self._projections)

    def connection(self, name: str) -> sqlcipher3.Connection:
        """Return the projection's live connection (for read-only queries)."""
        return self._conns[name]

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Open each projection's DB, apply schema.sql if new, subscribe to
        the bus with one callback per projection that dispatches to apply()."""
        if self._started:
            return
        self._started = True
        for name, projection in self._projections.items():
            conn = await asyncio.to_thread(self._open_projection_db, name, projection)
            self._conns[name] = conn
            self._write_locks[name] = asyncio.Lock()
            subscriber_id = f"projection:{name}"
            types: list[str] | Literal["*"]
            if projection.subscribes_to == "*":
                types = "*"
            else:
                types = list(cast(list[str], projection.subscribes_to))
            self._bus.subscribe(
                subscriber_id,
                types,
                self._make_callback(name, projection),
            )
        await self._bus.start()

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        await self._bus.stop()
        for conn in self._conns.values():
            try:
                await asyncio.to_thread(conn.close)
            except Exception:
                _log.exception("error closing projection connection")
        self._conns.clear()

    # ------------------------------------------------------------------
    # rebuild
    # ------------------------------------------------------------------
    async def rebuild(self, projection_name: str) -> None:
        """Drop the projection's DB, recreate from schema.sql, replay the
        entire event log through apply(), reset the bus checkpoint.

        Per §2 invariant 1: post-rebuild state is byte-equivalent to live.
        """
        if projection_name not in self._projections:
            raise KeyError(projection_name)
        projection = self._projections[projection_name]
        subscriber_id = f"projection:{projection_name}"

        async with self._write_locks.setdefault(projection_name, asyncio.Lock()):
            # Close current connection before removing the file.
            old = self._conns.pop(projection_name, None)
            if old is not None:
                await asyncio.to_thread(old.close)
            db_path = self._config.projection_db_path(projection_name)
            await asyncio.to_thread(self._remove_if_exists, db_path)

            conn = await asyncio.to_thread(self._open_projection_db, projection_name, projection)
            self._conns[projection_name] = conn

            # Replay every event through apply() inline. Handlers are
            # idempotent (§2 invariant 4), so any overlap with bus delivery
            # is safe.
            await self._replay_through(projection, conn)

            # Advance the bus checkpoint to the latest event so the live
            # worker does not re-apply every event it was going to deliver.
            # If the log is empty, leave the checkpoint as-is.
            latest = await self._log.latest_event_id()
            if latest is not None:
                await self._bus.set_checkpoint(subscriber_id, latest)

    async def _replay_through(
        self,
        projection: Projection,
        conn: sqlcipher3.Connection,
    ) -> None:
        types_filter: list[str] | None
        if projection.subscribes_to == "*":
            types_filter = None
        else:
            types_filter = list(projection.subscribes_to)  # type: ignore[arg-type]

        def _apply_batch(batch: list[dict[str, Any]]) -> None:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                for envelope in batch:
                    projection.apply(envelope, conn)
                projection.after_batch(conn)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

        batch: list[dict[str, Any]] = []
        async for envelope in self._log.read_since(None, types=types_filter, limit=500):
            batch.append(envelope)
            if len(batch) >= 500:
                await asyncio.to_thread(_apply_batch, batch)
                batch = []
        if batch:
            await asyncio.to_thread(_apply_batch, batch)

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    async def status(self) -> dict[str, dict[str, Any]]:
        """Per-projection dict: name, version, row_counts, checkpoint,
        lag_count, last_event_applied."""
        out: dict[str, dict[str, Any]] = {}
        for name, projection in self._projections.items():
            subscriber_id = f"projection:{name}"
            bus_status = await self._bus.subscriber_status(subscriber_id)
            row_counts = await asyncio.to_thread(
                self._collect_row_counts, self._conns[name]
            )
            out[name] = {
                "name": name,
                "version": projection.version,
                "row_counts": row_counts,
                "checkpoint": bus_status.get("checkpoint_event_id"),
                "lag_count": bus_status.get("lag_count"),
                "last_event_applied": bus_status.get("checkpoint_event_id"),
                "degraded": bus_status.get("degraded", False),
            }
        return out

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _make_callback(self, name: str, projection: Projection):
        write_locks = self._write_locks

        async def _cb(envelope: dict[str, Any]) -> None:
            lock = write_locks.setdefault(name, asyncio.Lock())
            async with lock:
                conn = self._conns.get(name)
                if conn is None:
                    raise RuntimeError(f"projection {name!r} connection missing")
                await asyncio.to_thread(self._apply_one, projection, conn, envelope)

        return _cb

    @staticmethod
    def _apply_one(
        projection: Projection,
        conn: sqlcipher3.Connection,
        envelope: dict[str, Any],
    ) -> None:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            projection.apply(envelope, conn)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def _open_projection_db(
        self,
        name: str,
        projection: Projection,
    ) -> sqlcipher3.Connection:
        db_path = self._config.projection_db_path(name)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlcipher3.connect(
            str(db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"{self._key_pragma}\"")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Touch the DB to surface bad-key errors eagerly.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()

        schema_sql = self._read_schema(projection)
        cur = conn.cursor()
        cur.executescript(schema_sql)
        # Stamp projection version in a tiny metadata table for debugging.
        cur.executescript(
            "CREATE TABLE IF NOT EXISTS _projection_meta ("
            "  key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        )
        cur.execute(
            "INSERT OR REPLACE INTO _projection_meta(key, value) VALUES (?, ?)",
            ("version", str(projection.version)),
        )
        cur.execute(
            "INSERT OR REPLACE INTO _projection_meta(key, value) VALUES (?, ?)",
            ("name", projection.name),
        )
        return conn

    @staticmethod
    def _read_schema(projection: Projection) -> str:
        path = Path(projection.schema_path)
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _remove_if_exists(path: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.unlink()

    @staticmethod
    def _collect_row_counts(conn: sqlcipher3.Connection) -> dict[str, int]:
        cur = conn.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE '\\_%' ESCAPE '\\'"
            )
        ]
        out: dict[str, int] = {}
        for t in tables:
            row = cur.execute(f"SELECT count(*) FROM {t}").fetchone()
            out[t] = int(row[0])
        return out
