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
from bootstrap.keychain_probe import configured_keychain_references
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


def validate_candidate(
    candidate: Path,
    *,
    active_root: Path | None = None,
    approve_runtime_migration: bool = False,
) -> dict[str, Any]:
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
    active = load_versions(active_root)
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
    runtime_changes = {
        component: {
            "from": active[component]["compatible"],
            "to": incoming[component]["compatible"],
        }
        for component in ("python", "node")
        if incoming[component]["compatible"] != active[component]["compatible"]
    }
    if runtime_changes and not approve_runtime_migration:
        raise UpgradeError(
            "candidate changes the Python/Node runtime baseline; rerun only after "
            "the clean migration rehearsal and explicit --approve-runtime-migration"
        )
    return {
        "compatible": True,
        "openclaw_version": active_claw["version"],
        "openclaw_commit": active_claw["commit"],
        "lockfile": "poetry.lock",
        "runtime_migration": runtime_changes or None,
    }


class UpgradeManager:
    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        root: Path,
        test_mode: bool = False,
        approve_runtime_migration: bool = False,
    ) -> None:
        self.paths = paths
        self.root = root.resolve()
        self.test_mode = test_mode
        self.approve_runtime_migration = approve_runtime_migration
        self.services = ServiceManager(paths, test_mode=test_mode)

    def _active_release_root(self) -> Path:
        pointer = self.paths.app_dir / "current-release.json"
        if not pointer.is_file():
            return self.root
        try:
            active = Path(json.loads(pointer.read_text("utf-8"))["path"])
            return ensure_within(active, self.paths.app_dir / "releases")
        except (
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
        ) as exc:
            raise UpgradeError("active-release pointer is invalid") from exc

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
                    "/opt/homebrew/opt/node@24/bin:/usr/local/bin:"
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
        try:
            completed = subprocess.run(
                command,
                cwd=release,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpgradeError(
                "candidate Doctor exceeded 120 seconds; verify candidate-runtime "
                "Keychain access and inspect the redacted service logs"
            ) from exc
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise UpgradeError(
                "candidate Doctor did not return a valid diagnostic report"
            ) from exc
        if not isinstance(result, dict):
            raise UpgradeError("candidate Doctor returned an invalid diagnostic report")
        return result

    def _preflight_keychain_access(self, candidate: Path) -> None:
        """Fail before preparation or service mutation when macOS ACLs would prompt."""

        if self.test_mode or not self.approve_runtime_migration:
            return
        references = configured_keychain_references(self.paths)
        if not references:
            raise UpgradeError(
                "candidate runtime migration found no configured macOS Keychain references"
            )
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("VIRTUAL_ENV", None)
        environment["ADMINME_REPOSITORY_ROOT"] = str(candidate)
        for reference in references:
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "bootstrap.keychain_probe",
                        "--instance-root",
                        str(self.paths.root),
                        reference,
                    ],
                    cwd=candidate,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except subprocess.TimeoutExpired as exc:
                item_names = ", ".join(
                    sorted(value.rsplit("/", 1)[-1] for value in references)
                )
                raise UpgradeError(
                    "candidate Python is waiting on macOS Keychain access before "
                    "cutover. In Keychain Access > login, keep 'Confirm before "
                    "allowing access' and add the reviewed Python 3.14 application "
                    f"to these AdministrateMe items: {item_names}. Never select "
                    "'Allow all applications'. The active release was not changed."
                ) from exc
            if completed.returncode:
                raise UpgradeError(
                    "candidate Python cannot resolve every configured macOS Keychain "
                    "reference; repair the item ACL or restore the missing item before "
                    "migration. The active release was not changed."
                )

    def activate(self, candidate: Path) -> dict[str, Any]:
        evidence = validate_candidate(
            candidate,
            active_root=self._active_release_root(),
            approve_runtime_migration=self.approve_runtime_migration,
        )
        candidate = candidate.resolve()
        commit = self._candidate_commit(candidate)
        release = self.paths.app_dir / "releases" / commit
        ensure_within(release, self.paths.app_dir / "releases")
        self._preflight_keychain_access(candidate)
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
        validate_candidate(
            release,
            active_root=self._active_release_root(),
            approve_runtime_migration=self.approve_runtime_migration,
        )
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
        except Exception as candidate_error:
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
            rollback_error: Exception | None = None
            if was_running:
                try:
                    self.services.start()
                except Exception as exc:  # preserve the candidate failure as primary
                    rollback_error = exc
            if rollback_error is not None:
                raise UpgradeError(
                    f"candidate failed ({candidate_error}); the previous release "
                    "pointer and launchd definitions were restored, but its restart "
                    f"also reported: {rollback_error}"
                ) from candidate_error
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
        validate_candidate(
            release,
            active_root=self._active_release_root(),
            approve_runtime_migration=True,
        )
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
        versions = load_versions(release)
        python_formula = str(versions["python"]["formula"])
        python_minor = str(versions["python"]["preferred"]).rsplit(".", 1)[0]
        python_executable = (
            Path("/opt/homebrew/opt") / python_formula / "bin" / f"python{python_minor}"
        )
        if not python_executable.is_file():
            raise UpgradeError(
                f"candidate preparation requires side-by-side {python_formula}; "
                "the active release was not changed"
            )
        python = release / ".venv" / "bin" / "python"
        poetry_version = str(versions["package_managers"]["poetry"])
        poetry_venv = self.paths.app_dir / "tooling" / f"poetry-{poetry_version}"
        poetry = poetry_venv / "bin" / "poetry"
        if not poetry.is_file():
            subprocess.run(
                [str(python_executable), "-m", "venv", str(poetry_venv)],
                check=True,
            )
            subprocess.run(
                [
                    str(poetry_venv / "bin" / "python"),
                    "-m",
                    "pip",
                    "install",
                    f"poetry=={poetry_version}",
                ],
                check=True,
            )
        if not python.is_file():
            subprocess.run(
                [str(python_executable), "-m", "venv", str(release / ".venv")],
                check=True,
            )
        poetry_environment = os.environ.copy()
        poetry_environment.pop("VIRTUAL_ENV", None)
        node_bin = Path("/opt/homebrew/opt") / str(versions["node"]["formula"]) / "bin"
        if not (node_bin / "node").is_file():
            raise UpgradeError(
                f"candidate preparation requires side-by-side {versions['node']['formula']}; "
                "the active release was not changed"
            )
        poetry_environment["PATH"] = f"{node_bin}:{poetry_environment.get('PATH', '')}"
        subprocess.run(
            [
                str(poetry),
                "sync",
                "--only",
                "main",
                "--no-interaction",
            ],
            cwd=release,
            check=True,
            env=poetry_environment,
        )
        plugin = release / "openclaw" / "administrateme"
        npm = str(node_bin / "npm")
        subprocess.run([npm, "ci", "--ignore-scripts"], cwd=plugin, check=True)
        subprocess.run([npm, "run", "check"], cwd=plugin, check=True)
        subprocess.run([npm, "run", "build"], cwd=plugin, check=True)

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
