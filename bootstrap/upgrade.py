"""Pre-replacement compatibility validation and rollback-aware release switching."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths, atomic_write, ensure_within, load_versions
from bootstrap.doctor import Doctor
from bootstrap.services import ServiceManager
from bootstrap.wizard import install_launch_agents


class UpgradeError(RuntimeError):
    pass


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
        (candidate / "openclaw" / "administrateme" / "package.json").read_text(
            "utf-8"
        )
    )
    build = package.get("openclaw", {}).get("build", {})
    compat = package.get("openclaw", {}).get("compat", {})
    if (
        build.get("openclawCommit") != active_claw["commit"]
        or compat.get("pluginApi") != active_claw["plugin_api"]
    ):
        raise UpgradeError("candidate plugin package contradicts its compatibility manifest")
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
            result = Doctor(
                self.paths,
                root=release,
                test_mode=self.test_mode,
            ).run()
            if result["status"] != "healthy":
                raise UpgradeError("candidate failed post-switch Doctor")
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
            result = Doctor(
                self.paths,
                root=release,
                test_mode=self.test_mode,
            ).run()
            if result["status"] != "healthy":
                raise UpgradeError("rolled-back release failed Doctor")
            if stopped:
                self.services.stop()
        except Exception:
            self.services.stop()
            atomic_write(pointer, json.dumps(current, indent=2).encode("utf-8"))
            install_launch_agents(
                self.paths,
                ensure_within(
                    Path(current["path"]), self.paths.app_dir / "releases"
                ),
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
        if not python.is_file():
            subprocess.run(
                [sys.executable, "-m", "venv", str(release / ".venv")],
                check=True,
            )
            subprocess.run(
                [str(python), "-m", "pip", "install", "poetry==2.2.1"],
                check=True,
            )
        subprocess.run(
            [
                str(release / ".venv" / "bin" / "poetry"),
                "install",
                "--only",
                "main",
                "--sync",
                "--no-interaction",
            ],
            cwd=release,
            check=True,
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
            return "release-" + __import__("hashlib").sha256(
                str(candidate).encode()
            ).hexdigest()[:12]
        return completed.stdout.strip()


__all__ = ["UpgradeError", "UpgradeManager", "validate_candidate"]
