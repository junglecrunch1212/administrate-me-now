"""Interactively create the owner-only private identity release profile."""

from __future__ import annotations

import argparse
import getpass
import os
import tempfile
from pathlib import Path

from scripts.identity_scan import (
    REQUIRED_RELEASE_CATEGORIES,
    default_private_patterns_file,
    load_private_patterns,
)


PROMPTS = {
    "name": "Household name literal",
    "email": "Household email literal",
    "address": "Household street-address literal",
}


def _collect() -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    print(
        "Values are hidden while typed and are written only to the owner-local "
        "0600 file. Enter every literal that must be absent from the repository."
    )
    for category in sorted(REQUIRED_RELEASE_CATEGORIES):
        collected: list[str] = []
        while True:
            suffix = " (blank when finished)" if collected else " (required)"
            value = getpass.getpass(f"{PROMPTS[category]}{suffix}: ").strip()
            if not value:
                if collected:
                    break
                print(f"At least one {category} literal is required.")
                continue
            if len(value) < 3:
                print("Each literal must contain at least three characters.")
                continue
            if value.casefold() not in {item.casefold() for item in collected}:
                collected.append(value)
        values[category] = collected
    return values


def _write_profile(
    path: Path,
    values: dict[str, list[str]],
    *,
    replace: bool,
    repository_root: Path | None = None,
) -> None:
    expanded = path.expanduser()
    resolved = expanded.resolve()
    if resolved.name != "private-identity-patterns.txt":
        raise ValueError("profile filename must be private-identity-patterns.txt")
    repository = (repository_root or Path(__file__).resolve().parents[1]).resolve()
    try:
        resolved.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ValueError("private identity release profile must stay outside Git")
    if expanded.is_symlink() or (resolved.exists() and not resolved.is_file()):
        raise ValueError("profile destination must be a real file path")
    if resolved.exists() and not replace:
        raise ValueError("profile already exists; rerun with --replace to replace it")
    resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".private-identity-patterns-",
        dir=resolved.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("# AdministrateMe owner-private release canaries v1\n")
            for category in sorted(values):
                for value in values[category]:
                    stream.write(f"{category}:{value}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, resolved)
        resolved.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patterns-file", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    destination = args.patterns_file or default_private_patterns_file()
    try:
        values = _collect()
        _write_profile(destination, values, replace=args.replace)
        load_private_patterns(destination, require_release_profile=True)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print("PASS private identity release profile configured in owner-local storage.")
    print("No private literal was printed or added to Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
