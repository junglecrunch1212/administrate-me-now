"""Read-only canonical-workspace checks for local recovery work."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .core import HarnessError


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no diagnostic"
        raise HarnessError(f"git {' '.join(arguments)} failed: {detail}")
    return process


def _exact_sha(value: str, label: str) -> str:
    candidate = value.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise HarnessError(f"{label} did not resolve to one exact commit")
    return candidate


def evaluate_workspace(
    *,
    branch: str,
    head_sha: str,
    local_main_sha: str,
    remote_main_sha: str,
    contains_remote_main: bool,
    worktree_changes: list[str],
) -> dict[str, Any]:
    """Evaluate already observed state without network or filesystem effects."""

    problems: list[str] = []
    if not branch:
        problems.append("The workspace is detached; use one saved recovery branch.")
    elif branch == "main":
        problems.append("Development must not occur directly on main.")
    elif not branch.startswith("codex/"):
        problems.append("The recovery branch must use the codex/ namespace.")
    if local_main_sha != remote_main_sha:
        problems.append("The local origin/main tracking ref is stale; fetch before work.")
    if not contains_remote_main:
        problems.append("The candidate does not contain current GitHub main.")
    return {
        "status": "PASS" if not problems else "FAIL",
        "branch": branch or "DETACHED",
        "head_sha": head_sha,
        "local_origin_main_sha": local_main_sha,
        "github_main_sha": remote_main_sha,
        "contains_github_main": contains_remote_main,
        "worktree_change_count": len(worktree_changes),
        "worktree_changes": worktree_changes,
        "problems": problems,
    }


def workspace_status(root: Path) -> dict[str, Any]:
    """Compare this worktree to GitHub main without fetching or modifying refs."""

    branch = _git(root, "branch", "--show-current").stdout.strip()
    head_sha = _exact_sha(_git(root, "rev-parse", "HEAD").stdout, "HEAD")
    local_main_sha = _exact_sha(
        _git(root, "rev-parse", "refs/remotes/origin/main").stdout,
        "local origin/main",
    )
    remote_output = _git(
        root,
        "ls-remote",
        "--exit-code",
        "origin",
        "refs/heads/main",
    ).stdout.splitlines()
    if len(remote_output) != 1:
        raise HarnessError("GitHub main did not resolve to one remote ref")
    remote_main_sha = _exact_sha(remote_output[0].split()[0], "GitHub main")
    contains = (
        _git(root, "merge-base", "--is-ancestor", remote_main_sha, "HEAD", check=False)
        .returncode
        == 0
    )
    changes = [
        line
        for line in _git(root, "status", "--short", "--untracked-files=all").stdout.splitlines()
        if line
    ]
    return evaluate_workspace(
        branch=branch,
        head_sha=head_sha,
        local_main_sha=local_main_sha,
        remote_main_sha=remote_main_sha,
        contains_remote_main=contains,
        worktree_changes=changes,
    )


def check_workspace(root: Path) -> dict[str, Any]:
    status = workspace_status(root)
    if status["status"] != "PASS":
        raise HarnessError("canonical workspace check failed: " + " ".join(status["problems"]))
    return status
