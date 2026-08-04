"""Server-side, secret-safe activation of the pinned OpenClaw provider boundary."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions


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
    )


def activate(paths: DeploymentPaths) -> dict[str, Any]:
    """Run OpenClaw-owned interactive auth, probe it, then write metadata only."""

    executable = shutil.which("openclaw")
    if not executable:
        raise RuntimeError("the pinned OpenClaw executable is unavailable")
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
    print("Never enter them in the member portal, a browser form, or AdministrateMe.")
    subprocess.run(
        [executable, "configure", "--section", "model", "--section", "channels"],
        check=True,
    )
    subprocess.run([executable, "config", "validate"], check=True)
    subprocess.run([executable, "models", "status", "--probe"], check=True)

    marker = {
        "schema_version": 1,
        "configured": True,
        "openclaw_version": expected,
        "probe": "passed",
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
