"""
Unit tests for the xlsx forward debounce machinery.

Per phase 07b prompt Commit 3 §Debounce: 5-second window; new trigger
events cancel the pending task and reschedule; ``regenerate_now``
bypasses entirely.

Tests use a very short ``debounce_s`` so they do not block, and patch
``_regenerate`` on the projection instance to count calls without
actually writing workbooks.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
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
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext

TEST_KEY = b"d" * 32


@pytest.fixture
async def projection_rig(tmp_path: Path):
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

    ctx = XlsxQueryContext(
        parties_conn=runner.connection("parties"),
        tasks_conn=runner.connection("tasks"),
        commitments_conn=runner.connection("commitments"),
        recurrences_conn=runner.connection("recurrences"),
        calendars_conn=runner.connection("calendars"),
        places_assets_accounts_conn=runner.connection("places_assets_accounts"),
        money_conn=runner.connection("money"),
    )
    projection = XlsxWorkbooksProjection(
        config, ctx, event_log=log, debounce_s=0.15
    )
    try:
        yield {
            "config": config, "log": log, "bus": bus, "runner": runner,
            "projection": projection,
        }
    finally:
        # Cancel any lingering debounce tasks before teardown.
        for task in projection._pending_tasks.values():
            if not task.done():
                task.cancel()
        await runner.stop()
        await log.close()


def _envelope_dict(event_type: str) -> dict[str, Any]:
    return {
        "event_id": "ev_test",
        "type": event_type,
        "tenant_id": "tenant-a",
        "payload": {},
    }


async def test_multiple_events_coalesce(projection_rig: dict[str, Any]) -> None:
    """Ten events in a burst: one regenerate runs after the debounce."""
    projection = projection_rig["projection"]
    call_count = 0

    async def fake_regen(workbook: str) -> None:
        nonlocal call_count
        call_count += 1

    projection._regenerate = fake_regen  # type: ignore[method-assign]

    for _ in range(10):
        projection.apply(_envelope_dict("task.created"), conn=None)

    # Wait long enough for the debounce to fire.
    await asyncio.sleep(0.35)
    assert call_count == 1


async def test_debounce_cancels_on_new_event(projection_rig: dict[str, Any]) -> None:
    """A new event arriving mid-debounce resets the timer."""
    projection = projection_rig["projection"]
    fired_at: list[float] = []

    async def fake_regen(workbook: str) -> None:
        fired_at.append(time.monotonic())

    projection._regenerate = fake_regen  # type: ignore[method-assign]

    start = time.monotonic()
    projection.apply(_envelope_dict("task.created"), conn=None)
    # Wait ~half the debounce interval then push another event.
    await asyncio.sleep(0.07)
    projection.apply(_envelope_dict("task.created"), conn=None)

    # Only one fire after the second debounce interval elapses.
    await asyncio.sleep(0.3)
    assert len(fired_at) == 1
    # Fired at least debounce_s after the second event, not after the first.
    elapsed = fired_at[0] - start
    assert elapsed >= 0.15  # new debounce interval from second event


async def test_regenerate_now_bypasses_debounce(
    projection_rig: dict[str, Any],
) -> None:
    """Even with a pending task scheduled, ``regenerate_now`` runs
    synchronously without waiting for the debounce."""
    projection = projection_rig["projection"]
    calls: list[tuple[str, str]] = []

    async def fake_regen(workbook: str) -> None:
        calls.append(("regen", workbook))

    projection._regenerate = fake_regen  # type: ignore[method-assign]

    # Schedule a debounced regen — it will NOT have fired yet.
    projection.apply(_envelope_dict("task.created"), conn=None)
    # Invoke regenerate_now; this call should run immediately.
    await projection.regenerate_now(OPS_WORKBOOK_NAME)

    # The immediate call happened; the scheduled debounce may or may not
    # have also fired depending on timing — accept both.
    assert any(c == ("regen", OPS_WORKBOOK_NAME) for c in calls)
