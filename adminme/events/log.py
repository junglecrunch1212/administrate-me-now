"""
L2 Event Log — append-only SQLCipher-backed event storage.

Per ADMINISTRATEME_BUILD.md §"L2: THE EVENT LOG" and SYSTEM_INVARIANTS.md §1.

Prompt 03 scope: storage, ordering, retrieval. Typed-payload validation lands
in prompt 04 (see `adminme/events/registry.py`); here events are opaque dicts
with a `type` string at minimum.

Schema (MVP; see migrations/0001_initial.sql — BUILD.md §L2 lists the full
column set that prompt 04 grows into):

    events (
        event_id        TEXT PRIMARY KEY,     -- "ev_" + 14-char Crockford base32
        event_at_ms     INTEGER NOT NULL,     -- insertion time, ms since epoch
        tenant_id       TEXT NOT NULL,
        owner_scope     TEXT NOT NULL,        -- partition key (indexed, not physical)
        type            TEXT NOT NULL,
        version         INTEGER NOT NULL,     -- schema version (prompt 04 validates)
        correlation_id  TEXT,
        source          TEXT,                 -- JSON
        payload         TEXT NOT NULL         -- JSON
    )

Partitioning: per SYSTEM_INVARIANTS.md §1 invariant 6, `owner_scope` is an
indexed column — not a physical partition — at v1.

Append-only: enforced by BEFORE UPDATE / BEFORE DELETE triggers in migration
0001 in addition to the code-level rule that only `append()` / `append_batch()`
write (per BUILD.md §L2 and SYSTEM_INVARIANTS.md §1 invariant 2).

Async model: `sqlcipher3-binary` is a synchronous DB-API driver and there is
no drop-in async SQLCipher for Python today. We keep a single writer
connection guarded by an `asyncio.Lock` and dispatch every DB call via
`asyncio.to_thread`. Reads and writes therefore run off the event loop
without blocking other coroutines.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import secrets
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import sqlcipher3

EVENT_ID_PREFIX = "ev_"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MS_CHARS = 10  # 50 bits of timestamp (plenty through year 37000+)
_CTR_CHARS = 4  # 20 bits (1M events per ms before overflow)
_CTR_MAX = 1 << (5 * _CTR_CHARS)

_REQUIRED_APPEND_FIELDS = ("type", "tenant_id", "owner_scope", "payload")


class EventLogError(RuntimeError):
    """Base class for event log errors."""


class CursorNotFound(EventLogError):
    """The supplied read cursor does not exist in the log."""


class AppendValidationError(EventLogError):
    """An event dict is missing required fields or has an invalid field type."""


def _encode_crockford(value: int, length: int) -> str:
    if value < 0:
        raise ValueError("value must be non-negative")
    out = [""] * length
    for i in range(length - 1, -1, -1):
        out[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    if value:
        raise ValueError("value does not fit in requested length")
    return "".join(out)


class _EventIdGenerator:
    """Time-sortable IDs with within-ms monotonic counter.

    Two events minted in the same millisecond get different, lexicographically
    increasing IDs. If the counter overflows (>=2**20 in one ms), we advance
    the embedded timestamp — the resulting ID is still sortable and unique.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = 0
        self._counter = 0

    def mint(self, now_ms: int) -> tuple[str, int]:
        with self._lock:
            if now_ms <= self._last_ms:
                now_ms = self._last_ms
                self._counter += 1
            else:
                self._last_ms = now_ms
                self._counter = 0
            if self._counter >= _CTR_MAX:
                self._last_ms += 1
                now_ms = self._last_ms
                self._counter = 0
            ts_enc = _encode_crockford(now_ms, _MS_CHARS)
            ctr_enc = _encode_crockford(self._counter, _CTR_CHARS)
            return f"{EVENT_ID_PREFIX}{ts_enc}{ctr_enc}", now_ms


def _load_migration(filename: str) -> str:
    pkg = "adminme.events.migrations"
    return importlib.resources.files(pkg).joinpath(filename).read_text(encoding="utf-8")


def _format_key(encryption_key: bytes) -> str:
    """SQLCipher PRAGMA key expects a quoted hex blob for raw keys."""
    if not isinstance(encryption_key, (bytes, bytearray)):
        raise TypeError("encryption_key must be bytes")
    if len(encryption_key) != 32:
        raise ValueError(f"encryption_key must be 32 bytes, got {len(encryption_key)}")
    return f"x'{bytes(encryption_key).hex()}'"


