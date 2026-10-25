"""Exclude ambient Codex account apps from this reviewed household runtime."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


def prepare_native_account_policy(
    gateway: dict[str, Any], release: Path
) -> dict[str, Any]:
    """Prepare one supported config setting; never alter auth or native files/tools.

    An older target lacking this declaration retains its existing configuration.
    Only an explicitly installed Codex runtime entry is changed. The native apps
    policy is separate from inherited MCP servers, filesystem and shell policy.
    """
    prepared = copy.deepcopy(gateway)
    manifest = release / "openclaw/administrateme/openclaw.plugin.json"
    if manifest.is_symlink():
        raise RuntimeError("native account policy requires the reviewed manifest")
    contract = json.loads(manifest.read_text("utf-8")).get("contracts", {})
    if contract.get("nativeAccountApps") != "disabled":
        return prepared
    codex = prepared.get("plugins", {}).get("entries", {}).get("codex")
    if not isinstance(codex, dict) or codex.get("enabled") is not True:
        return prepared
    config = codex.setdefault("config", {})
    if not isinstance(config, dict):
        raise RuntimeError("installed Codex plugin configuration is malformed")
    native_apps = config.setdefault("codexPlugins", {})
    if not isinstance(native_apps, dict):
        raise RuntimeError("installed Codex native app policy is malformed")
    native_apps["enabled"] = False
    return prepared
