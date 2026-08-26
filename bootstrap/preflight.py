"""Read-only preflight checks. No deployment path is created here."""

from __future__ import annotations

import os
import platform
import grp
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from bootstrap.contracts import load_versions


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    remediation: str | None = None


class PreflightFailure(RuntimeError):
    def __init__(self, checks: list[PreflightCheck]) -> None:
        self.checks = checks
        failures = "; ".join(check.detail for check in checks if not check.ok)
        super().__init__(failures)


def _macos_major(version: str) -> int:
    try:
        return int(version.split(".", 1)[0])
    except ValueError:
        return -1


def _command_version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    return (completed.stdout or completed.stderr).strip().splitlines()[0]


def _group_names() -> frozenset[str]:
    names: set[str] = set()
    for group_id in os.getgroups():
        try:
            names.add(grp.getgrgid(group_id).gr_name)
        except KeyError:
            continue
    return frozenset(names)


def run_preflight(
    target_root: Path,
    *,
    system: str | None = None,
    machine: str | None = None,
    macos_version: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    require_dependencies: bool = True,
    check_filevault: bool = True,
    group_names: frozenset[str] | None = None,
) -> list[PreflightCheck]:
    """Perform all checks before target-root mutation and raise on failure."""
    versions = load_versions()
    actual_system = system or platform.system()
    actual_machine = machine or platform.machine()
    actual_macos = macos_version or platform.mac_ver()[0]
    supported_majors = versions["macos"]["supported_majors"]
    get_effective_user_id = getattr(os, "geteuid", None)
    running_as_non_root = (
        get_effective_user_id() != 0 if callable(get_effective_user_id) else True
    )
    actual_groups = group_names
    if actual_groups is None:
        actual_groups = _group_names()
    dedicated_standard_account = running_as_non_root and "admin" not in actual_groups
    checks = [
        PreflightCheck(
            "operating_system",
            actual_system == "Darwin",
            f"host operating system is {actual_system or 'unknown'}",
            "Run this installer only on the disposable Apple-silicon Mac.",
        ),
        PreflightCheck(
            "architecture",
            actual_machine == "arm64",
            f"host architecture is {actual_machine or 'unknown'}",
            "Use an Apple-silicon Mac; Rosetta/x86_64 execution is unsupported.",
        ),
        PreflightCheck(
            "macos_version",
            _macos_major(actual_macos) in supported_majors,
            f"macOS version is {actual_macos or 'unknown'}",
            f"Install a supported macOS major: {supported_majors}.",
        ),
        PreflightCheck(
            "dedicated_account",
            dedicated_standard_account,
            (
                "installer is running as a standard non-root user"
                if dedicated_standard_account
                else "installer account is root or belongs to the admin group"
            ),
            "Log in to the dedicated Standard AdministrateMe account; do not use an administrator account or sudo.",
        ),
    ]
    parent = target_root.expanduser().resolve().parent
    existing_parent = parent
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    free_bytes = shutil.disk_usage(existing_parent).free
    checks.append(
        PreflightCheck(
            "disk_space",
            free_bytes >= 10 * 1024**3,
            f"free disk space is {free_bytes // 1024**3} GiB",
            "Free at least 10 GiB before bootstrap.",
        )
    )
    required_commands = (
        "git",
        "security",
        "launchctl",
        "plutil",
        "brew",
        "python3.14",
        "node",
        "npm",
    )
    if require_dependencies:
        for command in required_commands:
            found = which(command)
            checks.append(
                PreflightCheck(
                    f"command:{command}",
                    found is not None,
                    f"{command} is {'available' if found else 'missing'}",
                    f"Install or repair {command} before continuing.",
                )
            )
    if actual_system == "Darwin" and check_filevault:
        filevault = _command_version(["/usr/bin/fdesetup", "status"])
        checks.append(
            PreflightCheck(
                "filevault",
                bool(filevault and "On" in filevault),
                filevault or "FileVault status unavailable",
                "Turn on FileVault in System Settings, then rerun bootstrap.",
            )
        )
    if any(not check.ok for check in checks):
        raise PreflightFailure(checks)
    return checks


def public_checks(checks: list[PreflightCheck]) -> list[dict[str, object]]:
    return [asdict(check) for check in checks]
