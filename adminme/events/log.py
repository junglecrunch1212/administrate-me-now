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

Async model: ``sqlcipher3`` is a synchronous DB-API driver and there
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


class IdempotencyConflict(EventLogError):
    """An idempotency key was reused outside its original event identity."""


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
        (3, "0003_idempotency_keys.sql"),
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
        self._tenant_id_hint = (
            db_path.tenant_id if isinstance(db_path, _InstanceConfig) else None
        )
        self._migrate()
        ensure_autoloaded()

    # ------------------------------------------------------------------
    # connection management
    # ------------------------------------------------------------------
    @property
    def tenant_id(self) -> str | None:
        """Configured tenant when the log was opened from InstanceConfig."""

        return self._tenant_id_hint

    def _open_connection(self) -> sqlcipher3.Connection:
        conn = sqlcipher3.connect(
            str(self._db_path),
            isolation_level=None,  # we drive transactions explicitly
            check_same_thread=False,
        )
        conn.row_factory = sqlcipher3.Row
        conn.execute(f'PRAGMA key = "{self._key_pragma}"')
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
        # Closing a SQLCipher connection while an ``asyncio.to_thread`` DB
        # call still owns it can crash in native code.  The same lock guards
        # every connection operation, so close is queued behind in-flight
        # work and repeated shutdown is harmless.
        async with self._lock:
            if self._closed:
                return
            await asyncio.to_thread(self._conn.close)
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise EventLogError("event log is closed")

    async def _run_locked(self, func: Any, *args: Any) -> Any:
        async with self._lock:
            self._ensure_open()
            return await asyncio.to_thread(func, *args)

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
            self._ensure_open()
            resolved_ids = await asyncio.to_thread(
                self._insert_rows, [r for r, _ in prepared]
            )
        return resolved_ids

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
                    "append: no schema registered for %s v%d; proceeding because %s=1",
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
            envelope.schema_version,  # legacy `version` column (mirror)
            envelope.schema_version,  # canonical schema_version column
            envelope.occurred_at,
            recorded_at,
            envelope.source_adapter,
            envelope.source_account_id,
            envelope.visibility_scope,
            envelope.sensitivity,
            effective_correlation,
            effective_causation,
            None,  # legacy `source` column — unused
            payload_json,
            envelope.raw_ref,
            envelope.actor_identity,
            envelope.idempotency_key,
        )
        return row, event_id

    def _insert_rows(self, rows: list[tuple[Any, ...]]) -> list[str]:
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        resolved_ids: list[str] = []
        try:
            for row in rows:
                try:
                    cur.execute(
                        "INSERT INTO events"
                        "  (event_id, event_at_ms, tenant_id, owner_scope, type, version,"
                        "   schema_version, occurred_at, recorded_at, source_adapter,"
                        "   source_account_id, visibility_scope, sensitivity,"
                        "   correlation_id, causation_id, source, payload,"
                        "   raw_ref, actor_identity, idempotency_key)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        row,
                    )
                    resolved_ids.append(str(row[0]))
                except sqlcipher3.IntegrityError as exc:
                    idempotency_key = row[19]
                    if idempotency_key is None:
                        raise
                    existing = cur.execute(
                        "SELECT event_id, tenant_id, owner_scope, type, schema_version, "
                        "source_adapter, source_account_id, visibility_scope, sensitivity, "
                        "payload, raw_ref "
                        "FROM events WHERE tenant_id = ? AND idempotency_key = ?",
                        (row[2], idempotency_key),
                    ).fetchone()
                    if existing is None:
                        raise
                    stored_identity = (
                        existing["owner_scope"],
                        existing["type"],
                        existing["schema_version"],
                        existing["source_adapter"],
                        existing["source_account_id"],
                        existing["visibility_scope"],
                        existing["sensitivity"],
                        existing["payload"],
                        existing["raw_ref"],
                    )
                    incoming_identity = (
                        row[3],
                        row[4],
                        row[6],
                        row[9],
                        row[10],
                        row[11],
                        row[12],
                        row[16],
                        row[17],
                    )
                    if stored_identity != incoming_identity:
                        raise IdempotencyConflict(
                            "idempotency key already belongs to a different event identity"
                        ) from exc
                    resolved_ids.append(str(existing["event_id"]))
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        return resolved_ids

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
            exists = await self._run_locked(self._event_exists, cursor)
            if not exists:
                raise CursorNotFound(cursor)

        batch_size = max(limit, 500)
        last = cursor
        while True:
            rows = await self._run_locked(
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

    async def read_metadata_since(
        self,
        cursor: str | None = None,
        *,
        through: str | None = None,
        limit: int = 5000,
        types: list[str] | None = None,
        include_payload_for: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Read a bounded event metadata slice without materializing envelopes.

        Health and reconciliation policies usually need ordering, type, and
        timestamps but not the potentially large provider payload on every
        source fact. ``include_payload_for`` selectively includes payloads for
        the terminal event types whose typed result fields are required. A
        fixed ``through`` watermark makes one audit deterministic while new
        events continue to append.
        """

        if limit <= 0:
            raise ValueError("limit must be positive")
        if cursor is not None:
            exists = await self._run_locked(self._event_exists, cursor)
            if not exists:
                raise CursorNotFound(cursor)

        batch_size = max(limit, 500)
        last = cursor
        payload_types = tuple(include_payload_for or ())
        while True:
            rows = await self._run_locked(
                self._read_metadata_page,
                last,
                through,
                batch_size,
                tuple(types) if types else None,
                payload_types,
            )
            if not rows:
                return
            for row in rows:
                payload = row["payload"]
                yield {
                    "event_id": row["event_id"],
                    "type": row["type"],
                    "occurred_at": row["occurred_at"],
                    "recorded_at": row["recorded_at"],
                    "payload": json.loads(payload) if payload is not None else None,
                }
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

    def _read_metadata_page(
        self,
        after_id: str | None,
        through: str | None,
        limit: int,
        types: tuple[str, ...] | None,
        payload_types: tuple[str, ...],
    ) -> list[sqlcipher3.Row]:
        where: list[str] = []
        where_params: list[Any] = []
        if after_id is not None:
            where.append("event_id > ?")
            where_params.append(after_id)
        if through is not None:
            where.append("event_id <= ?")
            where_params.append(through)
        if types:
            placeholders = ",".join("?" for _ in types)
            where.append(f"type IN ({placeholders})")
            where_params.extend(types)

        if payload_types:
            payload_placeholders = ",".join("?" for _ in payload_types)
            payload_column = (
                f"CASE WHEN type IN ({payload_placeholders}) "
                "THEN payload ELSE NULL END AS payload"
            )
            params: list[Any] = [*payload_types, *where_params]
        else:
            payload_column = "NULL AS payload"
            params = where_params

        sql = (
            "SELECT event_id, type, occurred_at, recorded_at, "
            + payload_column
            + " FROM events"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY event_id ASC LIMIT ?"
        params.append(limit)
        cur = self._conn.cursor()
        return list(cur.execute(sql, params))

    async def get(self, event_id: str) -> dict[str, Any] | None:
        row = await self._run_locked(self._get_one, event_id)
        return _row_to_event(row) if row else None

    def _get_one(self, event_id: str) -> sqlcipher3.Row | None:
        cur = self._conn.cursor()
        cur.execute(
            _SELECT_COLUMNS + " FROM events WHERE event_id = ?",
            (event_id,),
        )
        return cur.fetchone()

    async def get_by_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        rows = await self._run_locked(self._get_correlation, correlation_id)
        return [_row_to_event(r) for r in rows]

    async def get_by_causation(self, causation_id: str) -> list[dict[str, Any]]:
        rows = await self._run_locked(self._get_causation, causation_id)
        return [_row_to_event(r) for r in rows]

    def _get_causation(self, causation_id: str) -> list[sqlcipher3.Row]:
        cur = self._conn.cursor()
        return list(
            cur.execute(
                _SELECT_COLUMNS
                + " FROM events WHERE causation_id = ? ORDER BY event_id ASC",
                (causation_id,),
            )
        )

    async def get_by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = await self._run_locked(self._get_idempotency, tenant_id, idempotency_key)
        return _row_to_event(row) if row else None

    def _get_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> sqlcipher3.Row | None:
        cur = self._conn.cursor()
        return cur.execute(
            _SELECT_COLUMNS
            + " FROM events WHERE tenant_id = ? AND idempotency_key = ?",
            (tenant_id, idempotency_key),
        ).fetchone()

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
        return await self._run_locked(self._latest_event_id)

    def _latest_event_id(self) -> str | None:
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT event_id FROM events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    async def count(self) -> int:
        return await self._run_locked(self._count)

    def _count(self) -> int:
        cur = self._conn.cursor()
        return int(cur.execute("SELECT count(*) FROM events").fetchone()[0])

    async def count_since(
        self,
        cursor: str | None,
        *,
        types: list[str] | None = None,
    ) -> int:
        return await self._run_locked(
            self._count_since,
            cursor,
            tuple(types) if types else None,
        )

    def _count_since(
        self,
        cursor: str | None,
        types: tuple[str, ...] | None,
    ) -> int:
        cur = self._conn.cursor()
        where: list[str] = []
        params: list[Any] = []
        if cursor is not None:
            where.append("event_id > ?")
            params.append(cursor)
        if types:
            placeholders = ",".join("?" for _ in types)
            where.append(f"type IN ({placeholders})")
            params.extend(types)
        sql = "SELECT count(*) FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return int(cur.execute(sql, params).fetchone()[0])


_SELECT_COLUMNS = (
    "SELECT event_id, event_at_ms, tenant_id, owner_scope, type, version,"
    "       schema_version, occurred_at, recorded_at, source_adapter,"
    "       source_account_id, visibility_scope, sensitivity,"
    "       correlation_id, causation_id, source, payload,"
    "       raw_ref, actor_identity, idempotency_key"
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
        "idempotency_key": row["idempotency_key"],
    }


def new_correlation_id() -> str:
    """Generate a fresh correlation_id — 22 chars of hex (88 bits of entropy).

    Correlation IDs are opaque strings; anything unique suffices. This helper
    exists so callers who don't already have one can mint one without pulling
    in a new dep.
    """
    return secrets.token_hex(11)
