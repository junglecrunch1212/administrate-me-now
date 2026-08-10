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
from bootstrap.openclaw_tools import (
    configure_required_llm_tool,
    reconcile_administrateme_plugin,
)
from bootstrap.services import ServiceManager, source_assurance_terminal_evidence
from bootstrap.wizard import install_launch_agents, service_environment_path


class UpgradeError(RuntimeError):
    pass


_TRANSIENT_POST_SWITCH_CHECKS = frozenset(
    {
        "fourteen_projections_and_checkpoints",
        "openclaw_compatibility_and_plugin_discovery",
        "openclaw_cron_reconciliation",
        "launchd_and_process_state",
        "ports_and_loopback_binding",
        "source_assurance_terminal_dispositions",
    }
)
_SOURCE_SETTLEMENT_DERIVED_DEGRADATIONS = frozenset(
    {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
)
# A full reviewed language-bound replay can traverse more than one thousand
# immutable message facts on a household instance.  Keep this bounded, but do
# not make a healthy, advancing replay race an unrealistically short rollback
# clock.
POST_SWITCH_REPLAY_SETTLEMENT_SECONDS = 60 * 60
POST_SWITCH_POLL_SECONDS = 2


def validate_candidate(
    candidate: Path,
    *,
    active_root: Path | None = None,
    approve_runtime_migration: bool = False,
    allow_openclaw_rollback: bool = False,
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
    for key in ("version", "plugin_api"):
        if incoming_claw[key] != active_claw[key]:
            raise UpgradeError(
                f"candidate OpenClaw {key} is incompatible; service replacement refused"
            )
    commit_changed = incoming_claw["commit"] != active_claw["commit"]
    reviewed_forward_transition = (
        incoming_claw.get("supersedes_commit") == active_claw["commit"]
    )
    if commit_changed and not (reviewed_forward_transition or allow_openclaw_rollback):
        raise UpgradeError(
            "candidate OpenClaw commit is not the declared reviewed successor; "
            "service replacement refused"
        )
    package = json.loads(
        (candidate / "openclaw" / "administrateme" / "package.json").read_text("utf-8")
    )
    build = package.get("openclaw", {}).get("build", {})
    compat = package.get("openclaw", {}).get("compat", {})
    if (
        build.get("openclawCommit") != incoming_claw["commit"]
        or compat.get("pluginApi") != active_claw["plugin_api"]
    ):
        raise UpgradeError(
            "candidate plugin package contradicts its compatibility manifest"
        )
    runtime_coordinates = ("compatible", "preferred", "formula")
    runtime_changes = {
        component: {
            "from": {
                coordinate: active[component][coordinate]
                for coordinate in runtime_coordinates
            },
            "to": {
                coordinate: incoming[component][coordinate]
                for coordinate in runtime_coordinates
            },
        }
        for component in ("python", "node")
        if any(
            incoming[component][coordinate] != active[component][coordinate]
            for coordinate in runtime_coordinates
        )
    }
    if runtime_changes and not approve_runtime_migration:
        raise UpgradeError(
            "candidate changes the Python/Node runtime baseline; rerun only after "
            "the clean migration rehearsal and explicit --approve-runtime-migration"
        )
    return {
        "compatible": True,
        "openclaw_version": active_claw["version"],
        "openclaw_commit": incoming_claw["commit"],
        "openclaw_migration": (
            {"from": active_claw["commit"], "to": incoming_claw["commit"]}
            if commit_changed
            else None
        ),
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

    def _attest_controller_provenance(self, candidate: Path, commit: str) -> None:
        """Require production upgrades to execute the reviewed candidate policy."""

        attested_commit = os.environ.get("ADMINME_UPGRADE_CONTROLLER_COMMIT")
        if self.test_mode and attested_commit is None:
            return
        if attested_commit is None:
            raise UpgradeError(
                "upgrade controller provenance is missing; use bootstrap/update.sh"
            )
        if attested_commit != commit:
            raise UpgradeError(
                "upgrade controller commit does not match the reviewed candidate"
            )
        controller_source = Path(__file__).resolve()
        candidate_source = (candidate / "bootstrap" / "upgrade.py").resolve()
        if controller_source != candidate_source:
            raise UpgradeError(
                "upgrade controller did not load from the reviewed candidate"
            )

    @staticmethod
    def _failed_check_names(result: dict[str, Any]) -> set[str]:
        return {
            str(check.get("name"))
            for check in result.get("checks", [])
            if isinstance(check, dict) and not check.get("ok")
        }

    @staticmethod
    def _has_new_structural_degradation(diagnostics: dict[str, Any]) -> bool:
        """Reject a runtime capability failure that emerges during replay."""

        capabilities = diagnostics.get("capabilities")
        if not isinstance(capabilities, dict):
            return True
        for capability_id, capability in capabilities.items():
            if capability_id in _SOURCE_SETTLEMENT_DERIVED_DEGRADATIONS:
                continue
            if isinstance(capability, dict) and capability.get("state") == "degraded":
                return True
        return False

    def _post_switch_doctor(self, release: Path) -> dict[str, Any]:
        """Wait for only known launch transients, then run the full proof once."""

        # Historical source replay may walk tens of thousands of encrypted
        # facts before its candidate-bearing source prefix. Repeating full
        # Doctor during that work competes for the same log and proves nothing
        # new. Poll only the content-free readiness document until source
        # settlement, then rerun the complete proof. Structural failures still
        # return on their first observation.
        deadline = time.monotonic() + (
            0 if self.test_mode else POST_SWITCH_REPLAY_SETTLEMENT_SECONDS
        )
        last = self._run_release_doctor(release, include_synthetic_proof=False)
        while True:
            if last.get("status") == "healthy":
                last = self._run_release_doctor(release, include_synthetic_proof=True)
                if last.get("status") == "healthy":
                    return last
            failed = self._failed_check_names(last)
            if not failed or not failed.issubset(_TRANSIENT_POST_SWITCH_CHECKS):
                return last
            if self.test_mode:
                return last

            if "source_assurance_terminal_dispositions" in failed:
                diagnostics = self.services.runtime_diagnostics()
                if diagnostics is None:
                    # A first source audit or saturated language-bound replay
                    # may briefly outlive one diagnostics request. Keep waiting
                    # only while the intentionally cheap liveness proof remains
                    # true; loss of liveness is structural and returns at once.
                    if self.services.runtime_is_live() and time.monotonic() < deadline:
                        time.sleep(POST_SWITCH_POLL_SECONDS)
                        continue
                    return last
                if self._has_new_structural_degradation(diagnostics):
                    return self._run_release_doctor(
                        release, include_synthetic_proof=False
                    )
                settled, _evidence = source_assurance_terminal_evidence(diagnostics)
                if not settled and time.monotonic() < deadline:
                    time.sleep(POST_SWITCH_POLL_SECONDS)
                    continue

            if time.monotonic() >= deadline:
                final = self._run_release_doctor(release, include_synthetic_proof=False)
                if final.get("status") == "healthy":
                    return self._run_release_doctor(
                        release, include_synthetic_proof=True
                    )
                return final

            time.sleep(POST_SWITCH_POLL_SECONDS)
            last = self._run_release_doctor(release, include_synthetic_proof=False)

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
                "PATH": service_environment_path(self.paths, release),
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
                    "candidate Python is waiting on its runtime-bound macOS "
                    f"Keychain copies before cutover: {item_names}. Do not reset "
                    "the login Keychain or select 'Allow all applications'. The "
                    "active release and its original items were not changed."
                ) from exc
            if completed.returncode:
                raise UpgradeError(
                    "candidate Python cannot resolve every configured logical and "
                    "runtime-bound macOS Keychain reference. The active release and "
                    "its original items were not changed."
                )

    def activate(self, candidate: Path) -> dict[str, Any]:
        candidate = candidate.resolve()
        commit = self._candidate_commit(candidate)
        self._attest_controller_provenance(candidate, commit)
        evidence = validate_candidate(
            candidate,
            active_root=self._active_release_root(),
            approve_runtime_migration=self.approve_runtime_migration,
        )
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
        previous_release = (
            ensure_within(Path(previous["path"]), self.paths.app_dir / "releases")
            if previous
            else None
        )
        was_running = self.services.status().status != "stopped"
        pointer_switched = False
        openclaw_switched = False
        plugin_registration_attempted = False
        try:
            self._install_reviewed_openclaw(release)
            openclaw_switched = True
            if was_running:
                self.services.stop()
            plugin_registration_attempted = True
            reconcile_administrateme_plugin(
                self.paths,
                release,
                test_mode=self.test_mode,
            )
            atomic_write(
                pointer,
                json.dumps(
                    {"path": str(release), "commit": commit, "previous": previous},
                    indent=2,
                ).encode("utf-8"),
            )
            pointer_switched = True
            configure_required_llm_tool(self.paths, test_mode=self.test_mode)
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
            if previous and pointer_switched:
                assert previous_release is not None
                atomic_write(
                    pointer,
                    json.dumps(previous, indent=2).encode("utf-8"),
                )
                install_launch_agents(
                    self.paths,
                    previous_release,
                    test_mode=self.test_mode,
                )
            elif pointer_switched:
                pointer.unlink(missing_ok=True)
            rollback_error: Exception | None = None
            if openclaw_switched and previous_release is not None:
                try:
                    self._install_reviewed_openclaw(previous_release)
                    if plugin_registration_attempted:
                        reconcile_administrateme_plugin(
                            self.paths,
                            previous_release,
                            test_mode=self.test_mode,
                        )
                except Exception as exc:
                    rollback_error = exc
            if was_running:
                try:
                    self.services.start()
                except Exception as exc:  # preserve the candidate failure as primary
                    rollback_error = rollback_error or exc
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
            "upgrade_controller_attested": True,
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
            allow_openclaw_rollback=True,
        )
        current_release = self._active_release_root()
        stopped = self.services.status().status == "stopped"
        try:
            self._install_reviewed_openclaw(release)
            if not stopped:
                self.services.stop()
            reconcile_administrateme_plugin(
                self.paths,
                release,
                test_mode=self.test_mode,
            )
            atomic_write(pointer, json.dumps(previous, indent=2).encode("utf-8"))
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
            self._install_reviewed_openclaw(current_release)
            reconcile_administrateme_plugin(
                self.paths,
                current_release,
                test_mode=self.test_mode,
            )
            install_launch_agents(
                self.paths,
                ensure_within(Path(current["path"]), self.paths.app_dir / "releases"),
                test_mode=self.test_mode,
            )
            if not stopped:
                self.services.start()
            raise
        return {"status": "rolled_back", "commit": previous["commit"]}

    def _install_reviewed_openclaw(self, release: Path) -> None:
        """Build and atomically select the exact OpenClaw pin for a release."""

        if self.test_mode:
            return
        script = release / "bootstrap" / "install-openclaw-source.sh"
        python = release / ".venv" / "bin" / "python"
        versions = load_versions(release)
        node_bin = Path("/opt/homebrew/opt") / str(versions["node"]["formula"]) / "bin"
        if (
            not script.is_file()
            or not python.is_file()
            or not (node_bin / "node").is_file()
        ):
            raise UpgradeError("candidate cannot install its reviewed OpenClaw source")
        environment = os.environ.copy()
        environment["PATH"] = f"{node_bin}:{environment.get('PATH', '')}"
        selected = self.paths.root / "bin" / "openclaw"
        previous_target = selected.resolve() if selected.exists() else None
        completed = subprocess.run(
            [
                str(script),
                "--instance-root",
                str(self.paths.root),
                "--python",
                str(python),
            ],
            cwd=release,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if completed.returncode:
            self._restore_openclaw_selection(selected, previous_target)
            raise UpgradeError("reviewed OpenClaw source installation failed")
        commit = str(versions["openclaw"]["commit"])
        expected = self.paths.app_dir / "openclaw" / commit / "openclaw.mjs"
        if (
            not expected.is_file()
            or not selected.exists()
            or selected.resolve() != expected.resolve()
        ):
            self._restore_openclaw_selection(selected, previous_target)
            raise UpgradeError("reviewed OpenClaw executable selection did not verify")

    @staticmethod
    def _restore_openclaw_selection(
        selected: Path, previous_target: Path | None
    ) -> None:
        """Atomically restore only the narrow executable selector after failure."""

        if previous_target is None:
            selected.unlink(missing_ok=True)
            return
        temporary = selected.with_name(f".{selected.name}.rollback.{os.getpid()}")
        temporary.unlink(missing_ok=True)
        try:
            temporary.symlink_to(previous_target)
            os.replace(temporary, selected)
        finally:
            temporary.unlink(missing_ok=True)

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
