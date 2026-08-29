from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from adminme.adapters.credentials import (
    credential_member_digest as runtime_credential_member_digest,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.google_oauth_contract import (
    GOOGLE_READONLY_ADAPTER_SCOPES,
    credential_member_digest,
    member_google_secret_name,
)
from bootstrap.provider_binding_transition import (
    ProviderBindingTransitionError,
    discover_readonly_google_enrollment,
    prepare_provider_binding_transition,
    valid_provider_binding_transition,
)
from bootstrap.secrets import secret_store


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


def test_bootstrap_owner_digest_matches_runtime_binding_digest() -> None:
    member_id = "member-fixture-b"

    assert credential_member_digest(member_id) == runtime_credential_member_digest(
        member_id
    )


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


def test_transition_adds_one_exact_readonly_google_profile() -> None:
    transition = prepare_provider_binding_transition(
        _legacy(),
        household_mapping=_mapping(),
        ownership_reviewed=True,
        readonly_google_enrollment={
            "member_id": "member-fixture-b",
            "provider_account_id": "principal-b@invalid.example",
            "oauth_credentials_ref": "keychain://fixture/google-b",
            "permission_scope": list(GOOGLE_READONLY_ADAPTER_SCOPES),
        },
    )

    assert transition.migrated_adapter_count == 3
    assert transition.enrolled_adapter_count == 3
    enrolled = transition.prepared["adapters"][-3:]
    assert [row["kind"] for row in enrolled] == [
        "messaging-gmail-api",
        "calendaring-google-calendar",
        "tasks-google-tasks",
    ]
    assert enrolled[0]["config"]["enable_writes"] is False
    assert enrolled[1]["config"]["read_only"] is True
    assert enrolled[1]["config"]["discover_calendars"] is True
    assert enrolled[2]["config"]["read_only"] is True
    assert enrolled[2]["config"]["include_all_tasklists"] is False
    assert transition.public()["contract"] == "provider-binding-config-transition-v2"
    assert transition.public()["capabilities_changed"] is True
    assert transition.public()["observation_required"] is True
    assert valid_provider_binding_transition(transition.public()) is True

    invalid = transition.public()
    invalid["enrolled_adapter_count"] = 2
    assert valid_provider_binding_transition(invalid) is False


def test_discovery_selects_only_the_unattached_principal_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.instance_dir.mkdir(parents=True)
    member_id = "member-fixture-b"
    document = {
        "client_id": "fixture-client",
        "client_secret": "fixture-secret",
        "owner_member_digest": credential_member_digest(member_id),
        "provider_account_id": "principal-b@invalid.example",
        "refresh_token": "fixture-refresh",
        "token_uri": "https://oauth2.invalid/token",
        "scopes": list(GOOGLE_READONLY_ADAPTER_SCOPES),
    }
    secret_store(paths, test_mode=True).put(
        member_google_secret_name(member_id),
        json.dumps(document).encode(),
    )

    discovered = discover_readonly_google_enrollment(
        paths,
        _legacy(),
        household_mapping=_mapping(),
        test_mode=True,
    )

    assert discovered is not None
    assert discovered["member_id"] == member_id
    assert discovered["permission_scope"] == sorted(GOOGLE_READONLY_ADAPTER_SCOPES)


def test_discovery_rejects_a_broader_stored_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.instance_dir.mkdir(parents=True)
    member_id = "member-fixture-b"
    secret_store(paths, test_mode=True).put(
        member_google_secret_name(member_id),
        json.dumps(
            {
                "client_id": "fixture-client",
                "client_secret": "fixture-secret",
                "owner_member_digest": credential_member_digest(member_id),
                "provider_account_id": "principal-b@invalid.example",
                "refresh_token": "fixture-refresh",
                "token_uri": "https://oauth2.invalid/token",
                "scopes": [
                    *GOOGLE_READONLY_ADAPTER_SCOPES,
                    "https://www.googleapis.com/auth/calendar",
                ],
            }
        ).encode(),
    )

    with pytest.raises(ProviderBindingTransitionError, match="grant is invalid"):
        discover_readonly_google_enrollment(
            paths,
            _legacy(),
            household_mapping=_mapping(),
            test_mode=True,
        )
