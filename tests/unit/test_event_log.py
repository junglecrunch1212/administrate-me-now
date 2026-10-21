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
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlcipher3
from pydantic import BaseModel

from adminme.events.envelope import EventEnvelope
from adminme.events import log as event_log_module
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
    causation_id: str | None = None,
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
        causation_id=causation_id,
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


async def test_default_recorded_at_is_precise_and_explicit_history_is_preserved(
    log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamped = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
    clock_calls = 0

    class RecordingClock:
        @staticmethod
        def now(zone):
            nonlocal clock_calls
            assert zone == timezone.utc
            clock_calls += 1
            return stamped

    monkeypatch.setattr(event_log_module, "datetime", RecordingClock)
    event_id = await log.append(_event(1))
    retained = await log.get(event_id)
    assert retained is not None
    assert retained["recorded_at"] == "2026-10-20T12:00:00.123456Z"
    for timestamp in (
        "2026-10-19T12:00:00Z",
        "2026-10-19T12:00:00.123456+00:00",
    ):
        historical = _event(2).model_copy(update={"recorded_at": timestamp})
        event_id = await log.append(historical)
        retained = await log.get(event_id)
        assert retained is not None
        assert retained["recorded_at"] == timestamp
    assert clock_calls == 1


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


async def test_restart_restores_native_id_floor_despite_older_clock_and_opaque_ids(
    tmp_path: Path,
) -> None:
    path = tmp_path / "future-event-log.db"
    original = EventLog(path, TEST_KEY)
    future = 2_022_408_000_000
    try:
        first = await original.append(_event(1, event_at_ms=future))
        second = await original.append(_event(2, event_at_ms=future))
        # Neither lexically later opaque IDs nor lookalikes can seed the native
        # generator; historical callers can still retrieve them unchanged.
        for legacy in ("legacy-z", "ev_ZZZZZZZZZZZZZ!", "ev_short", "f" * 36):
            await original.append(_event(3, event_id=legacy, event_at_ms=future))
    finally:
        await original.close()
    restored = EventLog(path, TEST_KEY)
    try:
        third = await restored.append(_event(4, event_at_ms=future - 1_000_000))
        fourth = await restored.append(_event(5, event_at_ms=future))
        assert first < second < third < fourth
        assert [
            event["event_id"]
            for event in await _collect(restored)
            if event["payload"]["i"] in {1, 2, 4, 5}
        ] == [first, second, third, fourth]
        assert await restored.get("ev_ZZZZZZZZZZZZZ!") is not None
    finally:
        await restored.close()


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


async def test_bounded_exact_receipt_lookup_is_indexed_tenant_scoped_and_complete(
    log: EventLog,
) -> None:
    keys = [f"fixture-receipt:{index}" for index in range(32)]
    ids = await log.append_batch(
        [
            _event(index, idempotency_key=key, causation_id=f"delivery:{index}")
            for index, key in enumerate(keys)
        ]
    )
    await log.append(_event(99, tenant_id="tenant-b", idempotency_key=keys[0]))
    await log.append(_event(100, idempotency_key="fixture-receipt:0:suffix"))
    statements = []
    await log._run_locked(log._conn.set_trace_callback, statements.append)  # noqa: SLF001
    try:
        found = await log.get_by_idempotency_keys("tenant-a", list(reversed(keys)))
    finally:
        await log._run_locked(log._conn.set_trace_callback, None)  # noqa: SLF001
    assert [event["event_id"] for event in found] == ids
    assert [event["causation_id"] for event in found] == [
        f"delivery:{index}" for index in range(32)
    ]
    assert [event["payload"] for event in found] == [
        {"i": index} for index in range(32)
    ]
    queries = [
        statement for statement in statements if "idempotency_key IN" in statement
    ]
    assert len(queries) == 1

    def explain():
        return [row[3] for row in log._conn.execute("EXPLAIN QUERY PLAN " + queries[0])]  # noqa: SLF001

    plan = await log._run_locked(explain)  # noqa: SLF001
    assert any("idx_events_tenant_idempotency" in row for row in plan), plan
    assert await log.get_by_idempotency_keys("tenant-a", []) == []
    assert (
        await log.get_by_idempotency_keys("tenant-a", [keys[0], keys[0]]) == found[:1]
    )
    assert await log.get_by_idempotency_keys("tenant-a", ["%' OR 1=1 --"]) == []
    with pytest.raises(ValueError, match="at most 32"):
        await log.get_by_idempotency_keys("tenant-a", keys + ["extra"])
    with pytest.raises(ValueError, match="nonempty"):
        await log.get_by_idempotency_keys("tenant-a", [""])


async def test_idempotency_key_rejects_different_event_family(log: EventLog) -> None:
    await log.append(_event(1, type="foo", idempotency_key="request:conflict"))
    with pytest.raises(IdempotencyConflict):
        await log.append(_event(1, type="bar", idempotency_key="request:conflict"))


async def test_legacy_receipt_prefix_is_indexed_exact_bounded_and_tenant_scoped(
    log: EventLog,
) -> None:
    prefix = "gmail-message:fixture_%:"
    expected = await log.append(_event(1, idempotency_key=prefix + "a" * 64))
    await log.append(_event(2, tenant_id="tenant-b", idempotency_key=prefix + "b" * 64))
    await log.append(_event(3, idempotency_key="gmail-message:fixture_%0:" + "c" * 64))
    await log.append(_event(4, idempotency_key="gmail-message:fixture_XX:" + "d" * 64))
    statements = []
    await log._run_locked(log._conn.set_trace_callback, statements.append)  # noqa: SLF001
    try:
        found = await log.get_by_idempotency_prefix("tenant-a", prefix)
    finally:
        await log._run_locked(log._conn.set_trace_callback, None)  # noqa: SLF001
    assert [event["event_id"] for event in found] == [expected]
    [query] = [
        statement for statement in statements if "idempotency_key >=" in statement
    ]
    assert "LIMIT 33" in query and "LIKE" not in query

    def explain():
        return [row[3] for row in log._conn.execute("EXPLAIN QUERY PLAN " + query)]  # noqa: SLF001

    plan = await log._run_locked(explain)  # noqa: SLF001
    assert any("idx_events_tenant_idempotency" in row for row in plan), plan
    for number in range(32):
        await log.append(_event(number, idempotency_key=prefix + f"{number:064x}"))
    with pytest.raises(EventLogError, match="exceeds 32"):
        await log.get_by_idempotency_prefix("tenant-a", prefix)
    for invalid in ("", "undelimited", "nonascii-é:", "line\nbreak:", "x" * 601 + ":"):
        with pytest.raises(ValueError, match="bounded ASCII"):
            await log.get_by_idempotency_prefix("tenant-a", invalid)


async def test_exact_event_headers_are_bounded_indexed_and_exclude_content(
    log: EventLog,
) -> None:
    ids = await log.append_batch(
        [
            _event(
                index,
                owner_scope="private:fixture-member",
                idempotency_key=f"header:{index}",
            ).model_copy(
                update={
                    "raw_ref": "private raw fixture",
                    "actor_identity": "private actor fixture",
                }
            )
            for index in range(32)
        ],
        causation_id="fixture-source",
    )
    foreign_id = await log.append(_event(100, tenant_id="tenant-b"))
    statements: list[str] = []
    await log._run_locked(log._conn.set_trace_callback, statements.append)  # noqa: SLF001
    try:
        headers = await log.get_event_headers("tenant-a", list(reversed(ids)))
    finally:
        await log._run_locked(log._conn.set_trace_callback, None)  # noqa: SLF001
    assert [header["event_id"] for header in headers] == ids
    assert all(header["owner_scope"] == "private:fixture-member" for header in headers)
    assert all(
        header["visibility_scope"] == "private:fixture-member" for header in headers
    )
    assert all(header["tenant_id"] == "tenant-a" for header in headers)
    assert all(header["causation_id"] == "fixture-source" for header in headers)
    assert all(header["schema_version"] == 1 for header in headers)
    assert all(header["source_adapter"] == "test:inproc" for header in headers)
    assert all(header["source_account_id"] == "test-account" for header in headers)
    assert all(header["sensitivity"] == "normal" for header in headers)
    assert [header["idempotency_key"] for header in headers] == [
        f"header:{index}" for index in range(32)
    ]
    assert all(
        not {
            "payload",
            "raw_ref",
            "actor_identity",
            "source",
            "correlation_id",
        }.intersection(header)
        for header in headers
    )
    queries = [statement for statement in statements if "event_id IN" in statement]
    assert len(queries) == 1
    selected_columns = queries[0].split("FROM events", 1)[0]
    assert "payload" not in selected_columns
    assert "raw_ref" not in selected_columns
    assert "actor_identity" not in selected_columns

    def explain():
        return [row[3] for row in log._conn.execute("EXPLAIN QUERY PLAN " + queries[0])]  # noqa: SLF001

    plan = await log._run_locked(explain)  # noqa: SLF001
    assert any("event_id=?" in row and "USING INDEX" in row for row in plan), plan
    assert await log.get_event_headers("tenant-a", [foreign_id]) == []
    assert await log.get_event_headers("tenant-a", [ids[0], ids[0]]) == headers[:1]
    assert await log.get_event_headers("tenant-a", ["missing", "%' OR 1=1 --"]) == []
    assert await log.get_event_headers("tenant-a", []) == []
    with pytest.raises(ValueError, match="at most 32"):
        await log.get_event_headers("tenant-a", ids + [foreign_id])
    with pytest.raises(ValueError, match="nonempty"):
        await log.get_event_headers("tenant-a", [""])


async def test_causal_headers_are_indexed_typed_tenant_scoped_and_snapshot_bounded(
    log: EventLog,
) -> None:
    first = await log.append(_event(1, type="foo"), causation_id="family-source")
    await log.append(_event(2, type="bar"), causation_id="family-source")
    await log.append(
        _event(3, type="foo", tenant_id="tenant-b"), causation_id="family-source"
    )
    await log.append(_event(4, type="foo"), causation_id="other-source")
    second = await log.append(_event(5, type="foo"), causation_id="family-source")
    await log.append(_event(6, type="foo"), causation_id="family-source")
    statements: list[str] = []
    await log._run_locked(log._conn.set_trace_callback, statements.append)  # noqa: SLF001
    try:
        headers = await log.get_causal_event_headers(
            "tenant-a", "family-source", types=["foo"], through=second
        )
    finally:
        await log._run_locked(log._conn.set_trace_callback, None)  # noqa: SLF001
    assert [header["event_id"] for header in headers] == [first, second]
    assert all(
        not {"payload", "raw_ref", "actor_identity", "source"}.intersection(header)
        for header in headers
    )
    [query] = [
        statement
        for statement in statements
        if "INDEXED BY idx_events_causation" in statement
    ]
    assert "payload" not in query and "raw_ref" not in query
    assert query.endswith("ORDER BY event_id ASC LIMIT 257")

    def explain():
        return [row[3] for row in log._conn.execute("EXPLAIN QUERY PLAN " + query)]  # noqa: SLF001

    plan = await log._run_locked(explain)  # noqa: SLF001
    assert any(
        "USING INDEX idx_events_causation" in row and "causation_id=?" in row
        for row in plan
    ), plan
    with pytest.raises(ValueError, match="explicit types"):
        await log.get_causal_event_headers(
            "tenant-a", "family-source", types=[], through=second
        )
    with pytest.raises(ValueError, match="identity and cutoff"):
        await log.get_causal_event_headers(
            "tenant-a", "family-source", types=["foo"], through=""
        )
    await log.append_batch(
        [_event(index, type="foo") for index in range(257)], causation_id="large-family"
    )
    through = await log.latest_event_id()
    assert through is not None
    with pytest.raises(EventLogError, match="ceiling exceeded"):
        await log.get_causal_event_headers(
            "tenant-a", "large-family", types=["foo"], through=through
        )


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


async def test_latest_event_id_respects_subscriber_types(log: EventLog) -> None:
    assert await log.latest_event_id(types=["foo"]) is None
    first = await log.append(_event(1, type="foo"))
    second = await log.append(_event(2, type="bar"))
    await log.append(_event(3))
    assert await log.latest_event_id(types=["foo"]) == first
    assert await log.latest_event_id(types=["foo", "bar"]) == second
    assert await log.latest_event_id(types=["missing"]) is None


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


@pytest.mark.parametrize("payload_types", [[], ["bar"]])
async def test_actual_metadata_query_covers_causation_and_pages_at_fixed_watermark(
    log: EventLog, payload_types: list[str]
) -> None:
    root = await log.append(_event(-1, type="foo"))
    ids = await log.append_batch(
        [
            _event(
                index,
                type="test.event" if index % 5 == 0 else "foo" if index % 2 else "bar",
                causation_id=root if index % 3 else None,
            )
            for index in range(1207)
        ]
    )
    through = ids[1105]
    expected = await _collect(log, cursor=root, through=through, types=["foo", "bar"])
    assert len(expected) > 500
    assert any(event["causation_id"] == root for event in expected)
    assert any(event["causation_id"] is None for event in expected)
    statements: list[str] = []
    await log._run_locked(  # noqa: SLF001 - trace the actual storage boundary
        log._conn.set_trace_callback,
        statements.append,  # noqa: SLF001
    )
    try:
        actual = [
            event
            async for event in log.read_metadata_since(
                cursor=root,
                through=through,
                types=["foo", "bar"],
                include_payload_for=payload_types,
                limit=500,
            )
        ]
    finally:
        await log._run_locked(log._conn.set_trace_callback, None)  # noqa: SLF001
    assert actual == [
        {
            **{
                field: event[field]
                for field in (
                    "event_id",
                    "type",
                    "occurred_at",
                    "recorded_at",
                    "causation_id",
                )
            },
            "payload": event["payload"] if event["type"] in payload_types else None,
        }
        for event in expected
    ]
    queries = [statement for statement in statements if "NULL AS payload" in statement]
    assert len(queries) == 2
    assert not any("sqlite_master" in statement for statement in statements)
    assert all("causation_id" in statement for statement in queries)
    assert all(
        statement.endswith("ORDER BY event_id ASC LIMIT 500") for statement in queries
    )
    assert all(f"event_id <= '{through}'" in statement for statement in queries)
    assert f"event_id > '{root}'" in queries[0]
    assert f"event_id > '{actual[499]['event_id']}'" in queries[1]

    def explain_actual_queries() -> list[list[str]]:
        return [
            [
                str(row[3])
                for row in log._conn.execute(  # noqa: SLF001
                    "EXPLAIN QUERY PLAN " + statement
                )
            ]
            for statement in queries
        ]

    plans = await log._run_locked(explain_actual_queries)  # noqa: SLF001
    for statement, plan in zip(queries, plans, strict=True):
        assert any(
            "COVERING INDEX idx_events_type_id_time_causation" in row for row in plan
        ), plan
        event_reads = [
            row for row in plan if "SEARCH events" in row or "SCAN events" in row
        ]
        if payload_types:
            assert "UNION ALL" in statement
            # The selected terminal-payload branch still reads its own rows;
            # only the source metadata branch is required to avoid table fetches.
            assert any("COVERING INDEX" not in row for row in event_reads), plan
        else:
            assert "UNION ALL" not in statement
            assert event_reads and all(
                "COVERING INDEX" in row for row in event_reads
            ), plan


@pytest.mark.parametrize("legacy_version", [3, 5])
async def test_legacy_encrypted_metadata_read_only_then_migration_and_reopen(
    tmp_path: Path, legacy_version: int
) -> None:
    class LegacyEventLog(EventLog):
        MIGRATIONS = tuple(
            entry for entry in EventLog.MIGRATIONS if entry[0] <= legacy_version
        )

    db = tmp_path / f"legacy-v{legacy_version}.db"
    legacy = LegacyEventLog(db, TEST_KEY)
    try:
        source = await legacy.append(_event(1, type="foo"))
        terminal = await legacy.append(_event(2, type="bar", causation_id=source))
        before = await _collect(legacy)
    finally:
        await legacy.close()

    def inventory(log: EventLog) -> dict:
        connection = log._conn  # noqa: SLF001 - migration inventory
        return {
            "versions": [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM _schema_version ORDER BY version"
                )
            ],
            "indexes": dict(
                connection.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='index'"
                )
            ),
        }

    archived = EventLog(db, TEST_KEY, read_only=True)
    try:
        original = await archived._run_locked(inventory, archived)  # noqa: SLF001
        assert original["versions"] == list(range(1, legacy_version + 1))
        # The exact header API relies only on the unchanged primary key from
        # 0001, including read-only snapshots predating the covering indexes.
        assert "sqlite_autoindex_events_1" in original["indexes"]
        headers = await archived.get_event_headers("tenant-a", [terminal, source])
        assert [header["event_id"] for header in headers] == [source, terminal]
        assert [header["causation_id"] for header in headers] == [None, source]
        assert all("payload" not in header for header in headers)
        assert "idx_events_type_id_time_causation" not in original["indexes"]
        for payload_types in ([], ["bar"]):
            rows = [
                event
                async for event in archived.read_metadata_since(
                    types=["foo", "bar"], include_payload_for=payload_types
                )
            ]
            assert [event["event_id"] for event in rows] == [source, terminal]
            assert [event["causation_id"] for event in rows] == [None, source]
            assert rows[0]["payload"] is None
            assert rows[1]["payload"] == ({"i": 2} if payload_types else None)
        after_read = await archived._run_locked(inventory, archived)  # noqa: SLF001
        assert after_read == original
        assert await _collect(archived) == before
    finally:
        await archived.close()

    migrated = EventLog(db, TEST_KEY)
    try:
        upgraded = await migrated._run_locked(inventory, migrated)  # noqa: SLF001
        assert upgraded["versions"] == [
            version for version, _filename in EventLog.MIGRATIONS
        ]
        assert "idx_events_type_id_time_causation" in upgraded["indexes"]
        if legacy_version >= 4:
            assert (
                upgraded["indexes"]["idx_events_type_id_time"]
                == original["indexes"]["idx_events_type_id_time"]
            )
        assert await _collect(migrated) == before
    finally:
        await migrated.close()
    reopened = EventLog(db, TEST_KEY)
    try:
        after_reopen = await reopened._run_locked(inventory, reopened)  # noqa: SLF001
        assert after_reopen == upgraded
        assert await _collect(reopened) == before
    finally:
        await reopened.close()


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
