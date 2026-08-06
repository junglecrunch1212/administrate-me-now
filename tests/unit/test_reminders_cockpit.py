"""Deterministic Reminders cockpit and context-cue behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from adminme.products.member.main import (
    ReminderCommandRequest,
    _provider_reminder_payload,
)
from adminme.reminders import compose_reminders_cockpit


NOW = datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc)


def _item(item_id: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "provider": "google_tasks",
        "provider_account_id": "google-a",
        "list_external_id": "list-a",
        "item_external_id": item_id,
        "list_name": "Personal",
        "title": item_id,
        "completed": 0,
        "priority": 0,
        "flagged": None,
        "due_at": None,
        "alarms_json": "[]",
        "recurrence_json": "[]",
        "source_links_json": "[]",
        "provider_data_json": "{}",
        "provider_position": None,
        "read_only_reason": None,
        "linked_item_kind": None,
    }
    row.update(overrides)
    return row


def _compose(
    items: list[dict[str, object]],
    *,
    cues: list[dict[str, object]] | None = None,
    signals: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return compose_reminders_cockpit(
        member_id="member-a",
        timezone_name="America/New_York",
        items=items,
        lists=[],
        accounts=[
            {
                "provider": "google_tasks",
                "provider_account_id": "google-a",
                "label": "Google Tasks",
                "operations": ["complete_task", "update_task"],
            }
        ],
        cues=cues or [],
        signals=signals or [],
        now=NOW,
    )


def test_fixed_lenses_preserve_distinct_list_item_truth() -> None:
    result = _compose(
        [
            _item("today", due_at="2026-08-06T00:00:00"),
            _item("later", due_at="2026-08-09T00:00:00"),
            _item(
                "place",
                alarms_json='[{"kind":"location","proximity":"enter"}]',
            ),
            _item("anytime"),
            _item("done", completed=1),
        ]
    )
    items = {item["item_external_id"]: item for item in result["items"]}  # type: ignore[index]
    assert {key: item["bucket"] for key, item in items.items()} == {
        "today": "now",
        "later": "scheduled",
        "place": "places",
        "anytime": "anytime",
        "done": "completed",
    }
    assert all(item["linked_item_kind"] is None for item in items.values())
    assert result["contract"] == {  # type: ignore[index]
        "provider_is_source_of_truth": True,
        "list_items_are_not_tasks": True,
        "model_ranking": False,
        "cue_evaluation": "typed operator against latest unexpired scoped fact",
    }


def test_fresh_typed_context_match_activates_now_with_explanation() -> None:
    cue = {
        "cue_id": "cue-garage",
        "list_external_id": "list-a",
        "item_external_id": "bins",
        "signal_key": "presence:garage",
        "source": "home_assistant",
        "kind": "presence",
        "operator": "equals",
        "expected_state": "present",
        "expected_number": None,
        "enabled": 1,
    }
    signal = {
        "signal_key": "presence:garage",
        "state": "present",
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
    }
    item = _compose([_item("bins")], cues=[cue], signals=[signal])["items"][0]  # type: ignore[index]
    assert item["bucket"] == "now"
    assert item["why"] == ["Presence cue matched"]
    assert item["cues"][0]["matched"] is True


def test_expired_context_fact_cannot_activate_a_reminder() -> None:
    cue = {
        "cue_id": "cue-rain",
        "list_external_id": "list-a",
        "item_external_id": "umbrella",
        "signal_key": "weather:rain",
        "source": "weather",
        "kind": "weather",
        "operator": "above",
        "expected_state": None,
        "expected_number": 0.5,
        "enabled": 1,
    }
    signal = {
        "signal_key": "weather:rain",
        "state": "rain",
        "numeric_value": 0.8,
        "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
    }
    item = _compose([_item("umbrella")], cues=[cue], signals=[signal])["items"][0]  # type: ignore[index]
    assert item["bucket"] == "anytime"
    assert item["cues"][0]["matched"] is False


def test_read_only_assignment_disables_provider_mutation() -> None:
    item = _compose(
        [_item("assigned", read_only_reason="Provider assignment restriction")]
    )["items"][0]  # type: ignore[index]
    assert item["can_complete"] is False
    assert item["can_edit"] is False


def test_possible_copies_are_labeled_but_never_merged() -> None:
    result = _compose(
        [
            _item(
                "first",
                title="Submit form",
                list_external_id="list-a",
                list_name="Personal",
            ),
            _item(
                "second",
                title="  submit   FORM ",
                list_external_id="list-b",
                list_name="Shared",
            ),
        ]
    )
    assert len(result["items"]) == 2
    groups = {item["possible_copy"]["group_id"] for item in result["items"]}
    assert len(groups) == 1
    assert all(
        item["possible_copy"]["member_review_required"] for item in result["items"]
    )
    assert all(
        len(item["possible_copy"]["provider_items_preserved"]) == 2
        for item in result["items"]
    )


def _command(
    *,
    provider: str,
    operation: str,
    payload: dict[str, object],
) -> ReminderCommandRequest:
    return ReminderCommandRequest.model_validate(
        {
            "provider": provider,
            "provider_account_id": "account-a",
            "operation": operation,
            "list_external_id": "list-a",
            "payload": payload,
            "request_id": "request-reminders-1",
            "openclaw_approved": True,
        }
    )


def test_apple_date_only_due_becomes_floating_eventkit_components() -> None:
    payload, _ = _provider_reminder_payload(
        _command(
            provider="apple_reminders",
            operation="create_reminder",
            payload={"title": "Pack lunch", "due": "2026-08-07"},
        )
    )
    assert payload["due"] == {
        "year": 2026,
        "month": 8,
        "day": 7,
        "all_day": True,
        "floating": True,
    }


def test_provider_list_fields_are_exact_and_apple_name_is_native() -> None:
    apple, _ = _provider_reminder_payload(
        _command(
            provider="apple_reminders",
            operation="create_list",
            payload={"name": "Errands", "color": "#8b5cf6"},
        )
    )
    assert apple == {"list_name": "Errands", "color": "#8b5cf6"}

    with pytest.raises(HTTPException, match="only expose a title"):
        _provider_reminder_payload(
            _command(
                provider="google_tasks",
                operation="create_list",
                payload={"name": "Errands"},
            )
        )


def test_google_due_is_validated_and_normalized_to_rfc3339() -> None:
    google, _ = _provider_reminder_payload(
        _command(
            provider="google_tasks",
            operation="create_task",
            payload={"title": "Pay bill", "due": "2026-08-09"},
        )
    )
    assert google["due"] == "2026-08-09T00:00:00.000Z"

    with pytest.raises(HTTPException, match="due must be YYYY-MM-DD"):
        _provider_reminder_payload(
            _command(
                provider="google_tasks",
                operation="create_task",
                payload={"title": "Pay bill", "due": "next Sunday"},
            )
        )
