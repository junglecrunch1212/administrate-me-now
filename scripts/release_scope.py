"""Deterministic component selection and exact-revision protected CI gates.

Only reviewed helper packaging has a component contract. Everything outside
that contract falls back to the unchanged system release validation. GitHub
labels, supplied path lists and previous runs cannot authorize a smaller gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from bootstrap.source_identity import load_git_tree
from bootstrap.member_bridge_component import (
    HELPER_COMPONENT_PATHS,
    NON_RUNTIME_PROSE_DIRECTORY,
    NON_RUNTIME_PROSE_PATHS,
    is_runtime_input,
    runtime_interface_fingerprint,
)


POLICY_PATHS = frozenset(
    {
        ".github/workflows/administrateme-consistency.yml",
        ".github/workflows/macos-bootstrap.yml",
        "scripts/release_scope.py",
        "tests/unit/test_ci_release_gates.py",
    }
)
HELPER_TEST_PATHS = frozenset(
    {
        "tests/unit/bootstrap/test_member_bridge_component.py",
        "tests/unit/bootstrap/test_member_bridge_helper_app.py",
        "tests/unit/bootstrap/test_member_bridge_helper_diagnostics.py",
        "tests/unit/bootstrap/test_member_bridge_permission_controller.py",
        "tests/unit/bootstrap/test_member_bridge_launch.py",
        "tests/acceptance/test_member_bridge_launch_runtime.py",
        "tests/acceptance/test_member_bridge_installer_runtime.py",
        "tests/acceptance/test_member_bridge_permission_recovery.py",
    }
)
SCOPES = frozenset({"documentation", "bridge-helper", "system-wide"})
CONTRACT = "administrateme-component-release-v1"


def _git(repository: Path, *arguments: str) -> str:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "--literal-pathspecs",
            "-C",
            str(repository),
            *arguments,
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout


def _commit(repository: Path, value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("release evidence requires a full commit identity")
    if _git(repository, "rev-parse", f"{value}^{{commit}}").strip() != value:
        raise ValueError("release commit does not resolve exactly")
    return value


def _prose(path: str) -> bool:
    return (
        path.endswith(".md")
        and (
            path in NON_RUNTIME_PROSE_PATHS
            or PurePosixPath(path).parent.as_posix() == NON_RUNTIME_PROSE_DIRECTORY
        )
        and not is_runtime_input(path, mode="100644")
    )


def choose_scope(
    changes: list[tuple[str, str]],
    *,
    interfaces_equal: bool,
    path_modes: dict[str, set[str]],
) -> str:
    if not changes:
        return "system-wide"
    if any(_prose(path) and path_modes.get(path) != {"100644"} for _, path in changes):
        return "system-wide"
    if all(_prose(path) and status in {"A", "M", "D"} for status, path in changes):
        return "documentation" if interfaces_equal else "system-wide"
    allowed = set(HELPER_COMPONENT_PATHS) | POLICY_PATHS | HELPER_TEST_PATHS
    if interfaces_equal and all(
        status in {"A", "M"} and (path in allowed or _prose(path))
        for status, path in changes
    ):
        return "bridge-helper"
    return "system-wide"


def select_scope(
    repository: Path, event: dict[str, Any], *, checkout_sha: str
) -> dict[str, Any]:
    checkout = _commit(repository, checkout_sha)
    if _git(repository, "rev-parse", "HEAD").strip() != checkout:
        raise ValueError("checkout differs from this workflow's immutable revision")
    pull = event.get("pull_request")
    if pull is None:
        return {
            "contract": CONTRACT,
            "scope": "system-wide",
            "draft": False,
            "base_sha": checkout,
            "head_sha": checkout,
            "checkout_sha": checkout,
            "interface_sha256": runtime_interface_fingerprint(repository, checkout),
            "changes": [],
            "reason": "non-PR runs require system validation",
        }
    if not isinstance(pull, dict) or pull.get("base", {}).get("ref") != "main":
        raise ValueError("release selection requires the protected main base")
    if not isinstance(pull.get("draft"), bool):
        raise ValueError("release selection needs an explicit draft state")
    base = _commit(repository, pull.get("base", {}).get("sha"))
    head = _commit(repository, pull.get("head", {}).get("sha"))
    parents = _git(repository, "rev-list", "--parents", "-n", "1", checkout).split()[1:]
    if checkout != head and parents != [base, head]:
        raise ValueError("test merge does not bind the exact event base and head")
    raw = _git(repository, "diff", "--no-renames", "--name-status", "-z", base, head)
    fields = raw.rstrip("\0").split("\0") if raw else []
    if len(fields) % 2:
        raise ValueError("unexpected Git diff record")
    changes = list(zip(fields[::2], fields[1::2], strict=True))
    for status, path in changes:
        if (
            status not in {"A", "M", "D", "T", "U"}
            or path.startswith("/")
            or ".." in Path(path).parts
            or any(ord(character) < 32 for character in path)
        ):
            raise ValueError("unsafe or unsupported diff entry")
    path_modes: dict[str, set[str]] = {}
    for revision in {base, head, checkout}:
        entries, _ = load_git_tree(repository, revision)
        for entry in entries:
            path_modes.setdefault(entry.path, set()).add(entry.mode)
    base_fingerprint = runtime_interface_fingerprint(repository, base)
    head_fingerprint = runtime_interface_fingerprint(repository, head)
    checkout_fingerprint = runtime_interface_fingerprint(repository, checkout)
    scope = choose_scope(
        changes,
        interfaces_equal=base_fingerprint == head_fingerprint == checkout_fingerprint,
        path_modes=path_modes,
    )
    return {
        "contract": CONTRACT,
        "scope": scope,
        "draft": pull["draft"],
        "base_sha": base,
        "head_sha": head,
        "checkout_sha": checkout,
        "interface_sha256": checkout_fingerprint,
        "changes": [{"status": status, "path": path} for status, path in changes],
    }


def required_workers(scope: str, platform: str) -> tuple[str, ...]:
    if scope not in SCOPES or platform not in {"linux", "macos"}:
        raise ValueError("unsupported release scope or platform")
    selected = {
        "documentation": "documentation-validation",
        "bridge-helper": "bridge-helper-validation",
        "system-wide": "system-consistency"
        if platform == "linux"
        else "system-rehearsal",
    }[scope]
    workers: tuple[str, ...] = ("release-scope", selected)
    if platform == "macos" and scope != "documentation":
        workers += ("bridge-installed-runtime",)
    return workers


def verify_gate(proof: dict[str, Any], needs: dict[str, Any], *, platform: str) -> None:
    if proof.get("contract") != CONTRACT or proof.get("draft") is not False:
        raise ValueError("development-only drafts cannot approve a component release")
    scope = proof.get("scope")
    if not isinstance(scope, str):
        raise ValueError("release scope is missing")
    for name in required_workers(scope, platform):
        worker = needs.get(name)
        if not isinstance(worker, dict) or worker.get("result") != "success":
            raise ValueError(f"required {name} did not finish successfully")
        outputs = worker.get("outputs", {})
        if outputs.get("validated_sha") != proof["checkout_sha"]:
            raise ValueError(f"required {name} has missing or stale revision evidence")
    selection = needs["release-scope"]["outputs"]
    if (
        selection.get("scope") != scope
        or selection.get("interface_sha256") != proof["interface_sha256"]
    ):
        raise ValueError("worker scope does not match recomputed interface evidence")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "gate"))
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--checkout-sha", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--platform", choices=("linux", "macos"))
    args = parser.parse_args()
    proof = select_scope(
        args.repository,
        json.loads(args.event.read_text("utf-8")),
        checkout_sha=args.checkout_sha,
    )
    if args.command == "gate":
        verify_gate(
            proof, json.loads(os.environ["RELEASE_NEEDS_JSON"]), platform=args.platform
        )
    serialized = json.dumps(proof, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for key, value in (
                ("scope", proof["scope"]),
                ("validated_sha", proof["checkout_sha"]),
                ("interface_sha256", proof["interface_sha256"]),
            ):
                stream.write(f"{key}={value}\n")
    print(serialized, end="")


if __name__ == "__main__":
    main()
