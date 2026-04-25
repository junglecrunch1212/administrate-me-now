"""
Unit tests for the calendars projection (prompt 06).

Covers calendar.event_added/updated/deleted with UNIQUE upsert on
(calendar_source, external_uid), rebuild correctness across add→update→
delete sequences, query behavior, and multi-tenant isolation.
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
from adminme.lib.session import Session, build_internal_session
from adminme.projections.calendars import CalendarsProjection
from adminme.projections.calendars.queries import (
    busy_slots,
    by_source,
    today,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"l" * 32


def _S(tenant_id: str = "tenant-a") -> Session:
    """Internal-actor Session for projection-read tests; carries tenant_id
    only. 08a + scope filtering use principal role so allowed_read accepts
    shared:household + private:<self> rows."""
    return build_internal_session("test_actor", "principal", tenant_id)



@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(CalendarsProjection())
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


def _added_payload(**overrides: Any) -> dict[str, Any]:
    # Uses the prompt-04 CalendarEventAddedV1 shape: source / external_event_id
    # / calendar_id / summary / start / end / location / attendees / body.
    base: dict[str, Any] = {
        "source": "google",
        "external_event_id": "ext-1",
        "calendar_id": "cal-primary",
        "summary": "Dentist",
        "start": "2026-04-25T14:00:00Z",
        "end": "2026-04-25T15:00:00Z",
        "location": "Downtown clinic",
        "attendees": [{"party_id": "m1"}],
    }
    base.update(overrides)
    return base


async def test_calendar_event_added_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(_envelope("calendar.event_added", _added_payload()))
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:calendars", eid)

    conn = runner.connection("calendars")
    row = by_source(conn, _S("tenant-a"), calendar_source="google", external_uid="ext-1"
    )
    assert row is not None
    assert row["summary"] == "Dentist"
    assert row["start_at"] == "2026-04-25T14:00:00Z"
    assert row["end_at"] == "2026-04-25T15:00:00Z"
    assert row["location"] == "Downtown clinic"


async def test_duplicate_added_upserts_to_single_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("calendar.event_added", _added_payload()))
    last = await log.append(
        _envelope("calendar.event_added", _added_payload(summary="Dentist rescheduled"))
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:calendars", last)

    conn = runner.connection("calendars")
    count = conn.execute(
        "SELECT count(*) FROM calendar_events "
        "WHERE tenant_id = ? AND calendar_source = ? AND external_uid = ?",
        ("tenant-a", "google", "ext-1"),
    ).fetchone()[0]
    assert count == 1
    row = by_source(conn, _S("tenant-a"), calendar_source="google", external_uid="ext-1"
    )
    assert row is not None
    assert row["summary"] == "Dentist rescheduled"


async def test_calendar_event_updated_applies_field_updates(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("calendar.event_added", _added_payload()))
    last = await log.append(
        _envelope(
            "calendar.event_updated",
            {
                "calendar_event_id": "ignored",
                "calendar_source": "google",
                "external_uid": "ext-1",
                "updated_at": "2026-04-25T10:00:00Z",
                "field_updates": {
                    "location": "Uptown clinic",
                    "end_at": "2026-04-25T16:00:00Z",
                },
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:calendars", last)

    conn = runner.connection("calendars")
    row = by_source(conn, _S("tenant-a"), calendar_source="google", external_uid="ext-1"
    )
    assert row is not None
    assert row["location"] == "Uptown clinic"
    assert row["end_at"] == "2026-04-25T16:00:00Z"
    # Untouched fields remain.
    assert row["summary"] == "Dentist"


async def test_calendar_event_deleted_removes_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    add_eid = await log.append(_envelope("calendar.event_added", _added_payload()))
    await bus.notify(add_eid)
    await _wait_for_checkpoint(bus, "projection:calendars", add_eid)
    before = _current_count(rig)

    last = await log.append(
        _envelope(
            "calendar.event_deleted",
            {
                "calendar_event_id": "ignored",
                "calendar_source": "google",
                "external_uid": "ext-1",
                "deleted_at": "2026-04-25T11:00:00Z",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:calendars", last)

    after = _current_count(rig)
    assert before == 1
    assert after == 0
    conn = runner.connection("calendars")
    row = by_source(conn, _S("tenant-a"), calendar_source="google", external_uid="ext-1"
    )
    assert row is None


def _current_count(rig: dict[str, Any]) -> int:
    conn = rig["runner"].connection("calendars")
    return int(
        conn.execute("SELECT count(*) FROM calendar_events").fetchone()[0]
    )


async def test_today_filters_by_member_and_window(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # Event today, m1 is attendee.
    await log.append(
        _envelope(
            "calendar.event_added",
            _added_payload(external_event_id="ev-today-m1"),
        )
    )
    # Event today, different attendee (no m1).
    await log.append(
        _envelope(
            "calendar.event_added",
            _added_payload(
                external_event_id="ev-today-m2",
                attendees=[{"party_id": "m2"}],
                summary="Meeting with m2",
            ),
        )
    )
    # Event next week — not today.
    last = await log.append(
        _envelope(
            "calendar.event_added",
            _added_payload(
                external_event_id="ev-next-week",
                start="2026-05-02T14:00:00Z",
                end="2026-05-02T15:00:00Z",
                summary="Next week",
            ),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:calendars", last)

    conn = runner.connection("calendars")
    rows = today(conn, _S("tenant-a"), member_party_id="m1",
        today_iso="2026-04-25T00:00:00Z",
        tz_name="UTC",
    )
    uids = {r["external_uid"] for r in rows}
    assert uids == {"ev-today-m1"}


async def test_busy_slots_merges_events_and_availability(
    rig: dict[str, Any],
) -> None:
    """busy_slots merges calendar_events (where party is owner or attendee)
    with availability_blocks. Tests both sources surface correctly."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # Add a calendar event m1 attends.
    last = await log.append(
        _envelope(
            "calendar.event_added",
            _added_payload(
                external_event_id="ev-1",
                start="2026-04-25T14:00:00Z",
                end="2026-04-25T15:00:00Z",
            ),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:calendars", last)

    # Inject an availability_block directly — prompt 06 does not yet have
    # an event type for availability ingestion (prompt 11 adds one).
    conn = runner.connection("calendars")
    conn.execute(
        "INSERT INTO availability_blocks "
        "(availability_id, tenant_id, party_id, start_at, end_at, "
        "source_adapter, last_event_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "avail-1",
            "tenant-a",
            "m1",
            "2026-04-25T16:00:00Z",
            "2026-04-25T17:00:00Z",
            "busy-source",
            "ev_seed",
        ),
    )
    conn.commit()

    slots = busy_slots(conn, _S("tenant-a"), member_party_id="m1",
        range_start_iso="2026-04-25T00:00:00Z",
        range_end_iso="2026-04-25T23:59:59Z",
    )
    starts = [s["start_at"] for s in slots]
    assert "2026-04-25T14:00:00Z" in starts
    assert "2026-04-25T16:00:00Z" in starts


async def test_rebuild_after_add_update_delete_sequence(
    rig: dict[str, Any],
) -> None:
    """Rebuild correctness when events include hard-deletes. After replay
    a deleted row stays deleted."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # 30 events: 10 add / 10 update / 5 delete / 5 add (that stay).
    for i in range(10):
        await log.append(
            _envelope(
                "calendar.event_added",
                _added_payload(
                    external_event_id=f"ext-{i}",
                    summary=f"Event {i}",
                ),
            )
        )
    for i in range(10):
        await log.append(
            _envelope(
                "calendar.event_updated",
                {
                    "calendar_event_id": "ignored",
                    "calendar_source": "google",
                    "external_uid": f"ext-{i}",
                    "updated_at": "2026-04-25T10:00:00Z",
                    "field_updates": {"summary": f"Updated {i}"},
                },
            )
        )
    for i in range(5):
        await log.append(
            _envelope(
                "calendar.event_deleted",
                {
                    "calendar_event_id": "ignored",
                    "calendar_source": "google",
                    "external_uid": f"ext-{i}",
                    "deleted_at": "2026-04-25T11:00:00Z",
                },
            )
        )
    for i in range(10, 15):
        await log.append(
            _envelope(
                "calendar.event_added",
                _added_payload(
                    external_event_id=f"ext-{i}",
                    summary=f"Fresh {i}",
                ),
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:calendars", last)

    conn = runner.connection("calendars")
    pre = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM calendar_events ORDER BY external_uid"
        ).fetchall()
    ]
    pre_count = int(
        conn.execute("SELECT count(*) FROM calendar_events").fetchone()[0]
    )
    # 10 + 5 - 5 = 10 rows.
    assert pre_count == 10

    await runner.rebuild("calendars")

    conn = runner.connection("calendars")
    post = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM calendar_events ORDER BY external_uid"
        ).fetchall()
    ]
    post_count = int(
        conn.execute("SELECT count(*) FROM calendar_events").fetchone()[0]
    )
    assert post_count == 10
    assert pre == post


async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "calendar.event_added",
            _added_payload(summary="A-event"),
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "calendar.event_added",
            _added_payload(summary="B-event"),
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:calendars", last)

    conn = runner.connection("calendars")
    a = by_source(conn, _S("tenant-a"), calendar_source="google", external_uid="ext-1"
    )
    b = by_source(conn, _S("tenant-b"), calendar_source="google", external_uid="ext-1"
    )
    assert a is not None and a["summary"] == "A-event"
    assert b is not None and b["summary"] == "B-event"


async def test_scope_canary_privileged_redacts_for_non_owner(
    rig: dict[str, Any],
) -> None:
    """Prompt 08a wired: a privileged-sensitivity calendar event with
    shared:household owner_scope drops from a non-owner's read; the row
    is still in the projection table per [§6.4] / [CONSOLE_PATTERNS §6]."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "calendar.event_added",
            _added_payload(summary="Private appointment"),
            sensitivity="privileged",
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:calendars", eid)

    conn = runner.connection("calendars")
    row = by_source(
        conn, _S("tenant-a"), calendar_source="google", external_uid="ext-1"
    )
    assert row is None

    raw = conn.execute(
        "SELECT count(*) FROM calendar_events "
        "WHERE tenant_id = ? AND calendar_source = ?",
        ("tenant-a", "google"),
    ).fetchone()
    assert raw[0] == 1
