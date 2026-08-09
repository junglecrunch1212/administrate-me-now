"""Gate 6 deterministic multi-factor what-now ranking."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from adminme.extensions import ExtensionCatalog
from adminme.what_now import rank_what_now


REPO_ROOT = Path(__file__).resolve().parents[2]
AS_OF = datetime(2034, 3, 2, 12, tzinfo=timezone.utc)


def test_ranker_is_deterministic_explained_and_feedback_sensitive(
    tmp_path: Path,
) -> None:
    profile = (
        ExtensionCatalog(REPO_ROOT, tmp_path).get("profile", "adhd_executive").data
    )
    task = {
        "task_id": "task-small",
        "title": "Put document by the door",
        "status": "next",
        "due_date": "2034-03-02T12:30:00Z",
        "energy": "low",
        "effort": "small",
        "domain": "home",
        "depends_on_json": "[]",
        "waiting_on": None,
    }
    blocked_task = {
        **task,
        "task_id": "task-waiting",
        "title": "Waiting task",
        "status": "waiting_on",
        "depends_on_json": '["task-external"]',
    }
    commitment = {
        "commitment_id": "commitment-1",
        "description": "Reply to Pat",
        "status": "pending",
        "due_at": "2034-03-03T12:00:00Z",
        "kind": "reply",
        "owed_to_party": "party-pat",
    }
    first = rank_what_now(
        [task, blocked_task],
        [commitment],
        member_id="member-a",
        profile=profile,
        as_of=AS_OF,
    )
    second = rank_what_now(
        [task, blocked_task],
        [commitment],
        member_id="member-a",
        profile=profile,
        as_of=AS_OF,
    )
    assert first == second
    assert first["planning_horizons"].keys() == {
        "now",
        "hour",
        "daypart",
        "day",
        "week",
    }
    explanation = first["horizons"]["right_now"]["explanation"]
    assert set(explanation) >= {
        "available_time",
        "temporal_horizon",
        "priority_urgency",
        "energy",
        "effort_minutes",
        "context_location",
        "dependencies_waiting",
        "relationship_consequence",
        "profile_behavior",
        "quiet_hours",
        "nudge_state",
        "recent_outcomes",
    }

    # Blockage/deferral feedback changes the next calculation; completion is
    # represented by the row leaving the authorized open set.
    after_feedback = rank_what_now(
        [task],
        [{**commitment, "status": "deferred"}],
        member_id="member-a",
        profile=profile,
        as_of=AS_OF,
    )
    assert (
        after_feedback["horizons"]["right_now"]["id"]
        != first["horizons"]["right_now"]["id"]
    )


def test_ranker_preserves_list_and_recurrence_semantics() -> None:
    ranked = rank_what_now(
        [],
        [],
        member_id="member-a",
        profile=None,
        as_of=AS_OF,
        list_items=[
            {
                "list_external_id": "groceries",
                "item_external_id": "milk",
                "title": "Pick up milk",
                "due_at": "2034-03-02T12:15:00Z",
                "priority": 1,
                "provider": "apple_reminders",
                "provider_account_id": "icloud-a",
                "provider_revision": "rev-1",
                "list_name": "Errands",
                "last_event_id": "event-list",
            }
        ],
        recurrences=[
            {
                "recurrence_id": "rec-medicine",
                "kind": "take_medicine",
                "next_occurrence": "2034-03-02T12:10:00Z",
                "trackable": 1,
                "notes": "Put the dose beside a glass of water.",
                "last_event_id": "event-recurrence",
            },
            {
                "recurrence_id": "rec-non-trackable",
                "kind": "birthday",
                "next_occurrence": "2034-03-02T12:05:00Z",
                "trackable": 0,
                "last_event_id": "event-hidden",
            },
        ],
        routines=[
            {
                "routine_id": "routine-leaving",
                "title": "Leave the house",
                "occurrence_date": "2034-03-02",
                "window_start_at": "2034-03-02T11:45:00Z",
                "window_end_at": "2034-03-02T12:30:00Z",
                "time_label": "11:45 AM–12:30 PM",
                "status": "in_progress",
                "current_step": {
                    "step_id": "keys",
                    "position": 0,
                    "title": "Pick up keys",
                    "micro_step": "Touch the keys.",
                },
                "steps": [],
                "completed_steps": 0,
                "skipped_steps": 0,
                "total_steps": 2,
                "last_event_id": "event-routine",
            }
        ],
    )

    by_kind = {item["kind"]: item for item in ranked["ranked"]}
    assert set(by_kind) == {"list_item", "recurrence", "routine"}
    assert by_kind["list_item"]["actions"] == ["complete_external", "evidence"]
    assert by_kind["recurrence"]["actions"] == ["complete", "pause", "evidence"]
    assert by_kind["recurrence"]["id"] == "rec-medicine"
    assert by_kind["list_item"]["id"] == "groceries:milk"
    assert by_kind["routine"]["id"] == "routine-leaving"
    assert by_kind["routine"]["actions"] == [
        "complete_step",
        "skip_step",
        "pause",
        "evidence",
    ]
    assert by_kind["routine"]["current_step"]["step_id"] == "keys"


def test_effective_google_item_can_complete_and_possible_copies_rank_once() -> None:
    copy = {
        "contract": "possible-reminder-copy-v1",
        "group_id": "possible-copy:fixture",
        "count": 2,
        "provider_items_preserved": ["google:one", "google:two"],
    }
    ranked = rank_what_now(
        [],
        [],
        member_id="member-a",
        profile=None,
        as_of=AS_OF,
        list_items=[
            {
                "id": "google:one",
                "list_external_id": "list-a",
                "item_external_id": "one",
                "title": "Submit form",
                "provider": "google_tasks",
                "provider_account_id": "google-a",
                "can_complete": True,
                "possible_copy": copy,
            },
            {
                "id": "google:two",
                "list_external_id": "list-b",
                "item_external_id": "two",
                "title": "Submit form",
                "provider": "google_tasks",
                "provider_account_id": "google-a",
                "can_complete": True,
                "possible_copy": copy,
            },
            {
                "id": "google:single",
                "list_external_id": "list-a",
                "item_external_id": "single",
                "title": "Single item",
                "provider": "google_tasks",
                "provider_account_id": "google-a",
                "can_complete": True,
            },
        ],
    )
    assert [item["kind"] for item in ranked["ranked"]].count("reminder_group") == 1
    assert len(ranked["ranked"]) == 2
    single = next(item for item in ranked["ranked"] if item["id"] == "google:single")
    assert single["actions"] == ["complete_external", "evidence"]
