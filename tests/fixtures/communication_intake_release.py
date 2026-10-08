"""Isolated reviewed-source layouts for each immutable intake release role."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from adminme.communication_intake_policy import POLICY_ID


def communication_intake_release(
    root: Path, destination: Path, role: Literal["cutover", "rollback-prerequisite"]
) -> Path:
    """Copy the manifest; share only read-only fixture source dependencies."""
    destination.mkdir(parents=True)
    for child in root.iterdir():
        if child.name.startswith(".") or child.name == "bootstrap":
            continue
        (destination / child.name).symlink_to(child, target_is_directory=child.is_dir())
    bootstrap = destination / "bootstrap"
    bootstrap.mkdir()
    for child in (root / "bootstrap").iterdir():
        if child.name == "versions.json":
            continue
        (bootstrap / child.name).symlink_to(child, target_is_directory=child.is_dir())
    versions = json.loads((root / "bootstrap" / "versions.json").read_text())
    application = versions["administrateme"]
    application["communication_intake_release_role"] = role
    application["supported_communication_intake_policies"] = [POLICY_ID]
    application["default_communication_intake_policy"] = (
        POLICY_ID if role == "cutover" else None
    )
    (bootstrap / "versions.json").write_text(json.dumps(versions))
    return destination
