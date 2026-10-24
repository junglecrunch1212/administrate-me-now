"""Validate and provision the single private identity inventory without granting access."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from adminme.workstation_inventory import (
    INVENTORY_FILE,
    IdentityInventory,
    read_private_document,
)
from bootstrap.contracts import DeploymentPaths, atomic_write


def provision(
    config_dir: Path, document: dict[str, Any], *, expected_revision: int
) -> dict[str, Any]:
    inventory = IdentityInventory.model_validate(document)
    destination = config_dir / INVENTORY_FILE
    if (
        config_dir.is_symlink()
        or not config_dir.is_dir()
        or config_dir.resolve() != config_dir
    ):
        raise ValueError("existing private configuration directory is required")
    metadata = config_dir.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("identity configuration directory must be private")
    # Serialize the compare-and-replace; do not race another operator's refresh.
    import fcntl

    descriptor = os.open(config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            previous = IdentityInventory.model_validate(
                read_private_document(destination)
            )
            current = previous.revision
        except FileNotFoundError:
            current = 0
            previous = None
        if expected_revision != current or inventory.revision != current + 1:
            raise ValueError(
                "identity inventory revision changed; review the current map"
            )
        if previous is not None:
            previous_bindings = {row.binding_id: row for row in previous.bindings}
            for row in inventory.bindings:
                prior = previous_bindings.get(row.binding_id)
                if prior is None:
                    continue
                coordinate = (
                    row.operating_principal_id,
                    row.resource_owner_principal_id,
                    row.expected_identity,
                    row.route,
                )
                old_coordinate = (
                    prior.operating_principal_id,
                    prior.resource_owner_principal_id,
                    prior.expected_identity,
                    prior.route,
                )
                if row.revision < prior.revision or (
                    coordinate != old_coordinate and row.revision <= prior.revision
                ):
                    raise ValueError(
                        "changed identity coordinates require a new binding revision"
                    )
        atomic_write(
            destination,
            (inventory.model_dump_json(indent=2) + "\n").encode(),
            mode=0o600,
        )
    finally:
        os.close(descriptor)
    return {
        "state": "provisioned",
        "revision": inventory.revision,
        "binding_count": len(inventory.bindings),
        "grants_changed": False,
        "provider_effect_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--instance-root", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    if args.schema:
        print(json.dumps(IdentityInventory.model_json_schema(), indent=2))
        return 0
    if (
        args.instance_root is None
        or args.input is None
        or args.expected_revision is None
    ):
        parser.error("provide --instance-root, --input and --expected-revision")
    try:
        result = provision(
            DeploymentPaths.from_root(args.instance_root).instance_dir / "config",
            read_private_document(args.input),
            expected_revision=args.expected_revision,
        )
    except (OSError, ValueError):
        # Validation errors may echo private input; never print them into logs.
        parser.exit(
            1,
            "Private identity inventory rejected; inspect its schema, permissions and revision.\n",
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
