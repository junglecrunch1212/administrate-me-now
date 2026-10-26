"""Prepare a native browser route and provision its private verified tab policy.

Only an operator uses this command after the guarded component is deployed.
Preparation does not activate the Gateway, install/pair an extension, import a
browser session, or enable any agent tool. Tab evidence comes from the actual
native route and verified intended profile, never a guessed browser label.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from adminme.openclaw_topology import service_agent_id
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.workstation_browser import (
    BROWSER_POLICY_FILE,
    BrowserPolicy,
    browser_resource,
    assert_single_extension_profile,
    prepare_browser_gateway,
)
from adminme.workstation_inventory import (
    INVENTORY_FILE,
    IdentityInventory,
    read_private_document,
)
from adminme.workstation_source_policy import BrowserSourceProvision, PROVISION_FILE
from bootstrap.contracts import DeploymentPaths, atomic_write


def prepare_config(previous: Path, output: Path) -> dict[str, Any]:
    """Write a new private candidate config; never edit active configuration."""
    prepared = prepare_browser_gateway(read_private_document(previous))
    descriptor = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(prepared, handle, sort_keys=True)
        handle.write("\n")
    return {
        "state": "prepared_not_activated",
        "agent_tools_changed": False,
        "session_imported": False,
        "provider_effect_count": 0,
    }


def provision(
    config_dir: Path, document: dict[str, Any], *, expected_revision: int
) -> dict[str, Any]:
    policy = BrowserPolicy.model_validate(document)
    if (
        config_dir.is_symlink()
        or not config_dir.is_dir()
        or config_dir.resolve() != config_dir
    ):
        raise ValueError("existing private configuration directory is required")
    metadata = config_dir.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("browser configuration directory must be private")
    import fcntl

    descriptor = os.open(config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        destination = config_dir / BROWSER_POLICY_FILE
        try:
            current = BrowserPolicy.model_validate(
                read_private_document(destination)
            ).revision
        except FileNotFoundError:
            current = 0
        if expected_revision != current or policy.revision != current + 1:
            raise ValueError("browser policy revision changed; review current policy")
        # Disabling remains possible when account evidence has expired or vanished.
        if policy.enabled:
            inventory = IdentityInventory.model_validate(
                read_private_document(config_dir / INVENTORY_FILE)
            )
            if not policy.resources:
                raise ValueError("enabled browser policy needs a verified native tab")
            for resource in policy.resources:
                browser_resource(policy, inventory, resource.resource_id)
        atomic_write(
            destination, (policy.model_dump_json(indent=2) + "\n").encode(), mode=0o600
        )
    finally:
        os.close(descriptor)
    return {
        "state": "policy_provisioned",
        "revision": policy.revision,
        "enabled": policy.enabled,
        "resource_count": len(policy.resources),
        "native_tool_execution_verified": False,
        "provider_effect_count": 0,
    }


def native_service_tools(
    gateway: dict[str, Any], paths: DeploymentPaths, release: Path
) -> list[str]:
    """An explicit private route enables only browser, under installed guards.

    Missing, disabled, stale or invalid setup removes native access without
    blocking unrelated deployment. This is availability, not execution proof.
    """
    try:
        manifest_path = release / "openclaw" / "administrateme" / "openclaw.plugin.json"
        if manifest_path.is_symlink():
            return []
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if set(manifest.get("contracts", {}).get("agentToolResultMiddleware", [])) != {
            "openclaw",
            "codex",
        }:
            return []
        plugins = gateway.get("plugins", {})
        entry = plugins.get("entries", {}).get("administrateme", {})
        if (
            plugins.get("enabled") is False
            or entry.get("enabled") is not True
            or entry.get("hooks", {}).get("allowPromptInjection") is not True
            or entry.get("hooks", {}).get("allowConversationAccess") is not True
            or plugins.get("entries", {}).get("browser", {}).get("enabled") is False
            or (
                isinstance(plugins.get("allow"), list)
                and not {"administrateme", "browser"}.issubset(plugins["allow"])
            )
        ):
            return []
        directory = paths.instance_dir / "config"
        policy = BrowserPolicy.model_validate(
            read_private_document(directory / BROWSER_POLICY_FILE)
        )
        if not policy.enabled or not policy.resources:
            return []
        inventory = IdentityInventory.model_validate(
            read_private_document(directory / INVENTORY_FILE)
        )
        assert_single_extension_profile(gateway)
        if gateway["browser"].get("evaluateEnabled") is not True:
            return []
        for resource in policy.resources:
            browser_resource(policy, inventory, resource.resource_id)
        return ["browser"]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def prepare_native_patch(
    snapshot: dict[str, Any],
    paths: DeploymentPaths,
    release: Path,
    *,
    disable: bool = False,
) -> dict[str, Any]:
    """Produce exact native config.patch params, never edit active configuration.

    The operator obtains a fresh config.get snapshot and applies this artifact
    through OpenClaw's authenticated, hash-checked config.patch/restart path.
    Only browser settings and an additive service browser tool can change.
    """
    if (
        snapshot.get("valid") is not True
        or not isinstance(snapshot.get("hash"), str)
        or len(snapshot["hash"]) != 64
    ):
        raise ValueError("a fresh valid native config.get snapshot is required")
    original = snapshot.get("config")
    if not isinstance(original, dict):
        raise ValueError("native configuration snapshot is unavailable")
    manifest = json.loads(
        (release / "openclaw" / "administrateme" / "openclaw.plugin.json").read_text(
            "utf-8"
        )
    )
    if set(manifest.get("contracts", {}).get("agentToolResultMiddleware", [])) != {
        "openclaw",
        "codex",
    }:
        raise ValueError("the deployed release must contain the reviewed browser guard")
    if disable:
        patch: dict[str, Any] = {"browser": {"enabled": False}}
    else:
        entry = original.get("plugins", {}).get("entries", {}).get("administrateme", {})
        if any(
            entry.get("hooks", {}).get(name) is not True
            for name in ("allowPromptInjection", "allowConversationAccess")
        ):
            raise ValueError("the deployed finite worker policy hook must be enabled")
        prepared = prepare_browser_gateway(original)
        patch = {
            "browser": prepared["browser"],
            "plugins": {"entries": {"browser": {"enabled": True}}},
        }
        if "allow" in prepared["plugins"]:
            patch["plugins"]["allow"] = prepared["plugins"]["allow"]
        tools = native_service_tools(prepared, paths, release)
        if tools:
            agents = original.get("agents")
            if not isinstance(agents, dict) or ("list" in agents) == (
                "entries" in agents
            ):
                raise ValueError(
                    "one unambiguous native agent representation is required"
                )
            agent_id = service_agent_id(COS_SERVICE_PRINCIPAL_ID)
            if "entries" in agents:
                entries = agents["entries"]
                if not isinstance(entries, dict) or any(
                    not isinstance(row, dict) for row in entries.values()
                ):
                    raise ValueError("native keyed agent entries are malformed")
                matches = [
                    row
                    for key, row in entries.items()
                    if key == agent_id or row.get("id") == agent_id
                ]
                if (
                    len(matches) != 1
                    or agent_id not in entries
                    or entries[agent_id].get("id", agent_id) != agent_id
                ):
                    raise ValueError(
                        "one current CoS worker must be deployed before browser admission"
                    )
            else:
                rows = agents["list"]
                if not isinstance(rows, list) or any(
                    not isinstance(row, dict) for row in rows
                ):
                    raise ValueError("native agent list is malformed")
                matches = [row for row in rows if row.get("id") == agent_id]
                if len(matches) != 1:
                    raise ValueError(
                        "one current CoS worker must be deployed before browser admission"
                    )
            current_tools = matches[0].get("tools")
            allowed = (
                current_tools.get("allow") if isinstance(current_tools, dict) else None
            )
            if not isinstance(allowed, list) or any(
                not isinstance(name, str) for name in allowed
            ):
                raise ValueError(
                    "the CoS worker needs an explicit existing tool allowlist"
                )
            additive = {"tools": {"allow": list(dict.fromkeys([*allowed, *tools]))}}
            patch["agents"] = (
                {"entries": {agent_id: additive}}
                if "entries" in agents
                else {"list": [{"id": agent_id, **additive}]}
            )
    return {
        "baseHash": snapshot["hash"],
        "raw": json.dumps(patch, separators=(",", ":")),
    }


def write_patch(
    snapshot_path: Path, paths: DeploymentPaths, output: Path, *, disable: bool
) -> dict[str, Any]:
    pointer = read_private_document(paths.app_dir / "current-release.json")
    release = Path(pointer["path"]).resolve()
    if not release.is_relative_to((paths.app_dir / "releases").resolve()):
        raise ValueError("deployed release pointer is outside the release store")
    document = prepare_native_patch(
        read_private_document(snapshot_path), paths, release, disable=disable
    )
    descriptor = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True)
        handle.write("\n")
    return {
        "state": "native_patch_prepared_not_applied",
        "disable": disable,
        "session_imported": False,
        "provider_effect_count": 0,
    }


def stage_source_request(config_dir: Path, document: dict[str, Any]) -> dict[str, Any]:
    staged = BrowserSourceProvision.model_validate(document)
    if not staged.current(datetime.now(timezone.utc)):
        raise ValueError("source request must expire within the next hour")
    if (
        config_dir.is_symlink()
        or not config_dir.is_dir()
        or config_dir.resolve() != config_dir
    ):
        raise ValueError("existing private configuration directory is required")
    metadata = config_dir.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("configuration directory must be private")
    destination = config_dir / PROVISION_FILE
    if destination.is_symlink():
        raise ValueError("source request cannot be a symlink")
    atomic_write(
        destination, (staged.model_dump_json(indent=2) + "\n").encode(), mode=0o600
    )
    return {
        "state": "source_policy_request_staged_not_applied",
        "request_id": staged.request_id,
        "provider_effect_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema")
    prepare = commands.add_parser("prepare-native-config")
    prepare.add_argument("--previous-config", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    patch = commands.add_parser("prepare-native-patch")
    patch.add_argument("--instance-root", type=Path, required=True)
    patch.add_argument("--snapshot", type=Path, required=True)
    patch.add_argument("--output", type=Path, required=True)
    patch.add_argument("--disable", action="store_true")
    stage = commands.add_parser("stage-source-policy")
    stage.add_argument("--instance-root", type=Path, required=True)
    stage.add_argument("--input", type=Path, required=True)
    configure = commands.add_parser("provision-policy")
    configure.add_argument("--instance-root", type=Path, required=True)
    configure.add_argument("--input", type=Path, required=True)
    configure.add_argument("--expected-revision", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.command == "schema":
            print(json.dumps(BrowserPolicy.model_json_schema(), indent=2))
            return 0
        if args.command == "prepare-native-config":
            result = prepare_config(args.previous_config, args.output)
        elif args.command == "prepare-native-patch":
            result = write_patch(
                args.snapshot,
                DeploymentPaths.from_root(args.instance_root),
                args.output,
                disable=args.disable,
            )
        elif args.command == "stage-source-policy":
            result = stage_source_request(
                DeploymentPaths.from_root(args.instance_root).instance_dir / "config",
                read_private_document(args.input),
            )
        else:
            result = provision(
                DeploymentPaths.from_root(args.instance_root).instance_dir / "config",
                read_private_document(args.input),
                expected_revision=args.expected_revision,
            )
    except (OSError, ValueError):
        # Neither private policy, credentials nor validation input goes to logs.
        parser.exit(
            1,
            "Browser preparation rejected; review private inputs, permissions and revisions.\n",
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