class EventLog:
    """Append-only, SQLCipher-encrypted event storage.

    Instances are safe to share across coroutines in one event loop; all DB
    access is serialized via a write lock and dispatched to a thread.
    """

    MIGRATIONS: tuple[tuple[int, str], ...] = (
        (1, "0001_initial.sql"),
        (2, "0002_full_envelope.sql"),
    )

    def __init__(self, db_path: Path, encryption_key: bytes) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_pragma = _format_key(encryption_key)
        self._conn = self._open_connection()
        self._lock = asyncio.Lock()
        self._id_gen = _EventIdGenerator()
        self._closed = False
        self._migrate()

    # ------------------------------------------------------------------
    # connection management
    # ------------------------------------------------------------------
    def _open_connection(self) -> sqlcipher3.Connection:
        conn = sqlcipher3.connect(
            str(self._db_path),
            isolation_level=None,  # we drive transactions explicitly
            check_same_thread=False,
        )
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"{self._key_pragma}\"")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Touch the DB to surface bad-key errors eagerly.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn

    def _migrate(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS _schema_version ("
            "  version INTEGER PRIMARY KEY, "
            "  applied_at_ms INTEGER NOT NULL"
            ")"
        )
        applied: set[int] = {
            row[0] for row in cur.execute("SELECT version FROM _schema_version")
        }
        # Each migration file is idempotent (CREATE ... IF NOT EXISTS), so a
        # crash between `executescript` and the version-row insert just means
        # we re-run the script harmlessly on next open. `executescript` issues
        # its own implicit COMMIT, which is why we don't wrap it in BEGIN.
        for version, filename in self.MIGRATIONS:
            if version in applied:
                continue
            sql = _load_migration(filename)
            cur.executescript(sql)
            cur.execute(
                "INSERT OR REPLACE INTO _schema_version(version, applied_at_ms)"
                " VALUES (?, ?)",
                (version, int(time.time() * 1000)),
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._conn.close)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    async def append(self, event: dict[str, Any]) -> str:
        ids = await self.append_batch([event])
        return ids[0]

    async def append_batch(self, events: list[dict[str, Any]]) -> list[str]:
        if not events:
            return []
        prepared: list[tuple[tuple[Any, ...], str]] = []
        for ev in events:
            row, event_id = self._prepare_row(ev)
            prepared.append((row, event_id))

        async with self._lock:
            await asyncio.to_thread(self._insert_rows, [r for r, _ in prepared])
        return [eid for _, eid in prepared]

    def _prepare_row(self, event: dict[str, Any]) -> tuple[tuple[Any, ...], str]:
        for field in _REQUIRED_APPEND_FIELDS:
            if field not in event:
                raise AppendValidationError(f"event missing required field: {field}")
        type_ = event["type"]
        if not isinstance(type_, str) or not type_:
            raise AppendValidationError("event.type must be a non-empty string")
        tenant_id = event["tenant_id"]
        if not isinstance(tenant_id, str) or not tenant_id:
            raise AppendValidationError("event.tenant_id must be a non-empty string")
        owner_scope = event["owner_scope"]
        if not isinstance(owner_scope, str) or not owner_scope:
            raise AppendValidationError("event.owner_scope must be a non-empty string")
        payload = event["payload"]
        try:
            payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=False)
        except (TypeError, ValueError) as exc:
            raise AppendValidationError(f"payload is not JSON-serializable: {exc}") from exc

        version = int(event.get("version", 1))
        correlation_id = event.get("correlation_id")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise AppendValidationError("correlation_id must be str or None")
        source = event.get("source")
        if source is None:
            source_json: str | None = None
        else:
            try:
                source_json = json.dumps(source, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise AppendValidationError(f"source is not JSON-serializable: {exc}") from exc

        event_at_ms = int(event.get("event_at_ms") or time.time() * 1000)
        supplied_id = event.get("event_id")
        if supplied_id is not None:
            if not isinstance(supplied_id, str) or not supplied_id:
                raise AppendValidationError("event_id must be a non-empty string")
            event_id = supplied_id
        else:
            event_id, event_at_ms = self._id_gen.mint(event_at_ms)

        row = (
            event_id,
            event_at_ms,
            tenant_id,
            owner_scope,
            type_,
            version,
            correlation_id,
            source_json,
            payload_json,
        )
        return row, event_id

    def _insert_rows(self, rows: list[tuple[Any, ...]]) -> None:
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.executemany(
                "INSERT INTO events"
                "  (event_id, event_at_ms, tenant_id, owner_scope, type, version,"
                "   correlation_id, source, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    async def read_since(
        self,
        cursor: str | None = None,
        *,
        limit: int = 1000,
        owner_scope: str | None = None,
        types: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if cursor is not None:
            exists = await asyncio.to_thread(self._event_exists, cursor)
            if not exists:
                raise CursorNotFound(cursor)

        batch_size = max(limit, 500)
        last = cursor
        while True:
            rows = await asyncio.to_thread(
                self._read_page,
                last,
                batch_size,
                owner_scope,
                tuple(types) if types else None,
            )
            if not rows:
                return
            for row in rows:
                yield _row_to_event(row)
            last = rows[-1]["event_id"]
            if len(rows) < batch_size:
                return

    def _event_exists(self, event_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("SELECT 1 FROM events WHERE event_id = ? LIMIT 1", (event_id,))
        return cur.fetchone() is not None

    def _read_page(
        self,
        after_id: str | None,
        limit: int,
        owner_scope: str | None,
        types: tuple[str, ...] | None,
    ) -> list[sqlcipher3.Row]:
        where: list[str] = []
        params: list[Any] = []
        if after_id is not None:
            where.append("event_id > ?")
            params.append(after_id)
        if owner_scope is not None:
            where.append("owner_scope = ?")
            params.append(owner_scope)
        if types:
            placeholders = ",".join("?" for _ in types)
            where.append(f"type IN ({placeholders})")
            params.extend(types)
        sql = _SELECT_COLUMNS + " FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY event_id ASC LIMIT ?"
        params.append(limit)
        cur = self._conn.cursor()
        return list(cur.execute(sql, params))

    async def get(self, event_id: str) -> dict[str, Any] | None:
        row = await asyncio.to_thread(self._get_one, event_id)
        return _row_to_event(row) if row else None

    def _get_one(self, event_id: str) -> sqlcipher3.Row | None:
        cur = self._conn.cursor()
        cur.execute(
            _SELECT_COLUMNS + " FROM events WHERE event_id = ?",
            (event_id,),
        )
        return cur.fetchone()

    async def get_by_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self._get_correlation, correlation_id)
        return [_row_to_event(r) for r in rows]

    def _get_correlation(self, correlation_id: str) -> list[sqlcipher3.Row]:
        cur = self._conn.cursor()
        return list(
            cur.execute(
                _SELECT_COLUMNS
                + " FROM events WHERE correlation_id = ? ORDER BY event_id ASC",
                (correlation_id,),
            )
        )

    async def latest_event_id(self) -> str | None:
        return await asyncio.to_thread(self._latest_event_id)

    def _latest_event_id(self) -> str | None:
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT event_id FROM events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    async def count(self) -> int:
        return await asyncio.to_thread(self._count)

    def _count(self) -> int:
        cur = self._conn.cursor()
        return int(cur.execute("SELECT count(*) FROM events").fetchone()[0])

    async def count_since(self, cursor: str | None) -> int:
        return await asyncio.to_thread(self._count_since, cursor)

    def _count_since(self, cursor: str | None) -> int:
        cur = self._conn.cursor()
        if cursor is None:
            return int(cur.execute("SELECT count(*) FROM events").fetchone()[0])
        return int(
            cur.execute(
                "SELECT count(*) FROM events WHERE event_id > ?", (cursor,)
            ).fetchone()[0]
        )


_SELECT_COLUMNS = (
    "SELECT event_id, event_at_ms, tenant_id, owner_scope, type, version,"
    "       schema_version, occurred_at, recorded_at, source_adapter,"
    "       source_account_id, visibility_scope, sensitivity,"
    "       correlation_id, causation_id, source, payload,"
    "       raw_ref, actor_identity"
)


def _row_to_event(row: sqlcipher3.Row) -> dict[str, Any]:
    """Return the full 15-column envelope shape (plus the legacy `version`
    and `source` MVP fields for backwards compatibility during the prompt-04
    transition). Prompt 05+ callers should read `schema_version` directly."""
    return {
        "event_id": row["event_id"],
        "event_at_ms": row["event_at_ms"],
        "tenant_id": row["tenant_id"],
        "owner_scope": row["owner_scope"],
        "type": row["type"],
        "version": row["version"],
        "schema_version": row["schema_version"],
        "occurred_at": row["occurred_at"],
        "recorded_at": row["recorded_at"],
        "source_adapter": row["source_adapter"],
        "source_account_id": row["source_account_id"],
        "visibility_scope": row["visibility_scope"],
        "sensitivity": row["sensitivity"],
        "correlation_id": row["correlation_id"],
        "causation_id": row["causation_id"],
        "source": json.loads(row["source"]) if row["source"] is not None else None,
        "payload": json.loads(row["payload"]),
        "raw_ref": row["raw_ref"],
        "actor_identity": row["actor_identity"],
    }


def new_correlation_id() -> str:
    """Generate a fresh correlation_id — 22 chars of hex (88 bits of entropy).

    Correlation IDs are opaque strings; anything unique suffices. This helper
    exists so callers who don't already have one can mint one without pulling
    in a new dep.
    """
    return secrets.token_hex(11)
