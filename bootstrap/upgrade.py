"""Pre-replacement compatibility validation and rollback-aware release switching."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import (
    DeploymentPaths,
    atomic_write,
    ensure_within,
    load_versions,
)
from bootstrap.doctor import Doctor
from bootstrap.services import ServiceManager
from bootstrap.wizard import install_launch_agents


class UpgradeError(RuntimeError):
    pass


_TRANSIENT_POST_SWITCH_CHECKS = frozenset(
    {
        "fourteen_projections_and_checkpoints",
        "openclaw_compatibility_and_plugin_discovery",
        "openclaw_cron_reconciliation",
        "launchd_and_process_state",
        "ports_and_loopback_binding",
    }
)


def validate_candidate(candidate: Path) -> dict[str, Any]:
    """Fail before any service or active-release mutation on incompatibility."""
    candidate = candidate.resolve()
    required = (
        candidate / "poetry.lock",
        candidate / "bootstrap" / "versions.json",
        candidate / "openclaw" / "administrateme" / "package.json",
        candidate / "openclaw" / "administrateme" / "openclaw.plugin.json",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise UpgradeError(f"candidate is incomplete: {missing}")
    active = load_versions()
    incoming = load_versions(candidate)
    active_claw = active["openclaw"]
    incoming_claw = incoming["openclaw"]
    for key in ("version", "commit", "plugin_api"):
        if incoming_claw[key] != active_claw[key]:
            raise UpgradeError(
                f"candidate OpenClaw {key} is incompatible; service replacement refused"
            )
    package = json.loads(
        (candidate / "openclaw" / "administrateme" / "package.json").read_text("utf-8")
    )
    build = package.get("openclaw", {}).get("build", {})
    compat = package.get("openclaw", {}).get("compat", {})
    if (
        build.get("openclawCommit") != active_claw["commit"]
        or compat.get("pluginApi") != active_claw["plugin_api"]
    ):
        raise UpgradeError(
            "candidate plugin package contradicts its compatibility manifest"
        )
    return {
        "compatible": True,
        "openclaw_version": active_claw["version"],
        "openclaw_commit": active_claw["commit"],
        "lockfile": "poetry.lock",
    }


class UpgradeManager:
    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        root: Path,
        test_mode: bool = False,
    ) -> None:
        self.paths = paths
        self.root = root.resolve()
        self.test_mode = test_mode
        self.services = ServiceManager(paths, test_mode=test_mode)

    @staticmethod
    def _failed_check_names(result: dict[str, Any]) -> set[str]:
        return {
            str(check.get("name"))
            for check in result.get("checks", [])
            if isinstance(check, dict) and not check.get("ok")
        }

    def _post_switch_doctor(self, release: Path) -> dict[str, Any]:
        """Wait for only known launch transients, then run the full proof once."""

        attempts = 1 if self.test_mode else 6
        last: dict[str, Any] = {}
        for attempt in range(attempts):
            last = self._run_release_doctor(release, include_synthetic_proof=False)
            if last.get("status") == "healthy":
                last = self._run_release_doctor(release, include_synthetic_proof=True)
                if last.get("status") == "healthy":
                    return last
            failed = self._failed_check_names(last)
            if not failed or not failed.issubset(_TRANSIENT_POST_SWITCH_CHECKS):
                return last
            if attempt + 1 < attempts:
                time.sleep(2)
        return last

    def _run_release_doctor(
        self, release: Path, *, include_synthetic_proof: bool
    ) -> dict[str, Any]:
        """Run Doctor with one coherent release registry, packs, and projections."""

        release_python = release / ".venv" / "bin" / "python"
        if self.test_mode or not release_python.is_file():
            return Doctor(
                self.paths,
                root=release,
                test_mode=self.test_mode,
            ).run(include_synthetic_proof=include_synthetic_proof)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("VIRTUAL_ENV", None)
        environment.update(
            {
                "ADMINME_INSTANCE_ROOT": str(self.paths.root),
                "ADMINME_INSTANCE_DIR": str(self.paths.instance_dir),
                "ADMINME_REPOSITORY_ROOT": str(release),
                "OPENCLAW_STATE_DIR": str(
                    self.paths.instance_dir / "openclaw" / "runtime"
                ),
                "OPENCLAW_CONFIG_PATH": str(
                    self.paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
                ),
                "PATH": (
                    f"{self.paths.root}/bin:/opt/homebrew/bin:"
                    "/opt/homebrew/opt/node@22/bin:/usr/local/bin:"
                    "/usr/bin:/bin:/usr/sbin:/sbin"
                ),
            }
        )
        command = [
            str(release_python),
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(self.paths.root),
            "doctor",
        ]
        if not include_synthetic_proof:
            command.append("--no-synthetic-proof")
        completed = subprocess.run(
            command,
            cwd=release,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise UpgradeError(
                "candidate Doctor did not return a valid diagnostic report"
            ) from exc
        if not isinstance(result, dict):
            raise UpgradeError("candidate Doctor returned an invalid diagnostic report")
        return result

    def activate(self, candidate: Path) -> dict[str, Any]:
        evidence = validate_candidate(candidate)
        candidate = candidate.resolve()
        commit = self._candidate_commit(candidate)
        release = self.paths.app_dir / "releases" / commit
        ensure_within(release, self.paths.app_dir / "releases")
        if not release.exists():
            shutil.copytree(
                candidate,
                release,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "node_modules",
                    "*.db",
                    "*.log",
                ),
            )
        validate_candidate(release)
        self._prepare_release(release)
        backup = BackupManager(
            self.paths,
            root=self.root,
            test_mode=self.test_mode,
        ).create()
        pointer = self.paths.app_dir / "current-release.json"
        previous = json.loads(pointer.read_text("utf-8")) if pointer.exists() else None
        was_running = self.services.status().status != "stopped"
        if was_running:
            self.services.stop()
        atomic_write(
            pointer,
            json.dumps(
                {"path": str(release), "commit": commit, "previous": previous},
                indent=2,
            ).encode("utf-8"),
        )
        try:
            install_launch_agents(self.paths, release, test_mode=self.test_mode)
            self.services.start()
            result = self._post_switch_doctor(release)
            if result["status"] != "healthy":
                failed = ", ".join(sorted(self._failed_check_names(result)))
                raise UpgradeError(
                    f"candidate failed post-switch Doctor: {failed or 'unknown check'}"
                )
            if not was_running:
                self.services.stop()
        except Exception:
            self.services.stop()
            if previous:
                atomic_write(
                    pointer,
                    json.dumps(previous, indent=2).encode("utf-8"),
                )
                install_launch_agents(
                    self.paths,
                    ensure_within(
                        Path(previous["path"]), self.paths.app_dir / "releases"
                    ),
                    test_mode=self.test_mode,
                )
            else:
                pointer.unlink(missing_ok=True)
            if was_running:
                self.services.start()
            raise
        return {
            "status": "upgraded",
            "commit": commit,
            "backup": backup["path"],
            "compatibility": evidence,
            "rollback_available": previous is not None,
        }

    def rollback(self) -> dict[str, Any]:
        pointer = self.paths.app_dir / "current-release.json"
        if not pointer.exists():
            raise UpgradeError("no active release pointer exists")
        current = json.loads(pointer.read_text("utf-8"))
        previous = current.get("previous")
        if not previous:
            raise UpgradeError("no prior release is recorded")
        release = Path(previous["path"]).resolve()
        ensure_within(release, self.paths.app_dir / "releases")
        validate_candidate(release)
        stopped = self.services.status().status == "stopped"
        if not stopped:
            self.services.stop()
        atomic_write(pointer, json.dumps(previous, indent=2).encode("utf-8"))
        try:
            install_launch_agents(self.paths, release, test_mode=self.test_mode)
            self.services.start()
            result = self._post_switch_doctor(release)
            if result["status"] != "healthy":
                failed = ", ".join(sorted(self._failed_check_names(result)))
                raise UpgradeError(
                    f"rolled-back release failed Doctor: {failed or 'unknown check'}"
                )
            if stopped:
                self.services.stop()
        except Exception:
            self.services.stop()
            atomic_write(pointer, json.dumps(current, indent=2).encode("utf-8"))
            install_launch_agents(
                self.paths,
                ensure_within(Path(current["path"]), self.paths.app_dir / "releases"),
                test_mode=self.test_mode,
            )
            if not stopped:
                self.services.start()
            raise
        return {"status": "rolled_back", "commit": previous["commit"]}

    def _prepare_release(self, release: Path) -> None:
        if self.test_mode:
            return
        if sys.version_info[:2] != (3, 11):
            raise UpgradeError("upgrade preparation requires the pinned Python 3.11")
        python = release / ".venv" / "bin" / "python"
        poetry_venv = self.paths.app_dir / "tooling" / "poetry-2.2.1"
        poetry = poetry_venv / "bin" / "poetry"
        if not poetry.is_file():
            subprocess.run(
                [sys.executable, "-m", "venv", str(poetry_venv)],
                check=True,
            )
            subprocess.run(
                [
                    str(poetry_venv / "bin" / "python"),
                    "-m",
                    "pip",
                    "install",
                    "poetry==2.2.1",
                ],
                check=True,
            )
        if not python.is_file():
            subprocess.run(
                [sys.executable, "-m", "venv", str(release / ".venv")],
                check=True,
            )
        poetry_environment = os.environ.copy()
        poetry_environment.pop("VIRTUAL_ENV", None)
        subprocess.run(
            [
                str(poetry),
                "install",
                "--only",
                "main",
                "--sync",
                "--no-interaction",
            ],
            cwd=release,
            check=True,
            env=poetry_environment,
        )
        plugin = release / "openclaw" / "administrateme"
        subprocess.run(["npm", "ci", "--ignore-scripts"], cwd=plugin, check=True)
        subprocess.run(["npm", "run", "check"], cwd=plugin, check=True)
        subprocess.run(["npm", "run", "build"], cwd=plugin, check=True)

    def _candidate_commit(self, candidate: Path) -> str:
        completed = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            return (
                "release-"
                + __import__("hashlib").sha256(str(candidate).encode()).hexdigest()[:12]
            )
        return completed.stdout.strip()


__all__ = ["UpgradeError", "UpgradeManager", "validate_candidate"]
