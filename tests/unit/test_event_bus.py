"""
Unit tests for adminme.events.bus.EventBus (prompt 03).

Covers fan-out, filtering, per-subscriber checkpoints, retry on failure,
degraded state, and persistence across restart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from adminme.events.bus import EventBus, LAG_WARN_THRESHOLD
from adminme.events.log import EventLog

TEST_KEY = b"k" * 32


def _event(i: int = 0, *, type: str = "test.event") -> dict:
    return {
        "type": type,
        "tenant_id": "tenant-a",
        "owner_scope": "shared:household",
        "version": 1,
        "payload": {"i": i},
    }


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
            calls >= 3
            and bus._subscribers["doomed"].degraded  # type: ignore[attr-defined]
        ),
        timeout=5.0,
    )
    status = await bus.subscriber_status("doomed")
    assert status["degraded"] is True
    # further appends do not get delivered
    await log.append(_event(2))
    await bus.notify("dummy")
    await asyncio.sleep(0.2)
    # calls count may have hit threshold exactly; shouldn't have advanced past it
    assert calls == 3

    # reset restores delivery — rewrite callback via a new subscriber would be
    # cleaner, but reset_subscriber alone is what the API advertises.
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


async def test_subscriber_registered_after_events_replays_on_start(tmp_path: Path) -> None:
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
