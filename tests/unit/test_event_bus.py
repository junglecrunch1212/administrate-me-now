"""
Unit tests for adminme.events.bus.EventBus (prompt 03, updated for prompt 04).

Covers fan-out, filtering, per-subscriber checkpoints, retry on failure,
degraded state, and persistence across restart. Prompt 04 shifted
``append`` to take an ``EventEnvelope``; the ``_event`` helper here builds
envelopes, and the test payload shape is registered via the schema
registry at module import (shared with test_event_log.py).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from adminme.events.bus import EventBus, LAG_WARN_THRESHOLD, RETRY_BACKOFF_S
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.registry import registry

TEST_KEY = b"k" * 32


class _BusTestPayloadV1(BaseModel):
    model_config = {"extra": "forbid"}
    i: int


for _name in (
    "test.event",
    "foo",
    "bar",
    "t0",
    "t1",
    "t2",
    "t3",
    "t4",
):
    if registry.get(_name, 1) is None:
        registry.register(_name, 1, _BusTestPayloadV1)


def _event(i: int = 0, *, type: str = "test.event") -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id="tenant-a",
        type=type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test:bus",
        source_account_id="test-account",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        payload={"i": i},
    )


async def _wait_for(predicate, timeout: float = 3.0, interval: float = 0.01) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"predicate never became true within {timeout}s")


@pytest.fixture
async def rig(tmp_path: Path):
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    bus = EventBus(log, tmp_path / "bus.db")
    try:
        yield log, bus, tmp_path
    finally:
        await bus.stop()
        await log.close()


async def test_single_subscriber_receives_event_after_subscribe(rig) -> None:
    log, bus, _ = rig
    received: list[dict] = []

    async def cb(ev: dict) -> None:
        received.append(ev)

    bus.subscribe("s1", "*", cb)
    await bus.start()
    eid = await log.append(_event(1))
    await bus.notify(eid)
    await _wait_for(lambda: len(received) == 1)
    assert received[0]["event_id"] == eid


async def test_type_filter_excludes_nonmatching(rig) -> None:
    log, bus, _ = rig
    foos: list[dict] = []

    async def cb(ev: dict) -> None:
        foos.append(ev)

    bus.subscribe("foo-only", ["foo"], cb)
    await bus.start()
    await log.append(_event(1, type="foo"))
    await log.append(_event(2, type="bar"))
    await log.append(_event(3, type="foo"))
    await bus.notify("dummy")
    await _wait_for(lambda: len(foos) == 2)
    assert [e["type"] for e in foos] == ["foo", "foo"]


async def test_wildcard_subscriber_receives_all(rig) -> None:
    log, bus, _ = rig
    got: list[dict] = []

    async def cb(ev: dict) -> None:
        got.append(ev)

    bus.subscribe("all", "*", cb)
    await bus.start()
    for i in range(5):
        await log.append(_event(i, type=f"t{i}"))
    await bus.notify("dummy")
    await _wait_for(lambda: len(got) == 5)


async def test_multiple_subscribers_all_receive(rig) -> None:
    log, bus, _ = rig
    a: list[dict] = []
    b: list[dict] = []

    async def cb_a(ev):
        a.append(ev)

    async def cb_b(ev):
        b.append(ev)

    bus.subscribe("a", "*", cb_a)
    bus.subscribe("b", "*", cb_b)
    await bus.start()
    for i in range(3):
        await log.append(_event(i))
    await bus.notify("dummy")
    await _wait_for(lambda: len(a) == 3 and len(b) == 3)


async def test_callback_failure_retries_and_eventually_succeeds(rig) -> None:
    log, bus, _ = rig
    attempts = 0
    delivered: list[dict] = []

    async def cb(ev: dict) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient")
        delivered.append(ev)

    bus.subscribe("flaky", "*", cb)
    await bus.start()
    eid = await log.append(_event(1))
    await bus.notify(eid)
    await _wait_for(lambda: len(delivered) == 1, timeout=5.0)
    status = await bus.subscriber_status("flaky")
    assert status["consecutive_failures"] == 0
    assert status["checkpoint_event_id"] == eid


async def test_subscriber_becomes_degraded_after_threshold(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    bus = EventBus(log, tmp_path / "bus.db", failure_threshold=3)

    calls = 0

    async def always_fail(ev: dict) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("nope")

    bus.subscribe("doomed", "*", always_fail)
    await bus.start()
    eid = await log.append(_event(1))
    await bus.notify(eid)

    await _wait_for(
        lambda: (
            calls >= 3 and bus._subscribers["doomed"].degraded  # type: ignore[attr-defined]
        ),
        timeout=5.0,
    )
    status = await bus.subscriber_status("doomed")
    assert status["degraded"] is True
    assert status["poison_event_id"] == eid
    assert status["retry_policy"] == {
        "delivery": "at_least_once",
        "failure_threshold": 3,
        "backoff_seconds": RETRY_BACKOFF_S,
    }
    # further appends do not get delivered
    await log.append(_event(2))
    await bus.notify("dummy")
    await asyncio.sleep(0.2)
    # calls count may have hit threshold exactly; shouldn't have advanced past it
    assert calls == 3

    # reset restores delivery — rewrite callback via a new subscriber would be
    # cleaner, but reset_subscriber alone is what the API advertises.
    # Poison metadata and degraded state survive a full bus restart.
    await bus.stop()
    bus = EventBus(log, tmp_path / "bus.db", failure_threshold=3)
    bus.subscribe("doomed", "*", always_fail)
    persisted = await bus.subscriber_status("doomed")
    assert persisted["degraded"] is True
    assert persisted["poison_event_id"] == eid
    await bus.start()

    await bus.reset_subscriber("doomed")
    # callback still raises, so it'll fail again — but the degraded flag was
    # lifted and the retry loop is running.
    await _wait_for(
        lambda: calls > 3,
        timeout=3.0,
    )
    await bus.stop()
    await log.close()


async def test_checkpoint_persists_across_restart(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    bus = EventBus(log, tmp_path / "bus.db")

    seen_a: list[str] = []

    async def cb_a(ev: dict) -> None:
        seen_a.append(ev["event_id"])

    bus.subscribe("s", "*", cb_a)
    await bus.start()
    first_batch = [await log.append(_event(i)) for i in range(5)]
    await bus.notify("dummy")
    await _wait_for(lambda: len(seen_a) == 5)
    await bus.stop()

    # restart with fresh callback captor
    bus2 = EventBus(log, tmp_path / "bus.db")
    seen_b: list[str] = []

    async def cb_b(ev: dict) -> None:
        seen_b.append(ev["event_id"])

    bus2.subscribe("s", "*", cb_b)
    status_before = await bus2.subscriber_status("s")
    assert status_before["checkpoint_event_id"] == first_batch[-1]
    await bus2.start()
    # no new events yet; subscriber should not re-see the old batch
    await asyncio.sleep(0.15)
    assert seen_b == []

    # append a new event; should be delivered
    new_id = await log.append(_event(99))
    await bus2.notify(new_id)
    await _wait_for(lambda: seen_b == [new_id])
    await bus2.stop()
    await log.close()


async def test_reviewed_consumer_version_replays_from_last_good_checkpoint(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    checkpoint_db = tmp_path / "bus.db"
    bus = EventBus(log, checkpoint_db, failure_threshold=1)

    async def broken(_event: dict) -> None:
        raise RuntimeError("poisoned consumer v1")

    bus.subscribe("pipeline:test", "*", broken, consumer_version="1.0.0")
    await bus.start()
    event_id = await log.append(_event(1))
    await bus.notify(event_id)
    await _wait_for(lambda: bus._subscribers["pipeline:test"].degraded)
    await bus.stop()

    same_version = EventBus(log, checkpoint_db, failure_threshold=1)
    same_version.subscribe("pipeline:test", "*", broken, consumer_version="1.0.0")
    assert (await same_version.subscriber_status("pipeline:test"))["degraded"]
    await same_version.stop()

    delivered: list[str] = []

    async def repaired(event: dict) -> None:
        delivered.append(event["event_id"])

    repaired_bus = EventBus(log, checkpoint_db, failure_threshold=1)
    repaired_bus.subscribe("pipeline:test", "*", repaired, consumer_version="2.0.0")
    status = await repaired_bus.subscriber_status("pipeline:test")
    assert status["degraded"] is False
    assert status["poison_event_id"] is None
    assert status["consumer_version"] == "2.0.0"
    await repaired_bus.start()
    await _wait_for(lambda: delivered == [event_id])
    await repaired_bus.stop()
    await log.close()


async def test_opt_in_version_change_replays_healthy_consumer_from_start(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    checkpoint_db = tmp_path / "bus.db"
    event_ids = [await log.append(_event(i)) for i in range(3)]
    first_delivery: list[str] = []

    async def v1(event: dict) -> None:
        first_delivery.append(event["event_id"])

    first_bus = EventBus(log, checkpoint_db)
    first_bus.subscribe("pipeline:test", "*", v1, consumer_version="1.0.0")
    await first_bus.start()
    await _wait_for(lambda: first_delivery == event_ids)
    await first_bus.stop()

    replayed: list[str] = []

    async def v2(event: dict) -> None:
        replayed.append(event["event_id"])

    second_bus = EventBus(log, checkpoint_db)
    second_bus.subscribe(
        "pipeline:test",
        "*",
        v2,
        consumer_version="2.0.0",
        replay_on_version_change=True,
    )
    before = await second_bus.subscriber_status("pipeline:test")
    assert before["checkpoint_event_id"] is None
    assert before["consumer_version"] == "2.0.0"
    await second_bus.start()
    await _wait_for(lambda: replayed == event_ids)
    await second_bus.stop()
    await log.close()


async def test_subscriber_registered_after_events_replays_on_start(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    # events exist before any subscriber does
    ids = [await log.append(_event(i)) for i in range(10)]

    bus = EventBus(log, tmp_path / "bus.db")
    seen: list[str] = []

    async def cb(ev: dict) -> None:
        seen.append(ev["event_id"])

    bus.subscribe("late", "*", cb)
    await bus.start()
    await _wait_for(lambda: len(seen) == 10)
    assert seen == ids
    await bus.stop()
    await log.close()


async def test_concurrent_appends_all_delivered(rig) -> None:
    log, bus, _ = rig
    seen: list[str] = []

    async def cb(ev: dict) -> None:
        seen.append(ev["event_id"])

    bus.subscribe("s", "*", cb)
    await bus.start()

    async def writer(start: int) -> list[str]:
        return [await log.append(_event(start + i)) for i in range(50)]

    results = await asyncio.gather(writer(0), writer(100), writer(200))
    await bus.notify("dummy")
    await _wait_for(lambda: len(seen) == 150, timeout=5.0)
    all_ids = {eid for batch in results for eid in batch}
    assert set(seen) == all_ids


async def test_opt_in_delivery_concurrency_is_bounded_and_checkpoints_batch(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    bus = EventBus(log, tmp_path / "bus.db")
    event_ids = [await log.append(_event(i)) for i in range(8)]
    active = 0
    maximum_active = 0
    completed: list[str] = []

    async def concurrent(event: dict) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.03)
        completed.append(event["event_id"])
        active -= 1

    bus.subscribe("bounded", "*", concurrent, delivery_concurrency=4)
    await bus.start()
    await _wait_for(lambda: len(completed) == len(event_ids), timeout=5.0)
    status = await bus.subscriber_status("bounded")

    assert maximum_active == 4
    assert status["delivery_concurrency"] == 4
    assert status["checkpoint_event_id"] == event_ids[-1]
    assert status["lag_count"] == 0
    await bus.stop()
    await log.close()


async def test_concurrent_failure_advances_only_successful_ordered_prefix(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    bus = EventBus(log, tmp_path / "bus.db", failure_threshold=1)
    event_ids = [await log.append(_event(i)) for i in range(3)]
    seen: list[str] = []

    async def fail_middle(event: dict) -> None:
        seen.append(event["event_id"])
        await asyncio.sleep(0.01)
        if event["event_id"] == event_ids[1]:
            raise RuntimeError("ordered failure")

    bus.subscribe("ordered", "*", fail_middle, delivery_concurrency=3)
    await bus.start()
    await _wait_for(lambda: bus._subscribers["ordered"].degraded)
    status = await bus.subscriber_status("ordered")

    assert set(seen) == set(event_ids)
    assert status["checkpoint_event_id"] == event_ids[0]
    assert status["poison_event_id"] == event_ids[1]
    assert status["lag_count"] == 2
    await bus.stop()
    await log.close()


@pytest.mark.parametrize("value", [0, -1, 17, True, 1.5])
async def test_delivery_concurrency_rejects_unsafe_values(
    tmp_path: Path, value: object
) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    bus = EventBus(log, tmp_path / "bus.db")

    async def callback(_event: dict) -> None:
        return None

    with pytest.raises(ValueError, match="delivery_concurrency"):
        bus.subscribe("invalid", "*", callback, delivery_concurrency=value)  # type: ignore[arg-type]
    await bus.stop()
    await log.close()


async def test_subscriber_status_reflects_lag(rig) -> None:
    log, bus, _ = rig
    block = asyncio.Event()
    seen: list[str] = []

    async def slow(ev: dict) -> None:
        # hold the first event to induce lag
        if not seen:
            await block.wait()
        seen.append(ev["event_id"])

    bus.subscribe("slow", "*", slow)
    await bus.start()
    for i in range(10):
        await log.append(_event(i))
    await bus.notify("dummy")
    # wait for the subscriber to be stuck on the first event
    await asyncio.sleep(0.1)
    status = await bus.subscriber_status("slow")
    assert status["lag_count"] == 10  # none delivered yet
    block.set()
    await _wait_for(lambda: len(seen) == 10)
    status2 = await bus.subscriber_status("slow")
    assert status2["lag_count"] == 0


async def test_start_is_idempotent_and_subscribe_rejected_while_running(rig) -> None:
    log, bus, _ = rig

    async def cb(ev: dict) -> None:
        pass

    bus.subscribe("s1", "*", cb)
    await bus.start()
    await bus.start()  # no-op
    with pytest.raises(RuntimeError):
        bus.subscribe("s2", "*", cb)


def test_lag_warn_threshold_is_large_enough() -> None:
    # sanity: the constant is the documented threshold
    assert LAG_WARN_THRESHOLD >= 10_000
