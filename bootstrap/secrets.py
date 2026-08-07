"""Secret providers used by bootstrap and service launchers."""

from __future__ import annotations

import base64
import json
import os
from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError

from adminme.lib.macos_keychain import (
    delete_runtime_password,
    get_runtime_password,
    put_runtime_password,
)
from bootstrap.contracts import DeploymentPaths, atomic_write

KEYCHAIN_ALIAS_FILENAME = "keychain-runtime-aliases.json"


class SecretStore(Protocol):
    provider_name: str

    def put(self, name: str, value: bytes) -> str: ...

    def get(self, reference: str) -> bytes: ...

    def has(self, reference: str) -> bool: ...

    def delete(self, reference: str) -> None: ...


class MacOSKeychainStore:
    """Login-Keychain provider. Values never cross a process argument."""

    provider_name = "macos-keychain"

    def __init__(self, paths: DeploymentPaths) -> None:
        self.account = paths.keychain_account()
        self._prefix = f"keychain://{self.account}/"
        self._aliases = self._load_aliases(
            paths.instance_dir / "config" / KEYCHAIN_ALIAS_FILENAME
        )

    def _load_aliases(self, path: os.PathLike[str]) -> dict[str, str]:
        alias_path = os.fspath(path)
        if not os.path.exists(alias_path):
            return {}
        if os.path.islink(alias_path):
            raise RuntimeError("refusing a symlinked Keychain runtime alias map")
        try:
            with open(alias_path, encoding="utf-8") as handle:
                document = json.loads(handle.read())
            raw_aliases = document["aliases"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Keychain runtime alias map is invalid") from exc
        if document.get("schema_version") != 1 or not isinstance(raw_aliases, dict):
            raise RuntimeError("Keychain runtime alias map is invalid")
        aliases: dict[str, str] = {}
        for source, target in raw_aliases.items():
            if (
                not isinstance(source, str)
                or not isinstance(target, str)
                or not source.startswith(self._prefix)
                or not target.startswith(self._prefix)
                or source == target
                or not source.removeprefix(self._prefix)
                or not target.removeprefix(self._prefix)
                or "/" in source.removeprefix(self._prefix)
                or "/" in target.removeprefix(self._prefix)
            ):
                raise RuntimeError(
                    "Keychain runtime alias map crosses an instance boundary"
                )
            aliases[source] = target
        for source in aliases:
            self._resolve_reference(source, aliases=aliases)
        return aliases

    def _resolve_reference(
        self, reference: str, *, aliases: dict[str, str] | None = None
    ) -> str:
        if not reference.startswith(self._prefix):
            raise RuntimeError("secret reference belongs to another instance")
        mapping = self._aliases if aliases is None else aliases
        resolved = reference
        visited: set[str] = set()
        while resolved in mapping:
            if resolved in visited:
                raise RuntimeError("Keychain runtime alias map contains a cycle")
            visited.add(resolved)
            resolved = mapping[resolved]
        return resolved

    def _service(self, name: str) -> str:
        return f"com.administrateme.{self.account}.{name}"

    def put(self, name: str, value: bytes) -> str:
        logical_reference = f"{self._prefix}{name}"
        physical_reference = self._resolve_reference(logical_reference)
        physical_name = physical_reference.removeprefix(self._prefix)
        encoded = base64.urlsafe_b64encode(value).decode("ascii")
        if physical_reference != logical_reference:
            put_runtime_password(self._service(physical_name), self.account, encoded)
        else:
            keyring.set_password(self._service(physical_name), self.account, encoded)
        return logical_reference

    def get(self, reference: str) -> bytes:
        physical_reference = self._resolve_reference(reference)
        name = physical_reference.removeprefix(self._prefix)
        encoded: str | None
        if physical_reference != reference:
            encoded = get_runtime_password(self._service(name), self.account)
        else:
            encoded = keyring.get_password(self._service(name), self.account)
        if encoded is None:
            raise RuntimeError("required Keychain item is unavailable")
        return base64.urlsafe_b64decode(encoded.encode("ascii"))

    def has(self, reference: str) -> bool:
        try:
            self.get(reference)
        except RuntimeError:
            return False
        return True

    def delete(self, reference: str) -> None:
        physical_reference = self._resolve_reference(reference)
        name = physical_reference.removeprefix(self._prefix)
        if physical_reference != reference:
            delete_runtime_password(self._service(name), self.account)
            return
        try:
            keyring.delete_password(self._service(name), self.account)
        except PasswordDeleteError:
            pass


class TestFileSecretStore:
    """Explicitly test-only provider for credential-free CI rehearsals."""

    provider_name = "test-file"

    def __init__(self, paths: DeploymentPaths) -> None:
        if os.environ.get("ADMINME_TEST_MODE") != "1":
            raise RuntimeError("test secret provider requires ADMINME_TEST_MODE=1")
        self.paths = paths
        self.path = paths.root / ".test-keychain.json"

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text("utf-8"))

    def put(self, name: str, value: bytes) -> str:
        values = self._read()
        values[name] = base64.urlsafe_b64encode(value).decode("ascii")
        atomic_write(
            self.path,
            json.dumps(values, sort_keys=True).encode("utf-8"),
            mode=0o600,
        )
        return f"test-secret://{self.paths.keychain_account()}/{name}"

    def get(self, reference: str) -> bytes:
        prefix = f"test-secret://{self.paths.keychain_account()}/"
        if not reference.startswith(prefix):
            raise RuntimeError("test secret reference belongs to another instance")
        name = reference.removeprefix(prefix)
        value = self._read().get(name)
        if value is None:
            raise RuntimeError("required test secret is unavailable")
        return base64.urlsafe_b64decode(value.encode("ascii"))

    def has(self, reference: str) -> bool:
        try:
            self.get(reference)
        except RuntimeError:
            return False
        return True

    def delete(self, reference: str) -> None:
        prefix = f"test-secret://{self.paths.keychain_account()}/"
        if not reference.startswith(prefix):
            raise RuntimeError("test secret reference belongs to another instance")
        name = reference.removeprefix(prefix)
        values = self._read()
        values.pop(name, None)
        if values:
            atomic_write(
                self.path,
                json.dumps(values, sort_keys=True).encode("utf-8"),
                mode=0o600,
            )
        else:
            self.path.unlink(missing_ok=True)


def secret_store(paths: DeploymentPaths, *, test_mode: bool = False) -> SecretStore:
    return TestFileSecretStore(paths) if test_mode else MacOSKeychainStore(paths)


def reference_for(paths: DeploymentPaths, name: str, *, test_mode: bool) -> str:
    scheme = "test-secret" if test_mode else "keychain"
    return f"{scheme}://{paths.keychain_account()}/{name}"


def put_if_absent(store: SecretStore, name: str, reference: str, size: int) -> str:
    if reference and store.has(reference):
        return reference
    return store.put(name, os.urandom(size))
