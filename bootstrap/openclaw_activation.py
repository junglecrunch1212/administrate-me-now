"""Server-side, secret-safe activation of the pinned OpenClaw provider boundary."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from bootstrap.contracts import (
    DeploymentPaths,
    atomic_write,
    load_versions,
    repository_root,
)
from bootstrap.openclaw_cron import OpenClawCronReconciler
from bootstrap.openclaw_install import resolve_reviewed_openclaw
from bootstrap.secrets import secret_store


def activation_path(paths: DeploymentPaths) -> Path:
    return paths.instance_dir / "config" / "openclaw-activation.json"


def model_provider_configured(paths: DeploymentPaths) -> bool:
    path = activation_path(paths)
    if not path.is_file() or path.is_symlink():
        return False
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        data.get("schema_version") == 1
        and data.get("configured") is True
        and data.get("openclaw_version") == load_versions()["openclaw"]["version"]
        and data.get("probe") == "passed"
        and data.get("workspace_configured") is True
        and data.get("cron_reconciled") is True
    )


def activate(
    paths: DeploymentPaths,
    *,
    root: Path | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Run OpenClaw-owned interactive auth, probe it, then write metadata only."""

    executable = resolve_reviewed_openclaw(paths, test_mode=test_mode)
    expected = load_versions()["openclaw"]["version"]
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if expected not in version:
        raise RuntimeError(
            "OpenClaw version is outside the tested compatibility contract"
        )

    print("OpenClaw will request provider credentials in this server terminal.")
    print(
        "Never enter them in the member portal or an AdministrateMe-owned form; a provider-owned OAuth page is allowed."
    )
    subprocess.run(
        [executable, "configure", "--section", "model", "--section", "channels"],
        check=True,
    )
    workspace = paths.instance_dir / "openclaw" / "workspace"
    required_workspace_files = ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md")
    if not all((workspace / name).is_file() for name in required_workspace_files):
        raise RuntimeError(
            "the compiled AdministrateMe OpenClaw workspace is incomplete; rerun bootstrap before provider activation"
        )
    subprocess.run(
        [
            executable,
            "config",
            "set",
            "agents.defaults.workspace",
            str(workspace),
        ],
        check=True,
    )
    subprocess.run([executable, "config", "validate"], check=True)
    subprocess.run([executable, "models", "status", "--probe"], check=True)
    subprocess.run([executable, "gateway", "status", "--require-rpc"], check=True)

    instance = yaml.safe_load(
        (paths.instance_dir / "config" / "instance.yaml").read_text("utf-8")
    )
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    gateway_token = (
        secret_store(paths, test_mode=test_mode)
        .get(references["openclaw_shared"])
        .decode("ascii")
    )
    cron = OpenClawCronReconciler(
        paths,
        root=(root or repository_root()),
        gateway_token=gateway_token,
        test_mode=test_mode,
    ).reconcile(instance["timezone"])

    marker = {
        "schema_version": 1,
        "configured": True,
        "openclaw_version": expected,
        "probe": "passed",
        "workspace_configured": True,
        "cron_reconciled": cron["status"] == "reconciled",
        "cron_jobs": len(cron["jobs"]),
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "contains_credentials": False,
    }
    atomic_write(
        activation_path(paths),
        json.dumps(marker, indent=2, sort_keys=True).encode("utf-8"),
        mode=0o600,
    )
    return marker


__all__ = ["activate", "activation_path", "model_provider_configured"]
