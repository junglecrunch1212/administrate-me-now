"""
Unit tests for the recurrences projection (prompt 06).

Covers recurrence.added/completed/updated, RRULE advancement, queries,
rebuild correctness, and multi-tenant isolation.
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
from adminme.projections.recurrences import RecurrencesProjection
from adminme.projections.recurrences.queries import (
    all_active,
    due_within,
    for_member,
    get_recurrence,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"r" * 32


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(RecurrencesProjection())
    await runner.start()
    try:
        yield {"config": config, "log": log, "bus": bus, "runner": runner}
    finally:
        await runner.stop()
        await log.close()


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str = "tenant-a",
    owner_scope: str = "shared:household",
    sensitivity: str = "normal",
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity=sensitivity,
        payload=payload,
    )


async def _wait_for_checkpoint(bus: EventBus, subscriber_id: str, target: str) -> None:
    import asyncio

    for _ in range(200):
        status = await bus.subscriber_status(subscriber_id)
        if status["checkpoint_event_id"] == target and status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"subscriber {subscriber_id} did not reach {target}: "
        f"{await bus.subscriber_status(subscriber_id)}"
    )


def _daily_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "recurrence_id": "rec-daily",
        "linked_kind": "household",
        "linked_id": "hh",
        "kind": "chore",
        "rrule": "FREQ=DAILY",
        "next_occurrence": "2026-04-24T08:00:00Z",
        "lead_time_days": 0,
        "trackable": False,
    }
    base.update(overrides)
    return base


async def test_recurrence_added_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(kind="birthday", linked_kind="party", linked_id="m1"),
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:recurrences", eid)

    conn = runner.connection("recurrences")
    row = get_recurrence(conn, tenant_id="tenant-a", recurrence_id="rec-daily")
    assert row is not None
    assert row["kind"] == "birthday"
    assert row["linked_kind"] == "party"
    assert row["linked_id"] == "m1"
    assert row["rrule"] == "FREQ=DAILY"
    assert row["next_occurrence"] == "2026-04-24T08:00:00Z"


async def test_recurrence_completed_advances_next_occurrence(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("recurrence.added", _daily_payload()))
    last = await log.append(
        _envelope(
            "recurrence.completed",
            {
                "recurrence_id": "rec-daily",
                "completed_at": "2026-04-24T08:30:00Z",
                "completed_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    row = get_recurrence(conn, tenant_id="tenant-a", recurrence_id="rec-daily")
    assert row is not None
    # Daily RRULE from 2026-04-24T08:00:00Z, completed at 08:30 → next is 04-25T08:00.
    assert row["next_occurrence"] == "2026-04-25T08:00:00Z"


async def test_recurrence_completed_weekly_advances_by_week(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(
                recurrence_id="rec-weekly",
                rrule="FREQ=WEEKLY",
                next_occurrence="2026-04-24T08:00:00Z",
            ),
        )
    )
    last = await log.append(
        _envelope(
            "recurrence.completed",
            {
                "recurrence_id": "rec-weekly",
                "completed_at": "2026-04-24T09:00:00Z",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    row = get_recurrence(conn, tenant_id="tenant-a", recurrence_id="rec-weekly")
    assert row is not None
    assert row["next_occurrence"] == "2026-05-01T08:00:00Z"


async def test_recurrence_updated_recomputes_next_when_rrule_changes(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("recurrence.added", _daily_payload()))
    last = await log.append(
        _envelope(
            "recurrence.updated",
            {
                "recurrence_id": "rec-daily",
                "updated_at": "2026-04-24T12:00:00Z",
                "field_updates": {"rrule": "FREQ=YEARLY", "lead_time_days": 7},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    row = get_recurrence(conn, tenant_id="tenant-a", recurrence_id="rec-daily")
    assert row is not None
    assert row["rrule"] == "FREQ=YEARLY"
    assert row["lead_time_days"] == 7
    # next_occurrence was recomputed from "now" when rrule changed.
    assert row["next_occurrence"] != "2026-04-24T08:00:00Z"


async def test_due_within_filters_cutoff(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(
                recurrence_id="soon",
                next_occurrence="2026-04-25T00:00:00Z",
            ),
        )
    )
    last = await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(
                recurrence_id="distant",
                next_occurrence="2026-06-01T00:00:00Z",
            ),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    rows = due_within(
        conn, tenant_id="tenant-a", days=30, as_of_iso="2026-04-24T00:00:00Z"
    )
    assert {r["recurrence_id"] for r in rows} == {"soon"}


async def test_for_member_includes_household(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(recurrence_id="rec-hh", linked_kind="household", linked_id="hh"),
        )
    )
    await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(
                recurrence_id="rec-member", linked_kind="party", linked_id="m1"
            ),
        )
    )
    last = await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(
                recurrence_id="rec-other", linked_kind="party", linked_id="m2"
            ),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    rows = for_member(conn, tenant_id="tenant-a", member_party_id="m1")
    assert {r["recurrence_id"] for r in rows} == {"rec-hh", "rec-member"}


async def test_recurrence_added_idempotent(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    env = _envelope("recurrence.added", _daily_payload())
    await log.append(env)
    last = await log.append(env)
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    count = conn.execute(
        "SELECT count(*) FROM recurrences WHERE tenant_id = ? AND recurrence_id = ?",
        ("tenant-a", "rec-daily"),
    ).fetchone()[0]
    assert count == 1


async def test_rebuild_produces_equivalent_state(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # 30 events: 20 added, 5 completed, 5 updated.
    for i in range(20):
        await log.append(
            _envelope(
                "recurrence.added",
                _daily_payload(
                    recurrence_id=f"rec-{i}",
                    linked_kind="household",
                    linked_id="hh",
                    kind="chore",
                    rrule="FREQ=DAILY",
                    next_occurrence=f"2026-04-{24 + (i % 5):02d}T08:00:00Z",
                ),
            )
        )
    for i in range(5):
        await log.append(
            _envelope(
                "recurrence.completed",
                {
                    "recurrence_id": f"rec-{i}",
                    "completed_at": f"2026-04-{24 + i:02d}T09:00:00Z",
                },
            )
        )
    for i in range(5, 10):
        await log.append(
            _envelope(
                "recurrence.updated",
                {
                    "recurrence_id": f"rec-{i}",
                    "updated_at": "2026-04-24T12:00:00Z",
                    "field_updates": {"trackable": True, "notes": f"note-{i}"},
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    pre = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM recurrences ORDER BY recurrence_id"
        ).fetchall()
    ]

    await runner.rebuild("recurrences")

    conn = runner.connection("recurrences")
    post = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM recurrences ORDER BY recurrence_id"
        ).fetchall()
    ]
    assert pre == post


async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(recurrence_id="rec-1", notes="Tenant A"),
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(recurrence_id="rec-1", notes="Tenant B"),
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    a = get_recurrence(conn, tenant_id="tenant-a", recurrence_id="rec-1")
    b = get_recurrence(conn, tenant_id="tenant-b", recurrence_id="rec-1")
    assert a is not None and a["notes"] == "Tenant A"
    assert b is not None and b["notes"] == "Tenant B"


async def test_all_active_orders_by_next(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(recurrence_id="rec-late", next_occurrence="2026-06-01T00:00:00Z"),
        )
    )
    last = await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(recurrence_id="rec-soon", next_occurrence="2026-04-25T00:00:00Z"),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:recurrences", last)

    conn = runner.connection("recurrences")
    rows = all_active(conn, tenant_id="tenant-a")
    assert [r["recurrence_id"] for r in rows] == ["rec-soon", "rec-late"]


async def test_scope_canary_stub_privileged_lands(rig: dict[str, Any]) -> None:
    """Prompt 06 stub — prompt 08 extends to ScopeViolation on query."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "recurrence.added",
            _daily_payload(recurrence_id="rec-priv"),
            sensitivity="privileged",
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:recurrences", eid)

    conn = runner.connection("recurrences")
    row = get_recurrence(conn, tenant_id="tenant-a", recurrence_id="rec-priv")
    assert row is not None
    assert row["sensitivity"] == "privileged"
