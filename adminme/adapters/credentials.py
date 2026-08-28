"""Resolve adapter credential documents without placing secrets in config."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from typing import Any

from adminme.lib.macos_keychain import (
    configured_alias_path,
    get_runtime_password,
    resolve_runtime_alias,
)


def credential_member_digest(member_id: str) -> str:
    """Return the non-identifying owner coordinate stored with OAuth grants."""
    normalized = member_id.strip()
    if not normalized:
        raise ValueError("member_id must be a non-empty string")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def assert_installed_user_binding(
    document: dict[str, Any], *, member_id: str, provider_account_id: str
) -> None:
    """Fail closed when a bound installed-user grant is assigned elsewhere.

    Pre-binding Keychain documents remain readable for migration. New OAuth
    enrollment documents contain both binding fields, so either field being
    present makes the complete binding mandatory.
    """
    owner_digest = document.get("owner_member_digest")
    account_id = document.get("provider_account_id")
    if owner_digest is None and account_id is None:
        return
    expected_account = provider_account_id.strip().casefold()
    if (
        not expected_account
        or owner_digest != credential_member_digest(member_id)
        or not isinstance(account_id, str)
        or account_id.strip().casefold() != expected_account
    ):
        raise RuntimeError(
            "installed-user credential binding does not match the adapter owner"
        )


def resolve_json_credentials(reference: str) -> dict[str, Any]:
    """Resolve JSON from a supported secret-provider reference.

    ``op://`` preserves the existing 1Password integration. ``keychain://``
    reads the login-Keychain item written by :mod:`bootstrap.secrets`; the
    secret is never passed through an argument, environment variable, or
    configuration file.
    """
    if reference.startswith("op://"):
        op_bin = shutil.which("op")
        if op_bin is None:
            raise RuntimeError(
                "1Password CLI `op` not on PATH; cannot resolve credential reference"
            )
        proc = subprocess.run(
            [op_bin, "read", "--no-newline", reference],
            check=True,
            capture_output=True,
            timeout=15,
        )
        raw = proc.stdout
    elif reference.startswith("keychain://"):
        import keyring

        alias_path = configured_alias_path()
        physical_reference = (
            resolve_runtime_alias(reference, alias_path)
            if alias_path is not None
            else reference
        )
        remainder = physical_reference.removeprefix("keychain://")
        account, separator, name = remainder.partition("/")
        if not separator or not account or not name or "/" in name:
            raise ValueError("invalid Keychain credential reference")
        service = f"com.administrateme.{account}.{name}"
        encoded = (
            get_runtime_password(service, account)
            if physical_reference != reference
            else keyring.get_password(service, account)
        )
        if encoded is None:
            raise RuntimeError("required Keychain credential is unavailable")
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    else:
        raise ValueError("credential reference must use op:// or keychain://")

    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("credential document must be a JSON object")
    return value
