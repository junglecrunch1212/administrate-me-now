from __future__ import annotations

from copy import deepcopy

import pytest

from bootstrap.provider_binding_transition import (
    ProviderBindingTransitionError,
    prepare_provider_binding_transition,
)


def _mapping() -> dict:
    return {
        "members": [
            {"member_id": "member-fixture-a", "role": "principal"},
            {"member_id": "member-fixture-b", "role": "principal"},
        ]
    }


def _legacy() -> dict:
    reference = "keychain://fixture/google-a"
    account = "principal-a@invalid.example"
    member = "member-fixture-a"
    return {
        "adapters": [
            {
                "kind": "messaging-gmail-api",
                "config": {
                    "member_id": member,
                    "user_email": account,
                    "workspace": "personal",
                    "oauth_credentials_ref": reference,
                    "enable_writes": False,
                },
            },
            {
                "kind": "calendaring-google-calendar",
                "config": {
                    "member_id": member,
                    "provider_account_id": account,
                    "oauth_credentials_ref": reference,
                    "calendar_id": "primary",
                    "discover_calendars": True,
                },
            },
            {
                "kind": "tasks-google-tasks",
                "config": {
                    "member_id": member,
                    "user_email": account,
                    "oauth_credentials_ref": reference,
                    "tasklist_id": "@default",
                },
            },
        ]
    }


def test_transition_adds_only_binding_metadata_and_preserves_capabilities() -> None:
    source = _legacy()
    before = deepcopy(source)

    transition = prepare_provider_binding_transition(
        source,
        household_mapping=_mapping(),
        ownership_reviewed=True,
    )

    assert source == before
    assert transition.migrated_adapter_count == 3
    rows = {row["kind"]: row["config"] for row in transition.prepared["adapters"]}
    assert rows["messaging-gmail-api"]["permission_scope"] == [
        "https://www.googleapis.com/auth/gmail.readonly"
    ]
    assert rows["calendaring-google-calendar"]["permission_scope"] == [
        "https://www.googleapis.com/auth/calendar"
    ]
    assert rows["tasks-google-tasks"]["permission_scope"] == [
        "https://www.googleapis.com/auth/tasks"
    ]
    assert all(row["workspace"] == "personal" for row in rows.values())
    assert all(row["ownership_attested"] is True for row in rows.values())
    assert transition.public()["credentials_changed"] is False
    assert transition.public()["capabilities_changed"] is False


def test_transition_requires_owner_review_and_rejects_ambiguity() -> None:
    with pytest.raises(ProviderBindingTransitionError, match="explicitly reviewed"):
        prepare_provider_binding_transition(
            _legacy(), household_mapping=_mapping(), ownership_reviewed=False
        )

    ambiguous = _legacy()
    ambiguous["adapters"][1]["config"]["workspace"] = "work"
    with pytest.raises(ProviderBindingTransitionError, match="inherited unambiguously"):
        prepare_provider_binding_transition(
            ambiguous, household_mapping=_mapping(), ownership_reviewed=True
        )


def test_transition_refuses_capability_or_binding_rewrite() -> None:
    contradictory = _legacy()
    contradictory["adapters"][0]["config"]["permission_scope"] = [
        "https://www.googleapis.com/auth/gmail.send"
    ]
    with pytest.raises(ProviderBindingTransitionError, match="contradicts"):
        prepare_provider_binding_transition(
            contradictory,
            household_mapping=_mapping(),
            ownership_reviewed=True,
        )
