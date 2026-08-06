from __future__ import annotations

import base64
import json

import pytest

from adminme.adapters.credentials import resolve_json_credentials


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


@pytest.mark.parametrize(
    "reference", ["raw-secret", "keychain://missing-name", "keychain:///name"]
)
def test_rejects_unsafe_references(reference: str) -> None:
    with pytest.raises(ValueError):
        resolve_json_credentials(reference)
