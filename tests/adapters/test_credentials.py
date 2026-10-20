from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from adminme.adapters.credentials import (
    assert_installed_user_binding,
    assert_service_oauth_binding,
    credential_member_digest,
    credential_service_digest,
    resolve_json_credentials,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID


def test_resolves_keychain_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"client_id": "id", "refresh_token": "token"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    calls: list[tuple[str, str]] = []

    def fake_get_password(service: str, account: str) -> str:
        calls.append((service, account))
        return encoded

    monkeypatch.setattr("keyring.get_password", fake_get_password)

    assert resolve_json_credentials("keychain://instance/google-oauth") == payload
    assert calls == [("com.administrateme.instance.google-oauth", "instance")]


def test_resolves_runtime_aliased_keychain_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"client_id": "id", "refresh_token": "token"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    instance_dir = tmp_path / "state"
    config = instance_dir / "config"
    config.mkdir(parents=True)
    logical = "keychain://instance/google-oauth"
    physical = "keychain://instance/google-oauth-runtime-py314-deadbeefcafe"
    (config / "keychain-runtime-aliases.json").write_text(
        json.dumps({"schema_version": 1, "aliases": {logical: physical}}),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def get_runtime_password(service: str, account: str) -> str:
        calls.append((service, account))
        return encoded

    monkeypatch.setenv("ADMINME_INSTANCE_DIR", str(instance_dir))
    monkeypatch.setattr(
        "adminme.adapters.credentials.get_runtime_password", get_runtime_password
    )
    monkeypatch.setattr(
        "keyring.get_password",
        lambda *_args: pytest.fail("aliased items must use the runtime CLI"),
    )

    assert resolve_json_credentials(logical) == payload
    assert calls == [
        (
            "com.administrateme.instance.google-oauth-runtime-py314-deadbeefcafe",
            "instance",
        )
    ]


@pytest.mark.parametrize(
    "reference", ["raw-secret", "keychain://missing-name", "keychain:///name"]
)
def test_rejects_unsafe_references(reference: str) -> None:
    with pytest.raises(ValueError):
        resolve_json_credentials(reference)


def test_accepts_matching_installed_user_binding() -> None:
    assert_installed_user_binding(
        {
            "owner_member_digest": credential_member_digest("member-fixture"),
            "provider_account_id": "member@example.invalid",
        },
        member_id="member-fixture",
        provider_account_id="MEMBER@example.invalid",
    )


@pytest.mark.parametrize(
    ("document", "member_id", "provider_account_id"),
    [
        (
            {
                "owner_member_digest": credential_member_digest("member-other"),
                "provider_account_id": "member@example.invalid",
            },
            "member-fixture",
            "member@example.invalid",
        ),
        (
            {
                "owner_member_digest": credential_member_digest("member-fixture"),
                "provider_account_id": "other@example.invalid",
            },
            "member-fixture",
            "member@example.invalid",
        ),
        (
            {"owner_member_digest": credential_member_digest("member-fixture")},
            "member-fixture",
            "member@example.invalid",
        ),
    ],
)
def test_rejects_mismatched_or_incomplete_installed_user_binding(
    document: dict[str, str], member_id: str, provider_account_id: str
) -> None:
    with pytest.raises(RuntimeError, match="does not match"):
        assert_installed_user_binding(
            document,
            member_id=member_id,
            provider_account_id=provider_account_id,
        )


def test_allows_prebinding_credentials_for_migration() -> None:
    assert_installed_user_binding(
        {"client_id": "legacy"},
        member_id="member-fixture",
        provider_account_id="member@example.invalid",
    )


def test_service_oauth_is_distinct_from_member_and_unbound_grants() -> None:
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    document = {
        "owner_kind": "service_principal",
        "owner_service_principal_digest": credential_service_digest(
            COS_SERVICE_PRINCIPAL_ID
        ),
        "provider_account_id": "cos-fixture@example.invalid",
        "client_id": "fixture-client",
        "client_secret": "fixture-secret",
        "refresh_token": "fixture-refresh",
        "scopes": scopes,
    }
    kwargs = {
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "provider_account_id": "cos-fixture@example.invalid",
        "required_scopes": scopes,
    }
    assert_service_oauth_binding(document, **kwargs)
    for update in (
        {"owner_kind": "member"},
        {"owner_member_digest": credential_member_digest("member-fixture")},
        {"owner_service_principal_digest": "0" * 24},
        {"provider_account_id": "other@example.invalid"},
        {"scopes": scopes + ["https://www.googleapis.com/auth/gmail.modify"]},
        {"scopes": scopes[:1]},
    ):
        with pytest.raises(RuntimeError, match="exact CoS account"):
            assert_service_oauth_binding({**document, **update}, **kwargs)
    with pytest.raises(RuntimeError, match="exact CoS account"):
        assert_service_oauth_binding({}, **kwargs)
