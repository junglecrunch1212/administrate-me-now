"""Exact-scope tests for the installed-user Google OAuth helper."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap import google_oauth


class _Store:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes]] = []

    def put(self, name: str, value: bytes) -> str:
        self.puts.append((name, value))
        return f"keychain://fixture/{name}"


class _Flow:
    def __init__(self, *, run_dir: Path, granted_scopes: list[str] | None = None):
        self.run_dir = run_dir
        self.client_config = {"token_uri": "https://oauth2.example.invalid/token"}
        self.authorization_url_calls: list[dict[str, object]] = []
        self.run_calls: list[dict[str, object]] = []
        self.credentials = SimpleNamespace(
            client_id="client-fixture",
            client_secret="secret-fixture",
            refresh_token="refresh-fixture",
            scopes=list(google_oauth.GOOGLE_READONLY_ADAPTER_SCOPES),
            granted_scopes=granted_scopes,
        )

    def authorization_url(self, **kwargs: object) -> tuple[str, str]:
        self.authorization_url_calls.append(kwargs)
        return ("https://accounts.example.invalid/authorize", "fixture-state")

    def run_local_server(self, **kwargs: object) -> object:
        self.run_calls.append(kwargs)
        self.authorization_url(access_type="offline", prompt="consent")
        assert (self.run_dir / "google-oauth-authorization-url").is_file()
        return self.credentials


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    granted_scopes: list[str] | None = None,
) -> tuple[_Flow, _Store, list[list[str]]]:
    flow = _Flow(run_dir=tmp_path, granted_scopes=granted_scopes)
    store = _Store()
    requested: list[list[str]] = []

    def from_client_secrets_file(_path: str, *, scopes: list[str]) -> _Flow:
        requested.append(list(scopes))
        return flow

    monkeypatch.setattr(
        google_oauth.DeploymentPaths,
        "default",
        lambda: SimpleNamespace(run_dir=tmp_path),
    )
    monkeypatch.setattr(google_oauth, "secret_store", lambda _paths: store)
    monkeypatch.setattr(
        google_oauth.InstalledAppFlow,
        "from_client_secrets_file",
        from_client_secrets_file,
    )
    return flow, store, requested


def test_enroll_requests_only_three_readonly_scopes_and_uses_member_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flow, store, requested = _install_fakes(monkeypatch, tmp_path)

    reference = google_oauth.enroll(
        tmp_path / "client.json",
        member_id="member-fixture-b",
        expected_account_id="member@example.invalid",
        account_resolver=lambda _credentials: "member@example.invalid",
    )

    assert requested == [list(google_oauth.GOOGLE_READONLY_ADAPTER_SCOPES)]
    assert flow.run_calls == [
        {
            "host": "127.0.0.1",
            "port": 0,
            "authorization_prompt_message": "",
            "success_message": (
                "AdministrateMe authorization complete. You may close this tab."
            ),
            "open_browser": False,
            "access_type": "offline",
            "prompt": "consent",
        }
    ]
    [(secret_name, raw_document)] = store.puts
    assert secret_name == google_oauth.member_google_secret_name("member-fixture-b")
    assert "member-fixture-b" not in secret_name
    assert reference == f"keychain://fixture/{secret_name}"
    document = json.loads(raw_document)
    assert document["scopes"] == sorted(google_oauth.GOOGLE_READONLY_ADAPTER_SCOPES)
    assert document["provider_account_id"] == "member@example.invalid"
    assert document["owner_member_digest"] == google_oauth.credential_member_digest(
        "member-fixture-b"
    )
    assert not (tmp_path / "google-oauth-authorization-url").exists()


def test_enroll_refuses_a_broader_google_grant_before_secret_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broader = [
        *google_oauth.GOOGLE_READONLY_ADAPTER_SCOPES,
        "https://www.googleapis.com/auth/calendar",
    ]
    _flow, store, _requested = _install_fakes(
        monkeypatch, tmp_path, granted_scopes=broader
    )

    with pytest.raises(RuntimeError, match="does not exactly match"):
        google_oauth.enroll(
            tmp_path / "client.json",
            member_id="member-fixture-b",
            expected_account_id="member@example.invalid",
            account_resolver=lambda _credentials: "member@example.invalid",
        )

    assert store.puts == []
    assert not (tmp_path / "google-oauth-authorization-url").exists()


@pytest.mark.parametrize("member_id", ["", " ", "\t"])
def test_member_google_secret_name_rejects_missing_member_id(member_id: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        google_oauth.member_google_secret_name(member_id)


def test_enroll_refuses_the_wrong_google_account_before_secret_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _flow, store, _requested = _install_fakes(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="does not match"):
        google_oauth.enroll(
            tmp_path / "client.json",
            member_id="member-fixture-b",
            expected_account_id="reviewed@example.invalid",
            account_resolver=lambda _credentials: "different@example.invalid",
        )

    assert store.puts == []


def test_cos_enrollment_binds_exact_read_send_scope_to_service_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flow, store, requested = _install_fakes(monkeypatch, tmp_path)
    flow.credentials.scopes = list(google_oauth.COS_GOOGLE_SCOPES)
    flow.credentials.granted_scopes = list(google_oauth.COS_GOOGLE_SCOPES)
    reference = google_oauth.enroll(
        tmp_path / "client.json",
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        expected_account_id="cos@example.test",
        account_resolver=lambda _credentials: "COS@EXAMPLE.TEST",
    )
    assert requested == [list(google_oauth.COS_GOOGLE_SCOPES)]
    assert requested[0] == [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    [(secret_name, raw_document)] = store.puts
    owner_digest = google_oauth.credential_service_digest(COS_SERVICE_PRINCIPAL_ID)
    assert secret_name == f"google-oauth-service-{owner_digest}"
    assert reference == f"keychain://fixture/{secret_name}"
    document = json.loads(raw_document)
    assert document["owner_kind"] == "service_principal"
    assert document["owner_service_principal_digest"] == owner_digest
    assert "owner_member_digest" not in document
    assert document["provider_account_id"] == "cos@example.test"
    assert document["scopes"] == sorted(google_oauth.COS_GOOGLE_SCOPES)
    marker_path = tmp_path / "cos-google-enrollment.json"
    marker = json.loads(marker_path.read_text())
    assert marker["contract"] == "cos-google-enrollment-v1"
    assert marker["credential_reference"] == reference
    assert marker["profile_verified"] is True
    assert marker["provider_effect_count"] == 0
    assert (
        marker["provider_account_sha256"]
        == hashlib.sha256(b"cos@example.test").hexdigest()
    )
    assert marker["owner_service_principal_digest"] == owner_digest
    assert stat.S_IMODE(marker_path.stat().st_mode) == 0o600
    for private_value in (
        "cos@example.test",
        flow.credentials.refresh_token,
        flow.credentials.client_secret,
    ):
        assert private_value not in marker_path.read_text()
    assert not (tmp_path / "google-oauth-authorization-url").exists()


def test_cos_wrong_account_cannot_reach_keychain_or_create_enrollment_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flow, store, _requested = _install_fakes(monkeypatch, tmp_path)
    flow.credentials.scopes = list(google_oauth.COS_GOOGLE_SCOPES)
    with pytest.raises(RuntimeError, match="does not match"):
        google_oauth.enroll(
            tmp_path / "client.json",
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            expected_account_id="cos@example.test",
            account_resolver=lambda _credentials: "personal@example.test",
        )
    assert store.puts == []
    assert not (tmp_path / "cos-google-enrollment.json").exists()
    assert not (tmp_path / "google-oauth-authorization-url").exists()


def test_cos_broader_scope_is_rejected_before_service_secret_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flow, store, _requested = _install_fakes(monkeypatch, tmp_path)
    flow.credentials.scopes = list(google_oauth.COS_GOOGLE_SCOPES)
    flow.credentials.granted_scopes = [
        *google_oauth.COS_GOOGLE_SCOPES,
        "https://www.googleapis.com/auth/calendar",
    ]
    with pytest.raises(RuntimeError, match="does not exactly match"):
        google_oauth.enroll(
            tmp_path / "client.json",
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            expected_account_id="cos@example.test",
            account_resolver=lambda _credentials: "cos@example.test",
        )
    assert store.puts == []
    assert not (tmp_path / "cos-google-enrollment.json").exists()


@pytest.mark.parametrize(
    ("member", "service"),
    [
        ("member-fixture", COS_SERVICE_PRINCIPAL_ID),
        (None, None),
        (None, "unrecognized-service"),
    ],
)
def test_enrollment_refuses_ambiguous_or_unrecognized_owners_before_oauth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    member: str | None,
    service: str | None,
) -> None:
    flow, store, requested = _install_fakes(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        google_oauth.enroll(
            tmp_path / "client.json",
            member_id=member,
            service_principal_id=service,
            expected_account_id="cos@example.test",
            account_resolver=lambda _credentials: "cos@example.test",
        )
    assert requested == []
    assert flow.run_calls == []
    assert store.puts == []
