"""Exact-scope tests for the installed-user Google OAuth helper."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    assert document["scopes"] == sorted(
        google_oauth.GOOGLE_READONLY_ADAPTER_SCOPES
    )
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
