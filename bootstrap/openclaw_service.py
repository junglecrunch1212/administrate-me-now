"""Keychain-backed launchd target for the exactly pinned OpenClaw Gateway."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.cos_apple_events import service_environment
from bootstrap.openclaw_install import resolve_reviewed_openclaw
from bootstrap.secrets import secret_store


def main() -> None:
    root_value = os.environ.get("ADMINME_INSTANCE_ROOT")
    if not root_value:
        raise RuntimeError("ADMINME_INSTANCE_ROOT is required")
    paths = DeploymentPaths.from_root(Path(root_value), production=True)
    executable = resolve_reviewed_openclaw(paths)
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
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    shared = secret_store(paths).get(references["openclaw_shared"]).decode("ascii")
    environment = os.environ.copy()
    environment["OPENCLAW_STATE_DIR"] = str(paths.instance_dir / "openclaw" / "runtime")
    environment["OPENCLAW_CONFIG_PATH"] = str(
        paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    )
    environment["ADMINME_OPENCLAW_SHARED_SECRET"] = shared
    environment["OPENCLAW_GATEWAY_TOKEN"] = shared
    # AdministrateMe owns exact protected runtime updates. Upstream's new node
    # auto-update default cannot create a second mutable update owner.
    environment["OPENCLAW_NO_AUTO_UPDATE"] = "1"
    environment["ADMINISTRATEME_COS_IMSG_GUARD_CONFIG"] = str(
        paths.instance_dir / "config" / "cos-imsg-guard.json"
    )
    # Trusted service startup fixes both coordinates; the admin RPC accepts
    # neither a caller-supplied path nor executable arguments.
    environment.update(service_environment())
    os.execve(executable, [executable, "gateway", "run"], environment)


if __name__ == "__main__":
    main()
