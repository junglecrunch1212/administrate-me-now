"""Pre-replacement compatibility validation and rollback-aware release switching."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from bootstrap.activation_transaction import (
    ActivationJournal,
    ActivationJournalError,
    sha256_json,
)
from bootstrap.backup_restore import BackupManager
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    build_active_attestation_from_pending,
    capture_deployment_snapshot,
    load_pending_attestation_for_activation,
)
from bootstrap.contracts import (
    PROJECTION_NAMES,
    DeploymentPaths,
    atomic_write,
    ensure_within,
    load_versions,
)
from bootstrap.doctor import Doctor, _required_reactive_pipeline_ids
from bootstrap.deployment_failure import (
    failure_evidence,
    failure_summary,
    persist_failure,
)
from bootstrap.deployment_budget import (
    DeploymentBudget,
    DeploymentBudgetError,
    deployment_clock,
)
from bootstrap.keychain_probe import configured_keychain_references
from bootstrap.openclaw_tools import (
    configure_required_llm_tool,
    reconcile_administrateme_plugin,
)
from bootstrap.provider_binding_transition import (
    ProviderBindingTransitionError,
    prepare_live_provider_binding_transition,
)
from bootstrap.services import (
    RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS,
    ServiceManager,
    isolated_degraded_adapter_ids,
)
from bootstrap.service_settlement import (
    POST_SWITCH_LIVE_READINESS_RETRIES,
    POST_SWITCH_POLL_SECONDS,
    POST_SWITCH_REPLAY_SETTLEMENT_SECONDS,
    RELEASE_DOCTOR_TIMEOUT_SECONDS,
    doctor_has_only_transient_deployment_readiness_gap,
    failed_check_names,
    failed_checks_are_known_transients,
    has_new_structural_degradation,
    runtime_has_only_transient_deployment_readiness_gap,
    run_release_doctor,
    wait_for_settled_doctor,
)
from bootstrap.source_assessment_repair import (
    SourceAssessmentRepairPolicy,
    hydrate_online_repair_diagnostics,
    load_repair_policy,
    offline_validate_repair_baseline,
    validate_declared_baseline,
)
from bootstrap.source_identity import (
    GENERATED_EXTRA_POLICY,
    SourceIdentityError,
    SourceProcessNotReaped,
    materialize_git_tree,
    remove_generated_runtime_artifacts,
    reviewed_candidate_commit,
    verify_release_source_identity,
    write_source_marker_at,
)
from bootstrap.wizard import install_launch_agents


class UpgradeError(RuntimeError):
    pass


_PREPARATION_STATE_CONTRACT = "administrateme-release-preparation-v1"
_PREPARATION_STATE_PHASES = frozenset(
    {"publishing", "publishing_unreaped", "preparing", "complete", "consumed"}
)
_PREPARATION_STATE_FIELDS = frozenset(
    {
        "contract",
        "phase",
        "commit",
        "release_name",
        "stage_name",
        "operation_nonce",
        "candidate_controller_commit",
        "uid",
        "instance_sha256",
        "pointer_chain_sha256",
        "generated_policy",
        "source_manifest_sha256",
        "source_file_count",
        "release_device",
        "release_inode",
        "activity_device",
        "activity_inode",
        "content_exposed",
    }
)
_MAX_RELEASE_POINTER_DEPTH = 64
_PREPARATION_COMMAND_TIMEOUT_SECONDS = 30 * 60


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


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
        approve_source_assessment_repair: bool = False,
        approve_provider_binding_migration: bool = False,
        pending_attestation_id: str | None = None,
    ) -> None:
        self.paths = paths
        self.root = root.resolve()
        self.test_mode = test_mode
        self.approve_runtime_migration = approve_runtime_migration
        self.approve_source_assessment_repair = approve_source_assessment_repair
        self.approve_provider_binding_migration = approve_provider_binding_migration
        self.pending_attestation_id = pending_attestation_id
        self.services = ServiceManager(paths, test_mode=test_mode)
        self.activation_journal = ActivationJournal(paths)
        self._failure_audit_attempt_id = uuid.uuid4().hex
        self._failure_audit_available: bool | None = None
        self._deployment_budget: DeploymentBudget | None = None
        self._settlement_deadline: float | None = None
        self._candidate_settlement_deadline: float | None = None
        self._recovery_settlement_deadline: float | None = None
        self._active_preparation_activity_descriptor: int | None = None
        self._active_preparation_activity_path: Path | None = None
        self._active_preparation_activity_coordinate: tuple[int, int] | None = None

    def _begin_deployment_budget(self) -> DeploymentBudget:
        if self._deployment_budget is None:
            supervised = os.environ.get("ADMINME_UPDATE_SUPERVISED") == "1"
            if supervised and (
                not os.environ.get("ADMINME_UPGRADE_OPERATION_LOCK_FD")
                or not os.environ.get("ADMINME_UPGRADE_CONTROLLER_COMMIT")
                or os.environ.get("ADMINME_UPGRADE_CONTROLLER_ROOT")
                != str(Path(__file__).resolve().parents[1])
            ):
                raise UpgradeError(
                    "supervised controller deadline provenance is missing"
                )
            try:
                self._deployment_budget = DeploymentBudget.from_environment(
                    os.environ, now=deployment_clock(), supervised=supervised
                )
            except DeploymentBudgetError as exc:
                raise UpgradeError(str(exc)) from exc
        return self._deployment_budget

    def _require_activation_capacity(self, *, begin_settlement: bool = True) -> None:
        try:
            deadline = self._begin_deployment_budget().candidate_deadline(
                now=deployment_clock()
            )
        except DeploymentBudgetError as exc:
            raise UpgradeError(str(exc)) from exc
        if not begin_settlement:
            return
        if self._candidate_settlement_deadline is None:
            self._candidate_settlement_deadline = deadline
        self._settlement_deadline = self._candidate_settlement_deadline

    def _begin_recovery_settlement(self) -> None:
        if self._recovery_settlement_deadline is None:
            try:
                self._recovery_settlement_deadline = (
                    self._begin_deployment_budget().recovery_deadline(
                        now=deployment_clock()
                    )
                )
            except DeploymentBudgetError as exc:
                raise UpgradeError(str(exc)) from exc
        self._settlement_deadline = self._recovery_settlement_deadline

    @staticmethod
    def _close_descriptors(*descriptors: int | None) -> BaseException | None:
        first_error: BaseException | None = None
        for descriptor in descriptors:
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                first_error = first_error or exc
        return first_error

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        """Serialize every cooperative activate/rollback operation."""

        self._ensure_private_directory(self.paths.root)
        self._ensure_private_directory(self.paths.app_dir, parent=self.paths.root)
        inherited_value = os.environ.get("ADMINME_UPGRADE_OPERATION_LOCK_FD")
        if inherited_value is not None:
            try:
                inherited_descriptor = int(inherited_value)
                metadata = os.fstat(inherited_descriptor)
                entry = (self.paths.app_dir / ".upgrade-operation.lock").lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or (metadata.st_dev, metadata.st_ino)
                    != (entry.st_dev, entry.st_ino)
                ):
                    raise UpgradeError("inherited upgrade operation lock is unsafe")
                fcntl.flock(
                    inherited_descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                locked = os.fstat(inherited_descriptor)
                public_after = (self.paths.app_dir / ".upgrade-operation.lock").lstat()
                if (locked.st_dev, locked.st_ino) != (
                    public_after.st_dev,
                    public_after.st_ino,
                ):
                    raise UpgradeError("inherited upgrade operation lock changed")
            except (OSError, ValueError) as exc:
                raise UpgradeError(
                    "inherited upgrade operation lock is unsafe"
                ) from exc
            yield
            return
        app_descriptor: int | None = None
        lock_descriptor: int | None = None
        failed = False
        try:
            app_descriptor = os.open(
                self.paths.app_dir,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            lock_descriptor = os.open(
                ".upgrade-operation.lock",
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=app_descriptor,
            )
            metadata = os.fstat(lock_descriptor)
            entry = os.stat(
                ".upgrade-operation.lock",
                dir_fd=app_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)
            ):
                raise UpgradeError("upgrade operation lock is unsafe")
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpgradeError(
                    "another upgrade or rollback operation is already running"
                ) from exc
            entry_after = os.stat(
                ".upgrade-operation.lock",
                dir_fd=app_descriptor,
                follow_symlinks=False,
            )
            locked = os.fstat(lock_descriptor)
            if (entry_after.st_dev, entry_after.st_ino) != (
                locked.st_dev,
                locked.st_ino,
            ):
                raise UpgradeError("upgrade operation lock changed")
            yield
        except BaseException:
            failed = True
            raise
        finally:
            cleanup_error: BaseException | None = None
            if lock_descriptor is not None:
                try:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            close_error = self._close_descriptors(
                lock_descriptor,
                app_descriptor,
            )
            cleanup_error = cleanup_error or close_error
            if cleanup_error is not None and not failed:
                raise UpgradeError(
                    "upgrade operation lock cleanup failed"
                ) from cleanup_error

    def _read_private_json_payload(
        self,
        path: Path,
        *,
        missing_ok: bool = False,
        maximum_size: int = 4 * 1024 * 1024,
    ) -> tuple[bytes | None, dict[str, Any] | None]:
        """Read one mutable control file without following or racing its name."""

        if not os.path.lexists(path):
            if missing_ok:
                return None, None
            raise UpgradeError(f"required control file is missing: {path.name}")
        descriptor: int | None = None
        try:
            public_before = path.lstat()
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size <= 0
                or before.st_size > maximum_size
                or (before.st_dev, before.st_ino)
                != (public_before.st_dev, public_before.st_ino)
            ):
                raise UpgradeError(f"control file is unsafe: {path.name}")
            payload = bytearray()
            while len(payload) < before.st_size:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, before.st_size - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            public_after = path.lstat()
            if (
                len(payload) != before.st_size
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                )
                or (after.st_dev, after.st_ino)
                != (public_after.st_dev, public_after.st_ino)
            ):
                raise UpgradeError(f"control file changed while read: {path.name}")

            def reject_duplicate_keys(
                pairs: list[tuple[str, Any]],
            ) -> dict[str, Any]:
                value: dict[str, Any] = {}
                for key, child in pairs:
                    if key in value:
                        raise UpgradeError(
                            f"control file has duplicate keys: {path.name}"
                        )
                    value[key] = child
                return value

            value = json.loads(
                bytes(payload).decode("utf-8"),
                object_pairs_hook=reject_duplicate_keys,
            )
            if not isinstance(value, dict):
                raise UpgradeError(f"control file is invalid: {path.name}")
            return bytes(payload), value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeError(f"control file is invalid: {path.name}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _hash_private_file(
        self,
        path: Path,
        *,
        maximum_size: int = 8 * 1024 * 1024 * 1024,
    ) -> str:
        """Hash one private regular artifact while binding its public coordinate."""

        descriptor: int | None = None
        try:
            public_before = path.lstat()
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size <= 0
                or before.st_size > maximum_size
                or (before.st_dev, before.st_ino)
                != (public_before.st_dev, public_before.st_ino)
            ):
                raise UpgradeError(f"private artifact is unsafe: {path.name}")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_size:
                    raise UpgradeError("private artifact exceeds its size boundary")
                digest.update(chunk)
            after = os.fstat(descriptor)
            public_after = path.lstat()
            if (
                total != before.st_size
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                )
                or (after.st_dev, after.st_ino)
                != (public_after.st_dev, public_after.st_ino)
            ):
                raise UpgradeError("private artifact changed while hashed")
            return digest.hexdigest()
        except OSError as exc:
            raise UpgradeError(f"private artifact is unavailable: {path.name}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _test_crash_after_journal_phase(self, phase: str) -> None:
        """Deterministic process-death seam available only to explicit test mode."""

        if (
            self.test_mode
            and os.environ.get("ADMINME_TEST_ACTIVATION_CRASH_AFTER") == phase
        ):
            os.kill(os.getpid(), signal.SIGKILL)

    def _read_release_pointer(self) -> dict[str, Any] | None:
        pointer = self.paths.app_dir / "current-release.json"
        if not os.path.lexists(pointer):
            return None
        descriptor: int | None = None
        try:
            public_before = pointer.lstat()
            descriptor = os.open(
                pointer,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size <= 0
                or before.st_size > 1024 * 1024
                or (before.st_dev, before.st_ino)
                != (public_before.st_dev, public_before.st_ino)
            ):
                raise UpgradeError("active-release pointer is unsafe")
            payload = bytearray()
            while len(payload) <= before.st_size:
                chunk = os.read(
                    descriptor, min(64 * 1024, before.st_size - len(payload))
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            public_after = pointer.lstat()
            if (
                len(payload) != before.st_size
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                or (after.st_dev, after.st_ino)
                != (
                    public_after.st_dev,
                    public_after.st_ino,
                )
            ):
                raise UpgradeError("active-release pointer changed while reading")
            document = json.loads(bytes(payload).decode("utf-8"))
            if not isinstance(document, dict):
                raise UpgradeError("active-release pointer is invalid")
            return document
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeError("active-release pointer is invalid") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _release_pointer_state(
        self,
    ) -> tuple[
        dict[str, Any] | None,
        Path,
        frozenset[Path],
        str,
        tuple[dict[str, str], ...],
    ]:
        document = self._read_release_pointer()
        if document is None:
            digest = hashlib.sha256(b"[]").hexdigest()
            return None, self.root, frozenset(), digest, ()
        releases = self.paths.app_dir / "releases"
        protected: set[Path] = set()
        commits: set[str] = set()
        rows: list[dict[str, str]] = []
        current: object = document
        for _depth in range(_MAX_RELEASE_POINTER_DEPTH):
            if not isinstance(current, dict):
                raise UpgradeError("active-release pointer chain is invalid")
            if not {"commit", "path"}.issubset(current) or not set(current).issubset(
                {"commit", "path", "previous"}
            ):
                raise UpgradeError("active-release pointer chain is invalid")
            commit = current.get("commit")
            raw_path = current.get("path")
            if (
                not isinstance(commit, str)
                or not _is_lower_hex(commit, 40)
                or not isinstance(raw_path, str)
            ):
                raise UpgradeError("active-release pointer chain is invalid")
            expected = releases / commit
            candidate = Path(raw_path)
            if not candidate.is_absolute() or candidate != expected:
                raise UpgradeError("active-release pointer path is invalid")
            try:
                metadata = candidate.lstat()
            except OSError as exc:
                raise UpgradeError("active-release pointer release is missing") from exc
            if (
                candidate.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022
            ):
                raise UpgradeError("active-release pointer release is unsafe")
            resolved = ensure_within(candidate, releases)
            if commit in commits or resolved in protected:
                raise UpgradeError(
                    "active-release pointer chain is duplicated or cyclic"
                )
            commits.add(commit)
            protected.add(resolved)
            rows.append({"commit": commit, "path": str(resolved)})
            previous = current.get("previous")
            if previous is None:
                break
            if not isinstance(previous, dict):
                raise UpgradeError("active-release pointer chain is invalid")
            current = previous
        else:
            raise UpgradeError("active-release pointer chain exceeds the safe depth")
        digest = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return (
            document,
            Path(rows[0]["path"]),
            frozenset(protected),
            digest,
            tuple(rows),
        )

    def _release_pointer_snapshot(
        self,
    ) -> tuple[Path, frozenset[Path], str, tuple[dict[str, str], ...]]:
        _document, active, protected, digest, rows = self._release_pointer_state()
        return active, protected, digest, rows

    def _active_release_root(self) -> Path:
        return self._release_pointer_snapshot()[0]

    def _active_release_commit(self) -> str:
        _active, _protected, _digest, rows = self._release_pointer_snapshot()
        if not rows:
            raise UpgradeError(
                "declared source-assessment repair requires an active release pointer"
            )
        return rows[0]["commit"]

    @staticmethod
    def _atomic_write_at(
        parent_descriptor: int,
        name: str,
        payload: bytes,
    ) -> None:
        temporary = f".{name}.{os.getpid()}.{time.time_ns()}"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise UpgradeError("release preparation state write was incomplete")
                view = view[written:]
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(
                temporary,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass

    @staticmethod
    def _preparation_state_name(commit: str) -> str:
        return f".release-state-{commit}.json"

    @staticmethod
    def _preparation_activity_name(commit: str) -> str:
        return f".release-activity-{commit}.lock"

    def _open_preparation_activity(
        self,
        releases_descriptor: int,
        commit: str,
        *,
        expected_coordinate: tuple[int, int] | None,
    ) -> tuple[int, tuple[int, int]]:
        name = self._preparation_activity_name(commit)
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        if expected_coordinate is None:
            flags |= os.O_CREAT
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=releases_descriptor,
            )
        except OSError as exc:
            raise UpgradeError("release preparation activity lease is unsafe") from exc
        try:
            metadata = os.fstat(descriptor)
            entry = os.stat(name, dir_fd=releases_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)
                or (
                    expected_coordinate is not None
                    and (metadata.st_dev, metadata.st_ino) != expected_coordinate
                )
            ):
                raise UpgradeError("release preparation activity lease is unsafe")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpgradeError(
                    "prior candidate preparation worker is still active"
                ) from exc
            return descriptor, (metadata.st_dev, metadata.st_ino)
        except BaseException:
            os.close(descriptor)
            raise

    def _load_preparation_state(
        self,
        releases_descriptor: int,
        commit: str,
    ) -> dict[str, Any] | None:
        name = self._preparation_state_name(commit)
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=releases_descriptor,
                )
            except FileNotFoundError:
                return None
            metadata = os.fstat(descriptor)
            entry = os.stat(name, dir_fd=releases_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size <= 0
                or metadata.st_size > 16 * 1024
                or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)
            ):
                raise UpgradeError("release preparation state is unsafe")
            payload = os.read(descriptor, metadata.st_size + 1)
            after = os.fstat(descriptor)
            entry_after = os.stat(
                name,
                dir_fd=releases_descriptor,
                follow_symlinks=False,
            )
            if (
                len(payload) != metadata.st_size
                or (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                or (after.st_dev, after.st_ino)
                != (
                    entry_after.st_dev,
                    entry_after.st_ino,
                )
            ):
                raise UpgradeError("release preparation state changed while reading")
            state = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeError("release preparation state is invalid") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._validate_preparation_state(state, expected_commit=commit)
        return state

    def _validate_preparation_state(
        self,
        state: object,
        *,
        expected_commit: str,
    ) -> None:
        if not isinstance(state, dict):
            raise UpgradeError("release preparation state is invalid")
        phase = state.get("phase")
        nonce = state.get("operation_nonce")
        stage_name = state.get("stage_name")
        bound_fields_are_valid = (
            _is_lower_hex(state.get("commit"), 40)
            and _is_lower_hex(state.get("candidate_controller_commit"), 40)
            and _is_lower_hex(state.get("instance_sha256"), 64)
            and _is_lower_hex(state.get("pointer_chain_sha256"), 64)
            and _is_lower_hex(nonce, 32)
            and isinstance(stage_name, str)
            and stage_name == f".staging-{expected_commit[:12]}.{nonce}"
        )
        pending_fields_are_valid = (
            state.get("source_manifest_sha256") is None
            and state.get("source_file_count") is None
            and state.get("release_device") is None
            and state.get("release_inode") is None
            and state.get("activity_device") is None
            and state.get("activity_inode") is None
        )
        published_fields_are_valid = (
            _is_lower_hex(state.get("source_manifest_sha256"), 64)
            and type(state.get("source_file_count")) is int
            and state["source_file_count"] >= 0
            and type(state.get("release_device")) is int
            and state["release_device"] > 0
            and type(state.get("release_inode")) is int
            and state["release_inode"] > 0
            and type(state.get("activity_device")) is int
            and state["activity_device"] > 0
            and type(state.get("activity_inode")) is int
            and state["activity_inode"] > 0
        )
        if (
            set(state) != _PREPARATION_STATE_FIELDS
            or state.get("contract") != _PREPARATION_STATE_CONTRACT
            or state.get("commit") != expected_commit
            or state.get("candidate_controller_commit") != expected_commit
            or state.get("release_name") != expected_commit
            or phase not in _PREPARATION_STATE_PHASES
            or state.get("generated_policy") != GENERATED_EXTRA_POLICY
            or type(state.get("uid")) is not int
            or state["uid"] != os.getuid()
            or state.get("content_exposed") is not False
            or state.get("instance_sha256")
            != hashlib.sha256(str(self.paths.root).encode()).hexdigest()
            or not bound_fields_are_valid
            or (
                phase in {"publishing", "publishing_unreaped"}
                and not pending_fields_are_valid
            )
            or (
                phase not in {"publishing", "publishing_unreaped"}
                and not published_fields_are_valid
            )
        ):
            raise UpgradeError("release preparation state is invalid")

    def _write_preparation_state(
        self,
        releases_descriptor: int,
        state: dict[str, Any],
    ) -> None:
        commit = state.get("commit")
        if not isinstance(commit, str):
            raise UpgradeError("release preparation state is invalid")
        self._validate_preparation_state(state, expected_commit=commit)
        self._atomic_write_at(
            releases_descriptor,
            self._preparation_state_name(commit),
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

    def _remove_preparation_state(
        self,
        releases_descriptor: int,
        commit: str,
    ) -> None:
        try:
            os.unlink(
                self._preparation_state_name(commit),
                dir_fd=releases_descriptor,
            )
        except FileNotFoundError:
            return
        os.fsync(releases_descriptor)

    def _new_preparation_state(
        self,
        *,
        commit: str,
        stage_name: str,
        pointer_chain_sha256: str,
    ) -> dict[str, Any]:
        controller_commit = os.environ.get("ADMINME_UPGRADE_CONTROLLER_COMMIT")
        if controller_commit is None and self.test_mode:
            controller_commit = commit
        if not _is_lower_hex(controller_commit, 40):
            raise UpgradeError("upgrade controller provenance is missing")
        return {
            "contract": _PREPARATION_STATE_CONTRACT,
            "phase": "publishing",
            "commit": commit,
            "release_name": commit,
            "stage_name": stage_name,
            "operation_nonce": stage_name.rsplit(".", 1)[-1],
            "candidate_controller_commit": controller_commit,
            "uid": os.getuid(),
            "instance_sha256": hashlib.sha256(
                str(self.paths.root).encode()
            ).hexdigest(),
            "pointer_chain_sha256": pointer_chain_sha256,
            "generated_policy": GENERATED_EXTRA_POLICY,
            "source_manifest_sha256": None,
            "source_file_count": None,
            "release_device": None,
            "release_inode": None,
            "activity_device": None,
            "activity_inode": None,
            "content_exposed": False,
        }

    def _validate_source_repair_online(
        self, policy: SourceAssessmentRepairPolicy
    ) -> dict[str, Any]:
        active_root = self._active_release_root()
        doctor = self._run_release_doctor(active_root, include_synthetic_proof=False)
        diagnostics = self.services.runtime_diagnostics()
        if diagnostics is None:
            raise UpgradeError(
                "declared source-assessment repair requires live runtime diagnostics"
            )
        try:
            diagnostics = hydrate_online_repair_diagnostics(
                self.paths,
                repository_root=active_root,
                diagnostics=diagnostics,
                test_mode=self.test_mode,
            )
            validate_declared_baseline(
                policy,
                active_commit=self._active_release_commit(),
                doctor=doctor,
                diagnostics=diagnostics,
            )
        except RuntimeError as exc:
            raise UpgradeError(str(exc)) from exc
        return diagnostics

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
        controller_root_value = os.environ.get("ADMINME_UPGRADE_CONTROLLER_ROOT")
        if controller_root_value is None:
            if self.test_mode:
                controller_root = candidate
            else:
                raise UpgradeError("fixed upgrade-controller root is missing")
        else:
            controller_root = Path(controller_root_value).resolve()
        controller_source = Path(__file__).resolve()
        expected_source = (controller_root / "bootstrap" / "upgrade.py").resolve()
        if controller_source != expected_source:
            raise UpgradeError(
                "upgrade controller did not load from fixed reviewed source"
            )
        try:
            verify_release_source_identity(
                controller_root,
                repository=candidate,
                commit=commit,
                allow_legacy_tree=False,
                require_tracked_tree=True,
                retained_by_commit=commit,
            )
        except SourceIdentityError as exc:
            raise UpgradeError("fixed upgrade-controller source is invalid") from exc

    _failed_check_names = staticmethod(failed_check_names)
    _doctor_has_only_transient_deployment_readiness_gap = staticmethod(
        doctor_has_only_transient_deployment_readiness_gap
    )
    _failed_checks_are_known_transients = staticmethod(
        failed_checks_are_known_transients
    )
    _runtime_has_only_transient_deployment_readiness_gap = staticmethod(
        runtime_has_only_transient_deployment_readiness_gap
    )
    _has_new_structural_degradation = staticmethod(has_new_structural_degradation)

    def _post_switch_doctor(
        self,
        release: Path,
        *,
        allowed_degraded_adapter_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        try:
            result = wait_for_settled_doctor(
                run_doctor=lambda **kwargs: self._run_release_doctor(release, **kwargs),
                services=self.services,
                test_mode=self.test_mode,
                timeout_seconds=POST_SWITCH_REPLAY_SETTLEMENT_SECONDS,
                poll_seconds=POST_SWITCH_POLL_SECONDS,
                live_readiness_retry_limit=POST_SWITCH_LIVE_READINESS_RETRIES,
                allowed_degraded_adapter_ids=allowed_degraded_adapter_ids,
                absolute_deadline=self._settlement_deadline,
            )
        except Exception as exc:
            safe = self._record_settlement_failure(release, None, exception=exc)
            stage = (
                "recovery_doctor"
                if self._recovery_settlement_deadline is not None
                else "candidate_doctor"
            )
            availability = (
                "; failure audit unavailable"
                if self._failure_audit_available is False
                else ""
            )
            raise UpgradeError(
                f"selected release Doctor failed: stage={stage}; {failure_summary(safe)}{availability}"
            ) from exc
        if result.get("status") != "healthy":
            self._record_settlement_failure(release, result)
        return result

    def _settlement_failure_summary(self, result: dict[str, Any]) -> str:
        allowed = self._failure_consumer_allowlist(self.root)
        summary = failure_summary(failure_evidence(result, allowed_consumers=allowed))
        if self._failure_audit_available is False:
            summary += "; failure audit unavailable"
        return summary

    @staticmethod
    def _failure_consumer_allowlist(release: Path) -> frozenset[str]:
        allowed = frozenset(f"projection:{name}" for name in PROJECTION_NAMES)
        try:
            return allowed | frozenset(_required_reactive_pipeline_ids(release))
        except Exception:
            return allowed

    def _record_settlement_failure(
        self,
        release: Path,
        result: dict[str, Any] | None,
        *,
        exception: Exception | None = None,
    ) -> dict[str, Any]:
        allowed = self._failure_consumer_allowlist(release)
        safe = failure_evidence(result, allowed_consumers=allowed, exception=exception)
        try:
            journal = self.activation_journal.load()
            stage = (
                "recovery_doctor"
                if self._recovery_settlement_deadline is not None
                else "candidate_doctor"
            )
            document = {
                "contract": "administrateme-deployment-failure-v1",
                "transaction_id": journal["transaction_id"]
                if journal
                else self._failure_audit_attempt_id,
                "operation": journal["operation"] if journal else "standalone",
                "stage": stage,
                "selected_commit": release.name
                if _is_lower_hex(release.name, 40)
                else None,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "failure": safe,
                "content_exposed": False,
            }
            persist_failure(self.activation_journal, document)
            self._failure_audit_available = True
        except Exception:
            # Failure diagnostics must never prevent the existing recovery path.
            # Do not log exception text: it can contain paths or diagnostic data.
            self._failure_audit_available = False
        return safe

    def _service_start_reached_doctor_boundary(self, status: Any) -> bool:
        """Require a live runtime before Doctor adjudicates blocked readiness."""

        if status.status in {"healthy", "degraded"}:
            return True
        return status.status == "failed" and self.services.runtime_is_live()

    def _run_release_doctor(
        self,
        release: Path,
        *,
        include_synthetic_proof: bool,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        return run_release_doctor(
            self.paths,
            release,
            include_synthetic_proof=include_synthetic_proof,
            test_mode=self.test_mode,
            doctor_factory=Doctor,
            error_type=UpgradeError,
            timeout_seconds=timeout_seconds,
        )

    def _isolated_recovery_has_full_proof(self, release: Path) -> bool:
        """Verify the existing isolated adapter boundary without waiving Doctor."""
        deadline = self._settlement_deadline
        # Two bounded runtime diagnostics and one complete Doctor must all fit.
        full_budget = (
            RELEASE_DOCTOR_TIMEOUT_SECONDS + 2 * RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS
        )
        if deadline is None or deployment_clock() + full_budget > deadline:
            return False
        pointer_path = self.paths.app_dir / "current-release.json"
        pointer_bytes, pointer = self._read_private_json_payload(pointer_path)
        if pointer is None or pointer.get("path") != str(release):
            return False
        boundary = isolated_degraded_adapter_ids(self.services.runtime_diagnostics())
        if (
            not boundary
            or deployment_clock()
            + RELEASE_DOCTOR_TIMEOUT_SECONDS
            + RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS
            > deadline
        ):
            return False
        full = self._run_release_doctor(
            release,
            include_synthetic_proof=True,
            timeout_seconds=RELEASE_DOCTOR_TIMEOUT_SECONDS,
        )
        if (
            deployment_clock() + RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS > deadline
            or not full.get("checks")
            or not self._failed_check_names(full).issubset(
                {"launchd_and_process_state"}
            )
            or self._read_private_json_payload(pointer_path)[0] != pointer_bytes
        ):
            return False
        final_boundary = isolated_degraded_adapter_ids(
            self.services.runtime_diagnostics()
        )
        return (
            final_boundary == boundary
            and deployment_clock() < deadline
            and self._read_private_json_payload(pointer_path)[0] == pointer_bytes
        )

    def _refresh_active_runtime_binding(
        self,
        active_readiness: dict[str, Any],
        *,
        candidate_commit: str,
    ) -> dict[str, Any]:
        """Bind promoted readiness to the updater-selected final configuration."""

        try:
            snapshot = capture_deployment_snapshot(self.paths)
        except CandidateReadinessError as exc:
            raise UpgradeError(
                "post-selector deployment binding could not be captured"
            ) from exc
        binding = active_readiness.get("deployment_binding")
        if (
            not isinstance(binding, dict)
            or binding.get("release_commit") != candidate_commit
            or binding.get("instance_fingerprint") != snapshot.instance_fingerprint
            or binding.get("host_fingerprint") != snapshot.host_fingerprint
            or snapshot.predecessor_commit != candidate_commit
        ):
            raise UpgradeError(
                "post-selector deployment binding disagrees with the authorized candidate"
            )
        refreshed = json.loads(json.dumps(active_readiness))
        refreshed["deployment_binding"]["configuration_fingerprint"] = (
            snapshot.configuration_fingerprint
        )
        return refreshed

    def _write_refreshed_runtime_binding(
        self,
        active_readiness: dict[str, Any] | None,
        *,
        candidate_commit: str,
    ) -> None:
        """Rebind retained owner evidence to the final selector configuration."""

        if active_readiness is None:
            return
        if self.test_mode and not isinstance(
            active_readiness.get("deployment_binding"), dict
        ):
            # Older unit fixtures predate deployment bindings. Production is
            # always strict through ``_refresh_active_runtime_binding``.
            return
        refreshed = self._refresh_active_runtime_binding(
            active_readiness,
            candidate_commit=candidate_commit,
        )
        readiness_path = (
            self.paths.instance_dir / "config" / "deployment-readiness.json"
        )
        atomic_write(
            readiness_path,
            json.dumps(refreshed, indent=2, sort_keys=True).encode("utf-8"),
            mode=0o600,
        )
        self._fsync_directory(readiness_path.parent)

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

    def _capture_activation_baseline(
        self,
    ) -> tuple[
        bytes,
        dict[str, Any],
        bytes | None,
        dict[str, Any] | None,
        bytes,
        dict[str, Any],
        bytes | None,
        dict[str, Any] | None,
    ]:
        pointer_path = self.paths.app_dir / "current-release.json"
        pointer_payload, pointer = self._read_private_json_payload(pointer_path)
        assert pointer_payload is not None and pointer is not None
        validated = self._read_release_pointer()
        if validated != pointer:
            raise UpgradeError("active-release pointer changed during journal capture")
        readiness_path = (
            self.paths.instance_dir / "config" / "deployment-readiness.json"
        )
        readiness_payload, readiness = self._read_private_json_payload(
            readiness_path,
            missing_ok=True,
        )
        if readiness is not None and readiness.get("contract") != (
            "deployment-readiness-v1"
        ):
            raise UpgradeError("active deployment readiness is invalid")
        adapter_payload, adapter_config = self._read_private_json_payload(
            self.paths.instance_dir / "config" / "adapters.json"
        )
        assert adapter_payload is not None and adapter_config is not None
        settings_payload, settings_control = self._read_private_json_payload(
            self.paths.instance_dir / "config" / "settings-control.json",
            missing_ok=True,
        )
        return (
            pointer_payload,
            pointer,
            readiness_payload,
            readiness,
            adapter_payload,
            adapter_config,
            settings_payload,
            settings_control,
        )

    def _restore_journal_control_files(self, journal: dict[str, Any]) -> None:
        try:
            pointer_payload = base64.b64decode(
                journal["initial_pointer_bytes"], validate=True
            )
        except (TypeError, ValueError) as exc:
            raise UpgradeError("activation journal pointer bytes are invalid") from exc
        if (
            hashlib.sha256(pointer_payload).hexdigest()
            != journal["initial_pointer_sha256"]
        ):
            raise UpgradeError("activation journal pointer bytes changed")
        atomic_write(
            self.paths.app_dir / "current-release.json",
            pointer_payload,
            mode=0o600,
        )
        self._fsync_directory(self.paths.app_dir)

        adapter_encoded = journal.get("initial_adapter_config_bytes")
        if adapter_encoded is not None:
            try:
                adapter_payload = base64.b64decode(adapter_encoded, validate=True)
            except (TypeError, ValueError) as exc:
                raise UpgradeError(
                    "activation journal adapter bytes are invalid"
                ) from exc
            if hashlib.sha256(adapter_payload).hexdigest() != journal.get(
                "initial_adapter_config_sha256"
            ):
                raise UpgradeError("activation journal adapter bytes changed")
            adapter_path = self.paths.instance_dir / "config" / "adapters.json"
            atomic_write(adapter_path, adapter_payload, mode=0o600)
            self._fsync_directory(adapter_path.parent)

        if "initial_settings_control_bytes" in journal:
            settings_path = self.paths.instance_dir / "config" / "settings-control.json"
            settings_encoded = journal["initial_settings_control_bytes"]
            if settings_encoded is None:
                if os.path.lexists(settings_path):
                    self._read_private_json_payload(settings_path)
                    settings_path.unlink()
                    self._fsync_directory(settings_path.parent)
            else:
                try:
                    settings_payload = base64.b64decode(settings_encoded, validate=True)
                except (TypeError, ValueError) as exc:
                    raise UpgradeError(
                        "activation journal settings-control bytes are invalid"
                    ) from exc
                if hashlib.sha256(settings_payload).hexdigest() != journal.get(
                    "initial_settings_control_sha256"
                ):
                    raise UpgradeError(
                        "activation journal settings-control bytes changed"
                    )
                atomic_write(settings_path, settings_payload, mode=0o600)
                self._fsync_directory(settings_path.parent)

        readiness_path = (
            self.paths.instance_dir / "config" / "deployment-readiness.json"
        )
        encoded = journal["initial_readiness_bytes"]
        if encoded is None:
            if os.path.lexists(readiness_path):
                self._read_private_json_payload(readiness_path)
                readiness_path.unlink()
                self._fsync_directory(readiness_path.parent)
        else:
            try:
                readiness_payload = base64.b64decode(encoded, validate=True)
            except (TypeError, ValueError) as exc:
                raise UpgradeError(
                    "activation journal readiness bytes are invalid"
                ) from exc
            if (
                hashlib.sha256(readiness_payload).hexdigest()
                != journal["initial_readiness_sha256"]
            ):
                raise UpgradeError("activation journal readiness bytes changed")
            atomic_write(readiness_path, readiness_payload, mode=0o600)
            self._fsync_directory(readiness_path.parent)

    def _prepared_adapter_config(
        self,
        pending: dict[str, Any],
        *,
        source_payload: bytes,
        source_config: dict[str, Any],
    ) -> tuple[bytes, dict[str, Any], dict[str, Any] | None]:
        declared = pending["deployment_binding"].get("provider_binding_transition")
        if declared is None:
            return source_payload, source_config, None
        if self.approve_provider_binding_migration is not True:
            raise UpgradeError(
                "provider-binding transition lacks explicit activation approval"
            )
        mapping_payload, mapping = self._read_private_json_payload(
            self.paths.instance_dir / "config" / "member-mapping.json"
        )
        assert mapping_payload is not None and mapping is not None
        try:
            transition = prepare_live_provider_binding_transition(
                self.paths,
                source_config,
                household_mapping=mapping,
                ownership_reviewed=True,
                source_payload=source_payload,
                test_mode=self.test_mode,
            )
        except ProviderBindingTransitionError as exc:
            raise UpgradeError(
                "provider-binding transition cannot be prepared exactly"
            ) from exc
        if transition.public() != declared:
            raise UpgradeError("provider-binding transition changed after readiness")
        return transition.prepared_bytes, transition.prepared, declared

    def _recover_interrupted_transaction(
        self,
        candidate_repository: Path | None = None,
    ) -> dict[str, Any] | None:
        """Converge an interrupted transaction to its exact captured predecessor."""

        try:
            journal = self.activation_journal.load()
        except ActivationJournalError as exc:
            raise UpgradeError("activation transaction journal is invalid") from exc
        if journal is None:
            return None
        if journal["phase"] in {"committed", "recovered"}:
            try:
                history = self.activation_journal.finish(journal)
            except ActivationJournalError as exc:
                raise UpgradeError(
                    "terminal activation journal cannot be archived"
                ) from exc
            return {
                "status": journal["phase"],
                "transaction_id": journal["transaction_id"],
                "history": str(history),
                "content_exposed": False,
            }

        predecessor_commit = journal["predecessor_commit"]
        self._begin_recovery_settlement()
        predecessor = Path(journal["predecessor_path"])
        expected = self.paths.app_dir / "releases" / predecessor_commit
        if predecessor != expected:
            raise UpgradeError("activation journal predecessor coordinate is invalid")
        ensure_within(predecessor, self.paths.app_dir / "releases")
        try:
            metadata = predecessor.lstat()
        except OSError as exc:
            raise UpgradeError("activation predecessor is unavailable") from exc
        if (
            predecessor.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
        ):
            raise UpgradeError("activation predecessor coordinate is unsafe")
        if not self.test_mode:
            recovery_repository: Path | None = None
            repository_candidates = [
                candidate_repository,
                Path(journal["candidate_path"]),
            ]
            for repository in repository_candidates:
                if repository is None:
                    continue
                try:
                    resolved_repository = repository.resolve(strict=True)
                    reviewed_candidate_commit(resolved_repository)
                except (OSError, SourceIdentityError):
                    continue
                # Recovery may be running from a later protected-main commit.
                # The legacy-tree verifier below independently requires the
                # journal's captured candidate and predecessor commits to exist
                # in this clean Git object database.
                recovery_repository = resolved_repository
                break
            if recovery_repository is None:
                raise UpgradeError(
                    "activation recovery candidate source is unavailable"
                )
            try:
                verify_release_source_identity(
                    predecessor,
                    repository=recovery_repository,
                    commit=predecessor_commit,
                    allow_legacy_tree=True,
                    require_tracked_tree=True,
                    retained_by_commit=journal["candidate_commit"],
                )
            except SourceIdentityError as exc:
                raise UpgradeError("activation predecessor source is invalid") from exc
            self._validate_prepared_runtime(predecessor)

        try:
            recovery_journal = self.activation_journal.transition(
                journal,
                "recovering",
            )
            journal = recovery_journal
            recovery_errors: list[Exception] = []
            try:
                self.services.stop()
            except Exception as exc:
                # Keep this journal retryable, but leave every selector and
                # control file untouched until process quiescence is proved.
                raise UpgradeError(
                    "interrupted activation recovery did not converge: "
                    "service quiescence is unproven; control files were not restored"
                ) from exc
            for operation in (
                lambda: self._install_reviewed_openclaw(predecessor),
                lambda: self._restore_journal_control_files(recovery_journal),
                lambda: reconcile_administrateme_plugin(
                    self.paths,
                    predecessor,
                    test_mode=self.test_mode,
                ),
                lambda: install_launch_agents(
                    self.paths,
                    predecessor,
                    test_mode=self.test_mode,
                ),
            ):
                try:
                    operation()
                except Exception as exc:
                    recovery_errors.append(exc)
            if not recovery_errors:
                try:
                    self._write_refreshed_runtime_binding(
                        recovery_journal["initial_readiness"],
                        candidate_commit=predecessor_commit,
                    )
                except Exception as exc:
                    recovery_errors.append(exc)
            if journal["was_running"] and not recovery_errors:
                try:
                    restored = self.services.start()
                    recovery_healthy = False
                    recovery_proof: dict[str, Any] | None = None
                    if self._service_start_reached_doctor_boundary(restored):
                        recovery_proof = self._post_switch_doctor(predecessor)
                        recovery_healthy = recovery_proof.get("status") == "healthy"
                    if (
                        not recovery_healthy
                        and recovery_proof is not None
                        and self._failed_check_names(recovery_proof).issubset(
                            {"launchd_and_process_state"}
                        )
                    ):
                        recovery_healthy = self._isolated_recovery_has_full_proof(
                            predecessor
                        )
                    if not recovery_healthy:
                        detail = (
                            "; " + self._settlement_failure_summary(recovery_proof)
                            if recovery_proof is not None
                            else ""
                        )
                        raise UpgradeError(
                            "recovered predecessor did not become healthy: "
                            f"status={restored.status}, runtime={restored.runtime}, "
                            f"openclaw={restored.openclaw}{detail}"
                        )
                except Exception as exc:
                    recovery_errors.append(exc)
            else:
                try:
                    self.services.stop()
                except Exception as exc:
                    recovery_errors.append(exc)
            if recovery_errors:
                detail = "; ".join(
                    f"{type(error).__name__}: {error}" for error in recovery_errors
                )
                raise UpgradeError(
                    "interrupted activation recovery did not converge: " + detail
                )
            journal = self.activation_journal.transition(journal, "recovered")
            history = self.activation_journal.finish(journal)
        except ActivationJournalError as exc:
            raise UpgradeError(
                "activation transaction recovery journal failed"
            ) from exc
        return {
            "status": "recovered",
            "transaction_id": journal["transaction_id"],
            "history": str(history),
            "content_exposed": False,
        }

    def _prove_predecessor_rollback_eligibility(
        self,
        *,
        candidate: Path,
        candidate_commit: str,
        candidate_release: Path,
        predecessor_commit: str,
        predecessor_release: Path,
        pointer_chain_sha256: str,
    ) -> dict[str, Any]:
        expected = self.paths.app_dir / "releases" / predecessor_commit
        if predecessor_release != expected:
            raise UpgradeError("retained predecessor coordinate is invalid")
        try:
            source = verify_release_source_identity(
                predecessor_release,
                repository=candidate,
                commit=predecessor_commit,
                allow_legacy_tree=True,
                require_tracked_tree=True,
                retained_by_commit=candidate_commit,
            )
        except SourceIdentityError as exc:
            raise UpgradeError("retained predecessor source is invalid") from exc
        compatibility = validate_candidate(
            predecessor_release,
            active_root=candidate_release,
            approve_runtime_migration=True,
            allow_openclaw_rollback=True,
        )
        self._validate_prepared_runtime(predecessor_release)
        for relative in (
            Path("bootstrap") / "install-openclaw-source.sh",
            Path("bootstrap") / "versions.json",
        ):
            if not (predecessor_release / relative).is_file():
                raise UpgradeError(
                    "retained predecessor recovery coordinate is missing"
                )
        return {
            "contract": "administrateme-predecessor-rollback-eligibility-v1",
            "predecessor_commit": predecessor_commit,
            "pointer_chain_sha256": pointer_chain_sha256,
            "source_identity_method": source["source_identity_method"],
            "tracked_source_tree_matches": source.get(
                "tracked_source_tree_matches", True
            ),
            "candidate_rollback_compatible": compatibility["compatible"] is True,
            "prepared_runtime_valid": True,
            "launchagent_restore_available": True,
            "openclaw_restore_available": True,
            "content_exposed": False,
        }

    def activate(self, candidate: Path) -> dict[str, Any]:
        with self._operation_lock():
            self._begin_deployment_budget()
            recovered = self._recover_interrupted_transaction(candidate)
            if recovered is not None:
                return recovered
            if self.pending_attestation_id is None:
                if not self.test_mode:
                    raise UpgradeError(
                        "activation requires an explicit candidate-pending attestation"
                    )
                return self._activate_locked(candidate)
            return self._activate_transactional_locked(candidate)

    def _activate_transactional_locked(self, candidate: Path) -> dict[str, Any]:
        """Run one evidence-bound, crash-recoverable candidate activation."""

        candidate = candidate.resolve()
        commit = self._candidate_commit(candidate)
        self._attest_controller_provenance(candidate, commit)
        active_root, protected_roots, pointer_digest, pointer_rows = (
            self._release_pointer_snapshot()
        )
        if not pointer_rows:
            raise UpgradeError("activation requires an exact predecessor pointer")
        if len(pointer_rows) >= _MAX_RELEASE_POINTER_DEPTH:
            raise UpgradeError(
                "active-release pointer chain is at its safe retention limit"
            )
        target_release = self.paths.app_dir / "releases" / commit
        if target_release.resolve() in protected_roots:
            raise UpgradeError(
                "candidate is already selected or retained; installed releases "
                "cannot be prepared or selected again"
            )
        assert self.pending_attestation_id is not None
        try:
            pending, pending_sha256, pending_snapshot = (
                load_pending_attestation_for_activation(
                    self.paths,
                    attestation_id=self.pending_attestation_id,
                    candidate=candidate,
                    candidate_commit=commit,
                    approve_provider_binding_migration=(
                        self.approve_provider_binding_migration
                    ),
                )
            )
        except CandidateReadinessError as exc:
            raise UpgradeError("candidate-pending readiness is not eligible") from exc

        repair_policy = (
            load_repair_policy(candidate)
            if self.approve_source_assessment_repair
            else None
        )
        repair_diagnostics = (
            self._validate_source_repair_online(repair_policy)
            if repair_policy is not None
            else None
        )
        compatibility = validate_candidate(
            candidate,
            active_root=active_root,
            approve_runtime_migration=self.approve_runtime_migration,
        )
        self._preflight_keychain_access(candidate)
        repair_adapter_ids = isolated_degraded_adapter_ids(
            self.services.runtime_diagnostics()
        )
        if repair_adapter_ids is None:
            repair_adapter_ids = frozenset()
        release = self._publish_release(candidate, commit)
        (
            pointer_payload,
            pointer,
            readiness_payload,
            readiness,
            adapter_payload,
            adapter_config,
            settings_payload,
            settings_control,
        ) = self._capture_activation_baseline()
        selected_adapter_payload, selected_adapter_config, binding_transition = (
            self._prepared_adapter_config(
                pending,
                source_payload=adapter_payload,
                source_config=adapter_config,
            )
        )
        (
            current_pointer,
            current_active,
            _current_protected,
            current_digest,
            _current_rows,
        ) = self._release_pointer_state()
        if (
            current_pointer != pointer
            or current_active.resolve() != active_root.resolve()
            or current_digest != pointer_digest
        ):
            raise UpgradeError("active-release pointer changed before journaling")
        predecessor_commit = pointer_rows[0]["commit"]
        predecessor_release = Path(pointer_rows[0]["path"])
        was_running = self.services.status().status != "stopped"
        if repair_policy is not None and not was_running:
            raise UpgradeError(
                "declared source-assessment repair requires running services "
                "at the quiescence boundary"
            )

        transaction_id = uuid.uuid4().hex
        try:
            journal = self.activation_journal.create(
                {
                    "transaction_id": transaction_id,
                    "operation": "activate",
                    "phase": "created",
                    "candidate_commit": commit,
                    "candidate_path": str(candidate),
                    "target_commit": commit,
                    "target_path": str(release),
                    "predecessor_commit": predecessor_commit,
                    "predecessor_path": str(predecessor_release),
                    "initial_pointer": pointer,
                    "initial_pointer_bytes": base64.b64encode(pointer_payload).decode(
                        "ascii"
                    ),
                    "initial_pointer_sha256": hashlib.sha256(
                        pointer_payload
                    ).hexdigest(),
                    "initial_readiness": readiness,
                    "initial_readiness_bytes": (
                        base64.b64encode(readiness_payload).decode("ascii")
                        if readiness_payload is not None
                        else None
                    ),
                    "initial_readiness_sha256": (
                        hashlib.sha256(readiness_payload).hexdigest()
                        if readiness_payload is not None
                        else None
                    ),
                    "initial_settings_control": settings_control,
                    "initial_settings_control_bytes": (
                        base64.b64encode(settings_payload).decode("ascii")
                        if settings_payload is not None
                        else None
                    ),
                    "initial_settings_control_sha256": (
                        hashlib.sha256(settings_payload).hexdigest()
                        if settings_payload is not None
                        else None
                    ),
                    "initial_adapter_config": adapter_config,
                    "initial_adapter_config_bytes": base64.b64encode(
                        adapter_payload
                    ).decode("ascii"),
                    "initial_adapter_config_sha256": hashlib.sha256(
                        adapter_payload
                    ).hexdigest(),
                    "selected_adapter_config": selected_adapter_config,
                    "selected_adapter_config_bytes": base64.b64encode(
                        selected_adapter_payload
                    ).decode("ascii"),
                    "selected_adapter_config_sha256": hashlib.sha256(
                        selected_adapter_payload
                    ).hexdigest(),
                    "provider_binding_transition": binding_transition,
                    "was_running": was_running,
                    "pending_attestation_id": self.pending_attestation_id,
                    "pending_attestation_sha256": pending_sha256,
                    "fresh_backup": None,
                    "rollback_eligibility": None,
                    "active_readiness": None,
                }
            )
        except ActivationJournalError as exc:
            raise UpgradeError("activation transaction cannot be journaled") from exc

        backup_result: dict[str, Any] | None = None
        rehearsal: dict[str, Any] | None = None
        eligibility: dict[str, Any] | None = None
        source_repair_evidence: dict[str, Any] | None = None
        history: Path | None = None
        try:
            backup_path = self.paths.backup_dir / (
                f"activation-{transaction_id}.ambackup"
            )
            journal = self.activation_journal.transition(
                journal,
                "backup_creating",
            )
            backup_manager = BackupManager(
                self.paths,
                root=candidate,
                test_mode=self.test_mode,
            )
            backup_result = backup_manager.create(backup_path)
            backup_sha256 = self._hash_private_file(backup_path)
            fresh_backup = {
                "path": str(backup_path),
                "artifact_sha256": backup_sha256,
                "manifest_sha256": backup_result["manifest_sha256"],
                "encrypted": backup_result["encrypted"] is True,
                "rehearsal_receipt_path": None,
                "rehearsal_receipt_sha256": None,
                "content_exposed": False,
            }
            journal = self.activation_journal.transition(
                journal,
                "backup_created",
                fresh_backup=fresh_backup,
            )
            receipt_path = self.paths.backup_dir / (
                f"activation-rehearsal-{transaction_id}.json"
            )
            journal = self.activation_journal.transition(
                journal,
                "backup_rehearsing",
            )
            rehearsal = backup_manager.rehearse(
                backup_path,
                candidate=candidate,
                receipt=receipt_path,
            )
            receipt_sha256 = self._hash_private_file(
                receipt_path,
                maximum_size=16 * 1024 * 1024,
            )
            if (
                rehearsal.get("status") != "healthy"
                or rehearsal.get("candidate", {}).get("commit") != commit
                or rehearsal.get("artifact", {}).get("sha256") != backup_sha256
                or rehearsal.get("deployment_binding") != pending_snapshot.public()
            ):
                raise UpgradeError("fresh backup rehearsal binding is invalid")
            fresh_backup = {
                **fresh_backup,
                "rehearsal_receipt_path": str(receipt_path),
                "rehearsal_receipt_sha256": receipt_sha256,
                "rehearsal_self_sha256": rehearsal["receipt_sha256"],
            }
            journal = self.activation_journal.transition(
                journal,
                "backup_rehearsed",
                fresh_backup=fresh_backup,
            )

            eligibility = self._prove_predecessor_rollback_eligibility(
                candidate=candidate,
                candidate_commit=commit,
                candidate_release=release,
                predecessor_commit=predecessor_commit,
                predecessor_release=predecessor_release,
                pointer_chain_sha256=pointer_digest,
            )
            eligibility_sha256 = sha256_json(eligibility)
            journal = self.activation_journal.transition(
                journal,
                "rollback_eligible",
                rollback_eligibility={
                    **eligibility,
                    "sha256": eligibility_sha256,
                },
            )

            try:
                pending_again, pending_hash_again, snapshot_again = (
                    load_pending_attestation_for_activation(
                        self.paths,
                        attestation_id=self.pending_attestation_id,
                        candidate=candidate,
                        candidate_commit=commit,
                        approve_provider_binding_migration=(
                            self.approve_provider_binding_migration
                        ),
                    )
                )
            except CandidateReadinessError as exc:
                raise UpgradeError(
                    "candidate-pending readiness drifted before selector mutation"
                ) from exc
            if (
                pending_again != pending
                or pending_hash_again != pending_sha256
                or snapshot_again != pending_snapshot
            ):
                raise UpgradeError(
                    "candidate-pending readiness changed before selector mutation"
                )
            active_readiness = build_active_attestation_from_pending(
                pending,
                pending_sha256=pending_sha256,
                fresh_backup_sha256=backup_sha256,
                fresh_rehearsal_receipt_sha256=receipt_sha256,
                rollback_eligibility_sha256=eligibility_sha256,
                tested_at=str(rehearsal["tested_at"]),
            )
            self._require_activation_capacity(begin_settlement=False)
            journal = self.activation_journal.transition(
                journal,
                "selector_mutation_authorized",
                active_readiness=active_readiness,
            )

            if repair_policy is not None:
                repair_diagnostics = self._validate_source_repair_online(repair_policy)
                journal = self.activation_journal.transition(
                    journal,
                    "services_quiescing_for_repair",
                )
                self.services.stop(require_openclaw_live_before_stop=True)
                assert repair_diagnostics is not None
                source_repair_evidence = offline_validate_repair_baseline(
                    self.paths,
                    repository_root=active_root,
                    candidate_commit=commit,
                    policy=repair_policy,
                    diagnostics=repair_diagnostics,
                    gateway_live_at_quiescence=True,
                    append_requests=True,
                    test_mode=self.test_mode,
                )
                journal = self.activation_journal.transition(
                    journal,
                    "services_quiesced_for_repair",
                )

            journal = self.activation_journal.transition(
                journal,
                "preparation_consuming",
            )
            activity_descriptor = self._consume_preparation_state(
                candidate,
                commit,
                release,
            )
            try:
                journal = self.activation_journal.transition(
                    journal,
                    "preparation_consumed",
                )
                self._require_activation_capacity()
                if repair_policy is None:
                    journal = self.activation_journal.transition(
                        journal,
                        "services_quiescing",
                    )
                    self.services.stop()
                    journal = self.activation_journal.transition(
                        journal,
                        "services_quiesced",
                    )
                # Graceful stop can consume time; reserve admission must still
                # hold at the first selector mutation. This does not reset the
                # already established candidate settlement deadline.
                self._require_activation_capacity()
                journal = self.activation_journal.transition(
                    journal,
                    "openclaw_selecting",
                )
                self._install_reviewed_openclaw(release)
                journal = self.activation_journal.transition(
                    journal,
                    "openclaw_selected",
                )
            finally:
                cleanup_error: BaseException | None = None
                try:
                    fcntl.flock(activity_descriptor, fcntl.LOCK_UN)
                except BaseException as exc:
                    cleanup_error = exc
                close_error = self._close_descriptors(activity_descriptor)
                cleanup_error = cleanup_error or close_error
                if cleanup_error is not None:
                    raise UpgradeError(
                        "consumed preparation activity cleanup failed"
                    ) from cleanup_error

            if binding_transition is not None:
                current_adapter_payload, current_adapter_config = (
                    self._read_private_json_payload(
                        self.paths.instance_dir / "config" / "adapters.json"
                    )
                )
                if (
                    current_adapter_payload != adapter_payload
                    or current_adapter_config != adapter_config
                ):
                    raise UpgradeError(
                        "adapter configuration changed before journaled migration"
                    )
                journal = self.activation_journal.transition(
                    journal,
                    "provider_config_selecting",
                )
                adapter_path = self.paths.instance_dir / "config" / "adapters.json"
                atomic_write(adapter_path, selected_adapter_payload, mode=0o600)
                self._fsync_directory(adapter_path.parent)
                journal = self.activation_journal.transition(
                    journal,
                    "provider_config_selected",
                )
            journal = self.activation_journal.transition(
                journal,
                "plugin_selecting",
            )
            reconcile_administrateme_plugin(
                self.paths,
                release,
                test_mode=self.test_mode,
            )
            journal = self.activation_journal.transition(
                journal,
                "plugin_selected",
            )

            pointer_path = self.paths.app_dir / "current-release.json"
            candidate_pointer = {
                "path": str(release),
                "commit": commit,
                "previous": pointer,
            }
            journal = self.activation_journal.transition(
                journal,
                "pointer_selecting",
            )
            atomic_write(
                pointer_path,
                json.dumps(candidate_pointer, indent=2).encode("utf-8"),
                mode=0o600,
            )
            self._fsync_directory(pointer_path.parent)
            journal = self.activation_journal.transition(
                journal,
                "pointer_selected",
            )
            self._test_crash_after_journal_phase("pointer_selected")

            readiness_path = (
                self.paths.instance_dir / "config" / "deployment-readiness.json"
            )
            journal = self.activation_journal.transition(
                journal,
                "readiness_promoting",
            )
            atomic_write(
                readiness_path,
                json.dumps(active_readiness, indent=2, sort_keys=True).encode("utf-8"),
                mode=0o600,
            )
            self._fsync_directory(readiness_path.parent)
            journal = self.activation_journal.transition(
                journal,
                "readiness_promoted",
            )

            journal = self.activation_journal.transition(
                journal,
                "llm_tool_selecting",
            )
            configure_required_llm_tool(self.paths, test_mode=self.test_mode)
            journal = self.activation_journal.transition(
                journal,
                "llm_tool_selected",
            )
            journal = self.activation_journal.transition(
                journal,
                "runtime_binding_refreshing",
            )
            active_readiness = self._refresh_active_runtime_binding(
                active_readiness,
                candidate_commit=commit,
            )
            atomic_write(
                readiness_path,
                json.dumps(active_readiness, indent=2, sort_keys=True).encode("utf-8"),
                mode=0o600,
            )
            self._fsync_directory(readiness_path.parent)
            journal = self.activation_journal.transition(
                journal,
                "runtime_binding_refreshed",
                active_readiness=active_readiness,
            )
            journal = self.activation_journal.transition(
                journal,
                "launchagents_selecting",
            )
            install_launch_agents(self.paths, release, test_mode=self.test_mode)
            journal = self.activation_journal.transition(
                journal,
                "launchagents_selected",
            )
            journal = self.activation_journal.transition(
                journal,
                "services_starting",
            )
            started = self.services.start()
            if not self._service_start_reached_doctor_boundary(started):
                raise UpgradeError(
                    "candidate services did not start: "
                    f"status={started.status}, runtime={started.runtime}, "
                    f"openclaw={started.openclaw}"
                )
            journal = self.activation_journal.transition(
                journal,
                "services_started",
            )
            journal = self.activation_journal.transition(
                journal,
                "doctor_running",
            )
            result = self._post_switch_doctor(
                release,
                allowed_degraded_adapter_ids=repair_adapter_ids,
            )
            if result["status"] != "healthy":
                failed = self._settlement_failure_summary(result)
                raise UpgradeError(
                    f"candidate failed post-switch Doctor: {failed or 'unknown check'}"
                )
            final_readiness = json.loads(json.dumps(active_readiness))
            final_readiness["activation_evidence"]["candidate_live_doctor"] = True
            atomic_write(
                readiness_path,
                json.dumps(final_readiness, indent=2, sort_keys=True).encode("utf-8"),
                mode=0o600,
            )
            self._fsync_directory(readiness_path.parent)
            journal = self.activation_journal.transition(
                journal,
                "doctor_healthy",
                active_readiness=final_readiness,
            )
            if not was_running:
                journal = self.activation_journal.transition(
                    journal,
                    "services_restopping",
                )
                self.services.stop()
                journal = self.activation_journal.transition(
                    journal,
                    "services_restopped",
                )
            journal = self.activation_journal.transition(journal, "committed")
            history = self.activation_journal.finish(journal)
        except Exception as candidate_error:
            try:
                recovery = self._recover_interrupted_transaction(candidate)
            except Exception as recovery_error:
                raise UpgradeError(
                    f"candidate failed ({candidate_error}); durable predecessor "
                    f"recovery also failed ({recovery_error})"
                ) from candidate_error
            raise UpgradeError(
                f"candidate failed and the predecessor was recovered "
                f"({recovery['transaction_id'] if recovery else transaction_id}): "
                f"{candidate_error}"
            ) from candidate_error

        assert backup_result is not None
        assert rehearsal is not None
        assert eligibility is not None
        assert history is not None
        return {
            "status": "upgraded",
            "commit": commit,
            "backup": backup_result["path"],
            "fresh_backup_sha256": journal["fresh_backup"]["artifact_sha256"],
            "fresh_rehearsal_receipt_sha256": journal["fresh_backup"][
                "rehearsal_receipt_sha256"
            ],
            "pending_attestation_id": self.pending_attestation_id,
            "pending_attestation_sha256": pending_sha256,
            "activation_transaction_id": transaction_id,
            "activation_history": str(history),
            "compatibility": compatibility,
            "upgrade_controller_attested": True,
            "rollback_available": True,
            "rollback_eligibility": {
                "eligible": True,
                "sha256": journal["rollback_eligibility"]["sha256"],
            },
            "source_assessment_repair": source_repair_evidence,
            "content_exposed": False,
        }

    def _activate_locked(self, candidate: Path) -> dict[str, Any]:
        candidate = candidate.resolve()
        commit = self._candidate_commit(candidate)
        self._attest_controller_provenance(candidate, commit)
        active_root, protected_roots, pointer_digest, pointer_rows = (
            self._release_pointer_snapshot()
        )
        if len(pointer_rows) >= _MAX_RELEASE_POINTER_DEPTH:
            raise UpgradeError(
                "active-release pointer chain is at its safe retention limit"
            )
        target_release = (self.paths.app_dir / "releases" / commit).resolve()
        if target_release in protected_roots:
            raise UpgradeError(
                "candidate is already selected or retained; installed releases "
                "cannot be prepared or selected again"
            )
        repair_policy = (
            load_repair_policy(candidate)
            if self.approve_source_assessment_repair
            else None
        )
        repair_diagnostics = (
            self._validate_source_repair_online(repair_policy)
            if repair_policy is not None
            else None
        )
        evidence = validate_candidate(
            candidate,
            active_root=active_root,
            approve_runtime_migration=self.approve_runtime_migration,
        )
        self._preflight_keychain_access(candidate)
        release = self._publish_release(candidate, commit)
        backup = BackupManager(
            self.paths,
            root=self.root,
            test_mode=self.test_mode,
        ).create()
        pointer = self.paths.app_dir / "current-release.json"
        (
            previous,
            current_active,
            _protected,
            current_digest,
            _rows,
        ) = self._release_pointer_state()
        if (
            current_active.resolve() != active_root.resolve()
            or current_digest != pointer_digest
        ):
            raise UpgradeError("active-release pointer changed before candidate switch")
        previous_release = (
            ensure_within(Path(previous["path"]), self.paths.app_dir / "releases")
            if previous
            else None
        )
        was_running = self.services.status().status != "stopped"
        if repair_policy is not None and not was_running:
            raise UpgradeError(
                "declared source-assessment repair requires running services "
                "at the quiescence boundary"
            )
        pointer_switched = False
        openclaw_switched = False
        plugin_registration_attempted = False
        source_repair_evidence: dict[str, Any] | None = None
        self._require_activation_capacity(begin_settlement=False)
        try:
            if repair_policy is not None:
                # Backup quiescing restarts the active release. Take the final
                # live receipt immediately before shutdown, then bind it to the
                # stopped event watermark and durable consumer state below.
                repair_diagnostics = self._validate_source_repair_online(repair_policy)
                self.services.stop(require_openclaw_live_before_stop=True)
                assert repair_diagnostics is not None
                source_repair_evidence = offline_validate_repair_baseline(
                    self.paths,
                    repository_root=self._active_release_root(),
                    candidate_commit=commit,
                    policy=repair_policy,
                    diagnostics=repair_diagnostics,
                    gateway_live_at_quiescence=True,
                    append_requests=True,
                    test_mode=self.test_mode,
                )
            activity_descriptor = self._consume_preparation_state(
                candidate, commit, release
            )
            try:
                self._require_activation_capacity()
                if repair_policy is None:
                    self.services.stop()
                self._require_activation_capacity()
                self._install_reviewed_openclaw(release)
                openclaw_switched = True
            finally:
                activity_cleanup_error: BaseException | None = None
                try:
                    fcntl.flock(activity_descriptor, fcntl.LOCK_UN)
                except BaseException as exc:
                    activity_cleanup_error = exc
                close_error = self._close_descriptors(activity_descriptor)
                activity_cleanup_error = activity_cleanup_error or close_error
                if activity_cleanup_error is not None:
                    raise UpgradeError(
                        "consumed preparation activity cleanup failed"
                    ) from activity_cleanup_error
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
                failed = self._settlement_failure_summary(result)
                raise UpgradeError(
                    f"candidate failed post-switch Doctor: {failed or 'unknown check'}"
                )
            if not was_running:
                self.services.stop()
        except Exception as candidate_error:
            rollback_errors: list[Exception] = []
            self._begin_recovery_settlement()
            try:
                self.services.stop()
            except Exception as exc:
                raise UpgradeError(
                    f"candidate failed ({candidate_error}); previous-release "
                    "restoration stopped because service quiescence is unproven"
                ) from exc
            if previous and pointer_switched:
                assert previous_release is not None
                try:
                    atomic_write(
                        pointer,
                        json.dumps(previous, indent=2).encode("utf-8"),
                    )
                except Exception as exc:
                    rollback_errors.append(exc)
                try:
                    install_launch_agents(
                        self.paths,
                        previous_release,
                        test_mode=self.test_mode,
                    )
                except Exception as exc:
                    rollback_errors.append(exc)
            elif pointer_switched:
                try:
                    pointer.unlink(missing_ok=True)
                except Exception as exc:
                    rollback_errors.append(exc)
            if openclaw_switched and previous_release is not None:
                try:
                    self._install_reviewed_openclaw(previous_release)
                except Exception as exc:
                    rollback_errors.append(exc)
                if plugin_registration_attempted:
                    try:
                        reconcile_administrateme_plugin(
                            self.paths,
                            previous_release,
                            test_mode=self.test_mode,
                        )
                    except Exception as exc:
                        rollback_errors.append(exc)
            if was_running:
                try:
                    restored = self.services.start()
                    recovered_healthy = restored.status == "healthy"
                    if (
                        previous_release is not None
                        and self._service_start_reached_doctor_boundary(restored)
                    ):
                        recovered_healthy = (
                            self._post_switch_doctor(previous_release).get("status")
                            == "healthy"
                        )
                    if not recovered_healthy:
                        raise UpgradeError(
                            "previous release restart did not become healthy: "
                            f"status={restored.status}, runtime={restored.runtime}, "
                            f"openclaw={restored.openclaw}"
                        )
                except Exception as exc:  # preserve the candidate failure as primary
                    rollback_errors.append(exc)
            if rollback_errors:
                rollback_detail = "; ".join(
                    f"{type(error).__name__}: {error}" for error in rollback_errors
                )
                raise UpgradeError(
                    f"candidate failed ({candidate_error}); the previous release "
                    "rollback continued through cleanup and restart, but also "
                    f"reported: {rollback_detail}"
                ) from candidate_error
            raise
        return {
            "status": "upgraded",
            "commit": commit,
            "backup": backup["path"],
            "compatibility": evidence,
            "upgrade_controller_attested": True,
            "rollback_available": previous is not None,
            "source_assessment_repair": source_repair_evidence,
        }

    def rollback(
        self,
        recovery_repository: Path | None = None,
    ) -> dict[str, Any]:
        with self._operation_lock():
            self._begin_deployment_budget()
            if not self.test_mode:
                if recovery_repository is None:
                    raise UpgradeError(
                        "rollback requires an explicit clean recovery repository"
                    )
                try:
                    recovery_repository = recovery_repository.resolve(strict=True)
                    reviewed_candidate_commit(recovery_repository)
                except (OSError, SourceIdentityError) as exc:
                    raise UpgradeError(
                        "rollback recovery repository is not a clean reviewed checkout"
                    ) from exc
            recovered = self._recover_interrupted_transaction(recovery_repository)
            if recovered is not None:
                return recovered
            history_exists = any(self.activation_journal.history.iterdir())
            if self.test_mode and not history_exists:
                return self._rollback_locked()
            return self._rollback_transactional_locked(
                recovery_repository=recovery_repository
            )

    def _rollback_transactional_locked(
        self,
        *,
        recovery_repository: Path | None = None,
    ) -> dict[str, Any]:
        pointer_path = self.paths.app_dir / "current-release.json"
        current, current_release, _protected, _digest, _rows = (
            self._release_pointer_state()
        )
        if current is None:
            raise UpgradeError("no active release pointer exists")
        previous = current.get("previous")
        if not isinstance(previous, dict):
            raise UpgradeError("no prior release is recorded")
        current_commit = current.get("commit")
        target_commit = previous.get("commit")
        target_path = previous.get("path")
        if (
            not isinstance(current_commit, str)
            or not isinstance(target_commit, str)
            or not _is_lower_hex(current_commit, 40)
            or not _is_lower_hex(target_commit, 40)
            or not isinstance(target_path, str)
        ):
            raise UpgradeError("rollback pointer is invalid")
        release = Path(target_path)
        if release != self.paths.app_dir / "releases" / target_commit:
            raise UpgradeError("rollback release coordinate is invalid")
        ensure_within(release, self.paths.app_dir / "releases")
        validate_candidate(
            release,
            active_root=current_release,
            approve_runtime_migration=True,
            allow_openclaw_rollback=True,
        )
        self._validate_prepared_runtime(release)
        try:
            activation_history = self.activation_journal.committed_activation(
                target_commit=current_commit,
                predecessor_commit=target_commit,
            )
        except ActivationJournalError as exc:
            raise UpgradeError(
                "rollback requires the exact committed activation history"
            ) from exc
        target_readiness = activation_history["initial_readiness"]
        target_readiness_encoded = activation_history["initial_readiness_bytes"]
        try:
            target_pointer_payload = base64.b64decode(
                activation_history["initial_pointer_bytes"],
                validate=True,
            )
        except (TypeError, ValueError) as exc:
            raise UpgradeError("rollback pointer history is invalid") from exc
        if (
            hashlib.sha256(target_pointer_payload).hexdigest()
            != activation_history["initial_pointer_sha256"]
            or json.loads(target_pointer_payload.decode("utf-8")) != previous
        ):
            raise UpgradeError("rollback pointer history does not match predecessor")
        target_readiness_payload: bytes | None
        if target_readiness_encoded is None:
            target_readiness_payload = None
        else:
            try:
                target_readiness_payload = base64.b64decode(
                    target_readiness_encoded,
                    validate=True,
                )
            except (TypeError, ValueError) as exc:
                raise UpgradeError("rollback readiness history is invalid") from exc
        restore_target_settings = "initial_settings_control_bytes" in activation_history
        target_settings_payload: bytes | None = None
        if restore_target_settings:
            target_settings_encoded = activation_history[
                "initial_settings_control_bytes"
            ]
            if target_settings_encoded is not None:
                try:
                    target_settings_payload = base64.b64decode(
                        target_settings_encoded,
                        validate=True,
                    )
                except (TypeError, ValueError) as exc:
                    raise UpgradeError(
                        "rollback settings-control history is invalid"
                    ) from exc
                if (
                    hashlib.sha256(target_settings_payload).hexdigest()
                    != (activation_history["initial_settings_control_sha256"])
                ):
                    raise UpgradeError("rollback settings-control history is invalid")

        (
            pointer_payload,
            pointer,
            readiness_payload,
            readiness,
            adapter_payload,
            adapter_config,
            settings_payload,
            settings_control,
        ) = self._capture_activation_baseline()
        binding_transition = activation_history.get("provider_binding_transition")
        target_adapter_encoded = activation_history.get("initial_adapter_config_bytes")
        if target_adapter_encoded is None:
            target_adapter_payload = adapter_payload
            target_adapter_config = adapter_config
        else:
            try:
                target_adapter_payload = base64.b64decode(
                    target_adapter_encoded,
                    validate=True,
                )
            except (TypeError, ValueError) as exc:
                raise UpgradeError(
                    "rollback adapter configuration history is invalid"
                ) from exc
            historical_adapter_config = activation_history.get("initial_adapter_config")
            if not isinstance(historical_adapter_config, dict) or hashlib.sha256(
                target_adapter_payload
            ).hexdigest() != activation_history.get("initial_adapter_config_sha256"):
                raise UpgradeError("rollback adapter configuration history is invalid")
            target_adapter_config = historical_adapter_config
        if pointer != current:
            raise UpgradeError("active-release pointer changed before rollback")
        was_running = self.services.status().status != "stopped"
        transaction_id = uuid.uuid4().hex
        try:
            journal = self.activation_journal.create(
                {
                    "transaction_id": transaction_id,
                    "operation": "rollback",
                    "phase": "created",
                    "candidate_commit": current_commit,
                    "candidate_path": str(current_release),
                    "target_commit": target_commit,
                    "target_path": str(release),
                    "predecessor_commit": current_commit,
                    "predecessor_path": str(current_release),
                    "initial_pointer": pointer,
                    "initial_pointer_bytes": base64.b64encode(pointer_payload).decode(
                        "ascii"
                    ),
                    "initial_pointer_sha256": hashlib.sha256(
                        pointer_payload
                    ).hexdigest(),
                    "initial_readiness": readiness,
                    "initial_readiness_bytes": (
                        base64.b64encode(readiness_payload).decode("ascii")
                        if readiness_payload is not None
                        else None
                    ),
                    "initial_readiness_sha256": (
                        hashlib.sha256(readiness_payload).hexdigest()
                        if readiness_payload is not None
                        else None
                    ),
                    "initial_settings_control": settings_control,
                    "initial_settings_control_bytes": (
                        base64.b64encode(settings_payload).decode("ascii")
                        if settings_payload is not None
                        else None
                    ),
                    "initial_settings_control_sha256": (
                        hashlib.sha256(settings_payload).hexdigest()
                        if settings_payload is not None
                        else None
                    ),
                    "initial_adapter_config": adapter_config,
                    "initial_adapter_config_bytes": base64.b64encode(
                        adapter_payload
                    ).decode("ascii"),
                    "initial_adapter_config_sha256": hashlib.sha256(
                        adapter_payload
                    ).hexdigest(),
                    "selected_adapter_config": target_adapter_config,
                    "selected_adapter_config_bytes": base64.b64encode(
                        target_adapter_payload
                    ).decode("ascii"),
                    "selected_adapter_config_sha256": hashlib.sha256(
                        target_adapter_payload
                    ).hexdigest(),
                    "provider_binding_transition": binding_transition,
                    "was_running": was_running,
                    "pending_attestation_id": None,
                    "pending_attestation_sha256": None,
                    "fresh_backup": None,
                    "rollback_eligibility": {
                        "activation_transaction_id": activation_history[
                            "transaction_id"
                        ],
                        "target_commit": target_commit,
                        "content_exposed": False,
                    },
                    "active_readiness": target_readiness,
                }
            )
        except ActivationJournalError as exc:
            raise UpgradeError("rollback transaction cannot be journaled") from exc

        history: Path | None = None
        try:
            self._require_activation_capacity()
            journal = self.activation_journal.transition(
                journal,
                "services_quiescing",
            )
            self.services.stop()
            journal = self.activation_journal.transition(
                journal,
                "services_quiesced",
            )
            self._require_activation_capacity()
            journal = self.activation_journal.transition(
                journal,
                "openclaw_selecting",
            )
            self._install_reviewed_openclaw(release)
            journal = self.activation_journal.transition(
                journal,
                "openclaw_selected",
            )
            if binding_transition is not None:
                current_adapter_payload, current_adapter_config = (
                    self._read_private_json_payload(
                        self.paths.instance_dir / "config" / "adapters.json"
                    )
                )
                if (
                    current_adapter_payload != adapter_payload
                    or current_adapter_config != adapter_config
                ):
                    raise UpgradeError(
                        "adapter configuration changed before journaled rollback"
                    )
                journal = self.activation_journal.transition(
                    journal,
                    "provider_config_selecting",
                )
                adapter_path = self.paths.instance_dir / "config" / "adapters.json"
                atomic_write(adapter_path, target_adapter_payload, mode=0o600)
                self._fsync_directory(adapter_path.parent)
                journal = self.activation_journal.transition(
                    journal,
                    "provider_config_selected",
                )
            journal = self.activation_journal.transition(
                journal,
                "plugin_selecting",
            )
            reconcile_administrateme_plugin(
                self.paths,
                release,
                test_mode=self.test_mode,
            )
            journal = self.activation_journal.transition(
                journal,
                "plugin_selected",
            )
            journal = self.activation_journal.transition(
                journal,
                "pointer_selecting",
            )
            atomic_write(
                pointer_path,
                target_pointer_payload,
                mode=0o600,
            )
            self._fsync_directory(pointer_path.parent)
            journal = self.activation_journal.transition(
                journal,
                "pointer_selected",
            )
            self._test_crash_after_journal_phase("pointer_selected")
            readiness_path = (
                self.paths.instance_dir / "config" / "deployment-readiness.json"
            )
            journal = self.activation_journal.transition(
                journal,
                "readiness_selecting",
            )
            if target_readiness_payload is None:
                if os.path.lexists(readiness_path):
                    self._read_private_json_payload(readiness_path)
                    readiness_path.unlink()
                    self._fsync_directory(readiness_path.parent)
            else:
                atomic_write(readiness_path, target_readiness_payload, mode=0o600)
                self._fsync_directory(readiness_path.parent)
            journal = self.activation_journal.transition(
                journal,
                "readiness_selected",
            )
            if restore_target_settings:
                settings_path = (
                    self.paths.instance_dir / "config" / "settings-control.json"
                )
                journal = self.activation_journal.transition(
                    journal,
                    "settings_control_selecting",
                )
                if target_settings_payload is None:
                    if os.path.lexists(settings_path):
                        self._read_private_json_payload(settings_path)
                        settings_path.unlink()
                        self._fsync_directory(settings_path.parent)
                else:
                    atomic_write(settings_path, target_settings_payload, mode=0o600)
                    self._fsync_directory(settings_path.parent)
                journal = self.activation_journal.transition(
                    journal,
                    "settings_control_selected",
                )
            configure_required_llm_tool(self.paths, test_mode=self.test_mode)
            self._write_refreshed_runtime_binding(
                target_readiness,
                candidate_commit=target_commit,
            )
            journal = self.activation_journal.transition(
                journal,
                "launchagents_selecting",
            )
            install_launch_agents(self.paths, release, test_mode=self.test_mode)
            journal = self.activation_journal.transition(
                journal,
                "launchagents_selected",
            )
            journal = self.activation_journal.transition(
                journal,
                "services_starting",
            )
            self.services.start()
            journal = self.activation_journal.transition(
                journal,
                "doctor_running",
            )
            result = self._post_switch_doctor(release)
            if result["status"] != "healthy":
                failed = self._settlement_failure_summary(result)
                raise UpgradeError(
                    f"rolled-back release failed Doctor: {failed or 'unknown check'}"
                )
            journal = self.activation_journal.transition(
                journal,
                "doctor_healthy",
            )
            if not was_running:
                self.services.stop()
                journal = self.activation_journal.transition(
                    journal,
                    "services_restopped",
                )
            journal = self.activation_journal.transition(journal, "committed")
            history = self.activation_journal.finish(journal)
        except Exception as rollback_error:
            try:
                recovery = self._recover_interrupted_transaction(recovery_repository)
            except Exception as recovery_error:
                raise UpgradeError(
                    f"rollback failed ({rollback_error}); durable current-release "
                    f"recovery also failed ({recovery_error})"
                ) from rollback_error
            raise UpgradeError(
                f"rollback failed and the current release was recovered "
                f"({recovery['transaction_id'] if recovery else transaction_id}): "
                f"{rollback_error}"
            ) from rollback_error
        assert history is not None
        return {
            "status": "rolled_back",
            "commit": target_commit,
            "activation_transaction_id": transaction_id,
            "activation_history": str(history),
            "content_exposed": False,
        }

    def _rollback_locked(self) -> dict[str, Any]:
        pointer = self.paths.app_dir / "current-release.json"
        current, current_release, _protected, _digest, _rows = (
            self._release_pointer_state()
        )
        if current is None:
            raise UpgradeError("no active release pointer exists")
        previous = current.get("previous")
        if not previous:
            raise UpgradeError("no prior release is recorded")
        release = Path(previous["path"])
        ensure_within(release, self.paths.app_dir / "releases")
        validate_candidate(
            release,
            active_root=current_release,
            approve_runtime_migration=True,
            allow_openclaw_rollback=True,
        )
        stopped = self.services.status().status == "stopped"
        self._require_activation_capacity()
        try:
            self.services.stop()
            self._require_activation_capacity()
            self._install_reviewed_openclaw(release)
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
                failed = self._settlement_failure_summary(result)
                raise UpgradeError(
                    f"rolled-back release failed Doctor: {failed or 'unknown check'}"
                )
            if stopped:
                self.services.stop()
        except Exception as rollback_candidate_error:
            self._begin_recovery_settlement()
            restoration_errors: list[Exception] = []
            try:
                self.services.stop()
            except Exception as exc:
                raise UpgradeError(
                    f"rollback failed ({rollback_candidate_error}); current-release "
                    "restoration stopped because service quiescence is unproven"
                ) from exc
            try:
                atomic_write(pointer, json.dumps(current, indent=2).encode("utf-8"))
            except Exception as exc:
                restoration_errors.append(exc)
            try:
                self._install_reviewed_openclaw(current_release)
            except Exception as exc:
                restoration_errors.append(exc)
            try:
                reconcile_administrateme_plugin(
                    self.paths,
                    current_release,
                    test_mode=self.test_mode,
                )
            except Exception as exc:
                restoration_errors.append(exc)
            try:
                install_launch_agents(
                    self.paths,
                    current_release,
                    test_mode=self.test_mode,
                )
            except Exception as exc:
                restoration_errors.append(exc)
            if not stopped:
                try:
                    restored = self.services.start()
                    recovered_healthy = False
                    if self._service_start_reached_doctor_boundary(restored):
                        recovered_healthy = (
                            self._post_switch_doctor(current_release).get("status")
                            == "healthy"
                        )
                    if not recovered_healthy:
                        raise UpgradeError(
                            "current release restart did not become healthy: "
                            f"status={restored.status}, runtime={restored.runtime}, "
                            f"openclaw={restored.openclaw}"
                        )
                except Exception as exc:
                    restoration_errors.append(exc)
            if restoration_errors:
                restoration_detail = "; ".join(
                    f"{type(error).__name__}: {error}" for error in restoration_errors
                )
                raise UpgradeError(
                    f"rollback failed ({rollback_candidate_error}); restoration "
                    f"continued through every step but also reported: "
                    f"{restoration_detail}"
                ) from rollback_candidate_error
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

    @staticmethod
    def _probe_runtime_version(
        executable: Path,
        *,
        expected: str,
        label: str,
        environment: dict[str, str] | None = None,
    ) -> None:
        try:
            completed = subprocess.run(
                [str(executable), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UpgradeError(f"candidate {label} version probe failed") from exc
        observed = (completed.stdout or completed.stderr).strip()
        if completed.returncode or observed != expected:
            raise UpgradeError(f"candidate preparation found the wrong pinned {label}")

    @staticmethod
    def _runtime_probe_environment(node_bin: Path | None = None) -> dict[str, str]:
        environment = {
            "HOME": "/var/empty",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONNOUSERSITE": "1",
        }
        if node_bin is not None:
            environment["PATH"] = f"{node_bin}:{environment['PATH']}"
        return environment

    @staticmethod
    def _preparation_environment(node_bin: Path, private_home: Path) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "CURL_CA_BUNDLE",
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "NO_PROXY",
                "REQUESTS_CA_BUNDLE",
                "SSL_CERT_DIR",
                "SSL_CERT_FILE",
                "TMPDIR",
            }
        }
        environment.update(
            {
                "HOME": str(private_home),
                "LANG": "C",
                "LC_ALL": "C",
                "NPM_CONFIG_CACHE": str(private_home / "npm-cache"),
                "NPM_CONFIG_USERCONFIG": os.devnull,
                "PATH": f"{node_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
                "PIP_CONFIG_FILE": os.devnull,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "POETRY_CACHE_DIR": str(private_home / "poetry-cache"),
                "POETRY_CONFIG_DIR": str(private_home / "poetry-config"),
                "POETRY_NO_INTERACTION": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        return environment

    @staticmethod
    def _require_private_runtime_directory(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise UpgradeError("candidate runtime preparation is incomplete") from exc
        if (
            path.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
        ):
            raise UpgradeError("candidate runtime preparation is unsafe")

    def _runtime_coordinates(
        self,
        release: Path,
    ) -> tuple[dict[str, Any], Path, Path, Path, Path]:
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
        poetry_override = os.environ.get("ADMINME_PREPARATION_POETRY")
        poetry = (
            Path(poetry_override)
            if poetry_override is not None
            else self.paths.app_dir
            / "tooling"
            / f"poetry-{poetry_version}"
            / "bin"
            / "poetry"
        )
        poetry_venv = poetry.parent.parent
        node_bin = Path("/opt/homebrew/opt") / str(versions["node"]["formula"]) / "bin"
        try:
            poetry_metadata = poetry.lstat()
        except OSError as exc:
            raise UpgradeError(
                "candidate preparation requires the reviewed pre-provisioned Poetry tool"
            ) from exc
        if (
            poetry.is_symlink()
            or not stat.S_ISREG(poetry_metadata.st_mode)
            or poetry_metadata.st_uid != os.getuid()
            or poetry_metadata.st_nlink != 1
            or poetry_metadata.st_mode & 0o022
            or not os.access(poetry, os.X_OK)
        ):
            raise UpgradeError(
                "candidate preparation requires the reviewed pre-provisioned Poetry tool"
            )
        self._require_private_runtime_directory(poetry_venv)
        self._probe_runtime_version(
            poetry,
            expected=f"Poetry (version {poetry_version})",
            label="Poetry",
            environment=self._runtime_probe_environment(),
        )
        return versions, python_executable, python, poetry, node_bin

    def _validate_prepared_runtime(self, release: Path) -> None:
        if self.test_mode:
            return
        versions, python_executable, python, poetry, node_bin = (
            self._runtime_coordinates(release)
        )
        expected_python = f"Python {versions['python']['preferred']}"
        self._probe_runtime_version(
            python_executable,
            expected=expected_python,
            label="Python formula",
            environment=self._runtime_probe_environment(),
        )
        if not python.is_file() or python.resolve() != python_executable.resolve():
            raise UpgradeError(
                "candidate venv interpreter is outside the pinned Python"
            )
        self._probe_runtime_version(
            python,
            expected=expected_python,
            label="venv Python",
            environment=self._runtime_probe_environment(),
        )
        poetry_version = str(versions["package_managers"]["poetry"])
        self._probe_runtime_version(
            poetry,
            expected=f"Poetry (version {poetry_version})",
            label="Poetry",
            environment=self._runtime_probe_environment(),
        )
        environment = self._runtime_probe_environment(node_bin)
        node = node_bin / "node"
        if not node.is_file():
            raise UpgradeError("candidate preparation requires the pinned Node formula")
        self._probe_runtime_version(
            node,
            expected=f"v{versions['node']['preferred']}",
            label="Node",
            environment=environment,
        )
        for directory in (
            release / ".venv",
            release / "openclaw" / "administrateme" / "node_modules",
            release / "openclaw" / "administrateme" / "dist",
        ):
            self._require_private_runtime_directory(directory)

    @staticmethod
    def _process_group_exists(group_id: int) -> bool:
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _terminate_preparation_group(
        cls,
        process: subprocess.Popen[bytes],
    ) -> None:
        for signal_value, timeout in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 30.0)):
            if not cls._process_group_exists(process.pid):
                break
            try:
                os.killpg(process.pid, signal_value)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + timeout
            while (
                cls._process_group_exists(process.pid) and time.monotonic() < deadline
            ):
                time.sleep(0.02)
        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if cls._process_group_exists(process.pid):
            raise UpgradeError(
                "candidate preparation process group could not be reaped"
            )

    def _assert_preparation_activity_coordinate(self) -> None:
        descriptor = self._active_preparation_activity_descriptor
        path = self._active_preparation_activity_path
        expected = self._active_preparation_activity_coordinate
        if descriptor is None or path is None or expected is None:
            raise UpgradeError("candidate preparation activity lease is missing")
        try:
            metadata = os.fstat(descriptor)
            public = path.lstat()
        except OSError as exc:
            raise UpgradeError("candidate preparation activity lease changed") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_dev, metadata.st_ino) != expected
            or (public.st_dev, public.st_ino) != expected
        ):
            raise UpgradeError("candidate preparation activity lease changed")

    def _run_preparation_command(
        self,
        command: list[str],
        *,
        release: Path,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        activity_descriptor = self._active_preparation_activity_descriptor
        activity_path = self._active_preparation_activity_path
        activity_coordinate = self._active_preparation_activity_coordinate
        if (
            activity_descriptor is None
            or activity_path is None
            or activity_coordinate is None
        ):
            raise UpgradeError("candidate preparation activity lease is missing")
        self._assert_preparation_activity_coordinate()
        lifecycle_path_value = os.environ.get("ADMINME_CONTROLLER_LIFECYCLE_PATH")
        lifecycle_device_value = os.environ.get("ADMINME_CONTROLLER_LIFECYCLE_DEVICE")
        lifecycle_inode_value = os.environ.get("ADMINME_CONTROLLER_LIFECYCLE_INODE")
        operation_lock_value = os.environ.get("ADMINME_UPGRADE_OPERATION_LOCK_FD")
        owned_operation_lock_descriptor: int | None = None
        if self.test_mode and not all(
            (
                lifecycle_path_value,
                lifecycle_device_value,
                lifecycle_inode_value,
                operation_lock_value,
            )
        ):
            lifecycle_path_value = str(activity_path)
            lifecycle_device_value = str(activity_coordinate[0])
            lifecycle_inode_value = str(activity_coordinate[1])
            # The preparation worker closes the inherited activity descriptor
            # after reopening that inode independently.  A distinct duplicate
            # preserves the test-only operation-lock boundary and mirrors the
            # two independent descriptors supplied by the production
            # supervisor.
            owned_operation_lock_descriptor = os.dup(activity_descriptor)
            operation_lock_value = str(owned_operation_lock_descriptor)
        try:
            lifecycle_device = int(str(lifecycle_device_value))
            lifecycle_inode = int(str(lifecycle_inode_value))
            operation_lock_descriptor = int(str(operation_lock_value))
        except (TypeError, ValueError) as exc:
            raise UpgradeError("controller lifecycle boundary is missing") from exc
        lifecycle_path = Path(str(lifecycle_path_value))
        operation_lock_metadata = os.fstat(operation_lock_descriptor)
        if (
            not lifecycle_path.is_absolute()
            or not stat.S_ISREG(operation_lock_metadata.st_mode)
            or operation_lock_metadata.st_uid != os.getuid()
            or operation_lock_metadata.st_nlink != 1
            or stat.S_IMODE(operation_lock_metadata.st_mode) != 0o600
        ):
            raise UpgradeError("controller lifecycle boundary is unsafe")
        process: subprocess.Popen[bytes] | None = None
        failed = False
        ready_read, ready_write = os.pipe()
        controller_read, controller_write = os.pipe()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(release / "bootstrap" / "preparation_worker.py"),
                    str(activity_path),
                    str(activity_descriptor),
                    str(ready_write),
                    str(controller_read),
                    str(activity_coordinate[0]),
                    str(activity_coordinate[1]),
                    str(lifecycle_path),
                    str(lifecycle_device),
                    str(lifecycle_inode),
                    str(operation_lock_descriptor),
                    str(_PREPARATION_COMMAND_TIMEOUT_SECONDS),
                    "--",
                    *command,
                ],
                cwd=cwd,
                env=environment,
                start_new_session=True,
                pass_fds=(
                    activity_descriptor,
                    ready_write,
                    controller_read,
                    operation_lock_descriptor,
                ),
            )
            os.close(ready_write)
            ready_write = -1
            os.close(controller_read)
            controller_read = -1
            readable, _writable, _exceptional = select.select(
                [ready_read],
                [],
                [],
                30,
            )
            if not readable or os.read(ready_read, 1) != b"1":
                raise UpgradeError(
                    "candidate preparation worker did not acquire its lease"
                )
            self._assert_preparation_activity_coordinate()
            return_code = process.wait(
                timeout=_PREPARATION_COMMAND_TIMEOUT_SECONDS + 60
            )
            if return_code:
                raise subprocess.CalledProcessError(return_code, command)
            if self._process_group_exists(process.pid):
                self._terminate_preparation_group(process)
                raise UpgradeError(
                    "candidate preparation command left a background process"
                )
            self._assert_preparation_activity_coordinate()
        except BaseException:
            failed = True
            if controller_write >= 0:
                os.close(controller_write)
                controller_write = -1
            if process is not None:
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired as exc:
                    # The worker may be the deliberate lease-owning keeper for
                    # a command whose native process group could not be proven
                    # terminal.  Killing that keeper would release all three
                    # cleanup-visible leases while the detached command might
                    # still be alive.  Leave it for the outer controller
                    # lifecycle boundary to keep the operation locked and the
                    # fixed workspace retained.
                    raise UpgradeError(
                        "candidate preparation terminality is unknown; "
                        "cleanup authority retained"
                    ) from exc
            raise
        finally:
            os.close(ready_read)
            if ready_write >= 0:
                os.close(ready_write)
            if controller_read >= 0:
                os.close(controller_read)
            if controller_write >= 0:
                os.close(controller_write)
            if process is not None and not failed and process.poll() is None:
                self._terminate_preparation_group(process)
            if owned_operation_lock_descriptor is not None:
                os.close(owned_operation_lock_descriptor)

    def _prepare_release(self, release: Path) -> None:
        if self.test_mode:
            return
        versions, python_executable, python, poetry, node_bin = (
            self._runtime_coordinates(release)
        )
        expected_python = f"Python {versions['python']['preferred']}"
        self._probe_runtime_version(
            python_executable,
            expected=expected_python,
            label="Python formula",
            environment=self._runtime_probe_environment(),
        )
        with tempfile.TemporaryDirectory(
            prefix="administrateme-release-preparation-"
        ) as temporary:
            private_home = Path(temporary)
            private_home.chmod(0o700)
            poetry_environment = self._preparation_environment(node_bin, private_home)
            if not python.is_file():
                self._run_preparation_command(
                    [str(python_executable), "-m", "venv", str(release / ".venv")],
                    release=release,
                    environment=poetry_environment,
                )
            if not python.is_file() or python.resolve() != python_executable.resolve():
                raise UpgradeError(
                    "candidate venv interpreter is outside the pinned Python"
                )
            self._probe_runtime_version(
                python,
                expected=expected_python,
                label="venv Python",
                environment=self._runtime_probe_environment(),
            )
            if not (node_bin / "node").is_file():
                raise UpgradeError(
                    f"candidate preparation requires side-by-side {versions['node']['formula']}; "
                    "the active release was not changed"
                )
            self._probe_runtime_version(
                node_bin / "node",
                expected=f"v{versions['node']['preferred']}",
                label="Node",
                environment=poetry_environment,
            )
            self._run_preparation_command(
                [
                    str(poetry),
                    "sync",
                    "--only",
                    "main",
                    "--no-interaction",
                ],
                release=release,
                cwd=release,
                environment=poetry_environment,
            )
            plugin = release / "openclaw" / "administrateme"
            npm = str(node_bin / "npm")
            self._run_preparation_command(
                [npm, "ci", "--ignore-scripts"],
                release=release,
                cwd=plugin,
                environment=poetry_environment,
            )
            self._run_preparation_command(
                [npm, "run", "check"],
                release=release,
                cwd=plugin,
                environment=poetry_environment,
            )
            self._run_preparation_command(
                [npm, "run", "build"],
                release=release,
                cwd=plugin,
                environment=poetry_environment,
            )
        self._validate_prepared_runtime(release)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _remove_release_stage(
        self,
        stage: Path,
        releases: Path,
        *,
        releases_descriptor: int | None = None,
    ) -> None:
        if releases_descriptor is not None:
            if not stage.name.startswith(".staging-"):
                raise UpgradeError("failed release staging cannot be safely removed")
            try:
                metadata = os.stat(
                    stage.name,
                    dir_fd=releases_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return
            except OSError as exc:
                raise UpgradeError(
                    "failed release staging cannot be safely removed"
                ) from exc
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
                or not shutil.rmtree.avoids_symlink_attacks
            ):
                raise UpgradeError("failed release staging cannot be safely removed")
            shutil.rmtree(stage.name, dir_fd=releases_descriptor)
            os.fsync(releases_descriptor)
            return
        try:
            ensure_within(stage, releases)
            metadata = stage.lstat()
        except FileNotFoundError:
            return
        except (OSError, RuntimeError) as exc:
            raise UpgradeError(
                "failed release staging cannot be safely removed"
            ) from exc
        if (
            not stage.name.startswith(".staging-")
            or stage.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise UpgradeError("failed release staging cannot be safely removed")
        shutil.rmtree(stage)
        self._fsync_directory(releases)

    def _verify_published_release(
        self,
        release: Path,
        *,
        repository: Path,
        commit: str,
    ) -> dict[str, object]:
        try:
            return verify_release_source_identity(
                release,
                repository=repository,
                commit=commit,
                allow_legacy_tree=False,
                require_tracked_tree=True,
                retained_by_commit=commit,
            )
        except SourceIdentityError as exc:
            raise UpgradeError("candidate release source identity is invalid") from exc

    @staticmethod
    def _ensure_private_directory(path: Path, *, parent: Path | None = None) -> None:
        if os.path.lexists(path):
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise UpgradeError("release publication directory is unsafe")
            return
        if parent is not None:
            UpgradeManager._ensure_private_directory(parent)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            # A concurrent creator must satisfy the same no-follow contract.
            pass
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise UpgradeError("release publication directory is unsafe")

    @staticmethod
    def _acquire_publication_lock(releases: Path) -> tuple[int, int, os.stat_result]:
        releases_descriptor = os.open(
            releases,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        releases_metadata = os.fstat(releases_descriptor)
        try:
            lock_descriptor = os.open(
                ".publication.lock",
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=releases_descriptor,
            )
        except BaseException:
            os.close(releases_descriptor)
            raise
        metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            os.close(lock_descriptor)
            os.close(releases_descriptor)
            raise UpgradeError("release publication lock is unsafe")
        try:
            entry = os.stat(
                ".publication.lock",
                dir_fd=releases_descriptor,
                follow_symlinks=False,
            )
        except BaseException:
            UpgradeManager._close_descriptors(
                lock_descriptor,
                releases_descriptor,
            )
            raise
        if (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino):
            os.close(lock_descriptor)
            os.close(releases_descriptor)
            raise UpgradeError("release publication lock changed")
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            UpgradeManager._close_descriptors(lock_descriptor, releases_descriptor)
            raise UpgradeError(
                "another release publication is already running"
            ) from exc
        try:
            locked = os.fstat(lock_descriptor)
            entry = os.stat(
                ".publication.lock",
                dir_fd=releases_descriptor,
                follow_symlinks=False,
            )
        except BaseException:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                UpgradeManager._close_descriptors(
                    lock_descriptor,
                    releases_descriptor,
                )
            raise
        if (locked.st_dev, locked.st_ino) != (entry.st_dev, entry.st_ino):
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                UpgradeManager._close_descriptors(lock_descriptor, releases_descriptor)
            raise UpgradeError("release publication lock changed")
        return lock_descriptor, releases_descriptor, releases_metadata

    @staticmethod
    def _assert_directory_coordinate(
        path: Path,
        descriptor: int,
        expected: os.stat_result,
    ) -> None:
        try:
            current = path.lstat()
            opened = os.fstat(descriptor)
        except OSError as exc:
            raise UpgradeError("release publication directory changed") from exc
        expected_coordinate = (
            expected.st_dev,
            expected.st_ino,
            expected.st_mode,
            expected.st_uid,
        )
        if (
            path.is_symlink()
            or (
                current.st_dev,
                current.st_ino,
                current.st_mode,
                current.st_uid,
            )
            != expected_coordinate
            or (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_uid,
            )
            != expected_coordinate
        ):
            raise UpgradeError("release publication directory changed")

    @staticmethod
    def _release_coordinate(
        releases_descriptor: int,
        release_name: str,
    ) -> tuple[int, int]:
        descriptor = os.open(
            release_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=releases_descriptor,
        )
        try:
            metadata = os.fstat(descriptor)
            entry = os.stat(
                release_name,
                dir_fd=releases_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022
                or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)
            ):
                raise UpgradeError("candidate release coordinate is unsafe")
            return metadata.st_dev, metadata.st_ino
        finally:
            os.close(descriptor)

    @staticmethod
    def _assert_entry_coordinate(
        parent_descriptor: int,
        name: str,
        expected: tuple[int, int],
    ) -> None:
        try:
            entry = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise UpgradeError("release publication coordinate changed") from exc
        if (entry.st_dev, entry.st_ino) != expected:
            raise UpgradeError("release publication coordinate changed")

    def _cleanup_failed_publication(
        self,
        *,
        stage: Path,
        release: Path,
        releases: Path,
        releases_descriptor: int,
        commit: str,
    ) -> None:
        """Remove only an unpublished stage; preserve state after a completed rename."""

        try:
            final_entry = os.stat(
                release.name,
                dir_fd=releases_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            final_entry = None
        if final_entry is not None:
            if (
                not stat.S_ISDIR(final_entry.st_mode)
                or final_entry.st_uid != os.getuid()
            ):
                raise UpgradeError("published release coordinate is unsafe")
            # A BaseException may land after rename(2) committed but before the
            # Python stage variable advanced.  The durable publishing record is
            # the only authority that makes this exact final coordinate retryable.
            return
        self._remove_release_stage(
            stage,
            releases,
            releases_descriptor=releases_descriptor,
        )
        self._remove_preparation_state(releases_descriptor, commit)

    @staticmethod
    def _assert_state_release_coordinate(
        state: dict[str, Any],
        coordinate: tuple[int, int],
    ) -> None:
        if (
            state.get("release_device") != coordinate[0]
            or state.get("release_inode") != coordinate[1]
        ):
            raise UpgradeError("release preparation state does not match the candidate")

    def _prepare_owned_release(
        self,
        release: Path,
        *,
        candidate: Path,
        commit: str,
        active_root: Path,
        protected_roots: frozenset[Path],
        pointer_chain_sha256: str,
        state: dict[str, Any] | None,
        releases_descriptor: int,
    ) -> None:
        evidence = self._verify_published_release(
            release,
            repository=candidate,
            commit=commit,
        )
        validate_candidate(
            release,
            active_root=active_root,
            approve_runtime_migration=self.approve_runtime_migration,
        )
        resolved_release = release.resolve()
        if resolved_release in protected_roots:
            self._validate_prepared_runtime(release)
            if resolved_release == active_root.resolve():
                return
            raise UpgradeError(
                "candidate is retained for rollback and cannot be prepared; use rollback"
            )
        if state is None:
            raise UpgradeError(
                "existing candidate has no durable preparation ownership"
            )
        if state.get("pointer_chain_sha256") != pointer_chain_sha256:
            raise UpgradeError("release preparation state has stale pointer binding")
        coordinate = self._release_coordinate(releases_descriptor, commit)
        phase = str(state["phase"])
        if phase in {"preparing", "complete", "consumed"}:
            self._assert_state_release_coordinate(state, coordinate)
        if phase in {"preparing", "complete", "consumed"} and state.get(
            "source_manifest_sha256"
        ) != evidence.get("tracked_source_manifest_sha256"):
            raise UpgradeError("release preparation source binding changed")
        if phase == "consumed":
            raise UpgradeError(
                "candidate preparation authority was already consumed by an activation"
            )
        if phase not in {"publishing", "preparing", "complete"}:
            raise UpgradeError("release preparation state has an invalid phase")
        expected_activity_coordinate = (
            (int(state["activity_device"]), int(state["activity_inode"]))
            if phase in {"preparing", "complete"}
            else None
        )
        activity_descriptor, activity_coordinate = self._open_preparation_activity(
            releases_descriptor,
            commit,
            expected_coordinate=expected_activity_coordinate,
        )
        try:
            if phase == "publishing":
                state = {
                    **state,
                    "phase": "preparing",
                    "source_manifest_sha256": evidence[
                        "tracked_source_manifest_sha256"
                    ],
                    "source_file_count": evidence["tracked_source_file_count"],
                    "release_device": coordinate[0],
                    "release_inode": coordinate[1],
                    "activity_device": activity_coordinate[0],
                    "activity_inode": activity_coordinate[1],
                }
                self._write_preparation_state(releases_descriptor, state)
            elif phase == "complete":
                state = {**state, "phase": "preparing"}
                self._write_preparation_state(releases_descriptor, state)
            remove_generated_runtime_artifacts(
                release,
                repository=candidate,
                commit=commit,
                expected_coordinate=coordinate,
            )
            cleaned = self._verify_published_release(
                release,
                repository=candidate,
                commit=commit,
            )
            if cleaned.get("tracked_source_manifest_sha256") != state.get(
                "source_manifest_sha256"
            ):
                raise UpgradeError("candidate source changed during generated cleanup")
            fcntl.flock(activity_descriptor, fcntl.LOCK_SH)
            self._active_preparation_activity_descriptor = activity_descriptor
            self._active_preparation_activity_path = (
                release.parent / self._preparation_activity_name(commit)
            )
            self._active_preparation_activity_coordinate = activity_coordinate
            try:
                self._prepare_release(release)
            finally:
                self._active_preparation_activity_descriptor = None
                self._active_preparation_activity_path = None
                self._active_preparation_activity_coordinate = None
            metadata = os.fstat(activity_descriptor)
            public = os.stat(
                self._preparation_activity_name(commit),
                dir_fd=releases_descriptor,
                follow_symlinks=False,
            )
            if (metadata.st_dev, metadata.st_ino) != activity_coordinate or (
                public.st_dev,
                public.st_ino,
            ) != activity_coordinate:
                raise UpgradeError("candidate preparation activity lease changed")
            try:
                fcntl.flock(activity_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpgradeError(
                    "candidate preparation worker remained active after its command"
                ) from exc
            validate_candidate(
                release,
                active_root=active_root,
                approve_runtime_migration=self.approve_runtime_migration,
            )
            prepared = self._verify_published_release(
                release,
                repository=candidate,
                commit=commit,
            )
            if prepared.get("tracked_source_manifest_sha256") != state.get(
                "source_manifest_sha256"
            ):
                raise UpgradeError("candidate source changed during preparation")
            self._validate_prepared_runtime(release)
            self._fsync_directory(release)
            public = os.stat(
                self._preparation_activity_name(commit),
                dir_fd=releases_descriptor,
                follow_symlinks=False,
            )
            if (public.st_dev, public.st_ino) != activity_coordinate:
                raise UpgradeError("candidate preparation activity lease changed")
            self._write_preparation_state(
                releases_descriptor,
                {**state, "phase": "complete"},
            )
        except (OSError, SourceIdentityError, subprocess.SubprocessError) as exc:
            raise UpgradeError(
                "candidate runtime preparation failed at its stable release path"
            ) from exc
        finally:
            os.close(activity_descriptor)

    def _consume_preparation_state(
        self,
        candidate: Path,
        commit: str,
        release: Path,
    ) -> int:
        releases = self.paths.app_dir / "releases"
        (
            lock_descriptor,
            releases_descriptor,
            releases_metadata,
        ) = self._acquire_publication_lock(releases)
        activity_descriptor: int | None = None
        failed = False
        try:
            self._assert_directory_coordinate(
                releases, releases_descriptor, releases_metadata
            )
            _active, protected, pointer_digest, _rows = self._release_pointer_snapshot()
            if release.resolve() in protected:
                raise UpgradeError(
                    "candidate became selected before preparation settled"
                )
            state = self._load_preparation_state(releases_descriptor, commit)
            if state is None or state.get("phase") != "complete":
                raise UpgradeError("candidate preparation is not complete")
            if state.get("pointer_chain_sha256") != pointer_digest:
                raise UpgradeError("candidate preparation pointer binding changed")
            coordinate = self._release_coordinate(releases_descriptor, commit)
            self._assert_state_release_coordinate(state, coordinate)
            evidence = self._verify_published_release(
                release,
                repository=candidate,
                commit=commit,
            )
            if evidence.get("tracked_source_manifest_sha256") != state.get(
                "source_manifest_sha256"
            ):
                raise UpgradeError("candidate preparation source binding changed")
            activity_descriptor, activity_coordinate = self._open_preparation_activity(
                releases_descriptor,
                commit,
                expected_coordinate=(
                    int(state["activity_device"]),
                    int(state["activity_inode"]),
                ),
            )
            if activity_coordinate != (
                int(state["activity_device"]),
                int(state["activity_inode"]),
            ):
                raise UpgradeError("candidate preparation activity lease changed")
            self._validate_prepared_runtime(release)
            self._write_preparation_state(
                releases_descriptor,
                {**state, "phase": "consumed"},
            )
        except BaseException:
            failed = True
            raise
        finally:
            cleanup_error: BaseException | None = None
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            close_error = self._close_descriptors(lock_descriptor, releases_descriptor)
            cleanup_error = cleanup_error or close_error
            if failed and activity_descriptor is not None:
                activity_close_error = self._close_descriptors(activity_descriptor)
                cleanup_error = cleanup_error or activity_close_error
                activity_descriptor = None
            if cleanup_error is not None and not failed:
                self._close_descriptors(activity_descriptor)
                raise UpgradeError(
                    "release publication lock cleanup failed"
                ) from cleanup_error
        assert activity_descriptor is not None
        return activity_descriptor

    def _publish_release(self, candidate: Path, commit: str) -> Path:
        """Publish fixed source, then prepare only at its stable final coordinate."""

        self._ensure_private_directory(self.paths.root)
        self._ensure_private_directory(self.paths.app_dir, parent=self.paths.root)
        releases = self.paths.app_dir / "releases"
        self._ensure_private_directory(releases, parent=self.paths.app_dir)
        (
            lock_descriptor,
            releases_descriptor,
            releases_metadata,
        ) = self._acquire_publication_lock(releases)
        release = releases / commit
        ensure_within(release, releases)
        stage: Path | None = None
        stage_coordinate: tuple[int, int] | None = None
        failed = False
        try:
            self._assert_directory_coordinate(
                releases, releases_descriptor, releases_metadata
            )
            active_root, protected_roots, pointer_digest, _rows = (
                self._release_pointer_snapshot()
            )
            state = self._load_preparation_state(releases_descriptor, commit)
            if os.path.lexists(release):
                if release.is_symlink() or not release.is_dir():
                    raise UpgradeError("existing candidate release is unsafe")
                self._prepare_owned_release(
                    release,
                    candidate=candidate,
                    commit=commit,
                    active_root=active_root,
                    protected_roots=protected_roots,
                    pointer_chain_sha256=pointer_digest,
                    state=state,
                    releases_descriptor=releases_descriptor,
                )
                return release
            if state is not None:
                if state.get("phase") != "publishing":
                    raise UpgradeError(
                        "release preparation state exists without its candidate"
                    )
                if state.get("pointer_chain_sha256") != pointer_digest:
                    raise UpgradeError(
                        "release preparation state has stale pointer binding"
                    )
                stale_stage = releases / str(state["stage_name"])
                self._remove_release_stage(
                    stale_stage,
                    releases,
                    releases_descriptor=releases_descriptor,
                )
                self._remove_preparation_state(releases_descriptor, commit)
            if release.resolve() == active_root.resolve():
                raise UpgradeError("active release pointer selects a missing release")

            stage = releases / f".staging-{commit[:12]}.{uuid.uuid4().hex}"
            ensure_within(stage, releases)
            state = self._new_preparation_state(
                commit=commit,
                stage_name=stage.name,
                pointer_chain_sha256=pointer_digest,
            )
            self._write_preparation_state(releases_descriptor, state)
            try:
                stage_coordinate = materialize_git_tree(
                    candidate,
                    commit,
                    stage,
                    parent_descriptor=releases_descriptor,
                )
                self._assert_directory_coordinate(
                    releases, releases_descriptor, releases_metadata
                )
                validate_candidate(
                    stage,
                    active_root=active_root,
                    approve_runtime_migration=self.approve_runtime_migration,
                )
                try:
                    stage_descriptor = os.open(
                        stage.name,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=releases_descriptor,
                    )
                    try:
                        write_source_marker_at(stage_descriptor, commit)
                    finally:
                        os.close(stage_descriptor)
                except SourceIdentityError as exc:
                    raise UpgradeError(
                        "candidate release marker could not be written"
                    ) from exc
                self._verify_published_release(
                    stage,
                    repository=candidate,
                    commit=commit,
                )
                self._fsync_directory(stage)
                self._assert_directory_coordinate(
                    releases, releases_descriptor, releases_metadata
                )
                if os.path.lexists(release):
                    raise UpgradeError("candidate release appeared during publication")
                assert stage_coordinate is not None
                self._assert_entry_coordinate(
                    releases_descriptor,
                    stage.name,
                    stage_coordinate,
                )
                os.rename(
                    stage.name,
                    release.name,
                    src_dir_fd=releases_descriptor,
                    dst_dir_fd=releases_descriptor,
                )
                stage = None
                os.fsync(releases_descriptor)
                if self._release_coordinate(releases_descriptor, release.name) != (
                    stage_coordinate
                ):
                    raise UpgradeError("published release coordinate changed")
                self._assert_directory_coordinate(
                    releases, releases_descriptor, releases_metadata
                )
            except SourceProcessNotReaped as exc:
                self._write_preparation_state(
                    releases_descriptor,
                    {**state, "phase": "publishing_unreaped"},
                )
                raise UpgradeError(
                    "candidate release publication terminality is unknown; "
                    "private stage retained"
                ) from exc
            except (OSError, SourceIdentityError, subprocess.SubprocessError) as exc:
                if stage is not None:
                    self._cleanup_failed_publication(
                        stage=stage,
                        release=release,
                        releases=releases,
                        releases_descriptor=releases_descriptor,
                        commit=commit,
                    )
                raise UpgradeError("candidate release publication failed") from exc
            except BaseException:
                if stage is not None:
                    self._cleanup_failed_publication(
                        stage=stage,
                        release=release,
                        releases=releases,
                        releases_descriptor=releases_descriptor,
                        commit=commit,
                    )
                raise

            self._prepare_owned_release(
                release,
                candidate=candidate,
                commit=commit,
                active_root=active_root,
                protected_roots=protected_roots,
                pointer_chain_sha256=pointer_digest,
                state=state,
                releases_descriptor=releases_descriptor,
            )
            return release
        except BaseException:
            failed = True
            raise
        finally:
            cleanup_error: BaseException | None = None
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            close_error = self._close_descriptors(lock_descriptor, releases_descriptor)
            cleanup_error = cleanup_error or close_error
            if cleanup_error is not None and not failed:
                raise UpgradeError(
                    "release publication lock cleanup failed"
                ) from cleanup_error

    def _candidate_commit(self, candidate: Path) -> str:
        try:
            return reviewed_candidate_commit(candidate)
        except SourceIdentityError as exc:
            raise UpgradeError(
                "upgrade requires one clean reviewed Git commit"
            ) from exc


__all__ = ["UpgradeError", "UpgradeManager", "validate_candidate"]
