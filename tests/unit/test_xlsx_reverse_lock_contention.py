"""
Unit tests for the xlsx reverse daemon's lock contention behaviour
(07c-β-2).

When the forward daemon holds the per-workbook advisory lock, the reverse
cycle MUST emit ``xlsx.reverse_skipped_during_forward`` and return — no
domain events, no ``xlsx.reverse_projected``. Per-workbook concurrent
cycles MUST serialize on the daemon's internal asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import pytest

from adminme.daemons.xlsx_sync import reverse as reverse_mod
from adminme.daemons.xlsx_sync.reverse import XlsxReverseDaemon
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.calendars import CalendarsProjection
from adminme.projections.commitments import CommitmentsProjection
from adminme.projections.money import MoneyProjection
from adminme.projections.parties import PartiesProjection
from adminme.projections.places_assets_accounts import PlacesAssetsAccountsProjection
from adminme.projections.recurrences import RecurrencesProjection
from adminme.projections.runner import ProjectionRunner
from adminme.projections.tasks import TasksProjection
from adminme.projections.xlsx_workbooks import (
    OPS_WORKBOOK_NAME,
    XlsxWorkbooksProjection,
)
from adminme.projections.xlsx_workbooks.lockfile import (
    acquire_workbook_lock as real_acquire,
)
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext

TEST_KEY = b"l" * 32


def _envelope(event_type: str, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id="tenant-a",
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload=payload,
    )


async def _wait_idle(bus: EventBus, sid: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = await bus.subscriber_status(sid)
        if s["lag_count"] == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{sid} stayed lagged: {s}")


async def _events_of_type(
    log: EventLog, event_type: str, after: str | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for e in log.read_since(after):
        if e["type"] == event_type:
            out.append(e)
    return out


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    (instance_dir / "config").mkdir()
    (instance_dir / "config" / "instance.yaml").write_text("tenant_id: tenant-a\n")
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(PartiesProjection())
    runner.register(TasksProjection())
    runner.register(CommitmentsProjection())
    runner.register(RecurrencesProjection())
    runner.register(CalendarsProjection())
    runner.register(PlacesAssetsAccountsProjection())
    runner.register(MoneyProjection())
    await runner.start()

    last = await log.append(_envelope("task.created", {
        "task_id": "t1", "title": "first task",
    }))
    await bus.notify(last)
    for name in (
        "parties", "tasks", "commitments", "recurrences", "calendars",
        "places_assets_accounts", "money",
    ):
        await _wait_idle(bus, f"projection:{name}")

    ctx = XlsxQueryContext(
        parties_conn=runner.connection("parties"),
        tasks_conn=runner.connection("tasks"),
        commitments_conn=runner.connection("commitments"),
        recurrences_conn=runner.connection("recurrences"),
        calendars_conn=runner.connection("calendars"),
        places_assets_accounts_conn=runner.connection("places_assets_accounts"),
        money_conn=runner.connection("money"),
    )
    forward = XlsxWorkbooksProjection(config, ctx, event_log=log, debounce_s=0.05)
    await forward.regenerate_now(OPS_WORKBOOK_NAME)
    reverse = XlsxReverseDaemon(
        config,
        ctx,
        event_log=log,
        flush_wait_s=0.05,
        forward_lock_timeout_s=0.5,
        delete_undo_window_s=0.05,
    )
    cursor = await log.latest_event_id()
    try:
        yield {
            "config": config,
            "log": log,
            "ctx": ctx,
            "forward": forward,
            "reverse": reverse,
            "cursor": cursor,
        }
    finally:
        await reverse.stop()
        await runner.stop()
        await log.close()


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

async def test_forward_lock_held_emits_skipped(
    rig: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    reverse = rig["reverse"]
    log = rig["log"]
    cursor = rig["cursor"]

    @contextmanager
    def _always_timeout(*_args: Any, **_kwargs: Any) -> Iterator[None]:
        raise TimeoutError("simulated forward lock held")
        yield  # pragma: no cover

    monkeypatch.setattr(reverse_mod, "acquire_workbook_lock", _always_timeout)

    await reverse.run_cycle_now(OPS_WORKBOOK_NAME)

    skipped = await _events_of_type(
        log, "xlsx.reverse_skipped_during_forward", cursor
    )
    assert len(skipped) == 1
    assert skipped[0]["payload"]["skip_reason"] == "forward_lock_held"
    projected = await _events_of_type(log, "xlsx.reverse_projected", cursor)
    assert projected == []


async def test_forward_lock_released_in_time_proceeds(
    rig: dict[str, Any]
) -> None:
    config = rig["config"]
    reverse = rig["reverse"]
    log = rig["log"]
    cursor = rig["cursor"]

    lock_path = (
        config.xlsx_workbooks_dir / OPS_WORKBOOK_NAME
    ).with_suffix(".xlsx.lock")

    holder_release = threading.Event()

    def _hold_lock() -> None:
        with real_acquire(lock_path, timeout_s=2.0):
            holder_release.wait(timeout=0.2)

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    # Give the holder a moment to acquire.
    await asyncio.sleep(0.05)

    cycle_task = asyncio.create_task(reverse.run_cycle_now(OPS_WORKBOOK_NAME))
    # Release the holder slightly before the daemon's lock timeout.
    await asyncio.sleep(0.1)
    holder_release.set()
    await cycle_task

    holder.join(timeout=1.0)

    skipped = await _events_of_type(
        log, "xlsx.reverse_skipped_during_forward", cursor
    )
    projected = await _events_of_type(log, "xlsx.reverse_projected", cursor)
    assert skipped == []
    assert len(projected) == 1


async def test_concurrent_cycles_serialized(rig: dict[str, Any]) -> None:
    reverse = rig["reverse"]

    await asyncio.gather(
        reverse.run_cycle_now(OPS_WORKBOOK_NAME),
        reverse.run_cycle_now(OPS_WORKBOOK_NAME),
    )
    # The instrumentation counter should never have observed >1 concurrent
    # cycle for the same workbook.
    assert reverse._max_concurrent_observed <= 1


async def test_skipped_does_not_emit_reverse_projected(
    rig: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    reverse = rig["reverse"]
    log = rig["log"]
    cursor = rig["cursor"]

    @contextmanager
    def _always_timeout(*_args: Any, **_kwargs: Any) -> Iterator[None]:
        raise TimeoutError("simulated forward lock held")
        yield  # pragma: no cover

    monkeypatch.setattr(reverse_mod, "acquire_workbook_lock", _always_timeout)

    await reverse.run_cycle_now(OPS_WORKBOOK_NAME)

    projected = await _events_of_type(log, "xlsx.reverse_projected", cursor)
    assert projected == [], (
        "skip cycle MUST NOT emit xlsx.reverse_projected; spec §3.11"
    )
