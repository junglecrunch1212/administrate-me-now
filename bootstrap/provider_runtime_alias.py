"""Prepare or roll back one runtime-bound alias for reviewed Google OAuth.

This command is deliberately separate from candidate readiness and activation.
It copies the already approved logical Keychain value into an Apple-signed-client
envelope, then publishes one additive alias only after the copy verifies. It
does not change the OAuth document, adapter configuration, observation mode, or
provider state.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Sequence

from adminme.lib.macos_keychain import (
    delete_runtime_password,
    get_runtime_password,
    put_runtime_password,
)
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.provider_binding_transition import (
    ProviderBindingTransitionError,
    discover_readonly_google_enrollment,
)
from bootstrap.secrets import KEYCHAIN_ALIAS_FILENAME, secret_store


_COMMIT = re.compile(r"[0-9a-f]{40}")
_RUNTIME_TAG = re.compile(r"[a-z0-9][a-z0-9-]{1,31}")
_ITEM_NAME = re.compile(r"[A-Za-z0-9._-]{1,180}")
_TARGET_PREFIX = "provider-runtime-"


class ProviderRuntimeAliasError(RuntimeError):
    """The exact provider runtime alias could not be changed safely."""


def _private_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        metadata = path.lstat()
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderRuntimeAliasError(
            f"private configuration is unavailable: {path.name}"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not isinstance(value, dict)
    ):
        raise ProviderRuntimeAliasError(f"private configuration is unsafe: {path.name}")
    return payload, value


def _active_runtime_coordinates(paths: DeploymentPaths) -> tuple[str, str]:
    _payload, pointer = _private_json(paths.app_dir / "current-release.json")
    commit = pointer.get("commit")
    release = pointer.get("path")
    if (
        not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or not isinstance(release, str)
    ):
        raise ProviderRuntimeAliasError("active release coordinate is invalid")
    release_path = Path(release).resolve()
    releases = (paths.app_dir / "releases").resolve()
    if releases not in release_path.parents:
        raise ProviderRuntimeAliasError("active release coordinate is invalid")
    try:
        versions = json.loads(
            (release_path / "bootstrap" / "versions.json").read_text("utf-8")
        )
        preferred = str(versions["python"]["preferred"])
        major, minor, _patch = preferred.split(".", 2)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderRuntimeAliasError("active runtime coordinate is invalid") from exc
    runtime_tag = f"py{major}{minor}"
    if _RUNTIME_TAG.fullmatch(runtime_tag) is None:
        raise ProviderRuntimeAliasError("active runtime coordinate is invalid")
    return commit, runtime_tag


def _alias_state(
    paths: DeploymentPaths,
) -> tuple[bytes | None, dict[str, Any], dict[str, str]]:
    path = paths.instance_dir / "config" / KEYCHAIN_ALIAS_FILENAME
    if not os.path.lexists(path):
        commit, runtime_tag = _active_runtime_coordinates(paths)
        document: dict[str, Any] = {
            "schema_version": 1,
            "target_commit": commit,
            "runtime_tag": runtime_tag,
            "aliases": {},
        }
        return None, document, {}
    payload, document = _private_json(path)
    if (
        set(document) != {"schema_version", "target_commit", "runtime_tag", "aliases"}
        or document.get("schema_version") != 1
        or _COMMIT.fullmatch(str(document.get("target_commit"))) is None
        or _RUNTIME_TAG.fullmatch(str(document.get("runtime_tag"))) is None
        or not isinstance(document.get("aliases"), dict)
    ):
        raise ProviderRuntimeAliasError("runtime alias map is invalid")
    prefix = f"keychain://{paths.keychain_account()}/"
    aliases: dict[str, str] = {}
    for source, target in document["aliases"].items():
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or not source.startswith(prefix)
            or not target.startswith(prefix)
            or source == target
            or not _ITEM_NAME.fullmatch(source.removeprefix(prefix))
            or not _ITEM_NAME.fullmatch(target.removeprefix(prefix))
        ):
            raise ProviderRuntimeAliasError("runtime alias map is invalid")
        aliases[source] = target
    if len(set(aliases.values())) != len(aliases):
        raise ProviderRuntimeAliasError("runtime alias targets are ambiguous")
    return payload, document, aliases


def _walk_references(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value.startswith("keychain://") else set()
    if isinstance(value, dict):
        result: set[str] = set()
        for child in value.values():
            result.update(_walk_references(child))
        return result
    if isinstance(value, list):
        result = set()
        for child in value:
            result.update(_walk_references(child))
        return result
    return set()


def _runtime_service(reference: str, account: str) -> tuple[str, str]:
    prefix = f"keychain://{account}/"
    name = reference.removeprefix(prefix)
    if not reference.startswith(prefix) or _ITEM_NAME.fullmatch(name) is None:
        raise ProviderRuntimeAliasError("runtime Keychain coordinate is invalid")
    return f"com.administrateme.{account}.{name}", name


def prepare_provider_runtime_alias(
    paths: DeploymentPaths,
    *,
    authorization_reviewed: bool,
    put_runtime: Callable[[str, str, str], None] = put_runtime_password,
    get_runtime: Callable[[str, str], str] = get_runtime_password,
    delete_runtime: Callable[[str, str], None] = delete_runtime_password,
) -> dict[str, Any]:
    """Copy and alias exactly one unattached reviewed principal grant."""

    if authorization_reviewed is not True:
        raise ProviderRuntimeAliasError(
            "live provider runtime-alias authorization is required"
        )
    _adapter_payload, adapters = _private_json(
        paths.instance_dir / "config" / "adapters.json"
    )
    _mapping_payload, mapping = _private_json(
        paths.instance_dir / "config" / "member-mapping.json"
    )
    try:
        enrollment = discover_readonly_google_enrollment(
            paths,
            adapters,
            household_mapping=mapping,
        )
    except ProviderBindingTransitionError as exc:
        raise ProviderRuntimeAliasError(
            "reviewed Google enrollment cannot be resolved exactly"
        ) from exc
    if enrollment is None:
        raise ProviderRuntimeAliasError(
            "exactly one unattached reviewed Google enrollment is required"
        )
    logical = enrollment["oauth_credentials_ref"]
    if not isinstance(logical, str) or not logical.startswith("keychain://"):
        raise ProviderRuntimeAliasError("reviewed Google credential is not in Keychain")
    alias_payload, document, aliases = _alias_state(paths)
    store = secret_store(paths)
    if logical in aliases:
        store.get(logical)
        return {
            "status": "provider_runtime_alias_already_ready",
            "alias_count": len(aliases),
            "credentials_changed": False,
            "capabilities_changed": False,
            "provider_called": False,
            "content_exposed": False,
        }

    try:
        credential = store.get(logical)
    except RuntimeError as exc:
        raise ProviderRuntimeAliasError(
            "reviewed Google credential cannot be read by the enrollment process"
        ) from exc
    account = paths.keychain_account()
    prefix = f"keychain://{account}/"
    digest = hashlib.sha256(logical.encode("utf-8")).hexdigest()[:24]
    target = f"{prefix}{_TARGET_PREFIX}{document['runtime_tag']}-{digest}"
    if target in aliases.values():
        raise ProviderRuntimeAliasError("provider runtime alias target already exists")
    service, _target_name = _runtime_service(target, account)
    encoded = base64.urlsafe_b64encode(credential).decode("ascii")
    alias_path = paths.instance_dir / "config" / KEYCHAIN_ALIAS_FILENAME
    published = False
    try:
        put_runtime(service, account, encoded)
        if get_runtime(service, account) != encoded:
            raise ProviderRuntimeAliasError(
                "provider runtime Keychain copy did not verify"
            )
        updated = {
            **document,
            "aliases": {**aliases, logical: target},
        }
        atomic_write(
            alias_path,
            json.dumps(updated, indent=2, sort_keys=True).encode("utf-8"),
            mode=0o600,
        )
        published = True
        if secret_store(paths).get(logical) != credential:
            raise ProviderRuntimeAliasError("published provider runtime alias drifted")
    except BaseException:
        if published:
            if alias_payload is None:
                alias_path.unlink(missing_ok=True)
            else:
                atomic_write(alias_path, alias_payload, mode=0o600)
        try:
            delete_runtime(service, account)
        except BaseException:
            pass
        raise
    return {
        "status": "provider_runtime_alias_ready",
        "alias_count": len(aliases) + 1,
        "credentials_changed": False,
        "capabilities_changed": False,
        "provider_called": False,
        "content_exposed": False,
    }


def rollback_provider_runtime_alias(
    paths: DeploymentPaths,
    *,
    authorization_reviewed: bool,
    delete_runtime: Callable[[str, str], None] = delete_runtime_password,
) -> dict[str, Any]:
    """Remove the one unused provider runtime alias and its copied envelope."""

    if authorization_reviewed is not True:
        raise ProviderRuntimeAliasError(
            "live provider runtime-alias authorization is required"
        )
    _payload, document, aliases = _alias_state(paths)
    candidates = [
        (source, target)
        for source, target in aliases.items()
        if target.removeprefix(f"keychain://{paths.keychain_account()}/").startswith(
            _TARGET_PREFIX
        )
    ]
    if len(candidates) != 1:
        raise ProviderRuntimeAliasError(
            "exactly one provider runtime alias is required for rollback"
        )
    logical, target = candidates[0]
    _adapter_payload, adapters = _private_json(
        paths.instance_dir / "config" / "adapters.json"
    )
    if logical in _walk_references(adapters):
        raise ProviderRuntimeAliasError(
            "provider runtime alias is still referenced by active adapters"
        )
    remaining = dict(aliases)
    remaining.pop(logical)
    alias_path = paths.instance_dir / "config" / KEYCHAIN_ALIAS_FILENAME
    atomic_write(
        alias_path,
        json.dumps({**document, "aliases": remaining}, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
        mode=0o600,
    )
    account = paths.keychain_account()
    service, _target_name = _runtime_service(target, account)
    delete_runtime(service, account)
    return {
        "status": "provider_runtime_alias_rolled_back",
        "alias_count": len(remaining),
        "credentials_changed": False,
        "capabilities_changed": False,
        "provider_called": False,
        "content_exposed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--authorize-live-runtime-alias", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    paths = DeploymentPaths.from_root(args.instance_root, production=True)
    try:
        operation = (
            rollback_provider_runtime_alias
            if args.rollback
            else prepare_provider_runtime_alias
        )
        result = operation(
            paths,
            authorization_reviewed=args.authorize_live_runtime_alias,
        )
    except (ProviderRuntimeAliasError, RuntimeError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "content_exposed": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
