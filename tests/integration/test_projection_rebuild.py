"""
Integration test: rebuild correctness across all seven projections.

Per SYSTEM_INVARIANTS.md §2 invariant 1: ``projection.rebuild(name)``
truncates the projection's tables and replays from event 0 producing
state equivalent to the live cursor. This test populates ~800 mixed
events across all seven projections (parties, interactions, artifacts,
commitments, tasks, recurrences, calendars), waits for catch-up,
snapshots the row data, calls rebuild per projection, and asserts
byte-equivalence.

Also includes a cross-DB referential-integrity audit: orphan foreign-
key references are logged informationally. Per [§2.3] projections do
not raise on data conflicts; they surface them via state. The test
passes regardless of orphan count.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.artifacts import ArtifactsProjection
from adminme.projections.calendars import CalendarsProjection
from adminme.projections.commitments import CommitmentsProjection
from adminme.projections.interactions import InteractionsProjection
from adminme.projections.parties import PartiesProjection
from adminme.projections.recurrences import RecurrencesProjection
from adminme.projections.runner import ProjectionRunner
from adminme.projections.tasks import TasksProjection

_log = logging.getLogger(__name__)

TEST_KEY = b"z" * 32


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


async def _wait_idle(bus: EventBus, subscriber_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await bus.subscriber_status(subscriber_id)
        if status["lag_count"] == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"subscriber {subscriber_id} stayed lagged: {status}")


def _snapshot(conn: Any, tables: list[str]) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for t in tables:
        # Sort by rowid to get a deterministic order; since we compare
        # structurally, we sort each table's rows as tuples afterward.
        rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]
        out[t] = sorted(rows)
    return out


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(PartiesProjection())
    runner.register(InteractionsProjection())
    runner.register(ArtifactsProjection())
    runner.register(CommitmentsProjection())
    runner.register(TasksProjection())
    runner.register(RecurrencesProjection())
    runner.register(CalendarsProjection())
    await runner.start()
    try:
        yield {"log": log, "bus": bus, "runner": runner}
    finally:
        await runner.stop()
        await log.close()


async def test_rebuild_preserves_all_seven_projections(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # Populate ~500 mixed events.
    # 100 parties; 100 identifiers; 50 memberships; 50 relationships;
    # 150 messaging; 30 telephony; 20 artifacts.
    for i in range(100):
        await log.append(
            _envelope("party.created", {
                "party_id": f"p{i:03d}",
                "kind": "person",
                "display_name": f"Name{i}",
                "sort_name": f"Name{i:03d}",
            })
        )
    for i in range(100):
        await log.append(
            _envelope("identifier.added", {
                "identifier_id": f"id{i:03d}",
                "party_id": f"p{i:03d}",
                "kind": "email",
                "value": f"p{i}@example.com",
                "value_normalized": f"p{i}@example.com",
                "verified": False,
                "primary_for_kind": True,
            })
        )
    # household + 50 memberships
    await log.append(
        _envelope("party.created", {
            "party_id": "hh",
            "kind": "household",
            "display_name": "HH",
            "sort_name": "HH",
        })
    )
    for i in range(50):
        await log.append(
            _envelope("membership.added", {
                "membership_id": f"mem{i:03d}",
                "party_id": f"p{i:03d}",
                "parent_party_id": "hh",
                "role": "principal",
            })
        )
    for i in range(50):
        await log.append(
            _envelope("relationship.added", {
                "relationship_id": f"rel{i:03d}",
                "party_a": f"p{i:03d}",
                "party_b": f"p{(i + 1) % 100:03d}",
                "label": "sibling",
                "direction": "mutual",
            })
        )
    for i in range(150):
        if i % 2 == 0:
            await log.append(
                _envelope(
                    "messaging.received",
                    {
                        "source_channel": "gmail",
                        "from_identifier": f"p{i % 100}@example.com",
                        "to_identifier": "me@example.com",
                        "thread_id": f"t{i % 10}",
                        "received_at": f"2026-04-10T10:{i % 60:02d}:00Z",
                    },
                )
            )
        else:
            await log.append(
                _envelope(
                    "messaging.sent",
                    {
                        "source_channel": "gmail",
                        "to_identifier": f"p{i % 100}@example.com",
                        "thread_id": f"t{i % 10}",
                        "sent_at": f"2026-04-10T10:{i % 60:02d}:00Z",
                        "delivery_status": "sent",
                    },
                )
            )
    for i in range(30):
        await log.append(
            _envelope(
                "telephony.sms_received",
                {
                    "from_number": f"+1555{i:07d}",
                    "to_number": "+15550000000",
                    "body": f"sms {i}",
                    "received_at": f"2026-04-10T11:{i % 60:02d}:00Z",
                },
            )
        )
    for i in range(20):
        await log.append(
            _envelope(
                "artifact.received",
                {
                    "source": "test:uploader",
                    "external_artifact_id": None,
                    "mime_type": "application/pdf",
                    "size_bytes": 1024 * (i + 1),
                    "filename": f"doc{i}.pdf",
                    "sha256": f"{i:064x}",
                    "artifact_ref": f"artifact://{i:064x}",
                    "received_at": f"2026-04-10T12:{i % 60:02d}:00Z",
                },
            )
        )

    # 50 commitments: mix of proposed/confirmed/completed/dismissed/
    # snoozed/edited/cancelled/delegated/expired.
    for i in range(50):
        await log.append(
            _envelope(
                "commitment.proposed",
                {
                    "commitment_id": f"c{i:03d}",
                    "kind": "reply",
                    "owed_by_member_id": f"p{i % 100:03d}",
                    "owed_to_party_id": f"p{(i + 1) % 100:03d}",
                    "text_summary": f"Commitment {i}",
                    "confidence": 0.8,
                    "strength": "confident",
                    "source_interaction_id": f"ix-{i % 10}",
                },
            )
        )
    for i in range(0, 15):
        await log.append(
            _envelope(
                "commitment.confirmed",
                {
                    "commitment_id": f"c{i:03d}",
                    "confirmed_by_member_id": f"p{i % 100:03d}",
                    "confirmed_at": f"2026-04-11T10:{i % 60:02d}:00Z",
                },
            )
        )
    for i in range(0, 8):
        await log.append(
            _envelope(
                "commitment.completed",
                {
                    "commitment_id": f"c{i:03d}",
                    "completed_at": f"2026-04-12T10:{i % 60:02d}:00Z",
                    "completed_by_party_id": f"p{i % 100:03d}",
                },
            )
        )
    for i in range(15, 20):
        await log.append(
            _envelope(
                "commitment.dismissed",
                {
                    "commitment_id": f"c{i:03d}",
                    "dismissed_at": "2026-04-11T13:00:00Z",
                    "dismissed_by_party_id": f"p{i % 100:03d}",
                },
            )
        )
    for i in range(20, 23):
        await log.append(
            _envelope(
                "commitment.snoozed",
                {
                    "commitment_id": f"c{i:03d}",
                    "snoozed_at": "2026-04-11T14:00:00Z",
                    "snoozed_until": f"2026-05-{1 + i % 28:02d}T00:00:00Z",
                    "snoozed_by_party_id": f"p{i % 100:03d}",
                },
            )
        )
    for i in range(23, 25):
        await log.append(
            _envelope(
                "commitment.edited",
                {
                    "commitment_id": f"c{i:03d}",
                    "edited_at": "2026-04-11T15:00:00Z",
                    "edited_by_party_id": f"p{i % 100:03d}",
                    "field_updates": {"description": f"edited-{i}"},
                },
            )
        )
    for i in range(25, 27):
        await log.append(
            _envelope(
                "commitment.cancelled",
                {
                    "commitment_id": f"c{i:03d}",
                    "cancelled_at": "2026-04-11T16:00:00Z",
                    "cancelled_by_party_id": f"p{i % 100:03d}",
                },
            )
        )
    for i in range(27, 30):
        await log.append(
            _envelope(
                "commitment.delegated",
                {
                    "commitment_id": f"c{i:03d}",
                    "delegated_at": "2026-04-11T17:00:00Z",
                    "delegated_by_party_id": f"p{i % 100:03d}",
                    "delegated_to_party_id": f"p{(i + 5) % 100:03d}",
                },
            )
        )
    await log.append(
        _envelope(
            "commitment.expired",
            {
                "commitment_id": "c030",
                "expired_at": "2026-04-25T00:00:00Z",
            },
        )
    )

    # 80 tasks: 60 created, 10 updated, 5 completed, 5 deleted.
    for i in range(60):
        await log.append(
            _envelope(
                "task.created",
                {
                    "task_id": f"t{i:03d}",
                    "title": f"Task {i}",
                    "owner_member_id": f"p{i % 100:03d}",
                    "due": f"2026-04-{15 + i % 10:02d}",
                    "energy": ("low", "medium", "high")[i % 3],
                },
            )
        )
    for i in range(10):
        await log.append(
            _envelope(
                "task.updated",
                {
                    "task_id": f"t{i:03d}",
                    "updated_at": "2026-04-15T10:00:00Z",
                    "previous_status": "inbox",
                    "new_status": ("next", "in_progress", "waiting_on")[i % 3],
                    "field_updates": {},
                },
            )
        )
    for i in range(10, 15):
        await log.append(
            _envelope(
                "task.completed",
                {
                    "task_id": f"t{i:03d}",
                    "completed_by_member_id": f"p{i % 100:03d}",
                    "completed_at": f"2026-04-16T10:{i % 60:02d}:00Z",
                },
            )
        )
    for i in range(15, 20):
        await log.append(
            _envelope(
                "task.deleted",
                {
                    "task_id": f"t{i:03d}",
                    "deleted_at": "2026-04-16T12:00:00Z",
                    "deleted_by_party_id": f"p{i % 100:03d}",
                },
            )
        )

    # 30 recurrences: 20 added, 5 completed, 5 updated.
    for i in range(20):
        await log.append(
            _envelope(
                "recurrence.added",
                {
                    "recurrence_id": f"r{i:03d}",
                    "linked_kind": "party" if i % 2 == 0 else "household",
                    "linked_id": f"p{i % 100:03d}" if i % 2 == 0 else "hh",
                    "kind": ("birthday", "chore", "oil_change")[i % 3],
                    "rrule": "FREQ=WEEKLY" if i % 2 == 0 else "FREQ=MONTHLY",
                    "next_occurrence": f"2026-05-{1 + i % 28:02d}T08:00:00Z",
                    "lead_time_days": i % 7,
                },
            )
        )
    for i in range(5):
        await log.append(
            _envelope(
                "recurrence.completed",
                {
                    "recurrence_id": f"r{i:03d}",
                    "completed_at": "2026-04-18T10:00:00Z",
                },
            )
        )
    for i in range(5, 10):
        await log.append(
            _envelope(
                "recurrence.updated",
                {
                    "recurrence_id": f"r{i:03d}",
                    "updated_at": "2026-04-18T11:00:00Z",
                    "field_updates": {"trackable": True, "notes": f"note-{i}"},
                },
            )
        )

    # 40 calendar events: 30 added, 5 updated, 5 deleted (deletes of earlier
    # adds, so final count = 25).
    for i in range(30):
        await log.append(
            _envelope(
                "calendar.event_added",
                {
                    "source": "google",
                    "external_event_id": f"cal-{i:03d}",
                    "calendar_id": "cal-primary",
                    "summary": f"Event {i}",
                    "start": f"2026-04-{15 + i % 10:02d}T14:00:00Z",
                    "end": f"2026-04-{15 + i % 10:02d}T15:00:00Z",
                    "attendees": [{"party_id": f"p{i % 100:03d}"}],
                },
            )
        )
    for i in range(5):
        await log.append(
            _envelope(
                "calendar.event_updated",
                {
                    "calendar_event_id": "ignored",
                    "calendar_source": "google",
                    "external_uid": f"cal-{i:03d}",
                    "updated_at": "2026-04-15T10:00:00Z",
                    "field_updates": {"summary": f"Updated Event {i}"},
                },
            )
        )
    for i in range(25, 30):
        await log.append(
            _envelope(
                "calendar.event_deleted",
                {
                    "calendar_event_id": "ignored",
                    "calendar_source": "google",
                    "external_uid": f"cal-{i:03d}",
                    "deleted_at": "2026-04-15T11:00:00Z",
                },
            )
        )

    latest = await log.latest_event_id()
    assert latest is not None
    await bus.notify(latest)

    for sid in (
        "projection:parties",
        "projection:interactions",
        "projection:artifacts",
        "projection:commitments",
        "projection:tasks",
        "projection:recurrences",
        "projection:calendars",
    ):
        await _wait_idle(bus, sid, timeout=15.0)

    parties_tables = ["parties", "identifiers", "memberships", "relationships"]
    interactions_tables = [
        "interactions",
        "interaction_participants",
        "interaction_attachments",
    ]
    artifacts_tables = ["artifacts", "artifact_links"]
    commitments_tables = ["commitments"]
    tasks_tables = ["tasks"]
    recurrences_tables = ["recurrences"]
    calendars_tables = ["calendar_events", "availability_blocks"]

    pre_parties = _snapshot(runner.connection("parties"), parties_tables)
    pre_interactions = _snapshot(
        runner.connection("interactions"), interactions_tables
    )
    pre_artifacts = _snapshot(runner.connection("artifacts"), artifacts_tables)
    pre_commitments = _snapshot(runner.connection("commitments"), commitments_tables)
    pre_tasks = _snapshot(runner.connection("tasks"), tasks_tables)
    pre_recurrences = _snapshot(runner.connection("recurrences"), recurrences_tables)
    pre_calendars = _snapshot(runner.connection("calendars"), calendars_tables)

    await runner.rebuild("parties")
    await runner.rebuild("interactions")
    await runner.rebuild("artifacts")
    await runner.rebuild("commitments")
    await runner.rebuild("tasks")
    await runner.rebuild("recurrences")
    await runner.rebuild("calendars")

    post_parties = _snapshot(runner.connection("parties"), parties_tables)
    post_interactions = _snapshot(
        runner.connection("interactions"), interactions_tables
    )
    post_artifacts = _snapshot(runner.connection("artifacts"), artifacts_tables)
    post_commitments = _snapshot(runner.connection("commitments"), commitments_tables)
    post_tasks = _snapshot(runner.connection("tasks"), tasks_tables)
    post_recurrences = _snapshot(runner.connection("recurrences"), recurrences_tables)
    post_calendars = _snapshot(runner.connection("calendars"), calendars_tables)

    assert pre_parties == post_parties
    assert pre_interactions == post_interactions
    assert pre_artifacts == post_artifacts
    assert pre_commitments == post_commitments
    assert pre_tasks == post_tasks
    assert pre_recurrences == post_recurrences
    assert pre_calendars == post_calendars

    # Sanity: rebuilt state has non-zero rows.
    assert len(post_parties["parties"]) == 101
    assert len(post_interactions["interactions"]) == 180  # 150 msg + 30 sms
    assert len(post_artifacts["artifacts"]) == 20
    assert len(post_commitments["commitments"]) == 50
    # Tasks: 60 created; soft-deletes keep rows.
    assert len(post_tasks["tasks"]) == 60
    assert len(post_recurrences["recurrences"]) == 20
    # Calendar: 30 added - 5 hard-deleted = 25.
    assert len(post_calendars["calendar_events"]) == 25

    # Cross-DB referential-integrity audit (informational only per [§2.3]).
    # Open parties DB and check that commitments.owed_by_party,
    # tasks.assignee_party, tasks.recurring_id, calendar_events.owner_party
    # point at extant parties / recurrences. Orphans are allowed — this is
    # a visibility check for BUILD_LOG review.
    parties_conn = runner.connection("parties")
    party_ids = {
        row[0]
        for row in parties_conn.execute(
            "SELECT party_id FROM parties WHERE tenant_id = 'tenant-a'"
        )
    }
    recurrences_conn = runner.connection("recurrences")
    recurrence_ids = {
        row[0]
        for row in recurrences_conn.execute(
            "SELECT recurrence_id FROM recurrences WHERE tenant_id = 'tenant-a'"
        )
    }

    def _orphans_of(conn: Any, query: str, valid: set[str]) -> list[str]:
        missing: list[str] = []
        for (value,) in conn.execute(query):
            if value is None:
                continue
            if value not in valid:
                missing.append(value)
        return missing

    c_orphans = _orphans_of(
        runner.connection("commitments"),
        "SELECT owed_by_party FROM commitments WHERE tenant_id='tenant-a'",
        party_ids,
    )
    t_party_orphans = _orphans_of(
        runner.connection("tasks"),
        "SELECT assignee_party FROM tasks WHERE tenant_id='tenant-a'",
        party_ids,
    )
    t_rec_orphans = _orphans_of(
        runner.connection("tasks"),
        "SELECT recurring_id FROM tasks WHERE tenant_id='tenant-a'",
        recurrence_ids,
    )
    cal_orphans = _orphans_of(
        runner.connection("calendars"),
        "SELECT owner_party FROM calendar_events WHERE tenant_id='tenant-a'",
        party_ids,
    )
    _log.info(
        "cross-DB orphan audit: commitments.owed_by=%d, tasks.assignee=%d, "
        "tasks.recurring_id=%d, calendar.owner_party=%d",
        len(c_orphans),
        len(t_party_orphans),
        len(t_rec_orphans),
        len(cal_orphans),
    )
