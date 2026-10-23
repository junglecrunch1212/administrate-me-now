"""Private web-client provisioning; no real Keychain, Google or runtime effects."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from adminme.google_oauth_transport import CALLBACK_PATH, GoogleOAuthError
from bootstrap import member_google_oauth as setup
from bootstrap.contracts import DeploymentPaths

ORIGIN = "https://console.fixture-tail.ts.net"


class Store:
    def __init__(self, account: str) -> None:
        self.account = account
        self.puts: list[tuple[str, bytes]] = []

    def put(self, name: str, payload: bytes) -> str:
        self.puts.append((name, payload))
        return f"keychain://{self.account}/{name}"


def fixture_client(path: Path, *, secret: str = "fixture-secret") -> Path:
    path.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "fixture-client.apps.googleusercontent.com",
                    "client_secret": secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [ORIGIN + CALLBACK_PATH],
                }
            }
        )
    )
    path.chmod(0o600)
    return path


@pytest.fixture
def environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[DeploymentPaths, Path, Store]:
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.root.mkdir(mode=0o700)
    client = fixture_client(tmp_path / "web.json")
    store = Store(paths.keychain_account())
    monkeypatch.setattr(setup, "secret_store", lambda _paths: store)
    return paths, client, store


def test_provision_contains_reference_only_and_loads_exact_callback(
    environment: tuple[Any, ...],
) -> None:
    paths, client, store = environment
    assert setup.load_configuration(paths) is None
    result = setup.provision(client, ORIGIN, paths=paths)
    path = setup.configuration_path(paths)
    assert setup.load_configuration(paths) == result
    assert result["redirect_uri"] == ORIGIN + CALLBACK_PATH
    assert result["contract"] == setup.CONTRACT
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "fixture-secret" not in path.read_text()
    assert "client_id" not in path.read_text()
    assert len(store.puts) == 1
    stored = json.loads(store.puts[0][1])
    assert stored["web"]["client_secret"] == "fixture-secret"
    assert stored["web"]["redirect_uris"] == [ORIGIN + CALLBACK_PATH]


def test_rotation_requires_explicit_replace_and_retains_old_key(
    environment: tuple[Any, ...],
) -> None:
    paths, client, store = environment
    first = setup.provision(client, ORIGIN, paths=paths)
    fixture_client(client, secret="rotated-fixture-secret")
    with pytest.raises(GoogleOAuthError, match="--replace"):
        setup.provision(client, ORIGIN, paths=paths)
    assert setup.load_configuration(paths) == first
    assert len(store.puts) == 1
    second = setup.provision(client, ORIGIN, paths=paths, replace_existing=True)
    assert first["client_ref"] != second["client_ref"]
    assert len(store.puts) == 2


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666])
def test_public_client_file_rejected_before_keychain(
    environment: tuple[Any, ...], mode: int
) -> None:
    paths, client, store = environment
    client.chmod(mode)
    with pytest.raises(GoogleOAuthError, match="owner-only"):
        setup.provision(client, ORIGIN, paths=paths)
    assert store.puts == []


def test_symlink_and_hard_link_client_files_are_rejected(
    environment: tuple[Any, ...],
) -> None:
    paths, client, store = environment
    link = client.with_name("symlink.json")
    link.symlink_to(client)
    with pytest.raises(GoogleOAuthError, match="owner-only"):
        setup.provision(link, ORIGIN, paths=paths)
    hard_link = client.with_name("hard-link.json")
    hard_link.hardlink_to(client)
    with pytest.raises(GoogleOAuthError, match="owner-only"):
        setup.provision(client, ORIGIN, paths=paths)
    assert store.puts == []


def test_client_config_failure_does_not_replace_existing_config(
    environment: tuple[Any, ...],
) -> None:
    paths, client, store = environment
    first = setup.provision(client, ORIGIN, paths=paths)
    client.write_text(json.dumps({"installed": {}}))
    with pytest.raises(GoogleOAuthError, match="Web application"):
        setup.provision(client, ORIGIN, paths=paths, replace_existing=True)
    assert setup.load_configuration(paths) == first
    assert len(store.puts) == 1


def test_foreign_reference_and_public_runtime_config_are_rejected(
    environment: tuple[Any, ...],
) -> None:
    paths, client, _store = environment
    config = setup.provision(client, ORIGIN, paths=paths)
    path = setup.configuration_path(paths)
    config["client_ref"] = (
        "keychain://another-instance/google-member-web-client-fixture"
    )
    path.write_text(json.dumps(config))
    with pytest.raises(GoogleOAuthError, match="configuration is invalid"):
        setup.load_configuration(paths)
    path.chmod(0o644)
    with pytest.raises(GoogleOAuthError, match="owner-only"):
        setup.load_configuration(paths)


def test_public_config_directory_rejected_before_secret_write(
    environment: tuple[Any, ...],
) -> None:
    paths, client, store = environment
    setup.configuration_path(paths).parent.mkdir(parents=True, mode=0o755)
    with pytest.raises(GoogleOAuthError, match="directory is not private"):
        setup.provision(client, ORIGIN, paths=paths)
    assert store.puts == []
