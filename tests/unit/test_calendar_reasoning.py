"""Deterministic calendar-policy composition and schema contracts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.calendar import compose_calendar_cockpit
from adminme.events.schemas.calendar_policy import (
    CalendarRuleConfiguredV1,
    CalendarSchedulePolicyConfiguredV1,
)


def _source(calendar_id: str, name: str, *, writable: bool = True) -> dict[str, object]:
    return {
        "member_id": "member-a",
        "provider": "google_calendar",
        "provider_account_id": "account-a",
        "calendar_external_id": calendar_id,
        "calendar_name": name,
        "access_role": "owner" if writable else "reader",
        "write_events": 1 if writable else 0,
        "read_details": 1,
        "read_free_busy": 1,
    }


def _rule(calendar_id: str, effect: str, purpose: str) -> dict[str, object]:
    return {
        "rule_id": f"calendar-rule-{calendar_id}",
        "subject_member_id": "member-a",
        "source_member_id": "member-a",
        "provider": "google_calendar",
        "provider_account_id": "account-a",
        "calendar_external_id": calendar_id,
        "purpose": purpose,
        "effect": effect,
        "tentative_effect": "soft_hold",
        "caregiving_load": "ignore",
        "caregiving_summary_values_json": "[]",
        "buffer_kind": "none",
        "write_target": 1 if calendar_id == "calendar-personal" else 0,
        "destination_kind": "personal" if calendar_id == "calendar-personal" else None,
        "enabled": 1,
        "write_events": 1,
    }


def _event(
    event_id: str,
    calendar_id: str,
    start: str,
    end: str,
    *,
    ical_uid: str | None = None,
    availability: str = "busy",
) -> dict[str, object]:
    return {
        "calendar_event_id": event_id,
        "member_id": "member-a",
        "calendar_source": "google_calendar",
        "provider_account_id": "account-a",
        "calendar_external_id": calendar_id,
        "calendar_name": calendar_id,
        "external_uid": event_id,
        "ical_uid": ical_uid,
        "summary": "Reviewed event",
        "start_at": start,
        "end_at": end,
        "all_day": 0,
        "status": "confirmed",
        "availability": availability,
    }


def test_composer_groups_duplicates_and_applies_capacity_rules() -> None:
    cockpit = compose_calendar_cockpit(
        member_id="member-a",
        timezone_name="America/New_York",
        as_of=datetime(2026, 8, 6, 14, tzinfo=timezone.utc),
        range_start=datetime(2026, 8, 6, 4, tzinfo=timezone.utc),
        range_end=datetime(2026, 8, 7, 4, tzinfo=timezone.utc),
        sources=[
            _source("calendar-personal", "Personal"),
            _source("calendar-shared", "Shared", writable=False),
        ],
        rules=[
            _rule("calendar-personal", "hard_block", "personal"),
            _rule("calendar-shared", "context", "household"),
        ],
        events=[
            _event(
                "event-personal",
                "calendar-personal",
                "2026-08-06T15:00:00Z",
                "2026-08-06T16:00:00Z",
                ical_uid="same-occurrence",
            ),
            _event(
                "event-shared-copy",
                "calendar-shared",
                "2026-08-06T15:00:00Z",
                "2026-08-06T16:00:00Z",
                ical_uid="same-occurrence",
            ),
            _event(
                "event-context",
                "calendar-shared",
                "2026-08-06T16:00:00Z",
                "2026-08-06T17:00:00Z",
            ),
        ],
        schedule_policy={
            "windows": [],
            "buffer_minutes": {},
            "accepted_chores_required": True,
            "one_confirm_write": True,
        },
    )

    assert cockpit["counts"]["provider_copies"] == 3
    assert cockpit["counts"]["effective_events"] == 2
    assert cockpit["counts"]["duplicates_collapsed"] == 1
    duplicate = next(item for item in cockpit["events"] if item["duplicate_collapsed"])
    assert duplicate["effect"] == "hard_block"
    assert duplicate["copy_count"] == 2
    assert {route["calendar_external_id"] for route in duplicate["source_routes"]} == {
        "calendar-personal",
        "calendar-shared",
    }
    assert cockpit["write_destinations"][0]["destination_kind"] == "personal"
    assert cockpit["provider_truth"]["model_decisions"] is False


def test_provider_free_event_does_not_become_a_hard_block() -> None:
    cockpit = compose_calendar_cockpit(
        member_id="member-a",
        timezone_name="UTC",
        as_of=datetime(2026, 8, 6, tzinfo=timezone.utc),
        range_start=datetime(2026, 8, 6, tzinfo=timezone.utc),
        range_end=datetime(2026, 8, 7, tzinfo=timezone.utc),
        sources=[_source("calendar-personal", "Personal")],
        rules=[_rule("calendar-personal", "hard_block", "personal")],
        events=[
            _event(
                "event-free",
                "calendar-personal",
                "2026-08-06T15:00:00Z",
                "2026-08-06T16:00:00Z",
                availability="free",
            )
        ],
        schedule_policy=None,
    )
    assert cockpit["events"][0]["effect"] == "context"
    assert cockpit["counts"]["hard_block"] == 0


def test_policy_schemas_reject_ambiguous_write_and_unknown_buffer() -> None:
    with pytest.raises(ValidationError, match="write_target requires destination_kind"):
        CalendarRuleConfiguredV1.model_validate(
            {
                "rule_id": "calendar-rule-personal",
                "subject_member_id": "member-a",
                "source_member_id": "member-a",
                "provider": "google_calendar",
                "provider_account_id": "account-a",
                "calendar_external_id": "calendar-personal",
                "purpose": "personal",
                "effect": "hard_block",
                "configured_at": "2026-08-06T00:00:00Z",
                "write_target": True,
                "destination_kind": None,
            }
        )
    with pytest.raises(ValidationError, match="unknown calendar buffer kind"):
        CalendarSchedulePolicyConfiguredV1.model_validate(
            {
                "member_id": "member-a",
                "timezone": "UTC",
                "windows": [],
                "buffer_minutes": {"invented_before": 10},
                "configured_at": "2026-08-06T00:00:00Z",
            }
        )
