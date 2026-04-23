"""
Unit tests for adminme.events.log.EventLog (prompt 03).

Covers prompt-03 deliverables: append/read/filter/persistence/encryption/
id-ordering semantics. Typed-payload validation is prompt 04 and out of scope.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlcipher3

from adminme.events.log import (
    AppendValidationError,
    CursorNotFound,
    EventLog,
    new_correlation_id,
)

TEST_KEY = b"k" * 32


def _event(
    i: int = 0,
    *,
    type: str = "test.event",
    owner_scope: str = "shared:household",
    tenant_id: str = "tenant-a",
    correlation_id: str | None = None,
) -> dict:
    return {
        "type": type,
        "tenant_id": tenant_id,
        "owner_scope": owner_scope,
        "version": 1,
        "correlation_id": correlation_id,
        "payload": {"i": i},
    }


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
    ev = _event(7)
    ev["event_id"] = custom_id
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
        await log.append({**_event(i), "event_at_ms": 1_700_000_000_000})
        for i in range(200)
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


async def test_missing_required_fields_raises(log: EventLog) -> None:
    with pytest.raises(AppendValidationError):
        await log.append({"tenant_id": "t", "owner_scope": "s", "payload": {}})
    with pytest.raises(AppendValidationError):
        await log.append({"type": "t", "owner_scope": "s", "payload": {}})
    with pytest.raises(AppendValidationError):
        await log.append({"type": "t", "tenant_id": "t", "payload": {}})
    with pytest.raises(AppendValidationError):
        await log.append({"type": "t", "tenant_id": "t", "owner_scope": "s"})


async def test_append_only_trigger_refuses_delete_and_update(log: EventLog) -> None:
    await log.append(_event(1))
    # peek inside: reuse the log's own connection to prove the trigger fires
    conn = log._conn  # type: ignore[attr-defined]
    with pytest.raises(sqlcipher3.DatabaseError):
        conn.execute("DELETE FROM events")
    with pytest.raises(sqlcipher3.DatabaseError):
        conn.execute("UPDATE events SET type = 'mutated'")
    assert await log.count() == 1
