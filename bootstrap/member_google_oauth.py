"""Provision the member Settings Google web-client configuration.

This operation installs only an OAuth client, never a member's Google grant.
Members subsequently authenticate and consent from their own Settings session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from adminme.google_oauth_transport import (
    GoogleOAuthError,
    dashboard_redirect_uri,
    validate_web_client,
)
from bootstrap.candidate_readiness import deployment_operation_lock
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.secrets import secret_store

CONTRACT = "member-google-web-oauth-v1"
CONFIG_FILENAME = "member-google-oauth.json"


def _private_json(path: Path) -> Any:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > 65536
            or metadata.st_nlink != 1
        ):
            raise ValueError("unsafe file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ValueError("file changed")
            return json.loads(handle.read(65537))
    except Exception:
        raise GoogleOAuthError(
            "Google OAuth configuration must be an owner-only regular JSON file"
        ) from None


def configuration_path(paths: DeploymentPaths) -> Path:
    return paths.instance_dir / "config" / CONFIG_FILENAME


def load_configuration(paths: DeploymentPaths) -> dict[str, str] | None:
    path = configuration_path(paths)
    if not path.exists() and not path.is_symlink():
        return None
    document = _private_json(path)
    if (
        not isinstance(document, dict)
        or set(document) != {"contract", "client_ref", "redirect_uri"}
        or document.get("contract") != CONTRACT
        or not isinstance(document.get("client_ref"), str)
        or not re.fullmatch(
            re.escape(
                f"keychain://{paths.keychain_account()}/google-member-web-client-"
            )
            + r"[0-9a-f]{24}",
            document["client_ref"],
        )
        or not isinstance(document.get("redirect_uri"), str)
    ):
        raise GoogleOAuthError("Member Google OAuth configuration is invalid")
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(document["redirect_uri"])
    except ValueError:
        raise GoogleOAuthError("Member Google OAuth callback is invalid") from None
    if document["redirect_uri"] != dashboard_redirect_uri(
        f"{parsed.scheme}://{parsed.netloc}"
    ):
        raise GoogleOAuthError("Member Google OAuth callback is invalid")
    return dict(document)


def provision(
    client_file: Path,
    dashboard_origin: str,
    *,
    paths: DeploymentPaths | None = None,
    replace_existing: bool = False,
) -> dict[str, str]:
    """Install an exact web client under the shared maintenance exclusion lock."""
    paths = paths or DeploymentPaths.default()
    redirect_uri = dashboard_redirect_uri(dashboard_origin)
    client = validate_web_client(_private_json(client_file), redirect_uri)
    payload = json.dumps(client, sort_keys=True).encode("utf-8")
    # Rotation uses a new item; rollback to an older config retains its secret.
    name = "google-member-web-client-" + hashlib.sha256(payload).hexdigest()[:24]
    destination = configuration_path(paths)
    config_parent = destination.parent
    if config_parent.exists() and (
        config_parent.is_symlink()
        or config_parent.resolve() != config_parent.absolute()
        or config_parent.stat().st_uid != os.getuid()
        or stat.S_IMODE(config_parent.stat().st_mode) & 0o077
    ):
        raise GoogleOAuthError(
            "Member Google OAuth configuration directory is not private"
        )
    store = secret_store(paths)
    with deployment_operation_lock(paths):
        prior = load_configuration(paths)
        expected_reference = f"keychain://{paths.keychain_account()}/{name}"
        desired = {
            "contract": CONTRACT,
            "client_ref": expected_reference,
            "redirect_uri": redirect_uri,
        }
        if prior is not None and prior != desired and not replace_existing:
            raise GoogleOAuthError(
                "Replacing the configured Google web client requires --replace"
            )
        try:
            reference = store.put(name, payload)
        except Exception:
            raise GoogleOAuthError(
                "Google web-client credentials could not be stored securely"
            ) from None
        if reference != expected_reference:
            raise GoogleOAuthError(
                "Google web client requires this instance's Keychain"
            )
        atomic_write(
            destination,
            json.dumps(desired, sort_keys=True, indent=2).encode("utf-8") + b"\n",
            mode=0o600,
        )
    return desired


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-file", type=Path, required=True)
    parser.add_argument("--dashboard-origin", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        provision(
            args.client_file, args.dashboard_origin, replace_existing=args.replace
        )
    except GoogleOAuthError as exc:
        parser.exit(1, f"{exc}\n")
    print(
        "Member Google OAuth client configured. Restart through the supported runtime procedure."
    )


if __name__ == "__main__":
    main()
