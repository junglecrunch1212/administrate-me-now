"""
L2 Event Log — append-only SQLCipher-backed event storage.

Per ADMINISTRATEME_BUILD.md §"L2: THE EVENT LOG" and SYSTEM_INVARIANTS.md §1.

Schema (prompt 04, full shape — see migrations/0002_full_envelope.sql and
DECISIONS.md §D16): 15 columns matching BUILD.md §L2. The legacy `version`
and `source` columns from the 0001 MVP persist but are not read by new code;
canonical access is via the ``EventEnvelope`` model's fields.

Partitioning: per SYSTEM_INVARIANTS.md §1 invariant 6, `owner_scope` is an
indexed column — not a physical partition — at v1.

Append-only: enforced by BEFORE UPDATE / BEFORE DELETE triggers in migration
0001 (preserved through 0002) in addition to the code-level rule that only
``append()`` / ``append_batch()`` write (SYSTEM_INVARIANTS.md §1 invariant 2).

Payload validation: per SYSTEM_INVARIANTS.md §1 invariants 3 + 9 and D7,
``append()`` validates the payload against the Pydantic model registered for
``(envelope.type, envelope.schema_version)`` before the insert. Unknown
schemas raise ``EventValidationError`` unless
``ADMINME_ALLOW_UNKNOWN_SCHEMAS=1`` is set in the environment, in which case
a warning is logged and the write proceeds (useful for tests and for draft
schema development).

Async model: ``sqlcipher3-binary`` is a synchronous DB-API driver and there
is no drop-in async SQLCipher for Python today (D14). We keep a single
writer connection guarded by an ``asyncio.Lock`` and dispatch every DB call
via ``asyncio.to_thread``. Reads and writes therefore run off the event
loop without blocking other coroutines.

Signature: ``append(envelope, *, correlation_id=None, causation_id=None)``
per D8 addition 2 — correlation_id and causation_id are explicit keyword
arguments at every call site, never dict-squatted into the envelope
silently. Passing ``None`` is fine when genuinely unknown; the kwargs
override whatever the envelope carries.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlcipher3

from adminme.events.envelope import EventEnvelope
from adminme.events.registry import (
    EventValidationError,
    SchemaNotFound,
    ensure_autoloaded,
    registry,
)

if TYPE_CHECKING:
    from adminme.lib.instance_config import InstanceConfig

_log = logging.getLogger(__name__)

EVENT_ID_PREFIX = "ev_"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MS_CHARS = 10  # 50 bits of timestamp (plenty through year 37000+)
_CTR_CHARS = 4  # 20 bits (1M events per ms before overflow)
_CTR_MAX = 1 << (5 * _CTR_CHARS)

_ALLOW_UNKNOWN_ENV = "ADMINME_ALLOW_UNKNOWN_SCHEMAS"


class EventLogError(RuntimeError):
    """Base class for event log errors."""


class CursorNotFound(EventLogError):
    """The supplied read cursor does not exist in the log."""


class AppendValidationError(EventLogError):
    """An envelope failed validation before insertion — missing required
    fields, schema not registered (strict mode), or payload rejected by the
    registered Pydantic model."""


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

    def __init__(
        self,
        db_path: "Path | InstanceConfig",
        encryption_key: bytes,
    ) -> None:
        # Prompt 05: EventLog now accepts an InstanceConfig and derives its
        # own path per §15/D15. Raw paths are still accepted for tests and
        # legacy callers — this keeps prompt-03/04 test fixtures working
        # without forcing a flag-day rewrite.
        from adminme.lib.instance_config import InstanceConfig as _InstanceConfig

        if isinstance(db_path, _InstanceConfig):
            resolved: Path = db_path.event_log_path
        else:
            resolved = Path(db_path)
        self._db_path = resolved
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_pragma = _format_key(encryption_key)
        self._conn = self._open_connection()
        self._lock = asyncio.Lock()
        self._id_gen = _EventIdGenerator()
        self._closed = False
        self._migrate()
        ensure_autoloaded()

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
    async def append(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        ids = await self.append_batch(
            [envelope],
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return ids[0]

    async def append_batch(
        self,
        envelopes: list[EventEnvelope],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[str]:
        if not envelopes:
            return []
        prepared: list[tuple[tuple[Any, ...], str]] = []
        for env in envelopes:
            row, event_id = self._prepare_row(
                env,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            prepared.append((row, event_id))

        async with self._lock:
            await asyncio.to_thread(self._insert_rows, [r for r, _ in prepared])
        return [eid for _, eid in prepared]

    def _prepare_row(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> tuple[tuple[Any, ...], str]:
        if not isinstance(envelope, EventEnvelope):
            raise AppendValidationError(
                f"append() requires an EventEnvelope, got {type(envelope).__name__}"
            )

        # kwargs override envelope-carried values per D8 addition 2
        effective_correlation = (
            correlation_id if correlation_id is not None else envelope.correlation_id
        )
        effective_causation = (
            causation_id if causation_id is not None else envelope.causation_id
        )

        # Validate payload against registered schema
        try:
            registry.validate(envelope.type, envelope.schema_version, envelope.payload)
        except SchemaNotFound:
            if os.environ.get(_ALLOW_UNKNOWN_ENV) == "1":
                _log.warning(
                    "append: no schema registered for %s v%d; proceeding because "
                    "%s=1",
                    envelope.type,
                    envelope.schema_version,
                    _ALLOW_UNKNOWN_ENV,
                )
            else:
                raise AppendValidationError(
                    f"no schema registered for {envelope.type!r} "
                    f"v{envelope.schema_version} (set {_ALLOW_UNKNOWN_ENV}=1 "
                    f"to allow unknown schemas)"
                )
        except EventValidationError as exc:
            raise AppendValidationError(str(exc)) from exc

        try:
            payload_json = json.dumps(
                envelope.payload, separators=(",", ":"), sort_keys=False
            )
        except (TypeError, ValueError) as exc:
            raise AppendValidationError(
                f"payload is not JSON-serializable: {exc}"
            ) from exc

        event_at_ms = int(envelope.event_at_ms or time.time() * 1000)
        if envelope.event_id:
            event_id = envelope.event_id
        else:
            event_id, event_at_ms = self._id_gen.mint(event_at_ms)

        recorded_at = envelope.recorded_at or EventEnvelope.now_utc_iso()

        row = (
            event_id,
            event_at_ms,
            envelope.tenant_id,
            envelope.owner_scope,
            envelope.type,
            envelope.schema_version,      # legacy `version` column (mirror)
            envelope.schema_version,      # canonical schema_version column
            envelope.occurred_at,
            recorded_at,
            envelope.source_adapter,
            envelope.source_account_id,
            envelope.visibility_scope,
            envelope.sensitivity,
            effective_correlation,
            effective_causation,
            None,                          # legacy `source` column — unused
            payload_json,
            envelope.raw_ref,
            envelope.actor_identity,
        )
        return row, event_id

    def _insert_rows(self, rows: list[tuple[Any, ...]]) -> None:
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.executemany(
                "INSERT INTO events"
                "  (event_id, event_at_ms, tenant_id, owner_scope, type, version,"
                "   schema_version, occurred_at, recorded_at, source_adapter,"
                "   source_account_id, visibility_scope, sensitivity,"
                "   correlation_id, causation_id, source, payload,"
                "   raw_ref, actor_identity)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
