"""Secret-safe macOS Keychain access for runtime-bound alias items.

Homebrew Python is ad-hoc signed.  A Keychain item created by that process can
verify in the creating process yet still prompt when a fresh launchd process
uses the same interpreter.  Runtime-bound aliases are therefore owned and read
by Apple's signed ``security`` client.  Values cross only anonymous standard-I/O
pipes; they never enter arguments, environment variables, files, or logs.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess


SECURITY_CLI = "/usr/bin/security"
SECURITY_TIMEOUT_SECONDS = 10
KEYCHAIN_ALIAS_FILENAME = "keychain-runtime-aliases.json"
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,240}$")
_ENVELOPE_PREFIX = "AMKC1"
_MANIFEST_PATTERN = re.compile(
    rf"^{_ENVELOPE_PREFIX}:(?P<generation>[0-9a-f]{{16}}):"
    r"(?P<count>[1-9][0-9]{0,3}):(?P<digest>[0-9a-f]{64})$"
)
# ``security add-generic-password -w`` uses Apple's prompt reader even when its
# input is an anonymous pipe.  That reader silently truncates around 128
# characters.  Keep every fragment and the manifest comfortably below it.
_PROMPT_VALUE_LIMIT = 120
_PART_CHARACTERS = 96
_MAX_SECRET_BYTES = 64 * 1024
_MAX_PARTS = 1000


class RuntimeKeychainError(RuntimeError):
    """A runtime-bound Keychain operation failed without exposing its value."""


def _security_environment() -> dict[str, str]:
    """Give Apple's CLI the login-home coordinate hidden from controllers."""

    try:
        login_home = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError) as exc:
        raise RuntimeKeychainError(
            "runtime-bound Keychain login coordinate is unavailable"
        ) from exc
    if not login_home or not Path(login_home).is_absolute():
        raise RuntimeKeychainError("runtime-bound Keychain login coordinate is invalid")
    environment = os.environ.copy()
    environment["HOME"] = login_home
    return environment


def _validate_label(value: str) -> None:
    if not _LABEL_PATTERN.fullmatch(value):
        raise RuntimeKeychainError("runtime-bound Keychain label is invalid")


def _get_single(service: str, account: str) -> str:
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
            env=_security_environment(),
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


def _put_single(service: str, account: str, value: str) -> None:
    _validate_label(service)
    _validate_label(account)
    if not value or len(value) > _PROMPT_VALUE_LIMIT or "\n" in value or "\r" in value:
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
            env=_security_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeKeychainError(
            "runtime-bound Keychain write did not complete"
        ) from exc
    if completed.returncode:
        raise RuntimeKeychainError("runtime-bound Keychain write failed")


def _delete_single(service: str, account: str) -> None:
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
            env=_security_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeKeychainError(
            "runtime-bound Keychain delete did not complete"
        ) from exc
    if completed.returncode not in (0, 44):
        raise RuntimeKeychainError("runtime-bound Keychain delete failed")


def _part_service(service: str, generation: str, index: int) -> str:
    part = f"{service}.amkc1-{generation}-{index:04d}"
    _validate_label(part)
    return part


def _new_generation() -> str:
    return os.urandom(8).hex()


def _parse_manifest(service: str, value: str) -> tuple[str, int, str] | None:
    if not value.startswith(f"{_ENVELOPE_PREFIX}:"):
        return None
    match = _MANIFEST_PATTERN.fullmatch(value)
    if match is None:
        raise RuntimeKeychainError("runtime-bound Keychain envelope is invalid")
    count = int(match.group("count"))
    if count > _MAX_PARTS:
        raise RuntimeKeychainError("runtime-bound Keychain envelope is invalid")
    # Validate the derived label before accepting the manifest.
    _part_service(service, match.group("generation"), count - 1)
    return match.group("generation"), count, match.group("digest")


def get_runtime_password(service: str, account: str) -> str:
    """Read and verify one CLI-owned, prompt-safe secret envelope."""

    root = _get_single(service, account)
    manifest = _parse_manifest(service, root)
    if manifest is None:
        # Compatibility for short runtime aliases created by the first signed-
        # client revision. New writes always use the versioned envelope.
        return root
    generation, count, expected_digest = manifest
    encoded = "".join(
        _get_single(_part_service(service, generation, index), account)
        for index in range(count)
    )
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RuntimeKeychainError(
            "runtime-bound Keychain envelope is invalid"
        ) from exc
    if (
        not value
        or len(raw) > _MAX_SECRET_BYTES
        or hashlib.sha256(raw).hexdigest() != expected_digest
    ):
        raise RuntimeKeychainError("runtime-bound Keychain envelope is invalid")
    return value


def put_runtime_password(service: str, account: str, value: str) -> None:
    """Atomically publish a prompt-safe envelope without exposing its value."""

    _validate_label(service)
    _validate_label(account)
    if not value:
        raise RuntimeKeychainError("runtime-bound Keychain value is invalid")
    raw = value.encode("utf-8")
    if len(raw) > _MAX_SECRET_BYTES:
        raise RuntimeKeychainError("runtime-bound Keychain value is invalid")
    encoded = base64.b64encode(raw).decode("ascii")
    chunks = tuple(
        encoded[offset : offset + _PART_CHARACTERS]
        for offset in range(0, len(encoded), _PART_CHARACTERS)
    )
    if not chunks or len(chunks) > _MAX_PARTS:
        raise RuntimeKeychainError("runtime-bound Keychain value is invalid")

    previous: tuple[str, int, str] | None = None
    try:
        previous = _parse_manifest(service, _get_single(service, account))
    except RuntimeKeychainError:
        # A missing item or a legacy/truncated candidate copy has no fragments
        # that this generation is authorized to remove.
        previous = None

    generation = _new_generation()
    part_services = tuple(
        _part_service(service, generation, index) for index in range(len(chunks))
    )
    written: list[str] = []
    try:
        for part, chunk in zip(part_services, chunks, strict=True):
            _put_single(part, account, chunk)
            written.append(part)
        manifest = (
            f"{_ENVELOPE_PREFIX}:{generation}:{len(chunks)}:"
            f"{hashlib.sha256(raw).hexdigest()}"
        )
        _put_single(service, account, manifest)
    except RuntimeKeychainError:
        for part in written:
            try:
                _delete_single(part, account)
            except RuntimeKeychainError:
                pass
        raise

    if previous is not None:
        old_generation, old_count, _old_digest = previous
        for index in range(old_count):
            try:
                _delete_single(_part_service(service, old_generation, index), account)
            except RuntimeKeychainError:
                # The new root is already committed and verified by callers;
                # stale-fragment cleanup cannot roll that transaction back.
                pass


def delete_runtime_password(service: str, account: str) -> None:
    """Delete one exact envelope without returning Keychain diagnostics."""

    manifest: tuple[str, int, str] | None = None
    try:
        manifest = _parse_manifest(service, _get_single(service, account))
    except RuntimeKeychainError:
        pass
    _delete_single(service, account)
    if manifest is not None:
        generation, count, _digest = manifest
        for index in range(count):
            _delete_single(_part_service(service, generation, index), account)


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
