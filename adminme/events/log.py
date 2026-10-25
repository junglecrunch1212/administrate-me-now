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
connection guarded by an ``asyncio.Lock`` and pin its creation, migration,
reads, writes, and close to one dedicated worker thread. SQLCipher's native
connection must not migrate among the default executor's worker threads even
when those calls are serialized. Reads and writes therefore remain off the
event loop without allowing native cross-thread connection use.

Signature: ``append(envelope, *, correlation_id=None, causation_id=None)``
per D8 addition 2 — correlation_id and causation_id are explicit keyword
arguments at every call site, never dict-squatted into the envelope
silently. Passing ``None`` is fine when genuinely unknown; the kwargs
override whatever the envelope carries.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.resources
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlcipher3

from adminme.events.envelope import EventEnvelope
from adminme.events.invariants import (
    EnvelopeInvariantError,
    validate_envelope_invariants,
)
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

    def __init__(self, last_native_id: str | None = None) -> None:
        self._lock = threading.Lock()
        self._last_ms = 0
        self._counter = 0
        if last_native_id is not None:
            # The log supplies only the exact native Crockford shape. Opaque
            # historical/UUID IDs remain readable without being reinterpreted.
            encoded = last_native_id.removeprefix(EVENT_ID_PREFIX)
            for character in encoded[:_MS_CHARS]:
                self._last_ms = self._last_ms * 32 + _CROCKFORD.index(character)
            for character in encoded[_MS_CHARS:]:
                self._counter = self._counter * 32 + _CROCKFORD.index(character)

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
        (4, "0004_source_assurance_covering_index.sql"),
        (5, "0005_b03_execution_recovery.sql"),
        (6, "0006_source_assurance_causation_covering_index.sql"),
    )

    def __init__(
        self,
        db_path: "Path | InstanceConfig",
        encryption_key: bytes,
        *,
        read_only: bool = False,
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
        self._read_only = read_only
        if not self._read_only:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_pragma = _format_key(encryption_key)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="adminme-event-log",
        )
        try:
            self._conn = self._executor.submit(self._open_connection).result()
            self._lock = asyncio.Lock()
            self._closed = False
            self._tenant_id_hint = (
                db_path.tenant_id if isinstance(db_path, _InstanceConfig) else None
            )
            if not self._read_only:
                self._executor.submit(self._migrate).result()
            self._id_gen = _EventIdGenerator(
                self._executor.submit(self._latest_native_event_id).result()
            )
            self._metadata_index_hint = self._executor.submit(
                self._metadata_index_hint_for_schema
            ).result()
            ensure_autoloaded()
        except BaseException:
            connection = getattr(self, "_conn", None)
            if connection is not None:
                try:
                    self._executor.submit(connection.close).result()
                except Exception:
                    _log.exception(
                        "failed to close event log after initialization error"
                    )
            self._executor.shutdown(wait=True, cancel_futures=True)
            raise

    def _latest_native_event_id(self) -> str | None:
        """Restore append ordering across restart and a backwards/future clock."""
        native_shape = EVENT_ID_PREFIX + f"[{_CROCKFORD}]" * (_MS_CHARS + _CTR_CHARS)
        row = self._conn.execute(
            "SELECT event_id FROM events WHERE event_id GLOB ? "
            "ORDER BY event_id DESC LIMIT 1",
            (native_shape,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    # ------------------------------------------------------------------
    # connection management
    # ------------------------------------------------------------------
    @property
    def tenant_id(self) -> str | None:
        """Configured tenant when the log was opened from InstanceConfig."""

        return self._tenant_id_hint

    def _open_connection(self) -> sqlcipher3.Connection:
        target = (
            f"{self._db_path.resolve().as_uri()}?mode=ro"
            if self._read_only
            else str(self._db_path)
        )
        connect_options: dict[str, Any] = {
            "isolation_level": None,  # we drive transactions explicitly
            "check_same_thread": False,
        }
        if self._read_only:
            connect_options["uri"] = True
        conn: sqlcipher3.Connection | None = None
        try:
            conn = sqlcipher3.connect(target, **connect_options)
            conn.row_factory = sqlcipher3.Row
            conn.execute(f'PRAGMA key = "{self._key_pragma}"')
            if self._read_only:
                conn.execute("PRAGMA query_only=ON")
            else:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # Touch the DB to surface bad-key errors eagerly.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            return conn
        except BaseException:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    _log.exception("failed to close rejected event log connection")
            raise

    def _metadata_index_hint_for_schema(self) -> str:
        # Read-only backup inspection must not require a newer migration.
        # Resolve this once on the connection worker, never on each audit page.
        preferred_indexes = (
            "idx_events_type_id_time_causation",
            "idx_events_type_id_time",
        )
        available = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name IN (?, ?)",
                preferred_indexes,
            )
        }
        for name in preferred_indexes:
            if name in available:
                return f" INDEXED BY {name}"
        return ""

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
        # Closing a SQLCipher connection on another thread, or while an
        # in-flight call owns it, can crash in native code. The same lock and
        # dedicated worker guard every operation, so close is queued behind
        # in-flight work and repeated shutdown is harmless.
        should_shutdown = False
        close_call: concurrent.futures.Future[Any] | None = None
        caller_cancelled = False
        try:
            async with self._lock:
                if self._closed:
                    return
                should_shutdown = True
                try:
                    # Submit synchronously before the first cancellation point.
                    # A cancelled operation may still occupy this worker, so the
                    # close must remain queued rather than being cancelled during
                    # executor shutdown.
                    close_call = self._executor.submit(self._conn.close)
                    await asyncio.shield(asyncio.wrap_future(close_call))
                except asyncio.CancelledError:
                    caller_cancelled = True
                    raise
                finally:
                    self._closed = True
        finally:
            if should_shutdown:
                self._executor.shutdown(wait=True, cancel_futures=False)
                if caller_cancelled and close_call is not None:
                    try:
                        close_call.result()
                    except Exception:
                        _log.exception(
                            "native event-log close failed after caller cancellation"
                        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise EventLogError("event log is closed")

    async def _run_locked(self, func: Any, *args: Any) -> Any:
        async with self._lock:
            self._ensure_open()
            return await self._run_on_connection_thread(func, *args)

    async def _run_on_connection_thread(self, func: Any, *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, func, *args)

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
            resolved_ids = await self._run_on_connection_thread(
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
            validate_envelope_invariants(envelope)
        except EnvelopeInvariantError as exc:
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

        # Observation boundaries retain microseconds. Truncating this local
        # ingestion timestamp can place a later authorized effect before its
        # activation boundary. Explicit historical timestamps remain untouched.
        recorded_at = envelope.recorded_at or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

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
        through: str | None = None,
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
                through,
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
        if not types:
            raise ValueError("metadata reads require an explicit type allowlist")
        requested_types = tuple(dict.fromkeys(types))
        payload_type_set = set(include_payload_for or ()) & set(requested_types)
        payload_types = tuple(
            event_type
            for event_type in requested_types
            if event_type in payload_type_set
        )
        metadata_types = tuple(
            event_type
            for event_type in requested_types
            if event_type not in payload_type_set
        )
        while True:
            rows = await self._run_locked(
                self._read_metadata_page,
                last,
                through,
                batch_size,
                metadata_types,
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
                    "causation_id": row["causation_id"],
                    "payload": json.loads(payload) if payload is not None else None,
                }
            last = rows[-1]["event_id"]
            if len(rows) < batch_size:
                return

    async def read_attribution_metadata_since(
        self,
        cursor: str | None = None,
        *,
        through: str | None = None,
        limit: int = 5000,
        types: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Read content-free ownership/provenance coordinates for an audit."""

        if limit <= 0:
            raise ValueError("limit must be positive")
        if not types:
            raise ValueError("attribution reads require an explicit type allowlist")
        requested_types = tuple(dict.fromkeys(types))
        last = cursor
        batch_size = max(limit, 500)
        while True:
            rows = await self._run_locked(
                self._read_attribution_page,
                last,
                through,
                batch_size,
                requested_types,
            )
            if not rows:
                return
            for row in rows:
                yield {
                    "event_id": row["event_id"],
                    "type": row["type"],
                    "occurred_at": row["occurred_at"],
                    "recorded_at": row["recorded_at"],
                    "owner_scope": row["owner_scope"],
                    "source_adapter": row["source_adapter"],
                    "source_account_id": row["source_account_id"],
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
        through: str | None,
        limit: int,
        owner_scope: str | None,
        types: tuple[str, ...] | None,
    ) -> list[sqlcipher3.Row]:
        where: list[str] = []
        params: list[Any] = []
        if after_id is not None:
            where.append("event_id > ?")
            params.append(after_id)
        if through is not None:
            where.append("event_id <= ?")
            params.append(through)
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
        metadata_types: tuple[str, ...],
        payload_types: tuple[str, ...],
    ) -> list[sqlcipher3.Row]:
        def branch_where(branch_types: tuple[str, ...]) -> tuple[str, list[Any]]:
            placeholders = ",".join("?" for _ in branch_types)
            where = [f"type IN ({placeholders})"]
            params: list[Any] = list(branch_types)
            if after_id is not None:
                where.append("event_id > ?")
                params.append(after_id)
            if through is not None:
                where.append("event_id <= ?")
                params.append(through)
            return " AND ".join(where), params

        branches: list[str] = []
        params: list[Any] = []
        if metadata_types:
            metadata_where, metadata_params = branch_where(metadata_types)
            branches.append(
                "SELECT event_id, type, occurred_at, recorded_at, causation_id, "
                "NULL AS payload "
                f"FROM events{self._metadata_index_hint} WHERE " + metadata_where
            )
            params.extend(metadata_params)
        if payload_types:
            payload_where, payload_params = branch_where(payload_types)
            branches.append(
                "SELECT event_id, type, occurred_at, recorded_at, causation_id, payload "
                "FROM events WHERE " + payload_where
            )
            params.extend(payload_params)
        if not branches:
            return []

        sql = " UNION ALL ".join(branches) + " ORDER BY event_id ASC LIMIT ?"
        params.append(limit)
        cur = self._conn.cursor()
        return list(cur.execute(sql, params))

    def _read_attribution_page(
        self,
        after_id: str | None,
        through: str | None,
        limit: int,
        types: tuple[str, ...],
    ) -> list[sqlcipher3.Row]:
        placeholders = ",".join("?" for _ in types)
        where = [f"type IN ({placeholders})"]
        params: list[Any] = list(types)
        if after_id is not None:
            where.append("event_id > ?")
            params.append(after_id)
        if through is not None:
            where.append("event_id <= ?")
            params.append(through)
        params.append(limit)
        sql = (
            "SELECT event_id, type, occurred_at, recorded_at, owner_scope, "
            "source_adapter, source_account_id FROM events WHERE "
            + " AND ".join(where)
            + " ORDER BY event_id ASC LIMIT ?"
        )
        return list(self._conn.execute(sql, params))

    async def read_finance_metadata_control(
        self, tenant_id: str, owner_id: str
    ) -> dict[str, Any] | None:
        """One bounded latest operational control on the already-open worker."""
        if not tenant_id or not owner_id:
            raise ValueError("exact tenant and owner are required")
        return await self._run_locked(
            self._read_finance_metadata_control, tenant_id, owner_id
        )

    def _read_finance_metadata_control(
        self, tenant_id: str, owner_id: str
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + 0.1
        steps = 0

        def bounded() -> int:
            nonlocal steps
            steps += 1000
            return int(steps > 100_000 or time.monotonic() > deadline)

        self._conn.set_progress_handler(bounded, 1000)
        try:
            row = self._conn.execute(
                "SELECT event_id,schema_version,actor_identity,visibility_scope,"
                "CASE WHEN length(CAST(payload AS BLOB))<=4096 THEN payload END AS payload "
                "FROM events WHERE tenant_id=? AND owner_scope=? "
                "AND type='settings.finance_metadata_changed' ORDER BY event_id DESC LIMIT 1",
                (tenant_id, f"private:{owner_id}"),
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["payload"] = json.loads(value["payload"])
            return value
        except (sqlcipher3.DatabaseError, TypeError, ValueError):
            raise RuntimeError("finance_metadata_control_unavailable") from None
        finally:
            self._conn.set_progress_handler(None, 0)

    async def read_finance_binding_metadata(
        self, tenant_id: str, owner_id: str
    ) -> dict[str, Any]:
        """Fixed owner-only scalar projection on the already-open log connection.

        No payload leaves the SQL worker. One transaction fixes the watermark;
        row/byte/VM/time bounds fail closed instead of claiming empty history.
        This storage primitive grants no authority; callers authenticate first.
        """
        if not tenant_id or not owner_id:
            raise ValueError("exact tenant and owner are required")
        return await self._run_locked(
            self._read_finance_binding_metadata, tenant_id, owner_id
        )

    def _read_finance_binding_metadata(
        self, tenant_id: str, owner_id: str
    ) -> dict[str, Any]:
        scope = f"private:{owner_id}"
        types = ("finance.publication_accepted", "finance.projection_verified")
        where = "tenant_id=? AND owner_scope=? AND type IN (?,?)"
        args = (tenant_id, scope, *types)
        result: dict[str, Any] = {
            "state": "history_incomplete",
            "through_event_id": None,
            "rows": [],
        }
        deadline = time.monotonic() + 0.1
        steps = 0

        def bounded() -> int:
            nonlocal steps
            steps += 1000
            return int(steps > 100_000 or time.monotonic() > deadline)

        fields = (
            "tenant_id",
            "owner_id",
            "book_id",
            "entity_id",
            "audience_policy_id",
            "audience_policy_version",
            "content_sha256",
            "publication_id",
            "prior_publication_id",
            "projection_id",
            "prior_projection_id",
            "accounting_publication_id",
            "accounting_content_sha256",
        )
        self._conn.set_progress_handler(bounded, 1000)
        try:
            self._conn.execute("BEGIN")
            head = self._conn.execute(
                "SELECT event_id FROM events WHERE "
                + where
                + " ORDER BY event_id DESC LIMIT 1",
                args,
            ).fetchone()
            result["through_event_id"] = head[0] if head else None
            headers = list(
                self._conn.execute(
                    "SELECT event_id,type,schema_version,visibility_scope,"
                    "length(CAST(payload AS BLOB)) AS payload_bytes FROM events WHERE "
                    + where
                    + " ORDER BY event_id ASC LIMIT 257",
                    args,
                )
            )
            if (
                len(headers) > 256
                or sum(h["payload_bytes"] for h in headers) > 33_554_432
            ):
                result["reason"] = "history_scan_limit"
                return result
            if any(h["schema_version"] != 1 for h in headers):
                result.update(
                    state="schema_unavailable", reason="unsupported_event_version"
                )
                return result
            if any(h["visibility_scope"] != scope for h in headers):
                result["reason"] = "history_scope_mismatch"
                return result
            for header in headers:
                root = "publication" if header["type"] == types[0] else "projection"
                columns: list[str] = []
                for field in fields:
                    path = f"$.{root}.{field}"
                    # Bound even corrupted scalar data before crossing the worker.
                    columns.extend(
                        (
                            f"substr(json_extract(payload, '{path}'),1,161) AS {field}",
                            f"json_type(payload, '{path}') AS {field}_type",
                        )
                    )
                row = self._conn.execute(
                    "SELECT " + ",".join(columns) + " FROM events WHERE event_id=? "
                    "AND tenant_id=? AND owner_scope=?",
                    (header["event_id"], tenant_id, scope),
                ).fetchone()
                result["rows"].append(
                    {
                        "event_id": header["event_id"],
                        "type": header["type"],
                        **dict(row),
                    }
                )
                if time.monotonic() > deadline:
                    result["rows"] = []
                    result["reason"] = "history_scan_limit"
                    return result
            result["state"] = "complete"
            return result
        except sqlcipher3.DatabaseError:
            # No database diagnostics or partially extracted coordinates escape.
            result.update(
                state="history_incomplete", rows=[], reason="history_read_unavailable"
            )
            return result
        finally:
            self._conn.set_progress_handler(None, 0)
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")

    async def get(self, event_id: str) -> dict[str, Any] | None:
        row = await self._run_locked(self._get_one, event_id)
        return _row_to_event(row) if row else None

    async def get_bridge_attribution_coordinates(
        self, event_id: str
    ) -> dict[str, Any] | None:
        """Read exact bridge ownership coordinates without provider content.

        The fixed scalar allowlist excludes note bodies, event descriptions,
        titles, attachments, and other source content from historical auditing.
        """
        return await self._run_locked(
            self._get_bridge_attribution_coordinates, event_id
        )

    def _get_bridge_attribution_coordinates(
        self, event_id: str
    ) -> dict[str, Any] | None:
        keys = (
            "member_id",
            "source_kind",
            "provider",
            "provider_account_id",
            "calendar_identifier",
            "calendar_external_id",
            "calendar_id",
            "target_list_identifier",
            "destination_list_external_id",
            "list_external_id",
            "list_identifier",
        )
        columns = ", ".join(
            f"json_extract(payload, '$.{key}') AS coordinate_{key}" for key in keys
        )
        row = self._conn.execute(
            "SELECT event_id, tenant_id, type, schema_version, owner_scope, "
            "visibility_scope, source_adapter, source_account_id, actor_identity, "
            + columns
            + " FROM events WHERE event_id = ? AND source_adapter IN "
            "('calendaring-apple-calendar', 'reminders-apple-eventkit', 'knowledge-apple-notes')",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["payload"] = {
            key: value.pop(f"coordinate_{key}")
            for key in keys
            if value[f"coordinate_{key}"] is not None
        }
        for key in keys:
            value.pop(f"coordinate_{key}", None)
        return value

    def _get_one(self, event_id: str) -> sqlcipher3.Row | None:
        cur = self._conn.cursor()
        cur.execute(
            _SELECT_COLUMNS + " FROM events WHERE event_id = ?",
            (event_id,),
        )
        return cur.fetchone()

    async def get_event_headers(
        self, tenant_id: str, event_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Read bounded exact identity headers without payload or raw content."""
        if len(event_ids) > 32:
            raise ValueError("at most 32 exact event IDs may be read")
        if any(not isinstance(event_id, str) or not event_id for event_id in event_ids):
            raise ValueError("event IDs must be nonempty strings")
        ids = tuple(dict.fromkeys(event_ids))
        if not ids:
            return []
        rows = await self._run_locked(self._get_event_headers, tenant_id, ids)
        return [dict(row) for row in rows]

    def _get_event_headers(
        self, tenant_id: str, event_ids: tuple[str, ...]
    ) -> list[sqlcipher3.Row]:
        placeholders = ",".join("?" for _ in event_ids)
        return list(
            self._conn.execute(
                "SELECT event_id, tenant_id, type, schema_version, occurred_at, "
                "recorded_at, source_adapter, source_account_id, owner_scope, "
                "visibility_scope, sensitivity, causation_id, idempotency_key "
                # 0001 creates this immutable event_id primary key. Force
                # exact probes even when a tenant index has misleading stats;
                # missing schema invariants fail instead of widening the read.
                "FROM events INDEXED BY sqlite_autoindex_events_1 "
                f"WHERE tenant_id = ? AND event_id IN ({placeholders}) "
                "ORDER BY event_id ASC",
                (tenant_id, *event_ids),
            )
        )

    async def get_by_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        rows = await self._run_locked(self._get_correlation, correlation_id)
        return [_row_to_event(r) for r in rows]

    async def get_causal_event_headers(
        self,
        tenant_id: str,
        causation_id: str,
        *,
        types: list[str],
        through: str,
    ) -> list[dict[str, Any]]:
        """Read one bounded causal identity family through an immutable cutoff."""
        if not causation_id or not through:
            raise ValueError("causal header reads require identity and cutoff")
        if (
            not types
            or len(types) > 32
            or any(not isinstance(value, str) or not value for value in types)
        ):
            raise ValueError("causal header reads require at most 32 explicit types")
        rows = await self._run_locked(
            self._get_causal_event_headers,
            tenant_id,
            causation_id,
            tuple(dict.fromkeys(types)),
            through,
        )
        if len(rows) > 256:
            raise EventLogError("causal event header ceiling exceeded")
        return [dict(row) for row in rows]

    def _get_causal_event_headers(
        self, tenant_id: str, causation_id: str, types: tuple[str, ...], through: str
    ) -> list[sqlcipher3.Row]:
        placeholders = ",".join("?" for _ in types)
        return list(
            self._conn.execute(
                "SELECT event_id, tenant_id, type, schema_version, occurred_at, "
                "recorded_at, source_adapter, source_account_id, owner_scope, "
                "visibility_scope, sensitivity, causation_id, idempotency_key "
                "FROM events INDEXED BY idx_events_causation "
                f"WHERE tenant_id = ? AND causation_id = ? AND type IN ({placeholders}) "
                "AND event_id <= ? ORDER BY event_id ASC LIMIT 257",
                (tenant_id, causation_id, *types, through),
            )
        )

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

    async def get_by_idempotency_keys(
        self, tenant_id: str, idempotency_keys: list[str]
    ) -> list[dict[str, Any]]:
        """Read one bounded, tenant-scoped group of exact immutable receipts."""
        if len(idempotency_keys) > 32:
            raise ValueError("at most 32 exact idempotency keys may be read")
        if any(not isinstance(key, str) or not key for key in idempotency_keys):
            raise ValueError("idempotency keys must be nonempty strings")
        keys = tuple(dict.fromkeys(idempotency_keys))
        if not keys:
            return []
        rows = await self._run_locked(self._get_idempotency_keys, tenant_id, keys)
        return [_row_to_event(row) for row in rows]

    async def get_by_idempotency_prefix(
        self, tenant_id: str, prefix: str
    ) -> list[dict[str, Any]]:
        """Read at most 32 legacy receipts for one delimited native ID.

        A binary range uses the existing tenant/idempotency index, never a
        payload scan or LIKE (native IDs may contain wildcard characters).
        Refuse overflow rather than silently overlooking a committed receipt.
        Callers must still verify the complete source identity and key suffix.
        """
        if (
            not tenant_id
            or not prefix
            or not prefix.isascii()
            or not prefix.endswith(":")
            or len(prefix) > 600
            or any(ord(char) < 32 for char in prefix)
        ):
            raise ValueError("receipt prefix must be a bounded ASCII delimiter prefix")
        rows = await self._run_locked(self._get_idempotency_prefix, tenant_id, prefix)
        if len(rows) > 32:
            raise EventLogError("legacy source receipt lookup exceeds 32 candidates")
        return [_row_to_event(row) for row in rows]

    def _get_idempotency_prefix(
        self, tenant_id: str, prefix: str
    ) -> list[sqlcipher3.Row]:
        return list(
            self._conn.execute(
                _SELECT_COLUMNS
                + " FROM events WHERE tenant_id = ? AND idempotency_key >= ?"
                + " AND idempotency_key < ? ORDER BY idempotency_key LIMIT 33",
                (tenant_id, prefix, prefix[:-1] + ";"),
            )
        )

    def _get_idempotency_keys(
        self, tenant_id: str, keys: tuple[str, ...]
    ) -> list[sqlcipher3.Row]:
        placeholders = ",".join("?" for _ in keys)
        return list(
            self._conn.execute(
                _SELECT_COLUMNS
                + f" FROM events WHERE tenant_id = ? AND idempotency_key IN ({placeholders})"
                + " ORDER BY event_id ASC",
                (tenant_id, *keys),
            )
        )

    def _get_correlation(self, correlation_id: str) -> list[sqlcipher3.Row]:
        cur = self._conn.cursor()
        return list(
            cur.execute(
                _SELECT_COLUMNS
                + " FROM events WHERE correlation_id = ? ORDER BY event_id ASC",
                (correlation_id,),
            )
        )

    async def latest_event_id(self, *, types: list[str] | None = None) -> str | None:
        return await self._run_locked(
            self._latest_event_id, tuple(types) if types else None
        )

    def _latest_event_id(self, types: tuple[str, ...] | None = None) -> str | None:
        cur = self._conn.cursor()
        where = ""
        if types:
            placeholders = ",".join("?" for _ in types)
            where = f" WHERE type IN ({placeholders})"
        row = cur.execute(
            "SELECT event_id FROM events" + where + " ORDER BY event_id DESC LIMIT 1",
            types or (),
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
