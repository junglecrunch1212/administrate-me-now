"""Contracts for the member's primary bidirectional Apple/Google surfaces."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from adminme.events.schemas.external_sync import ExternalWriteRequestedV1
from adminme.events.schemas.ingest import CalendarEventV2
from adminme.events.schemas.knowledge import MemberKnowledgeNoteV2
from adminme.events.schemas.lists import ListItemV2
from adminme.lib.eventkit_recurrence import eventkit_rule_to_rrule, parse_rrule
from adminme.projections.calendars.handlers import apply_event as apply_calendar
from adminme.projections.lists.handlers import apply_event as apply_list
from adminme.projections.member_knowledge.handlers import apply_event as apply_note
from adminme.projections.tasks.handlers import apply_event as apply_task


ROOT = Path(__file__).resolve().parents[2]


def _conn(projection: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        (ROOT / "adminme" / "projections" / projection / "schema.sql").read_text(
            encoding="utf-8"
        )
    )
    return conn


def _envelope(event_type: str, payload: dict, *, version: int = 2) -> dict:
    return {
        "event_id": "01JPRIMARYSURFACE000000000001",
        "tenant_id": "tenant-a",
        "type": event_type,
        "schema_version": version,
        "occurred_at": "2026-08-05T12:00:00+00:00",
        "source_adapter": "test-provider",
        "source_account_id": "principal-a@example.invalid",
        "owner_scope": "private:member-a",
        "visibility_scope": "private:member-a",
        "sensitivity": "normal",
        "payload": payload,
    }


def _calendar_payload() -> dict:
    return {
        "member_id": "member-a",
        "provider": "google_calendar",
        "provider_account_id": "principal-a@example.invalid",
        "calendar_external_id": "primary",
        "calendar_name": "Personal",
        "external_event_id": "event-1",
        "ical_uid": "uid-1",
        "provider_revision": '"etag-1"',
        "status": "confirmed",
        "event_type": "default",
        "summary": "Dentist",
        "description": "Bring card",
        "location": "Clinic",
        "structured_location": None,
        "start": "2026-08-06T14:00:00-04:00",
        "end": "2026-08-06T15:00:00-04:00",
        "start_time_zone": "America/New_York",
        "end_time_zone": "America/New_York",
        "all_day": False,
        "availability": "busy",
        "visibility": "private",
        "organizer": {"email": "principal-a@example.invalid", "self": True},
        "attendees": [{"email": "other@example.com", "response_status": "accepted"}],
        "alarms": [{"method": "alert", "relative_offset_seconds": -1800}],
        "use_default_alarms": False,
        "recurrence": {"lines": ["RRULE:FREQ=MONTHLY;BYDAY=1MO;COUNT=5"]},
        "sequence": 2,
        "conference": {"entryPoints": []},
        "attachments": [],
        "color_id": "7",
        "source_url": "adminme://task/task-1",
        "html_link": "https://example.test/e",
        "linked_item_kind": "task",
        "linked_item_id": "task-1",
        "completion_policy": "on_elapsed",
        "created_at": "2026-08-01T12:00:00Z",
        "updated_at": "2026-08-05T12:00:00Z",
    }


def test_rich_calendar_snapshot_validates_and_replays_losslessly() -> None:
    payload = CalendarEventV2.model_validate(_calendar_payload()).model_dump(
        by_alias=True, mode="json"
    )
    conn = _conn("calendars")
    apply_calendar(_envelope("calendar.event_added", payload), conn)
    row = conn.execute("SELECT * FROM calendar_events").fetchone()
    assert row["provider_account_id"] == "principal-a@example.invalid"
    assert json.loads(row["recurrence_json"])["lines"][0].startswith("RRULE:")
    assert json.loads(row["alarms_json"])[0]["relative_offset_seconds"] == -1800
    assert row["linked_item_id"] == "task-1"


def test_reminder_geofence_recurrence_and_completion_link_replay() -> None:
    payload = ListItemV2.model_validate(
        {
            "member_id": "member-a",
            "provider": "apple_reminders",
            "provider_account_id": "icloud-member-a",
            "list_external_id": "list-home",
            "item_external_id": "reminder-1",
            "list_name": "Errands",
            "title": "Pick up medicine",
            "priority": 1,
            "completed": False,
            "due": {
                "year": 2026,
                "month": 8,
                "day": 6,
                "hour": 17,
                "minute": 30,
                "time_zone": "America/New_York",
            },
            "alarms": [
                {
                    "kind": "location",
                    "location": {
                        "title": "Pharmacy",
                        "latitude": 40.1,
                        "longitude": -74.2,
                        "radius_meters": 150,
                    },
                    "proximity": "enter",
                }
            ],
            "recurrence_rules": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TH"],
            "linked_item_kind": "task",
            "linked_item_id": "task-1",
            "completion_policy": "mirror",
            "last_modified_at": "2026-08-05T12:00:00Z",
            "sync_cycle_id": "sync-1",
        }
    ).model_dump(mode="json")
    conn = _conn("lists")
    apply_list(_envelope("list_item.added", payload), conn)
    row = conn.execute("SELECT * FROM list_items").fetchone()
    assert json.loads(row["alarms_json"])[0]["proximity"] == "enter"
    assert json.loads(row["recurrence_json"]) == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TH"]
    assert row["completion_policy"] == "mirror"


def test_note_html_folder_and_capability_warnings_replay() -> None:
    payload = MemberKnowledgeNoteV2.model_validate(
        {
            "knowledge_id": "knowledge-1",
            "member_id": "member-a",
            "title": "Packing",
            "body": "<div>Passport</div>",
            "body_format": "html",
            "source_kind": "apple_notes",
            "source_external_id": "note-1",
            "account_external_id": "icloud-member-a",
            "folder_external_id": "folder-travel",
            "folder_path": "Travel",
            "content_sha256": "a" * 64,
            "captured_at": "2026-08-05T12:00:00Z",
            "provider_modified_at": "2026-08-05T12:00:00Z",
            "locked": False,
            "capability_warnings": ["attachments_not_scriptable"],
        }
    ).model_dump(mode="json")
    conn = _conn("member_knowledge")
    apply_note(_envelope("member_knowledge.note_added", payload), conn)
    row = conn.execute("SELECT * FROM member_knowledge").fetchone()
    assert row["body_format"] == "html"
    assert row["folder_path"] == "Travel"
    assert json.loads(row["capability_warnings_json"]) == ["attachments_not_scriptable"]


def test_linked_reminder_completion_closes_task_only_under_mirror_policy() -> None:
    conn = _conn("tasks")
    task = _envelope(
        "task.created",
        {"task_id": "task-1", "title": "Medicine", "owner_member_id": "member-a"},
        version=1,
    )
    task["event_id"] = "01JPRIMARYSURFACE000000000000"
    apply_task(task, conn)
    reminder = ListItemV2.model_validate(
        {
            "member_id": "member-a",
            "provider": "apple_reminders",
            "provider_account_id": "icloud-member-a",
            "list_external_id": "list-home",
            "item_external_id": "rem-1",
            "list_name": "Errands",
            "title": "Medicine",
            "priority": 1,
            "completed": True,
            "completion_at": "2026-08-05T12:00:00Z",
            "linked_item_kind": "task",
            "linked_item_id": "task-1",
            "completion_policy": "mirror",
            "last_modified_at": "2026-08-05T12:00:00Z",
            "sync_cycle_id": "sync-1",
        }
    ).model_dump(mode="json")
    apply_task(_envelope("list_item.completed", reminder), conn)
    row = conn.execute("SELECT * FROM tasks WHERE task_id='task-1'").fetchone()
    assert row["status"] == "done"
    assert row["completed_by"] == "member-a"


def test_external_write_command_keeps_optimistic_revision() -> None:
    command = ExternalWriteRequestedV1.model_validate(
        {
            "command_id": "request-123",
            "member_id": "member-a",
            "provider": "google_calendar",
            "provider_account_id": "principal-a@example.invalid",
            "operation": "update_event",
            "target_external_id": "event-1",
            "expected_provider_revision": '"etag-1"',
            "payload": {"summary": "New title"},
            "requested_at": datetime.now(timezone.utc),
        }
    )
    assert command.expected_provider_revision == '"etag-1"'


def test_rrule_parser_and_serializer_preserve_advanced_selectors() -> None:
    parsed = parse_rrule("RRULE:FREQ=YEARLY;INTERVAL=2;BYMONTH=3;BYDAY=-1FR;COUNT=4")
    assert parsed["weekdays"] == [(6, -1)]
    assert parsed["months"] == [3]

    class End:
        def occurrenceCount(self) -> int:
            return 4

        def endDate(self):
            return None

    class Day:
        def dayOfTheWeek(self) -> int:
            return 6

        def weekNumber(self) -> int:
            return -1

    class Rule:
        def frequency(self) -> int:
            return 3

        def interval(self) -> int:
            return 2

        def daysOfTheWeek(self):
            return [Day()]

        def monthsOfTheYear(self):
            return [3]

        def daysOfTheMonth(self):
            return []

        def weeksOfTheYear(self):
            return []

        def daysOfTheYear(self):
            return []

        def setPositions(self):
            return []

        def recurrenceEnd(self):
            return End()

    assert (
        eventkit_rule_to_rrule(Rule())
        == "RRULE:FREQ=YEARLY;INTERVAL=2;BYDAY=-1FR;BYMONTH=3;COUNT=4"
    )
