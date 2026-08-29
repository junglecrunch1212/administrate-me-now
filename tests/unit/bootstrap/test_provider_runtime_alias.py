from __future__ import annotations

import json
from pathlib import Path

import pytest

from bootstrap import provider_runtime_alias
from bootstrap.contracts import DeploymentPaths
from bootstrap.provider_runtime_alias import (
    ProviderRuntimeAliasError,
    prepare_provider_runtime_alias,
    rollback_provider_runtime_alias,
)
from bootstrap.secrets import KEYCHAIN_ALIAS_FILENAME


def _private_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _deployment(tmp_path: Path) -> tuple[DeploymentPaths, str, dict[str, str]]:
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    for directory in (paths.root, paths.app_dir, paths.instance_dir):
        directory.chmod(0o700)
    _private_write(paths.instance_dir / "config" / "adapters.json", {"adapters": []})
    _private_write(
        paths.instance_dir / "config" / "member-mapping.json",
        {"members": [{"member_id": "member-fixture-b", "role": "principal"}]},
    )
    account = paths.keychain_account()
    prefix = f"keychain://{account}/"
    existing = {
        f"{prefix}event-log": f"{prefix}event-log-runtime-py314-fixture",
    }
    _private_write(
        paths.instance_dir / "config" / KEYCHAIN_ALIAS_FILENAME,
        {
            "schema_version": 1,
            "target_commit": "a" * 40,
            "runtime_tag": "py314",
            "aliases": existing,
        },
    )
    return paths, f"{prefix}google-logical-fixture", existing


def test_prepare_is_additive_verified_and_reversibly_unused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, logical, existing = _deployment(tmp_path)
    credential = b'{"refresh_token":"fixture-only"}'

    class Store:
        def get(self, reference: str) -> bytes:
            assert reference == logical
            return credential

    monkeypatch.setattr(provider_runtime_alias, "secret_store", lambda _paths: Store())
    monkeypatch.setattr(
        provider_runtime_alias,
        "discover_readonly_google_enrollment",
        lambda *_args, **_kwargs: {"oauth_credentials_ref": logical},
    )
    runtime_values: dict[tuple[str, str], str] = {}
    deleted: list[tuple[str, str]] = []

    def put_runtime(service: str, account: str, value: str) -> None:
        runtime_values[(service, account)] = value

    def get_runtime(service: str, account: str) -> str:
        return runtime_values[(service, account)]

    result = prepare_provider_runtime_alias(
        paths,
        authorization_reviewed=True,
        put_runtime=put_runtime,
        get_runtime=get_runtime,
        delete_runtime=lambda service, account: deleted.append((service, account)),
    )

    alias_path = paths.instance_dir / "config" / KEYCHAIN_ALIAS_FILENAME
    document = json.loads(alias_path.read_text("utf-8"))
    assert result == {
        "status": "provider_runtime_alias_ready",
        "alias_count": 2,
        "credentials_changed": False,
        "capabilities_changed": False,
        "provider_called": False,
        "content_exposed": False,
    }
    assert all(document["aliases"].get(key) == value for key, value in existing.items())
    target = document["aliases"][logical]
    assert target.startswith(f"keychain://{paths.keychain_account()}/provider-runtime-")
    assert "fixture-only" not in alias_path.read_text("utf-8")
    assert not deleted

    _private_write(
        paths.instance_dir / "config" / "adapters.json",
        {"adapters": [{"config": {"oauth_credentials_ref": logical}}]},
    )
    with pytest.raises(ProviderRuntimeAliasError, match="still referenced"):
        rollback_provider_runtime_alias(
            paths,
            authorization_reviewed=True,
            delete_runtime=lambda service, account: deleted.append((service, account)),
        )

    _private_write(paths.instance_dir / "config" / "adapters.json", {"adapters": []})
    rolled_back = rollback_provider_runtime_alias(
        paths,
        authorization_reviewed=True,
        delete_runtime=lambda service, account: deleted.append((service, account)),
    )
    remaining = json.loads(alias_path.read_text("utf-8"))["aliases"]
    assert rolled_back["status"] == "provider_runtime_alias_rolled_back"
    assert remaining == existing
    assert len(deleted) == 1


def test_prepare_requires_exact_live_authorization(tmp_path: Path) -> None:
    paths, _logical, _existing = _deployment(tmp_path)

    with pytest.raises(ProviderRuntimeAliasError, match="authorization is required"):
        prepare_provider_runtime_alias(paths, authorization_reviewed=False)
