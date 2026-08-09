"""Executable totality and fail-closed tests for candidate source policy."""

from __future__ import annotations

import pytest

from adminme.candidate_policy import (
    CANDIDATE_KINDS,
    SOURCE_CATALOG,
    default_policy,
    effective_policy,
    filter_candidates,
    source_id_for_event,
    validate_policy_override,
)
from adminme.settings_control import SettingsControlStore


def test_catalog_covers_every_member_surface_and_candidate_kind() -> None:
    ids = [str(source["id"]) for source in SOURCE_CATALOG]
    assert len(ids) == len(set(ids))
    assert {
        "surface:today",
        "surface:triage",
        "surface:comms",
        "surface:reminders",
        "surface:calendar",
        "surface:people",
        "surface:notes",
        "surface:finance",
        "surface:scoreboard",
        "surface:settings",
        "surface:openclaw",
    } <= set(ids)
    assert set(CANDIDATE_KINDS) == {
        kind for source in SOURCE_CATALOG for kind in source["candidate_kinds"]
    }
    assert {source["implementation"] for source in SOURCE_CATALOG} == {
        "current",
        "planned",
    }
    assert {source["policy_execution"] for source in SOURCE_CATALOG} == {
        "current",
        "planned",
    }


def test_explicit_member_entry_is_locked_and_unknown_source_fails_closed() -> None:
    policy = default_policy("surface:today")
    assert policy["locked"] is True
    assert policy["mode"] == "review"
    with pytest.raises(ValueError, match="cannot be disabled"):
        validate_policy_override(
            "surface:today",
            mode="quiet",
            language_enabled=False,
            candidate_kinds=["task"],
        )
    unknown = default_policy("adapter:not-reviewed")
    assert unknown == {
        "source_id": "adapter:not-reviewed",
        "mode": "review",
        "language_enabled": False,
        "candidate_kinds": list(CANDIDATE_KINDS),
        "locked": True,
        "known_source": False,
    }


def test_effective_policy_filters_mode_and_kind_without_routing_authority() -> None:
    policy = effective_policy(
        "adapter:gmail",
        {
            "mode": "review",
            "language_enabled": False,
            "candidate_kinds": ["calendar_event"],
        },
    )
    assert policy["overridden"] is True
    assert filter_candidates(
        [
            {"target_kind": "task"},
            {"target_kind": "calendar_event"},
        ],
        policy,
    ) == [{"target_kind": "calendar_event"}]
    assert (
        filter_candidates(
            [{"target_kind": "calendar_event"}], {**policy, "mode": "quiet"}
        )
        == []
    )


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {
                "type": "messaging.received",
                "source_adapter": "gmail-api",
                "payload": {"source_channel": "gmail"},
            },
            "adapter:gmail",
        ),
        (
            {
                "type": "messaging.received",
                "source_adapter": "reviewed-custom-adapter",
                "payload": {"source_channel": "reviewed-custom-channel"},
            },
            "adapter:generic_messaging",
        ),
        (
            {
                "type": "member_knowledge.note_added",
                "payload": {"source_kind": "fathom"},
            },
            "adapter:fathom",
        ),
        ({"type": "money_flow.recorded", "payload": {}}, "adapter:plaid"),
        ({"type": "context.snapshot", "payload": {}}, "adapter:home_assistant"),
        (
            {
                "type": "triage.intent.submitted",
                "payload": {"source_surface": "calendar"},
            },
            "surface:calendar",
        ),
    ],
)
def test_event_types_map_to_reviewed_source_rows(
    event: dict[str, object], expected: str
) -> None:
    assert source_id_for_event(event) == expected


def test_settings_journal_migrates_v1_and_rejects_corrupt_policy(tmp_path) -> None:
    path = tmp_path / "settings-control.json"
    path.write_text(
        '{"schema_version":1,"revision":0,"connections":{},'
        '"member_preferences":{},"bridge_requests":{}}',
        encoding="utf-8",
    )
    store = SettingsControlStore(path)
    assert store.read()["candidate_policies"] == {}
    mutation = store.set_candidate_policy(
        "member-a",
        "adapter:gmail",
        mode="quiet",
        language_enabled=False,
        candidate_kinds=["task"],
        expected_revision=0,
    )
    assert mutation.revision == 1
    assert store.effective_candidate_policy("member-a", "adapter:gmail")["mode"] == (
        "quiet"
    )
    path.write_text(
        '{"schema_version":2,"revision":1,"connections":{},'
        '"member_preferences":{},"bridge_requests":{},'
        '"candidate_policies":{"member-a":{"adapter:missing":{'
        '"mode":"quiet","language_enabled":false,"candidate_kinds":["task"]}}}}',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="candidate policy is invalid"):
        store.read()
