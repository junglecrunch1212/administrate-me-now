"""Deterministic Daily Brief composition contracts."""

from __future__ import annotations

from datetime import datetime, timezone

from adminme.today import add_today_decision_context, compose_daily_brief


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
                {
                    "id": "routine-ordered-1",
                    "kind": "routine",
                    "title": "Leave the house",
                    "status": "in_progress",
                    "due": "2034-03-02T15:00:00Z",
                    "time_label": "10:00 AM–11:00 AM",
                    "current_step": {
                        "step_id": "shoes",
                        "position": 0,
                        "title": "Put on shoes",
                    },
                    "actions": ["complete_step", "skip_step", "evidence"],
                    "last_event_id": "event-routine-ordered",
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
    assert first["contract"] == "deterministic-daily-brief-v2"
    assert first["date_iso"] == "2034-03-02"
    assert first["date_label"] == "Thursday, March 2"
    assert first["timezone"] == "America/New_York"
    assert first["as_of"] == "2034-03-02T15:30:00+00:00"
    assert first["stats"] == {
        "events": 1,
        "timed_events": 1,
        "day_context": 0,
        "moves": 4,
        "messages": 1,
        "questions": 1,
        "overdue": 1,
    }

    assert [item["kind"] for item in first["plan"]] == [
        "task",
        "list_item",
        "recurrence",
        "routine",
    ]
    by_kind = {item["kind"]: item for item in first["carousel"]["items"]}
    assert by_kind["task"]["actions"][0] == "complete"
    assert by_kind["list_item"]["actions"][0] == "complete_external"
    assert by_kind["recurrence"]["actions"][0] == "complete"
    assert by_kind["routine"]["actions"][:2] == ["complete_step", "skip_step"]
    assert by_kind["calendar_event"]["actions"] == ["evidence"]
    assert by_kind["clarifying_question"]["actions"][0] == "open_triage"
    assert by_kind["clarifying_question"]["surface_link"]["href"] == (
        "/member/triage?item=proposal-1&matter=proposal-1&view=all_candidates"
    )
    assert by_kind["message"]["actions"][0] == "open_comms"
    assert by_kind["message"]["surface_link"]["surface"] == "comms"
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


def test_all_day_context_is_not_the_next_appointment_or_action_card() -> None:
    brief = compose_daily_brief(
        member_id="member-a",
        timezone_name="Etc/UTC",
        as_of=AS_OF,
        what_now={"ranked": [], "horizons": {}},
        calendar_events=[
            {
                "calendar_event_id": "context-free",
                "summary": "Free",
                "start_at": "2034-03-02T00:00:00Z",
                "end_at": "2034-03-03T00:00:00Z",
                "all_day": 1,
                "effect": "context",
                "last_event_id": "event-context",
            },
            {
                "calendar_event_id": "timed-appointment",
                "summary": "Appointment",
                "start_at": "2034-03-02T16:00:00Z",
                "end_at": "2034-03-02T17:00:00Z",
                "all_day": 0,
                "effect": "hard_block",
                "last_event_id": "event-timed",
            },
        ],
        interactions=[],
        triage_items=[],
    )
    assert brief["next_event"]["id"] == "timed-appointment"
    assert [item["id"] for item in brief["day_context"]] == ["context-free"]
    assert "context-free" not in {item["id"] for item in brief["carousel"]["items"]}


def test_today_and_comms_share_triage_link_as_an_attention_signal() -> None:
    brief = compose_daily_brief(
        member_id="member-a",
        timezone_name="Etc/UTC",
        as_of=AS_OF,
        what_now={"ranked": [], "horizons": {}},
        calendar_events=[],
        interactions=[
            {
                "interaction_id": "message-triage",
                "direction": "inbound",
                "subject": "An ordinary message with a derived candidate",
                "response_urgency": "none",
                "unread": 0,
                "raw_event_ids": '["source-for-triage"]',
                "last_event_id": "event-message",
            }
        ],
        triage_items=[
            {
                "proposal_id": "proposal-linked",
                "title": "Review the derived candidate",
                "target_kind": "task",
                "source_event_id": "source-for-triage",
                "last_event_id": "event-proposal",
            }
        ],
    )
    message = brief["messages"][0]
    assert message["triage_count"] == 1
    assert message["attention"]["rule_id"] == "comms.focus-v2"
    assert message["attention"]["signals"]["triage_count"] == 1


def test_today_decision_context_names_question_source_choices_and_authority() -> None:
    brief = compose_daily_brief(
        member_id="member-a",
        timezone_name="Etc/UTC",
        as_of=AS_OF,
        what_now={"ranked": [], "horizons": {}},
        calendar_events=[],
        interactions=[],
        triage_items=[
            {
                "proposal_id": "proposal-exact",
                "context_id": "matter-exact",
                "title": "Send the enrollment form",
                "target_kind": "task",
                "queue_lane": "time_bound",
                "source_event_id": "source-exact",
                "last_event_id": "proposal-event",
                "candidate": {"due": "2034-03-03"},
                "interpretation": {
                    "context_source_event_ids": ["source-exact"],
                },
                "eligibility_rule_ids": ["candidate_policy_v1:explicit_action"],
            }
        ],
    )
    question = brief["questions"][0]
    question["source_context"] = {
        "state": "available",
        "channel": "gmail",
        "sender": "School office",
        "subject": "Enrollment form",
        "occurred_at": "2034-03-02T14:00:00Z",
        "meaning": {"summary": "The school asked for the signed form."},
        "evidence": {"quote": "Please send the signed enrollment form."},
        "interpretation": {
            "case_summary": "The school requested the signed enrollment form.",
            "why_member": "The request is addressed to the member and due tomorrow.",
            "next_step": "Check whether the form is already signed.",
            "unresolved_questions": ["Has the form already been signed?"],
        },
    }
    add_today_decision_context({"brief": brief})

    value = question["decision_context"]
    assert value["contract"] == "member-decision-context-v1"
    assert value["decision_maker"] == "member"
    assert value["candidate_id"] == "proposal-exact"
    assert value["matter_id"] == "matter-exact"
    assert value["decision_prompt"] == "Has the form already been signed?"
    assert value["what_happened"] == (
        "The school requested the signed enrollment form."
    )
    assert value["source"]["label"] == "gmail · School office · Enrollment form"
    assert [choice["id"] for choice in value["choices"]] == [
        "confirm",
        "correct",
        "dismiss",
    ]
    assert value["interpretation_disclosure"] == "bounded_language_review_aid"
    assert question["title"] == "Has the form already been signed?"
    carousel_question = next(
        item
        for item in brief["carousel"]["items"]
        if item["candidate_id"] == "proposal-exact"
    )
    assert carousel_question["decision_context"] == value
    assert carousel_question["source_context"] == question["source_context"]
    assert carousel_question["title"] == question["title"]
    assert carousel_question["context"] == question["context"]
    assert carousel_question["micro_step"] == question["micro_step"]
