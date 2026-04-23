"""
Unit tests for ``xlsx.regenerated`` emit — the ONLY emit from the xlsx
forward projection per [§2.2] resolution.

Validates that after ``regenerate_now`` completes, an ``xlsx.regenerated``
system event lands in the log with the expected payload shape.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

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
    FINANCE_WORKBOOK_NAME,
    OPS_WORKBOOK_NAME,
    XlsxWorkbooksProjection,
)
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext

TEST_KEY = b"e" * 32


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
        payload=payload,
    )


async def _wait_idle(bus: EventBus, subscriber_id: str, timeout: float = 10.0) -> None:
    import asyncio

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await bus.subscriber_status(subscriber_id)
        if status["lag_count"] == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"subscriber {subscriber_id} stayed lagged: {status}")


@pytest.fixture
async def rig(tmp_path: Path):
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

    # A trivial fixture so sheets have data.
    await log.append(_envelope("party.created", {
        "party_id": "p1", "kind": "person",
        "display_name": "A", "sort_name": "A",
    }))
    last = await log.append(_envelope("task.created", {
        "task_id": "t1", "title": "t",
    }))
    assert last is not None
    await bus.notify(last)
    for name in [
        "parties", "tasks", "commitments", "recurrences", "calendars",
        "places_assets_accounts", "money",
    ]:
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
    projection = XlsxWorkbooksProjection(config, ctx, event_log=log, debounce_s=0.1)
    try:
        yield {
            "config": config, "log": log, "bus": bus, "runner": runner,
            "ctx": ctx, "projection": projection,
        }
    finally:
        await runner.stop()
        await log.close()


async def _latest_xlsx_event(log: EventLog) -> dict[str, Any] | None:
    """Return the most recent xlsx.regenerated event in the log, or None."""
    events: list[dict[str, Any]] = []
    async for e in log.read_since(None):
        events.append(e)
    for e in reversed(events):
        if e["type"] == "xlsx.regenerated":
            return e
    return None


async def test_regenerate_emits_xlsx_regenerated(rig: dict[str, Any]) -> None:
    projection = rig["projection"]
    log = rig["log"]

    await projection.regenerate_now(OPS_WORKBOOK_NAME)

    evt = await _latest_xlsx_event(log)
    assert evt is not None
    assert evt["type"] == "xlsx.regenerated"
    assert evt["source_adapter"] == "xlsx_workbooks"
    assert evt["owner_scope"] == "shared:household"
    payload = evt["payload"]
    assert payload["workbook_name"] == OPS_WORKBOOK_NAME
    assert payload["sheets_regenerated"] == [
        "Tasks", "Recurrences", "Commitments", "People", "Metadata"
    ]
    assert payload["duration_ms"] >= 0
    assert isinstance(payload["last_event_id_consumed"], str)


async def test_regenerate_finance_emits_finance_name(rig: dict[str, Any]) -> None:
    projection = rig["projection"]
    log = rig["log"]

    await projection.regenerate_now(FINANCE_WORKBOOK_NAME)

    evt = await _latest_xlsx_event(log)
    assert evt is not None
    assert evt["payload"]["workbook_name"] == FINANCE_WORKBOOK_NAME
    assert evt["payload"]["sheets_regenerated"] == [
        "Raw Data", "Accounts", "Metadata"
    ]


async def test_regenerate_duration_ms_sensible(rig: dict[str, Any]) -> None:
    projection = rig["projection"]
    log = rig["log"]

    await projection.regenerate_now(OPS_WORKBOOK_NAME)

    evt = await _latest_xlsx_event(log)
    assert evt is not None
    duration = evt["payload"]["duration_ms"]
    assert 0 <= duration < 60000


async def test_regenerate_last_event_id_matches_latest(rig: dict[str, Any]) -> None:
    projection = rig["projection"]
    log = rig["log"]

    # Capture the latest id BEFORE xlsx appends its own event.
    before = await log.latest_event_id()
    assert before is not None

    await projection.regenerate_now(OPS_WORKBOOK_NAME)

    evt = await _latest_xlsx_event(log)
    assert evt is not None
    assert evt["payload"]["last_event_id_consumed"] == before
