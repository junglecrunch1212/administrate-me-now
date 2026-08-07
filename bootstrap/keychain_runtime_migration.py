#!/usr/bin/env python3
"""Copy logical secrets into runtime-bound Keychain items without exposing values.

The coordinator is intentionally Python 3.11 compatible: it runs under the
active release so macOS permits reads of the existing items. It sends values
only through an anonymous pipe to a candidate-interpreter child, which creates
and verifies new items bound to that interpreter. Only after every child write
succeeds does the coordinator atomically publish the non-secret alias map.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Sequence

import keyring


ALIAS_FILENAME = "keychain-runtime-aliases.json"
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
_ACCOUNT_PATTERN = re.compile(r"^instance-[0-9a-f]{16}$")
_ITEM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
_PYTHON_VERSION_PATTERN = re.compile(
    r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:\.[0-9]+)?$"
)
_PYTHON_FORMULA_PATTERN = re.compile(r"^python@(?P<major>[0-9]+)\.(?P<minor>[0-9]+)$")
_MAX_PIPE_BYTES = 16 * 1024 * 1024


class KeychainRuntimeMigrationError(RuntimeError):
    pass


def _account_for(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return f"instance-{digest}"


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


def configured_logical_references(config_dir: Path, account: str) -> tuple[str, ...]:
    """Discover logical references while excluding the generated alias map."""

    references: set[str] = set()
    if not config_dir.is_dir():
        return ()
    for path in sorted(config_dir.glob("*.json")):
        if path.name == ALIAS_FILENAME:
            continue
        try:
            document = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        references.update(_walk_references(document))
    prefix = f"keychain://{account}/"
    return tuple(sorted(ref for ref in references if ref.startswith(prefix)))


def _read_existing_aliases(
    path: Path, prefix: str
) -> tuple[dict[str, str], str | None]:
    if not path.exists():
        return {}, None
    if path.is_symlink():
        raise KeychainRuntimeMigrationError("refusing a symlinked Keychain alias map")
    try:
        document = json.loads(path.read_text("utf-8"))
        raw = document["aliases"]
        source_runtime_tag = document["runtime_tag"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise KeychainRuntimeMigrationError(
            "existing Keychain alias map is invalid"
        ) from exc
    if (
        document.get("schema_version") != 1
        or not isinstance(raw, dict)
        or not isinstance(source_runtime_tag, str)
        or not _RUNTIME_TAG_PATTERN.fullmatch(source_runtime_tag)
    ):
        raise KeychainRuntimeMigrationError("existing Keychain alias map is invalid")
    aliases: dict[str, str] = {}
    for source, target in raw.items():
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or not source.startswith(prefix)
            or not target.startswith(prefix)
            or source == target
            or not _ITEM_NAME_PATTERN.fullmatch(source.removeprefix(prefix))
            or not _ITEM_NAME_PATTERN.fullmatch(target.removeprefix(prefix))
        ):
            raise KeychainRuntimeMigrationError(
                "existing Keychain alias map crosses an instance boundary"
            )
        aliases[source] = target
    for reference in aliases:
        _resolve_reference(reference, aliases, prefix)
    return aliases, source_runtime_tag


def _active_runtime_tag(root: Path) -> str | None:
    """Return the runtime family that can actually read current physical items."""

    pointer = root / "app" / "current-release.json"
    if not pointer.exists():
        return None
    if pointer.is_symlink():
        raise KeychainRuntimeMigrationError(
            "refusing a symlinked active-release pointer"
        )
    try:
        document = json.loads(pointer.read_text("utf-8"))
        release = Path(document["path"]).resolve()
        release_parent = (root / "app" / "releases").resolve()
        if release_parent not in release.parents:
            raise KeychainRuntimeMigrationError(
                "active-release pointer escapes the deployment"
            )
        versions = json.loads(
            (release / "bootstrap" / "versions.json").read_text("utf-8")
        )
        python_receipt = versions["python"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise KeychainRuntimeMigrationError(
            "active release runtime receipt is invalid"
        ) from exc

    if not isinstance(python_receipt, dict):
        raise KeychainRuntimeMigrationError("active release runtime receipt is invalid")
    preferred = python_receipt.get("preferred")
    formula = python_receipt.get("formula")
    match = (
        _PYTHON_VERSION_PATTERN.fullmatch(preferred)
        if isinstance(preferred, str)
        else None
    )
    if match is None:
        match = (
            _PYTHON_FORMULA_PATTERN.fullmatch(formula)
            if isinstance(formula, str)
            else None
        )
    if match is None:
        raise KeychainRuntimeMigrationError("active release runtime receipt is invalid")
    return f"py{match.group('major')}{match.group('minor')}"


def _resolve_reference(reference: str, aliases: dict[str, str], prefix: str) -> str:
    if not reference.startswith(prefix):
        raise KeychainRuntimeMigrationError(
            "secret reference belongs to another instance"
        )
    resolved = reference
    visited: set[str] = set()
    while resolved in aliases:
        if resolved in visited:
            raise KeychainRuntimeMigrationError("Keychain alias map contains a cycle")
        visited.add(resolved)
        resolved = aliases[resolved]
    return resolved


def _service(account: str, name: str) -> str:
    return f"com.administrateme.{account}.{name}"


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise KeychainRuntimeMigrationError("refusing to replace a symlinked alias map")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            payload = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _child() -> int:
    raw = sys.stdin.buffer.read(_MAX_PIPE_BYTES + 1)
    if len(raw) > _MAX_PIPE_BYTES:
        raise KeychainRuntimeMigrationError("candidate secret transfer exceeds limit")
    try:
        payload = json.loads(raw)
        account = payload["account"]
        entries = payload["entries"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise KeychainRuntimeMigrationError(
            "candidate secret transfer is invalid"
        ) from exc
    if not isinstance(account, str) or not _ACCOUNT_PATTERN.fullmatch(account):
        raise KeychainRuntimeMigrationError("candidate Keychain account is invalid")
    if not isinstance(entries, list) or not entries:
        raise KeychainRuntimeMigrationError("candidate secret transfer is empty")
    observed_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise KeychainRuntimeMigrationError("candidate secret entry is invalid")
        name = entry.get("name")
        value = entry.get("value")
        if (
            not isinstance(name, str)
            or not _ITEM_NAME_PATTERN.fullmatch(name)
            or name in observed_names
            or not isinstance(value, str)
            or not value
        ):
            raise KeychainRuntimeMigrationError("candidate secret entry is invalid")
        observed_names.add(name)
        try:
            keyring.set_password(_service(account, name), account, value)
            verified = keyring.get_password(_service(account, name), account)
        except Exception as exc:
            raise KeychainRuntimeMigrationError(
                "candidate interpreter could not access a copied Keychain item"
            ) from exc
        if verified != value:
            raise KeychainRuntimeMigrationError(
                "candidate interpreter could not verify a copied Keychain item"
            )
    print(json.dumps({"status": "verified", "count": len(entries)}, sort_keys=True))
    return 0


def migrate(
    *,
    instance_root: Path,
    candidate_python: Path,
    candidate_commit: str,
    runtime_tag: str,
) -> dict[str, Any]:
    root = instance_root.expanduser().resolve()
    if (
        not root.is_absolute()
        or root == Path(root.anchor)
        or root == Path.home().resolve()
    ):
        raise KeychainRuntimeMigrationError("refusing a broad deployment root")
    # Preserve the venv launcher path. Resolving this symlink escapes the venv
    # and makes the Homebrew base interpreter lose the controller dependencies.
    candidate_python = Path(os.path.abspath(candidate_python.expanduser()))
    if not candidate_python.is_file() or not os.access(candidate_python, os.X_OK):
        raise KeychainRuntimeMigrationError("candidate Python is unavailable")
    if not _COMMIT_PATTERN.fullmatch(candidate_commit):
        raise KeychainRuntimeMigrationError("candidate commit is invalid")
    if not _RUNTIME_TAG_PATTERN.fullmatch(runtime_tag):
        raise KeychainRuntimeMigrationError("candidate runtime tag is invalid")

    account = _account_for(root)
    prefix = f"keychain://{account}/"
    config_dir = root / "state" / "config"
    alias_path = config_dir / ALIAS_FILENAME
    logical_references = configured_logical_references(config_dir, account)
    if not logical_references:
        raise KeychainRuntimeMigrationError(
            "runtime migration found no configured macOS Keychain references"
        )
    existing_aliases, alias_runtime_tag = _read_existing_aliases(alias_path, prefix)
    if alias_runtime_tag != _active_runtime_tag(root):
        # A rolled-back older release ignores a newer alias map and still owns
        # the original items. Retry from what that active interpreter can read.
        existing_aliases = {}
    entries: list[dict[str, str]] = []
    aliases: dict[str, str] = {}
    suffix = f"runtime-{runtime_tag}-{candidate_commit[:12]}"
    for logical_reference in logical_references:
        logical_name = logical_reference.removeprefix(prefix)
        if not _ITEM_NAME_PATTERN.fullmatch(logical_name):
            raise KeychainRuntimeMigrationError(
                "configured logical Keychain reference is invalid"
            )
        source_reference = _resolve_reference(
            logical_reference, existing_aliases, prefix
        )
        source_name = source_reference.removeprefix(prefix)
        try:
            value = keyring.get_password(_service(account, source_name), account)
        except Exception as exc:
            raise KeychainRuntimeMigrationError(
                "active interpreter cannot access a configured Keychain item"
            ) from exc
        if value is None:
            raise KeychainRuntimeMigrationError(
                "active interpreter cannot resolve every configured Keychain item"
            )
        target_name = f"{logical_name}-{suffix}"
        if not _ITEM_NAME_PATTERN.fullmatch(target_name):
            raise KeychainRuntimeMigrationError(
                "derived candidate Keychain name is invalid"
            )
        target_reference = f"{prefix}{target_name}"
        aliases[logical_reference] = target_reference
        entries.append({"name": target_name, "value": value})

    transfer = json.dumps({"account": account, "entries": entries})
    try:
        completed = subprocess.run(
            [str(candidate_python), str(Path(__file__).resolve()), "--child"],
            input=transfer,
            text=True,
            capture_output=True,
            check=False,
            timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        raise KeychainRuntimeMigrationError(
            "candidate interpreter timed out while creating runtime-bound Keychain items"
        ) from exc
    if completed.returncode:
        raise KeychainRuntimeMigrationError(
            "candidate interpreter could not create runtime-bound Keychain items"
        )
    try:
        receipt = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise KeychainRuntimeMigrationError(
            "candidate Keychain copy did not return a valid receipt"
        ) from exc
    if receipt != {"status": "verified", "count": len(entries)}:
        raise KeychainRuntimeMigrationError(
            "candidate Keychain copy receipt is invalid"
        )

    document = {
        "schema_version": 1,
        "target_commit": candidate_commit,
        "runtime_tag": runtime_tag,
        "aliases": aliases,
    }
    _atomic_write(alias_path, document)
    return {
        "status": "runtime-keychain-aliases-ready",
        "count": len(aliases),
        "alias_file": str(alias_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--instance-root", type=Path)
    parser.add_argument("--candidate-python", type=Path)
    parser.add_argument("--candidate-commit")
    parser.add_argument("--runtime-tag")
    args = parser.parse_args(argv)
    try:
        if args.child:
            return _child()
        if not all(
            (
                args.instance_root,
                args.candidate_python,
                args.candidate_commit,
                args.runtime_tag,
            )
        ):
            parser.error("coordinator arguments are required")
        result = migrate(
            instance_root=args.instance_root,
            candidate_python=args.candidate_python,
            candidate_commit=args.candidate_commit,
            runtime_tag=args.runtime_tag,
        )
    except KeychainRuntimeMigrationError as exc:
        print(f"AdministrateMe: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
