"""Secret-safe macOS Keychain access for runtime-bound alias items.

Homebrew Python is ad-hoc signed.  A Keychain item created by that process can
verify in the creating process yet still prompt when a fresh launchd process
uses the same interpreter.  Runtime-bound aliases are therefore owned and read
by Apple's signed ``security`` client.  Values cross only anonymous standard-I/O
pipes; they never enter arguments, environment variables, files, or logs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


SECURITY_CLI = "/usr/bin/security"
SECURITY_TIMEOUT_SECONDS = 10
KEYCHAIN_ALIAS_FILENAME = "keychain-runtime-aliases.json"
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,240}$")


class RuntimeKeychainError(RuntimeError):
    """A runtime-bound Keychain operation failed without exposing its value."""


def _validate_label(value: str) -> None:
    if not _LABEL_PATTERN.fullmatch(value):
        raise RuntimeKeychainError("runtime-bound Keychain label is invalid")


def get_runtime_password(service: str, account: str) -> str:
    """Read one CLI-owned item through a fresh Apple-signed process."""

    _validate_label(service)
    _validate_label(account)
    try:
        completed = subprocess.run(
            [
                SECURITY_CLI,
                "find-generic-password",
                "-a",
                account,
                "-s",
                service,
                "-w",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=SECURITY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeKeychainError(
            "runtime-bound Keychain read did not complete"
        ) from exc
    if completed.returncode:
        raise RuntimeKeychainError("runtime-bound Keychain item is unavailable")
    value = completed.stdout.removesuffix("\n")
    if not value or "\n" in value or "\r" in value:
        raise RuntimeKeychainError("runtime-bound Keychain item is invalid")
    return value


def put_runtime_password(service: str, account: str, value: str) -> None:
    """Create or replace one CLI-owned item without placing value in argv."""

    _validate_label(service)
    _validate_label(account)
    if not value or "\n" in value or "\r" in value:
        raise RuntimeKeychainError("runtime-bound Keychain value is invalid")
    try:
        completed = subprocess.run(
            [
                SECURITY_CLI,
                "add-generic-password",
                "-U",
                "-a",
                account,
                "-s",
                service,
                "-w",
            ],
            input=f"{value}\n{value}\n",
            text=True,
            capture_output=True,
            check=False,
            timeout=SECURITY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeKeychainError(
            "runtime-bound Keychain write did not complete"
        ) from exc
    if completed.returncode:
        raise RuntimeKeychainError("runtime-bound Keychain write failed")


def delete_runtime_password(service: str, account: str) -> None:
    """Delete one exact CLI-owned item without returning Keychain diagnostics."""

    _validate_label(service)
    _validate_label(account)
    try:
        completed = subprocess.run(
            [
                SECURITY_CLI,
                "delete-generic-password",
                "-a",
                account,
                "-s",
                service,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=SECURITY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeKeychainError(
            "runtime-bound Keychain delete did not complete"
        ) from exc
    if completed.returncode not in (0, 44):
        raise RuntimeKeychainError("runtime-bound Keychain delete failed")


def resolve_runtime_alias(reference: str, alias_path: Path) -> str:
    """Resolve an instance-scoped logical reference through a validated map."""

    remainder = reference.removeprefix("keychain://")
    account, separator, name = remainder.partition("/")
    if (
        not reference.startswith("keychain://")
        or not separator
        or not account
        or not name
        or "/" in name
    ):
        raise RuntimeKeychainError("Keychain reference is invalid")
    _validate_label(account)
    _validate_label(name)
    if alias_path.is_symlink():
        raise RuntimeKeychainError("refusing a symlinked Keychain alias map")
    if not alias_path.exists():
        return reference
    try:
        document = json.loads(alias_path.read_text("utf-8"))
        raw = document["aliases"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeKeychainError("Keychain runtime alias map is invalid") from exc
    if document.get("schema_version") != 1 or not isinstance(raw, dict):
        raise RuntimeKeychainError("Keychain runtime alias map is invalid")
    prefix = f"keychain://{account}/"
    aliases: dict[str, str] = {}
    for source, target in raw.items():
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or not source.startswith(prefix)
            or not target.startswith(prefix)
            or source == target
        ):
            raise RuntimeKeychainError(
                "Keychain runtime alias map crosses an instance boundary"
            )
        _validate_label(source.removeprefix(prefix))
        _validate_label(target.removeprefix(prefix))
        aliases[source] = target

    resolved = reference
    visited: set[str] = set()
    while resolved in aliases:
        if resolved in visited:
            raise RuntimeKeychainError("Keychain runtime alias map contains a cycle")
        visited.add(resolved)
        resolved = aliases[resolved]
    return resolved


def configured_alias_path() -> Path | None:
    """Return the production alias path when running inside the central service."""

    instance_dir = os.environ.get("ADMINME_INSTANCE_DIR")
    if not instance_dir:
        return None
    return Path(instance_dir) / "config" / KEYCHAIN_ALIAS_FILENAME
