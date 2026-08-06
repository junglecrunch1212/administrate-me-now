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
    )

    by_kind = {item["kind"]: item for item in ranked["ranked"]}
    assert set(by_kind) == {"list_item", "recurrence"}
    assert by_kind["list_item"]["actions"] == ["complete_external", "evidence"]
    assert by_kind["recurrence"]["actions"] == ["complete", "evidence"]
    assert by_kind["recurrence"]["id"] == "rec-medicine"
    assert by_kind["list_item"]["id"] == "groceries:milk"
