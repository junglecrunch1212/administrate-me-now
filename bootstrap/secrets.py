"""Secret providers used by bootstrap and service launchers."""

from __future__ import annotations

import base64
import json
import os
from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError

from bootstrap.contracts import DeploymentPaths, atomic_write


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

    def _service(self, name: str) -> str:
        return f"com.administrateme.{self.account}.{name}"

    def put(self, name: str, value: bytes) -> str:
        encoded = base64.urlsafe_b64encode(value).decode("ascii")
        keyring.set_password(self._service(name), self.account, encoded)
        return f"keychain://{self.account}/{name}"

    def get(self, reference: str) -> bytes:
        prefix = f"keychain://{self.account}/"
        if not reference.startswith(prefix):
            raise RuntimeError("secret reference belongs to another instance")
        name = reference.removeprefix(prefix)
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
        prefix = f"keychain://{self.account}/"
        if not reference.startswith(prefix):
            raise RuntimeError("secret reference belongs to another instance")
        name = reference.removeprefix(prefix)
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
