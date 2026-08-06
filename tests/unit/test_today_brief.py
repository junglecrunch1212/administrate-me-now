"""Deterministic Daily Brief composition contracts."""

from __future__ import annotations

from datetime import datetime, timezone

from adminme.today import compose_daily_brief


AS_OF = datetime(2034, 3, 2, 15, 30, 45, tzinfo=timezone.utc)


def _compose() -> dict:
    return compose_daily_brief(
        member_id="member-a",
        timezone_name="America/New_York",
        as_of=AS_OF,
        what_now={
            "generated_by": "deterministic-ranker-v2",
            "horizons": {},
            "ranked": [
                {
                    "id": "task-1",
                    "kind": "task",
                    "title": "Put the document by the door",
                    "status": "next",
                    "due": "2034-03-02T16:00:00Z",
                    "micro_step": "Pick up only the document.",
                    "actions": ["complete", "defer", "block", "evidence"],
                    "last_event_id": "event-task",
                },
                {
                    "id": "list-a:item-a",
                    "kind": "list_item",
                    "title": "Pick up milk",
                    "status": "open",
                    "due": "2034-03-02T17:00:00Z",
                    "actions": ["complete_external", "evidence"],
                    "last_event_id": "event-list",
                },
                {
                    "id": "routine-1",
                    "kind": "recurrence",
                    "title": "Morning reset",
                    "status": "due",
                    "due": "2034-03-02T14:00:00Z",
                    "actions": ["complete", "evidence"],
                    "last_event_id": "event-routine",
                },
            ],
        },
        calendar_events=[
            {
                "calendar_event_id": "calendar-1",
                "calendar_source": "google_calendar",
                "external_uid": "external-1",
                "member_id": "member-a",
                "summary": "School meeting",
                "start_at": "2034-03-02T16:00:00Z",
                "end_at": "2034-03-02T17:00:00Z",
                "all_day": 0,
                "location": "Library",
                "last_event_id": "event-calendar",
            }
        ],
        interactions=[
            {
                "interaction_id": "message-1",
                "direction": "inbound",
                "subject": "Can you confirm?",
                "response_urgency": "today",
                "suggested_action": "Reply with a yes or no.",
                "last_event_id": "event-message",
            },
            {
                "interaction_id": "message-no-action",
                "direction": "inbound",
                "subject": "Newsletter",
                "response_urgency": "none",
                "last_event_id": "event-newsletter",
            },
            {
                "interaction_id": "message-auto-handled",
                "direction": "inbound",
                "subject": "Already handled",
                "response_urgency": "urgent",
                "auto_handled": 1,
                "last_event_id": "event-auto-handled",
            },
            {
                "interaction_id": "message-noise",
                "direction": "inbound",
                "subject": "Promotional noise",
                "response_urgency": "urgent",
                "classification": "noise",
                "last_event_id": "event-noise",
            },
        ],
        triage_items=[
            {
                "proposal_id": "proposal-1",
                "title": "Is this a household commitment?",
                "target_kind": "commitment",
                "source_event_id": "event-source",
                "last_event_id": "event-proposal",
            }
        ],
    )


def test_daily_brief_is_repeatable_local_and_semantically_distinct() -> None:
    first = _compose()
    assert first == _compose()
    assert first["contract"] == "deterministic-daily-brief-v1"
    assert first["date_iso"] == "2034-03-02"
    assert first["date_label"] == "Thursday, March 2"
    assert first["timezone"] == "America/New_York"
    assert first["as_of"] == "2034-03-02T15:30:00+00:00"
    assert first["stats"] == {
        "events": 1,
        "moves": 3,
        "messages": 1,
        "questions": 1,
        "overdue": 1,
    }

    assert [item["kind"] for item in first["plan"]] == [
        "task",
        "list_item",
        "recurrence",
    ]
    by_kind = {item["kind"]: item for item in first["carousel"]["items"]}
    assert by_kind["task"]["actions"][0] == "complete"
    assert by_kind["list_item"]["actions"][0] == "complete_external"
    assert by_kind["recurrence"]["actions"][0] == "complete"
    assert by_kind["calendar_event"]["actions"] == ["evidence"]
    assert by_kind["clarifying_question"]["actions"][0] == "open_triage"
    assert by_kind["message"]["actions"][0] == "open_chat"
    assert first["carousel"]["later_is_view_only"] is True
    assert first["carousel"]["items"][0]["kind"] == "calendar_event"
    assert first["carousel"]["recommended_item"] == {
        "kind": "calendar_event",
        "id": "calendar-1",
    }


def test_elapsed_calendar_event_requests_member_confirmed_outcome() -> None:
    brief = compose_daily_brief(
        member_id="member-a",
        timezone_name="Etc/UTC",
        as_of=AS_OF,
        what_now={"ranked": [], "horizons": {}},
        calendar_events=[
            {
                "calendar_event_id": "calendar-past",
                "calendar_source": "apple_calendar",
                "external_uid": "external-past",
                "member_id": "member-a",
                "summary": "Appointment",
                "start_at": "2034-03-02T13:00:00Z",
                "end_at": "2034-03-02T14:00:00Z",
                "all_day": 0,
                "last_event_id": "event-calendar-past",
            }
        ],
        interactions=[],
        triage_items=[],
    )
    event = brief["schedule"][0]
    assert event["status"] == "awaiting_outcome"
    assert event["actions"] == ["attended", "missed", "evidence"]
