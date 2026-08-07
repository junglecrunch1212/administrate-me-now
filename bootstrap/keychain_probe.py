"""Bounded, value-free probe for candidate-runtime Keychain access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import KEYCHAIN_ALIAS_FILENAME, secret_store


def _walk_references(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value.startswith("keychain://") else set()
    if isinstance(value, dict):
        references: set[str] = set()
        for child in value.values():
            references.update(_walk_references(child))
        return references
    if isinstance(value, list):
        references = set()
        for child in value:
            references.update(_walk_references(child))
        return references
    return set()


def configured_keychain_references(paths: DeploymentPaths) -> tuple[str, ...]:
    """Return non-secret references recorded in validated instance JSON."""

    references: set[str] = set()
    config_dir = paths.instance_dir / "config"
    if not config_dir.is_dir():
        return ()
    for path in sorted(config_dir.glob("*.json")):
        if path.name == KEYCHAIN_ALIAS_FILENAME:
            continue
        try:
            document = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        references.update(_walk_references(document))
    prefix = f"keychain://{paths.keychain_account()}/"
    return tuple(
        sorted(reference for reference in references if reference.startswith(prefix))
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("reference")
    args = parser.parse_args(argv)
    paths = DeploymentPaths.from_root(args.instance_root, production=True)
    configured = configured_keychain_references(paths)
    if args.reference not in configured:
        return 2
    return 0 if secret_store(paths).has(args.reference) else 1


if __name__ == "__main__":
    raise SystemExit(main())
