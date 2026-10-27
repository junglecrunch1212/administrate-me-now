"""Contract tests for the single durable event-write seam."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.projections.runner import ProjectionRunner
from adminme.projections.tasks import TasksProjection

TEST_KEY = b"w" * 32


def _envelope(event_type: str, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_780_000_000_000,
        tenant_id="tenant-test",
        type=event_type,
        schema_version=1,
        occurred_at="2026-05-28T12:00:00Z",
        source_adapter="writer-contract-test",
        source_account_id="account-test",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        correlation_id="corr-writer-test",
        causation_id="cause-writer-test",
        payload=payload,
    )


async def _wait_until(predicate: Any, timeout_s: float = 3.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not await predicate():
            await asyncio.sleep(0.01)


async def test_successful_write_commits_then_wakes_bus(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    bus = EventBus(log, tmp_path / "bus.db")
    delivered: list[dict[str, Any]] = []
    delivered_signal = asyncio.Event()

    async def consume(event: dict[str, Any]) -> None:
        delivered.append(event)
        delivered_signal.set()

    bus.subscribe("writer-test", ["task.created"], consume)
    await bus.start()
    writer = EventWriter(log, bus)
    try:
        event_id = await writer.write(
            _envelope("task.created", {"task_id": "task-1", "title": "One"})
        )
        await asyncio.wait_for(delivered_signal.wait(), timeout=3.0)
        assert delivered[0]["event_id"] == event_id
        assert delivered[0]["source_adapter"] == "writer-contract-test"
        assert delivered[0]["correlation_id"] == "corr-writer-test"
        assert delivered[0]["causation_id"] == "cause-writer-test"
    finally:
        await bus.stop()
        await log.close()


async def test_failed_append_never_notifies_delivery() -> None:
    class FailingLog:
        _tenant_id_hint = "tenant-test"

        async def append_batch(self, *_args: Any, **_kwargs: Any) -> list[str]:
            raise OSError("simulated durable append failure")

    class RecordingBus:
        def __init__(self) -> None:
            self.notifications: list[str] = []

        async def notify(
            self, event_id: str, *, event_types: frozenset[str] | None = None
        ) -> None:
            self.notifications.append(event_id)

    bus = RecordingBus()
    writer = EventWriter(FailingLog(), bus)  # type: ignore[arg-type]
    with pytest.raises(OSError, match="durable append failure"):
        await writer.write(
            _envelope("task.created", {"task_id": "task-1", "title": "One"})
        )
    assert bus.notifications == []


async def test_redelivery_after_restart_keeps_projection_idempotent(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)

    bus_one = EventBus(log, config.bus_checkpoint_path)
    runner_one = ProjectionRunner(bus_one, log, config, encryption_key=TEST_KEY)
    runner_one.register(TasksProjection())
    await runner_one.start()
    writer = EventWriter(log, bus_one)
    await writer.write(
        _envelope("task.created", {"task_id": "task-replay", "title": "Stable"})
    )

    async def first_drained() -> bool:
        return (await bus_one.subscriber_status("projection:tasks"))["lag_count"] == 0

    await _wait_until(first_drained)
    assert (
        runner_one.connection("tasks")
        .execute("SELECT COUNT(*) FROM tasks WHERE task_id='task-replay'")
        .fetchone()[0]
        == 1
    )

    # Simulate commit-before-checkpoint crash: force the durable checkpoint
    # behind the committed event, then construct a fresh bus and projection.
    await bus_one.set_checkpoint("projection:tasks", None)
    await runner_one.stop()

    bus_two = EventBus(log, config.bus_checkpoint_path)
    runner_two = ProjectionRunner(bus_two, log, config, encryption_key=TEST_KEY)
    runner_two.register(TasksProjection())
    await runner_two.start()
    try:

        async def replay_drained() -> bool:
            return (await bus_two.subscriber_status("projection:tasks"))[
                "lag_count"
            ] == 0

        await _wait_until(replay_drained)
        assert (
            runner_two.connection("tasks")
            .execute("SELECT COUNT(*) FROM tasks WHERE task_id='task-replay'")
            .fetchone()[0]
            == 1
        )
    finally:
        await runner_two.stop()
        await log.close()
