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
from typing import Any

from adminme.workstation_browser import (
    BROWSER_POLICY_FILE,
    BrowserPolicy,
    browser_resource,
    prepare_browser_gateway,
)
from adminme.workstation_inventory import (
    INVENTORY_FILE,
    IdentityInventory,
    read_private_document,
)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema")
    prepare = commands.add_parser("prepare-native-config")
    prepare.add_argument("--previous-config", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
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
