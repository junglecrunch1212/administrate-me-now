"""
Unit tests for adminme.events.log.EventLog (prompt 03, updated for prompt 04).

Covers prompt-03 deliverables: append/read/filter/persistence/encryption/
id-ordering semantics. Prompt 04 shifted ``append`` to take an
``EventEnvelope`` and wired schema validation; the ``_event`` fixture below
now builds envelopes, and test payloads register via the schema registry
at module import.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest
import sqlcipher3
from pydantic import BaseModel

from adminme.events.envelope import EventEnvelope
from adminme.events.log import (
    AppendValidationError,
    CursorNotFound,
    EventLog,
    EventLogError,
    IdempotencyConflict,
    new_correlation_id,
)
from adminme.events.registry import registry

TEST_KEY = b"k" * 32


class _TestEventPayloadV1(BaseModel):
    model_config = {"extra": "forbid"}
    i: int


# Register the payload shape used by _event() so validation passes. Tests
# that intentionally use the unknown-schema path set ADMINME_ALLOW_UNKNOWN_SCHEMAS
# directly.
for _name in ("test.event", "foo", "bar"):
    if registry.get(_name, 1) is None:
        registry.register(_name, 1, _TestEventPayloadV1)


def _event(
    i: int = 0,
    *,
    type: str = "test.event",
    owner_scope: str = "shared:household",
    tenant_id: str = "tenant-a",
    correlation_id: str | None = None,
    event_at_ms: int | None = None,
    event_id: str = "",
    idempotency_key: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_at_ms=event_at_ms if event_at_ms is not None else int(time.time() * 1000),
        tenant_id=tenant_id,
        type=type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test:inproc",
        source_account_id="test-account",
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        payload={"i": i},
    )


async def _collect(log: EventLog, **kwargs) -> list[dict]:
    out = []
    async for ev in log.read_since(**kwargs):
        out.append(ev)
    return out


@pytest.fixture
async def log(tmp_path: Path):
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    try:
        yield log
    finally:
        await log.close()


async def test_append_and_read_single(log: EventLog) -> None:
    eid = await log.append(_event(1))
    got = await log.get(eid)
    assert got is not None
    assert got["event_id"] == eid
    assert got["type"] == "test.event"
    assert got["payload"] == {"i": 1}
    assert got["tenant_id"] == "tenant-a"
    assert got["owner_scope"] == "shared:household"
    assert got["version"] == 1
    assert got["correlation_id"] is None


async def test_append_many_preserves_order(log: EventLog) -> None:
    ids = []
    for i in range(1000):
        ids.append(await log.append(_event(i)))
    assert ids == sorted(ids)  # monotonic
    events = await _collect(log)
    assert len(events) == 1000
    assert [e["event_id"] for e in events] == ids
    assert [e["payload"]["i"] for e in events] == list(range(1000))


async def test_append_with_explicit_event_id(log: EventLog) -> None:
    custom_id = "ev_99999999999999"
    ev = _event(7, event_id=custom_id)
    returned = await log.append(ev)
    assert returned == custom_id
    got = await log.get(custom_id)
    assert got is not None and got["event_id"] == custom_id


async def test_append_batch_atomic(log: EventLog) -> None:
    batch = [_event(i) for i in range(100)]
    ids = await log.append_batch(batch)
    assert len(ids) == 100
    assert await log.count() == 100
    for ev in await _collect(log):
        assert ev["event_id"] in ids


async def test_idempotency_key_returns_original_committed_event(log: EventLog) -> None:
    first = await log.append(_event(1, idempotency_key="request:one"))
    duplicate = await log.append(_event(1, idempotency_key="request:one"))
    assert duplicate == first
    assert await log.count() == 1
    stored = await log.get_by_idempotency("tenant-a", "request:one")
    assert stored is not None and stored["event_id"] == first


async def test_idempotency_key_rejects_different_event_family(log: EventLog) -> None:
    await log.append(_event(1, type="foo", idempotency_key="request:conflict"))
    with pytest.raises(IdempotencyConflict):
        await log.append(_event(1, type="bar", idempotency_key="request:conflict"))


async def test_idempotency_key_rejects_different_payload(log: EventLog) -> None:
    await log.append(_event(1, idempotency_key="request:payload-conflict"))
    with pytest.raises(IdempotencyConflict):
        await log.append(_event(2, idempotency_key="request:payload-conflict"))


async def test_get_by_correlation(log: EventLog) -> None:
    cid = new_correlation_id()
    other = new_correlation_id()
    await log.append(_event(1, correlation_id=cid))
    await log.append(_event(2, correlation_id=other))
    await log.append(_event(3, correlation_id=cid))
    await log.append(_event(4, correlation_id=cid))
    got = await log.get_by_correlation(cid)
    assert [e["payload"]["i"] for e in got] == [1, 3, 4]
    assert await log.get_by_correlation(new_correlation_id()) == []


async def test_read_since_cursor_exclusive(log: EventLog) -> None:
    ids = [await log.append(_event(i)) for i in range(5)]
    rest = await _collect(log, cursor=ids[0])
    assert [e["event_id"] for e in rest] == ids[1:]


async def test_read_since_stops_at_fixed_watermark(log: EventLog) -> None:
    first = await log.append(_event(1))
    through = await log.latest_event_id()
    second = await log.append(_event(2))

    bounded = await _collect(log, through=through)
    complete = await _collect(log)

    assert [event["event_id"] for event in bounded] == [first]
    assert [event["event_id"] for event in complete] == [first, second]


async def test_read_since_filters_by_type(log: EventLog) -> None:
    await log.append(_event(1, type="foo"))
    await log.append(_event(2, type="bar"))
    await log.append(_event(3, type="foo"))
    foos = await _collect(log, types=["foo"])
    assert [e["payload"]["i"] for e in foos] == [1, 3]
    both = await _collect(log, types=["foo", "bar"])
    assert [e["payload"]["i"] for e in both] == [1, 2, 3]


async def test_read_since_filters_by_owner_scope(log: EventLog) -> None:
    await log.append(_event(1, owner_scope="private:m1"))
    await log.append(_event(2, owner_scope="shared:household"))
    await log.append(_event(3, owner_scope="private:m1"))
    only = await _collect(log, owner_scope="private:m1")
    assert [e["payload"]["i"] for e in only] == [1, 3]


async def test_read_since_unknown_cursor_raises(log: EventLog) -> None:
    await log.append(_event(1))
    with pytest.raises(CursorNotFound):
        await _collect(log, cursor="ev_zzzzzzzzzzzzzz")


async def test_persistence_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    log = EventLog(db, TEST_KEY)
    ids = [await log.append(_event(i)) for i in range(10)]
    await log.close()

    reopened = EventLog(db, TEST_KEY)
    try:
        assert await reopened.count() == 10
        got = [e["event_id"] async for e in reopened.read_since()]
        assert got == ids
        # appending after reopen continues the sequence
        next_id = await reopened.append(_event(99))
        assert next_id > ids[-1]
    finally:
        await reopened.close()


async def test_encryption_rejects_bad_key(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    log = EventLog(db, TEST_KEY)
    await log.append(_event(0))
    await log.close()

    with pytest.raises((sqlcipher3.DatabaseError, sqlite3.DatabaseError)):
        conn = sqlcipher3.connect(str(db))
        # wrong key
        conn.execute(f"PRAGMA key = \"x'{bytes([0] * 32).hex()}'\"")
        conn.execute("SELECT count(*) FROM events").fetchone()
        conn.close()


async def test_event_ids_unique_within_same_ms(log: EventLog) -> None:
    ids = [
        await log.append(_event(i, event_at_ms=1_700_000_000_000)) for i in range(200)
    ]
    assert len(set(ids)) == len(ids)
    # all still sortable
    assert ids == sorted(ids)


async def test_latest_event_id(log: EventLog) -> None:
    assert await log.latest_event_id() is None
    first = await log.append(_event(1))
    assert await log.latest_event_id() == first
    second = await log.append(_event(2))
    assert await log.latest_event_id() == second


async def test_metadata_read_is_watermarked_and_payload_selective(
    log: EventLog,
) -> None:
    first = await log.append(_event(1, type="foo"))
    through = await log.latest_event_id()
    second = await log.append(_event(2, type="bar"))

    bounded = [
        event
        async for event in log.read_metadata_since(
            through=through,
            types=["foo", "bar"],
            include_payload_for=["foo"],
        )
    ]
    assert [event["event_id"] for event in bounded] == [first]
    assert bounded[0]["payload"] == {"i": 1}

    complete = [
        event
        async for event in log.read_metadata_since(
            types=["foo", "bar"],
            include_payload_for=["foo"],
        )
    ]
    assert [event["event_id"] for event in complete] == [first, second]
    assert complete[1]["payload"] is None

    def query_plan() -> list[str]:
        return [
            str(row[3])
            for row in log._conn.execute(  # noqa: SLF001 - storage-plan contract
                "EXPLAIN QUERY PLAN "
                "SELECT event_id, type, occurred_at, recorded_at, NULL AS payload "
                "FROM events INDEXED BY idx_events_type_id_time "
                "WHERE type IN (?, ?) AND event_id <= ? "
                "ORDER BY event_id ASC LIMIT ?",
                ("foo", "bar", second, 5000),
            )
        ]

    plan = await log._run_locked(query_plan)  # noqa: SLF001 - storage-plan contract
    assert any("COVERING INDEX idx_events_type_id_time" in row for row in plan)


async def test_sqlcipher_connection_operations_stay_on_one_worker_thread(
    log: EventLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: set[int] = set()
    insert_rows = log._insert_rows  # noqa: SLF001 - thread contract
    read_page = log._read_page  # noqa: SLF001 - thread contract

    def record_thread() -> int:
        thread_id = threading.get_ident()
        observed.add(thread_id)
        return thread_id

    def tracked_insert(rows):
        record_thread()
        return insert_rows(rows)

    def tracked_read(*args):
        record_thread()
        return read_page(*args)

    monkeypatch.setattr(log, "_insert_rows", tracked_insert)
    monkeypatch.setattr(log, "_read_page", tracked_read)

    first = await log._run_locked(record_thread)  # noqa: SLF001 - thread contract
    await log.append(_event(1))
    await _collect(log)
    second = await log._run_locked(record_thread)  # noqa: SLF001 - thread contract

    assert first == second
    assert observed == {first}
    assert threading.get_ident() != first


async def test_connection_open_migration_and_operations_share_worker_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, int] = {}
    original_open = EventLog._open_connection  # noqa: SLF001 - thread contract
    original_migrate = EventLog._migrate  # noqa: SLF001 - thread contract

    def tracked_open(self: EventLog):
        observed["open"] = threading.get_ident()
        return original_open(self)

    def tracked_migrate(self: EventLog) -> None:
        observed["migrate"] = threading.get_ident()
        original_migrate(self)

    monkeypatch.setattr(EventLog, "_open_connection", tracked_open)
    monkeypatch.setattr(EventLog, "_migrate", tracked_migrate)

    log = EventLog(tmp_path / "thread-affinity.db", TEST_KEY)
    try:
        worker = await log._run_locked(  # noqa: SLF001 - thread contract
            threading.get_ident
        )
        await log.append(_event(1))
        assert observed == {"open": worker, "migrate": worker}
        assert worker != threading.get_ident()
    finally:
        await log.close()


def test_migration_failure_closes_connection_and_worker(tmp_path: Path) -> None:
    observed: dict[str, int | bool] = {}

    class _ConnectionProxy:
        def __init__(self, connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def close(self) -> None:
            observed["close_thread"] = threading.get_ident()
            observed["closed"] = True
            self._connection.close()

    class _MigrationFailureLog(EventLog):
        def _open_connection(self):
            observed["open_thread"] = threading.get_ident()
            return _ConnectionProxy(super()._open_connection())

        def _migrate(self) -> None:
            observed["migration_thread"] = threading.get_ident()
            raise RuntimeError("simulated migration failure")

    with pytest.raises(RuntimeError, match="simulated migration failure"):
        _MigrationFailureLog(tmp_path / "migration-failure.db", TEST_KEY)

    worker = observed["open_thread"]
    assert observed == {
        "open_thread": worker,
        "migration_thread": worker,
        "close_thread": worker,
        "closed": True,
    }
    assert worker not in {thread.ident for thread in threading.enumerate()}


def test_post_connect_failure_closes_rejected_connection_on_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, int | bool] = {}

    class _RejectedConnection:
        row_factory = None

        def execute(self, _statement: str):
            observed["execute_thread"] = threading.get_ident()
            raise RuntimeError("simulated post-connect failure")

        def close(self) -> None:
            observed["close_thread"] = threading.get_ident()
            observed["closed"] = True

    def rejected_connect(*_args, **_kwargs):
        observed["connect_thread"] = threading.get_ident()
        return _RejectedConnection()

    monkeypatch.setattr(sqlcipher3, "connect", rejected_connect)
    with pytest.raises(RuntimeError, match="simulated post-connect failure"):
        EventLog(tmp_path / "post-connect-failure.db", TEST_KEY)

    worker = observed["connect_thread"]
    assert observed == {
        "connect_thread": worker,
        "execute_thread": worker,
        "close_thread": worker,
        "closed": True,
    }
    assert worker not in {thread.ident for thread in threading.enumerate()}


def test_schema_autoload_failure_closes_connection_and_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, int | bool] = {}

    class _ConnectionProxy:
        def __init__(self, connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def close(self) -> None:
            observed["close_thread"] = threading.get_ident()
            observed["closed"] = True
            self._connection.close()

    class _AutoloadFailureLog(EventLog):
        def _open_connection(self):
            observed["open_thread"] = threading.get_ident()
            return _ConnectionProxy(super()._open_connection())

    def fail_autoload() -> None:
        raise RuntimeError("simulated schema autoload failure")

    monkeypatch.setattr("adminme.events.log.ensure_autoloaded", fail_autoload)
    with pytest.raises(RuntimeError, match="simulated schema autoload failure"):
        _AutoloadFailureLog(tmp_path / "autoload-failure.db", TEST_KEY)

    worker = observed["open_thread"]
    assert observed == {
        "open_thread": worker,
        "close_thread": worker,
        "closed": True,
    }
    assert worker not in {thread.ident for thread in threading.enumerate()}


async def test_close_waits_for_queued_connection_work(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "queued-close.db", TEST_KEY)
    started = threading.Event()
    release = threading.Event()

    def held_operation() -> None:
        started.set()
        if not release.wait(timeout=5):
            raise RuntimeError("test did not release queued connection work")

    operation = asyncio.create_task(
        log._run_locked(held_operation)  # noqa: SLF001 - shutdown contract
    )
    assert await asyncio.to_thread(started.wait, 2)
    closing = asyncio.create_task(log.close())
    await asyncio.sleep(0)
    assert not closing.done()

    release.set()
    await operation
    await closing
    with pytest.raises(EventLogError, match="closed"):
        await log.count()


async def test_cancelled_close_still_closes_once_and_stops_worker(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "cancelled-close.db", TEST_KEY)
    raw_connection = log._conn  # noqa: SLF001 - cancellation contract
    started = threading.Event()
    release = threading.Event()
    close_count = 0
    close_thread: int | None = None

    class _HeldCloseConnection:
        def __getattr__(self, name: str):
            return getattr(raw_connection, name)

        def close(self) -> None:
            nonlocal close_count, close_thread
            close_count += 1
            close_thread = threading.get_ident()
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release held close")
            raw_connection.close()

    log._conn = _HeldCloseConnection()  # type: ignore[assignment]  # noqa: SLF001
    closing = asyncio.create_task(log.close())
    assert await asyncio.to_thread(started.wait, 2)
    timer = threading.Timer(0.05, release.set)
    timer.start()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    timer.join(timeout=1)

    assert close_count == 1
    assert close_thread is not None
    assert close_thread not in {thread.ident for thread in threading.enumerate()}
    await log.close()
    with pytest.raises(EventLogError, match="closed"):
        await log.count()


async def test_cancelled_queued_close_runs_after_cancelled_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = EventLog(tmp_path / "cancelled-queued-close.db", TEST_KEY)
    raw_connection = log._conn  # noqa: SLF001 - cancellation contract
    operation_started = threading.Event()
    release_operation = threading.Event()
    close_submitted = asyncio.Event()
    close_count = 0
    close_thread: int | None = None

    class _TrackedCloseConnection:
        def __getattr__(self, name: str):
            return getattr(raw_connection, name)

        def close(self) -> None:
            nonlocal close_count, close_thread
            close_count += 1
            close_thread = threading.get_ident()
            raw_connection.close()

    log._conn = _TrackedCloseConnection()  # type: ignore[assignment]  # noqa: SLF001
    original_submit = log._executor.submit  # noqa: SLF001 - cancellation contract

    def tracked_submit(func, *args, **kwargs):
        submitted = original_submit(func, *args, **kwargs)
        if getattr(func, "__self__", None) is log._conn and (
            getattr(func, "__name__", None) == "close"
        ):
            close_submitted.set()
        return submitted

    monkeypatch.setattr(log._executor, "submit", tracked_submit)  # noqa: SLF001

    def held_operation() -> None:
        operation_started.set()
        if not release_operation.wait(timeout=5):
            raise RuntimeError("test did not release cancelled operation")

    operation = asyncio.create_task(
        log._run_locked(held_operation)  # noqa: SLF001 - cancellation contract
    )
    assert await asyncio.to_thread(operation_started.wait, 2)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation

    closing = asyncio.create_task(log.close())
    await asyncio.wait_for(close_submitted.wait(), timeout=2)
    timer = threading.Timer(0.05, release_operation.set)
    timer.start()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    timer.join(timeout=1)

    assert close_count == 1
    assert close_thread is not None
    assert close_thread not in {thread.ident for thread in threading.enumerate()}
    await log.close()


async def test_cancelled_close_observes_native_close_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = EventLog(tmp_path / "cancelled-failed-close.db", TEST_KEY)
    raw_connection = log._conn  # noqa: SLF001 - cancellation contract
    close_started = threading.Event()
    release_close = threading.Event()

    class _FailingCloseConnection:
        def __getattr__(self, name: str):
            return getattr(raw_connection, name)

        def close(self) -> None:
            close_started.set()
            if not release_close.wait(timeout=5):
                raise RuntimeError("test did not release failed close")
            raw_connection.close()
            raise RuntimeError("simulated native close failure")

    log._conn = _FailingCloseConnection()  # type: ignore[assignment]  # noqa: SLF001
    closing = asyncio.create_task(log.close())
    assert await asyncio.to_thread(close_started.wait, 2)
    timer = threading.Timer(0.05, release_close.set)
    timer.start()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    timer.join(timeout=1)

    assert "native event-log close failed after caller cancellation" in caplog.text
    assert not any(
        thread.name.startswith("adminme-event-log") for thread in threading.enumerate()
    )
    await log.close()


async def test_concurrent_close_calls_close_native_connection_once(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "concurrent-close.db", TEST_KEY)
    raw_connection = log._conn  # noqa: SLF001 - shutdown contract
    close_count = 0

    class _TrackedCloseConnection:
        def __getattr__(self, name: str):
            return getattr(raw_connection, name)

        def close(self) -> None:
            nonlocal close_count
            close_count += 1
            raw_connection.close()

    log._conn = _TrackedCloseConnection()  # type: ignore[assignment]  # noqa: SLF001
    await asyncio.gather(log.close(), log.close())

    assert close_count == 1
    with pytest.raises(EventLogError, match="closed"):
        await log.count()


async def test_missing_required_fields_raises(log: EventLog) -> None:
    # EventEnvelope construction itself rejects missing fields — exercise both
    # that path and the log-layer type guard against non-envelope arguments.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EventEnvelope(  # type: ignore[call-arg]
            event_at_ms=1,
            tenant_id="t",
            type="test.event",
            # missing schema_version
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="a",
            source_account_id="b",
            owner_scope="s",
            visibility_scope="s",
            payload={"i": 0},
        )
    with pytest.raises(AppendValidationError, match="EventEnvelope"):
        await log.append({"not": "an envelope"})  # type: ignore[arg-type]


async def test_append_only_trigger_refuses_delete_and_update(log: EventLog) -> None:
    await log.append(_event(1))
    with pytest.raises(sqlcipher3.DatabaseError):
        await log._run_locked(  # noqa: SLF001 - append-only storage contract
            log._conn.execute,  # noqa: SLF001 - append-only storage contract
            "DELETE FROM events",
        )
    with pytest.raises(sqlcipher3.DatabaseError):
        await log._run_locked(  # noqa: SLF001 - append-only storage contract
            log._conn.execute,  # noqa: SLF001 - append-only storage contract
            "UPDATE events SET type = 'mutated'",
        )
    assert await log.count() == 1
