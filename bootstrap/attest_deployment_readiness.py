"""Owner-only writer for content-free deployment readiness attestations.

This command records that the owner completed physical/device/provider checks
that software cannot safely infer.  It does not perform those checks, change a
Tailscale node, grant a permission, touch a credential, or enable a provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, atomic_write


class ReadinessAttestationError(RuntimeError):
    pass


def build_deployment_binding(paths: DeploymentPaths) -> dict[str, str]:
    """Fingerprint this host, instance, reviewed release, and routing config."""

    pointer = paths.app_dir / "current-release.json"
    try:
        release_commit = str(json.loads(pointer.read_text("utf-8"))["commit"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ReadinessAttestationError(
            "an immutable active-release pointer is required"
        ) from exc
    if re.fullmatch(r"[0-9a-f]{40}", release_commit) is None:
        raise ReadinessAttestationError("active release commit is invalid")
    config_paths = (
        paths.instance_dir / "config" / "instance.yaml",
        paths.instance_dir / "config" / "member-mapping.json",
        paths.instance_dir / "config" / "adapters.json",
        paths.instance_dir / "bridges" / "member-mapping.json",
    )
    config_digest = hashlib.sha256()
    for path in config_paths:
        config_digest.update(path.name.encode())
        if path.is_file() and not path.is_symlink():
            config_digest.update(path.read_bytes())
        else:
            config_digest.update(b"<absent>")
    host_material = f"{os.uname().sysname}|{uuid.getnode()}".encode()
    return {
        "release_commit": release_commit,
        "instance_fingerprint": hashlib.sha256(
            str(paths.root.resolve()).encode()
        ).hexdigest(),
        "host_fingerprint": hashlib.sha256(host_material).hexdigest(),
        "configuration_fingerprint": config_digest.hexdigest(),
    }


def build_attestation(
    paths: DeploymentPaths,
    *,
    confirmations: dict[str, bool],
) -> dict[str, Any]:
    required = {
        "distinct_identities",
        "separate_admin",
        "filevault_unlock",
        "automatic_login_disabled",
        "backup_authenticated",
        "restore_rehearsed",
        "prior_release_retained",
        "automatic_rollback_eligible",
    }
    missing = sorted(name for name in required if confirmations.get(name) is not True)
    if missing:
        raise ReadinessAttestationError(
            "every physical readiness confirmation is required: " + ", ".join(missing)
        )
    mapping_path = paths.instance_dir / "config" / "member-mapping.json"
    if not mapping_path.is_file():
        raise ReadinessAttestationError("reviewed member configuration is missing")
    mapping = json.loads(mapping_path.read_text("utf-8"))
    portal_members = sorted(
        str(member["member_id"])
        for member in mapping.get("members", [])
        if isinstance(member, dict)
        and isinstance(member.get("member_id"), str)
        and isinstance(member.get("tailscale_identity"), str)
        and member["tailscale_identity"].strip()
    )
    if not portal_members:
        raise ReadinessAttestationError("no configured portal member identity exists")
    now = datetime.now(timezone.utc).isoformat()
    return {
        "contract": "deployment-readiness-v1",
        "deployment_binding": build_deployment_binding(paths),
        "identity_acceptance": {
            "member_ids": portal_members,
            "distinct_identities": True,
            "tested_at": now,
        },
        "recovery": {
            "separate_admin_available": True,
            "filevault_unlock_tested": True,
            "automatic_login_disabled": True,
            "tested_at": now,
        },
        "backup_rollback": {
            "encrypted_backup_authenticated": True,
            "restore_rehearsed": True,
            "prior_release_retained": True,
            "automatic_rollback_eligible": True,
            "tested_at": now,
        },
        "provider_effect_window": {"started_at": now},
        "content_exposed": False,
    }


def record_attestation(
    paths: DeploymentPaths,
    *,
    confirmations: dict[str, bool],
) -> dict[str, Any]:
    value = build_attestation(paths, confirmations=confirmations)
    target = paths.instance_dir / "config" / "deployment-readiness.json"
    atomic_write(
        target, json.dumps(value, indent=2, sort_keys=True).encode(), mode=0o600
    )
    return {
        "status": "recorded",
        "contract": value["contract"],
        "portal_member_count": len(value["identity_acceptance"]["member_ids"]),
        "content_exposed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record owner-completed, content-free deployment readiness checks."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    for flag in (
        "distinct-identities",
        "separate-admin",
        "filevault-unlock",
        "automatic-login-disabled",
        "backup-authenticated",
        "restore-rehearsed",
        "prior-release-retained",
        "automatic-rollback-eligible",
    ):
        parser.add_argument(f"--confirm-{flag}", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    confirmations = {
        key.removeprefix("confirm_"): bool(value)
        for key, value in vars(args).items()
        if key.startswith("confirm_")
    }
    paths = DeploymentPaths.from_root(args.instance_root, production=True)
    result = record_attestation(paths, confirmations=confirmations)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "ReadinessAttestationError",
    "build_attestation",
    "build_deployment_binding",
    "record_attestation",
]
