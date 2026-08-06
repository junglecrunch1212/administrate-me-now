"""Resolve adapter credential documents without placing secrets in config."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from typing import Any


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

        remainder = reference.removeprefix("keychain://")
        account, separator, name = remainder.partition("/")
        if not separator or not account or not name or "/" in name:
            raise ValueError("invalid Keychain credential reference")
        encoded = keyring.get_password(
            f"com.administrateme.{account}.{name}", account
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
