"""Explicit, reversible replacement of a disposable household test dataset.

This is an offline state operation used by the reviewed updater, not a repair
of production event history. An old activation journal is retained byte for
byte and is never transitioned to ``recovered`` by this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from bootstrap.candidate_readiness import _json_no_duplicates, _read_private_file
from bootstrap.contracts import DeploymentPaths, atomic_write, sha256_file

CONTRACT = "disposable-test-reset-v1"
DATASET_CONTRACT = "disposable-test-dataset-v1"
ADMISSION_CONTRACT = "disposable-test-admissions-v1"
ADMISSION_FILENAME = "lifetime-admissions.json"
KEEP_DIRECTORIES = ("config", "openclaw", "bridges")
REPLACE_DIRECTORIES = ("events", "projections", "data")
RESET_CONFIG_FILES = frozenset(
    {
        "openclaw-approvals.sqlite3",
        "openclaw-approvals.sqlite3-wal",
        "openclaw-approvals.sqlite3-shm",
        "temporary-surfaces.json",
    }
)
MUTABLE_CONFIG_FILES = RESET_CONFIG_FILES | {"pack-manifests.json"}
PRESERVED_CONFIGURATION = (
    "openclaw/runtime/openclaw.json",
    "bridges/member-mapping.json",
)


class TestResetError(RuntimeError):
    """The exact approved reset cannot be performed safely."""


def _json(path: Path) -> dict[str, Any]:
    payload, _metadata = _read_private_file(path)
    return _json_no_duplicates(payload, label="test-reset input")


def manifest_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _directory(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise TestResetError("test-reset directory is not private and owned")
    return metadata.st_dev, metadata.st_ino


def _sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _move(source: Path, target: Path) -> None:
    if os.path.lexists(target):
        raise TestResetError("test-reset destination already exists")
    os.rename(source, target)
    _sync(source.parent)
    _sync(target.parent)


def _config_hashes(paths: DeploymentPaths) -> dict[str, str]:
    root = paths.instance_dir / "config"
    _directory(root)
    result = {}
    for path in sorted(root.iterdir()):
        if path.name in MUTABLE_CONFIG_FILES:
            continue
        payload, _metadata = _read_private_file(path)
        result[path.name] = hashlib.sha256(payload).hexdigest()
    return result


def _preserved_config_hashes(paths: DeploymentPaths) -> dict[str, str | None]:
    result = {}
    for relative in PRESERVED_CONFIGURATION:
        path = paths.instance_dir / relative
        result[relative] = (
            hashlib.sha256(_read_private_file(path)[0]).hexdigest()
            if os.path.lexists(path)
            else None
        )
    return result


def build_manifest(
    paths: DeploymentPaths,
    *,
    candidate_commit: str,
    acquisition: dict[str, Any],
    diagnostic_backup: Path,
) -> dict[str, Any]:
    """Read metadata only. The result authorizes nothing until owner approval."""

    from adminme.adapters.disposable_test_acquisition import restrict_adapter_config

    if re.fullmatch(r"[0-9a-f]{40}", candidate_commit) is None:
        raise TestResetError("reset requires an exact reviewed candidate commit")
    for directory in (paths.root, paths.app_dir, paths.rollback_dir):
        _directory(directory)
    state_coordinate = _directory(paths.instance_dir)
    names = sorted(path.name for path in paths.instance_dir.iterdir())
    if set(names) - set(KEEP_DIRECTORIES + REPLACE_DIRECTORIES):
        raise TestResetError("state has unclassified top-level paths")
    adapter_config = _json(paths.instance_dir / "config" / "adapters.json")
    adapter_config["disposable_test_acquisition"] = acquisition
    restrict_adapter_config(adapter_config)
    observation = _json(paths.instance_dir / "config" / "observation.json")
    if observation.get("active") is not True:
        raise TestResetError("test reset requires observation already active")
    if diagnostic_backup.parent != paths.backup_dir or diagnostic_backup.is_symlink():
        raise TestResetError("diagnostic backup must be an existing retained archive")
    backup_metadata = diagnostic_backup.lstat()
    if (
        not stat.S_ISREG(backup_metadata.st_mode)
        or backup_metadata.st_nlink != 1
        or backup_metadata.st_uid != os.getuid()
        or stat.S_IMODE(backup_metadata.st_mode) & 0o077
    ):
        raise TestResetError("diagnostic backup is unsafe")
    pointer_payload, _metadata = _read_private_file(
        paths.app_dir / "current-release.json"
    )
    pointer = _json_no_duplicates(pointer_payload, label="release pointer")
    journal_path = paths.app_dir / "activation" / "active.json"
    journal_payload = (
        _read_private_file(journal_path)[0] if journal_path.exists() else None
    )
    journal = (
        _json_no_duplicates(journal_payload, label="activation journal")
        if journal_payload is not None
        else None
    )
    reset_id = uuid.uuid4().hex
    archive = paths.rollback_dir / f"test-reset-{reset_id}"
    return {
        "contract": CONTRACT,
        "reset_id": reset_id,
        "instance_root": str(paths.root),
        "candidate_commit": candidate_commit,
        "selected_commit": pointer["commit"],
        "pointer_sha256": hashlib.sha256(pointer_payload).hexdigest(),
        "state_coordinate": list(state_coordinate),
        "state_directories": names,
        "configuration_sha256": _config_hashes(paths),
        "preserved_configuration_sha256": _preserved_config_hashes(paths),
        "acquisition": acquisition,
        "keep": [name for name in KEEP_DIRECTORIES if name in names],
        "replace": list(REPLACE_DIRECTORIES),
        "reset_config_files": sorted(RESET_CONFIG_FILES),
        "generated_openclaw_scratch": "retain offline only; regenerate plugin discovery links and codex-home/tmp",
        "settings": "retain secure enrollment files and member preferences; reset event-backed requests, rules and decisions",
        "archive": str(archive),
        "lifetime_admissions": {
            "path": str(archive / ADMISSION_FILENAME),
            "contract": ADMISSION_CONTRACT,
            "distinct_message_cap": 200,
            "disposition": "retain monotonic digest-only admission union outside restored state; never reset or recreate if missing",
        },
        "activation_journal": {
            "sha256": hashlib.sha256(journal_payload).hexdigest()
            if journal_payload
            else None,
            "transaction_id": journal.get("transaction_id") if journal else None,
            "phase": journal.get("phase") if journal else None,
            "disposition": "retain unchanged offline; no recovery claim",
        },
        "diagnostic_backup": {
            "path": str(diagnostic_backup),
            "size": backup_metadata.st_size,
            "sha256": sha256_file(diagnostic_backup),
        },
        "provider_mutations": False,
        "observation_active": True,
        "production_rollback_proven": False,
    }


def validate_manifest(paths: DeploymentPaths, manifest: dict[str, Any]) -> None:
    """Bind approval to the instance, selection, config, journal and destinations."""

    if (
        manifest.get("contract") != CONTRACT
        or manifest.get("instance_root") != str(paths.root)
        or not isinstance(manifest.get("reset_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", manifest["reset_id"]) is None
    ):
        raise TestResetError("reset manifest identity is invalid")
    expected = build_manifest(
        paths,
        candidate_commit=manifest["candidate_commit"],
        acquisition=manifest["acquisition"],
        diagnostic_backup=Path(manifest["diagnostic_backup"]["path"]),
    )
    expected["reset_id"] = manifest["reset_id"]
    expected["archive"] = str(paths.rollback_dir / f"test-reset-{manifest['reset_id']}")
    expected["lifetime_admissions"]["path"] = str(
        Path(expected["archive"]) / ADMISSION_FILENAME
    )
    if expected != manifest:
        raise TestResetError(
            "reset manifest is stale or changed; prepare an exact new manifest"
        )


def _empty_admissions(manifest: dict[str, Any]) -> dict[str, Any]:
    owner = {
        key: manifest["acquisition"][key]
        for key in ("contract", "adapter_id", "member_id", "provider_account_id")
    }
    return {
        "contract": ADMISSION_CONTRACT,
        "dataset_id": manifest["reset_id"],
        "manifest_sha256": manifest_digest(manifest),
        "owner_sha256": hashlib.sha256(
            json.dumps(owner, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "message_sha256": [],
    }


def _read_admissions(archive: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    from adminme.adapters.disposable_test_acquisition import MAX_MESSAGES
    from bootstrap.candidate_readiness import CandidateReadinessError

    expected = _empty_admissions(manifest)
    try:
        value = _json(archive / ADMISSION_FILENAME)
    except (OSError, CandidateReadinessError) as exc:
        raise TestResetError(
            "bounded lifetime admission receipt is unavailable"
        ) from exc
    digests = value.get("message_sha256")
    if (
        set(value) != set(expected)
        or any(
            value[key] != expected[key] for key in expected if key != "message_sha256"
        )
        or not isinstance(digests, list)
        or len(digests) > MAX_MESSAGES
        or any(
            not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
            for item in digests
        )
        or len(set(digests)) != len(digests)
    ):
        raise TestResetError("bounded lifetime admission receipt is invalid")
    return value


def _reserve_admissions(paths: DeploymentPaths, message_ids: tuple[str, ...]) -> int:
    from adminme.adapters.disposable_test_acquisition import MAX_MESSAGES

    marker = _json(paths.instance_dir / "config/disposable-test-dataset.json")
    archive = paths.rollback_dir / f"test-reset-{marker['dataset_id']}"
    manifest = _json(archive / "manifest.json")
    value = _read_admissions(archive, manifest)
    admitted = set(value["message_sha256"])
    admitted.update(
        hashlib.sha256(message.encode()).hexdigest() for message in message_ids
    )
    if len(admitted) > MAX_MESSAGES:
        raise TestResetError("bounded dataset lifetime admission cap would be exceeded")
    if admitted != set(value["message_sha256"]):
        value["message_sha256"] = sorted(admitted)
        atomic_write(archive / ADMISSION_FILENAME, json.dumps(value, indent=2).encode())
        _sync(archive)
    return len(admitted)


def _copy_private_tree(source: Path, target: Path) -> None:
    """Copy only owned ordinary files; generated plugin discovery is regenerated."""

    metadata = source.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise TestResetError("preserved directory ownership or write access is unsafe")
    target.mkdir(mode=0o700)
    for path in sorted(source.iterdir()):
        destination = target / path.name
        metadata = path.lstat()
        if path.name == "tmp" and source.name == "codex-home":
            # Reproducible provider execution scratch is retained only in the
            # original offline state, never used by the new dataset.
            continue
        if stat.S_ISLNK(metadata.st_mode):
            if source.name == "plugin-skills":
                continue
            raise TestResetError("preserved state contains an unsupported link")
        if metadata.st_uid != os.getuid():
            raise TestResetError("preserved state has different ownership")
        if stat.S_ISDIR(metadata.st_mode):
            _copy_private_tree(path, destination)
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
            shutil.copyfile(path, destination, follow_symlinks=False)
            destination.chmod(0o700 if metadata.st_mode & 0o100 else 0o600)
        else:
            raise TestResetError("preserved state contains a non-regular file")


class OfflineTestReset:
    """State move primitive. Caller holds the updater lock and proves stop first.

    The durable record precedes each move. Recovery uses directory coordinates
    to handle interruption between a rename and its next journal write.
    """

    def __init__(self, paths: DeploymentPaths, manifest: dict[str, Any]) -> None:
        self.paths = paths
        self.manifest = manifest
        if (
            manifest.get("contract") != CONTRACT
            or manifest.get("instance_root") != str(paths.root)
            or re.fullmatch(r"[0-9a-f]{32}", str(manifest.get("reset_id"))) is None
        ):
            raise TestResetError("reset manifest identity is invalid")
        self.archive = paths.rollback_dir / f"test-reset-{manifest['reset_id']}"
        if manifest.get("archive") != str(self.archive):
            raise TestResetError("reset archive coordinate is invalid")
        self.record_path = self.archive / "reset.json"
        self.fresh = self.archive / "fresh-state"
        self.original = self.archive / "original-state"

    def _record(self, value: dict[str, Any]) -> None:
        atomic_write(
            self.record_path, json.dumps(value, sort_keys=True, indent=2).encode()
        )
        _sync(self.archive)

    def begin(self) -> dict[str, Any]:
        """Publish complete recovery intent atomically before service shutdown."""
        import tempfile

        validate_manifest(self.paths, self.manifest)
        if self.manifest["acquisition"]["message_ids"]:
            raise TestResetError("reset initialization requires an empty selection")
        with tempfile.TemporaryDirectory(
            prefix=".test-reset-intent-", dir=self.paths.rollback_dir
        ) as temporary:
            staging = Path(temporary)
            (staging / "fresh-state").mkdir(mode=0o700)
            record = {
                "contract": CONTRACT,
                "manifest_sha256": manifest_digest(self.manifest),
                "phase": "initialized",
                "original_coordinate": list(_directory(self.paths.instance_dir)),
                "fresh_coordinate": list(_directory(staging / "fresh-state")),
                "production_rollback_proven": False,
            }
            atomic_write(staging / "reset.json", json.dumps(record, indent=2).encode())
            atomic_write(
                staging / "manifest.json", json.dumps(self.manifest, indent=2).encode()
            )
            atomic_write(
                staging / ADMISSION_FILENAME,
                json.dumps(_empty_admissions(self.manifest), indent=2).encode(),
            )
            for source, destination in (
                (self.paths.app_dir / "current-release.json", "original-release.json"),
                (
                    self.paths.app_dir / "activation" / "active.json",
                    "original-activation.json",
                ),
            ):
                if source.exists():
                    payload, _metadata = _read_private_file(source)
                    atomic_write(staging / destination, payload)
            _sync(staging)
            _move(staging, self.archive)
        return record

    def prepare(self) -> dict[str, Any]:
        if not self.record_path.exists():
            self.begin()
        validate_manifest(self.paths, self.manifest)
        record = _json(self.record_path)
        if record.get("phase") != "initialized" or record.get(
            "manifest_sha256"
        ) != manifest_digest(self.manifest):
            raise TestResetError("reset preparation intent is invalid")
        record["phase"] = "preparing"
        self._record(record)
        for name in self.manifest["keep"]:
            _copy_private_tree(self.paths.instance_dir / name, self.fresh / name)
        for name in REPLACE_DIRECTORIES:
            (self.fresh / name).mkdir(mode=0o700)
        config = self.fresh / "config"
        for name in RESET_CONFIG_FILES:
            (config / name).unlink(missing_ok=True)
        # Secure enrollment files are separate from event-backed Settings request
        # history, which is reset along with its events. Member preferences stay.
        control_path = config / "settings-control.json"
        if control_path.exists():
            control = _json(control_path)
            for name in (
                "candidate_policies",
                "source_policies_v2",
                "source_policy_v2_revisions",
                "source_policy_v2_migrations",
                "message_reply_controls",
                "message_reply_control_revisions",
                "bridge_requests",
                "bridge_request_revisions",
                "capability_requests",
                "capability_request_revisions",
            ):
                if name in control:
                    control[name] = {}
            atomic_write(control_path, json.dumps(control, indent=2).encode())
        adapter_path = config / "adapters.json"
        adapters = _json(adapter_path)
        adapters["disposable_test_acquisition"] = copy.deepcopy(
            self.manifest["acquisition"]
        )
        atomic_write(adapter_path, json.dumps(adapters, indent=2).encode())
        atomic_write(
            config / "disposable-test-dataset.json",
            json.dumps(
                {
                    "contract": DATASET_CONTRACT,
                    "dataset_id": self.manifest["reset_id"],
                    "reset_manifest_sha256": manifest_digest(self.manifest),
                    "acquisition_contract": "disposable-test-acquisition-v1",
                },
                indent=2,
            ).encode(),
        )
        record["phase"] = "prepared"
        self._record(record)
        return record

    def switch(self) -> dict[str, Any]:
        record = _json(self.record_path)
        if record.get("phase") != "prepared":
            raise TestResetError("reset is not prepared")
        if list(_directory(self.paths.instance_dir)) != record["original_coordinate"]:
            raise TestResetError("active dataset changed before reset")
        if list(_directory(self.fresh)) != record["fresh_coordinate"]:
            raise TestResetError("fresh dataset changed before reset")
        record["phase"] = "switching"
        self._record(record)
        _move(self.paths.instance_dir, self.original)
        _move(self.fresh, self.paths.instance_dir)
        journal = self.paths.app_dir / "activation" / "active.json"
        if journal.exists():
            payload, _metadata = _read_private_file(journal)
            if (
                hashlib.sha256(payload).hexdigest()
                != self.manifest["activation_journal"]["sha256"]
            ):
                raise TestResetError(
                    "activation journal changed before offline retention"
                )
            # The original exact bytes already exist in the offline archive.
            journal.unlink()
            _sync(journal.parent)
        record["phase"] = "selected_unaccepted"
        self._record(record)
        return record

    def restore(self) -> dict[str, Any]:
        """Restore exact pre-reset state; this deliberately claims no health."""

        record = _json(self.record_path)
        if record.get("manifest_sha256") != manifest_digest(self.manifest):
            raise TestResetError("reset recovery manifest differs")
        if self.original.exists():
            if list(_directory(self.original)) != record["original_coordinate"]:
                raise TestResetError("original dataset coordinate changed")
            record["phase"] = "restoring_original"
            self._record(record)
            if self.paths.instance_dir.exists():
                if (
                    list(_directory(self.paths.instance_dir))
                    != record["fresh_coordinate"]
                ):
                    raise TestResetError(
                        "refusing to replace an unrelated active dataset"
                    )
                _move(self.paths.instance_dir, self.archive / "failed-test-state")
            _move(self.original, self.paths.instance_dir)
        elif list(_directory(self.paths.instance_dir)) != record["original_coordinate"]:
            raise TestResetError("original dataset is unavailable")
        elif record.get("phase") != "restoring_original":
            # Intent can precede its active marker. Never restore stale control
            # bytes after another operation changed the still-original dataset.
            validate_manifest(self.paths, self.manifest)
        original_journal = self.archive / "original-activation.json"
        if original_journal.exists():
            atomic_write(
                self.paths.app_dir / "activation" / "active.json",
                _read_private_file(original_journal)[0],
            )
        original_pointer = self.archive / "original-release.json"
        if original_pointer.exists():
            atomic_write(
                self.paths.app_dir / "current-release.json",
                _read_private_file(original_pointer)[0],
            )
        record["phase"] = "original_restored_health_unproven"
        self._record(record)
        return record


def execute_reset(
    upgrades: Any, candidate: Path, manifest_path: Path
) -> dict[str, Any]:
    """Use the guarded updater's immutable preparation, ownership and Doctor.

    Called only while its canonical operation lock is held. A reset never
    closes the old failed activation or upgrades/migrates provider enrollment.
    """

    import asyncio
    import dataclasses
    import fcntl
    from datetime import datetime, timezone

    from adminme.events.log import EventLog
    from bootstrap.backup_restore import BackupManager, _authenticate_backup_to_path
    from bootstrap.candidate_readiness import capture_deployment_snapshot
    from bootstrap.openclaw_tools import (
        configure_required_llm_tool,
        reconcile_administrateme_plugin,
    )
    from bootstrap.upgrade import validate_candidate
    from bootstrap.wizard import install_launch_agents

    manifest = _json(manifest_path)
    operation = OfflineTestReset(upgrades.paths, manifest)
    commit = upgrades._candidate_commit(candidate)
    upgrades._attest_controller_provenance(candidate, commit)
    if commit != manifest.get("candidate_commit"):
        raise TestResetError("reset manifest does not name this immutable release")
    if any(
        (
            upgrades.approve_runtime_migration,
            upgrades.approve_source_assessment_repair,
            upgrades.approve_provider_binding_migration,
            upgrades.pending_attestation_id,
        )
    ):
        raise TestResetError(
            "test reset cannot be combined with migration or history repair"
        )
    if manifest["acquisition"]["message_ids"]:
        raise TestResetError("clean reset must precede selection and reseeding")
    previous_release = upgrades._active_release_root()
    services = upgrades.services
    active_reset = upgrades.paths.app_dir / "disposable-test-reset-active.json"
    if active_reset.exists() and _json(active_reset).get(
        "manifest_sha256"
    ) != manifest_digest(manifest):
        raise TestResetError("another disposable test reset requires recovery")

    def clear_active_reset() -> None:
        if active_reset.exists():
            active_reset.unlink()
            _sync(active_reset.parent)

    def validate_restored_control(record: dict[str, Any]) -> None:
        if (
            list(_directory(upgrades.paths.instance_dir))
            != record["original_coordinate"]
            or _read_private_file(upgrades.paths.app_dir / "current-release.json")[0]
            != _read_private_file(operation.archive / "original-release.json")[0]
        ):
            raise TestResetError("restored original release coordinate changed")
        journal = upgrades.paths.app_dir / "activation" / "active.json"
        digest = (
            hashlib.sha256(_read_private_file(journal)[0]).hexdigest()
            if journal.exists()
            else None
        )
        if digest != manifest["activation_journal"]["sha256"]:
            raise TestResetError("restored original journal changed")

    def restore_original() -> dict[str, Any]:
        # No control or state move follows an unproven service stop.
        services.stop()
        if services.status().status != "stopped":
            raise TestResetError(
                "reset reversal requires absent managed services and listeners"
            )
        record = _json(operation.record_path)
        if record.get("phase") == "original_restored_health_unproven":
            # The state/pointer/journal move completed, but release/service
            # restoration can still need a retry after an interrupted start.
            validate_restored_control(record)
            result = record
        else:
            result = operation.restore()
        original = _json(operation.archive / "original-release.json")
        old_release = Path(original["path"])
        upgrades._install_reviewed_openclaw(old_release)
        reconcile_administrateme_plugin(
            upgrades.paths, old_release, test_mode=upgrades.test_mode
        )
        install_launch_agents(upgrades.paths, old_release, test_mode=upgrades.test_mode)
        status = services.start()
        result["original_service_status"] = status.status
        result["original_health_proven"] = False
        result["original_service_restoration_complete"] = True
        operation._record(result)
        clear_active_reset()
        return result

    if operation.record_path.exists():
        record = _json(operation.record_path)
        if record.get("manifest_sha256") != manifest_digest(manifest):
            raise TestResetError("existing reset record has a different manifest")
        if record["phase"] == "accepted":
            if upgrades._active_release_commit() != commit:
                raise TestResetError("accepted reset is no longer the selected release")
            _accepted_dataset(upgrades.paths, commit)
            clear_active_reset()
            return record
        if (
            record["phase"] == "original_restored_health_unproven"
            and record.get("original_service_restoration_complete") is True
        ):
            validate_restored_control(record)
            clear_active_reset()
            return record
        return restore_original()
    validate_manifest(upgrades.paths, manifest)
    validate_candidate(
        candidate, active_root=previous_release, approve_runtime_migration=False
    )
    upgrades._preflight_keychain_access(candidate)
    release = upgrades._publish_release(candidate, commit)
    # Verify the already retained encrypted diagnostic archive once, without
    # asserting its historical assessments are healthy or starting a replay.
    manager = BackupManager(
        upgrades.paths, root=candidate, test_mode=upgrades.test_mode
    )
    references = manager._references()
    backup_key = manager.store.get(references["backup"])
    _envelope, archive_digest = _authenticate_backup_to_path(
        Path(manifest["diagnostic_backup"]["path"]),
        backup_key,
        None,
        expected_artifact_sha256=manifest["diagnostic_backup"]["sha256"],
    )
    if archive_digest != manifest["diagnostic_backup"]["sha256"]:
        raise TestResetError("diagnostic archive changed during authentication")
    activity_descriptor: int | None = None
    try:
        operation.begin()
        atomic_write(
            active_reset,
            json.dumps(
                {
                    "contract": CONTRACT,
                    "manifest_sha256": manifest_digest(manifest),
                    "reset_id": manifest["reset_id"],
                    "archive": str(operation.archive),
                },
                indent=2,
            ).encode(),
        )
        _sync(active_reset.parent)
        services.stop()
        if services.status().status != "stopped":
            raise TestResetError("reset requires absent managed services and listeners")
        operation.prepare()
        event_key = manager.store.get(references["event_log"])
        # Opening a fresh SQLCipher log creates schemas but no household facts.
        event_log = EventLog(operation.fresh / "events" / "events.db", event_key)
        asyncio.run(event_log.close())
        fresh_paths = dataclasses.replace(upgrades.paths, instance_dir=operation.fresh)
        fresh_manager = BackupManager(
            fresh_paths, root=candidate, test_mode=upgrades.test_mode
        )
        fresh_manager._services = services
        clean_backup = (
            upgrades.paths.backup_dir / f"test-reset-{manifest['reset_id']}.ambackup"
        )
        backup = fresh_manager.create(clean_backup)
        receipt_path = (
            upgrades.paths.backup_dir
            / f"test-reset-{manifest['reset_id']}-rehearsal.json"
        )
        rehearsal = fresh_manager.rehearse(
            clean_backup, candidate=candidate, receipt=receipt_path
        )
        if rehearsal.get("status") != "healthy":
            raise TestResetError("fresh dataset restore rehearsal failed")
        record = _json(operation.record_path)
        record["clean_backup"] = backup
        record["clean_rehearsal_sha256"] = sha256_file(receipt_path)
        operation._record(record)
        activity_descriptor = upgrades._consume_preparation_state(
            candidate, commit, release
        )
        operation.switch()
        upgrades._install_reviewed_openclaw(release)
        reconcile_administrateme_plugin(
            upgrades.paths, release, test_mode=upgrades.test_mode
        )
        original_pointer = _json(operation.archive / "original-release.json")
        atomic_write(
            upgrades.paths.app_dir / "current-release.json",
            json.dumps(
                {
                    "path": str(release),
                    "commit": commit,
                    "previous": original_pointer,
                },
                indent=2,
            ).encode(),
        )
        configure_required_llm_tool(upgrades.paths, test_mode=upgrades.test_mode)
        install_launch_agents(upgrades.paths, release, test_mode=upgrades.test_mode)
        snapshot = capture_deployment_snapshot(upgrades.paths)
        evidence_path = (
            upgrades.paths.instance_dir / "config" / "deployment-readiness.json"
        )
        evidence = _json(evidence_path) if evidence_path.exists() else {}
        evidence.pop("activation_evidence", None)
        evidence["contract"] = "deployment-readiness-v1"
        evidence["deployment_binding"] = {
            "release_commit": commit,
            "configuration_fingerprint": snapshot.configuration_fingerprint,
            "host_fingerprint": snapshot.host_fingerprint,
            "instance_fingerprint": snapshot.instance_fingerprint,
        }
        # These are independent new reset proofs. Never relabel predecessor
        # production-recovery evidence as a successful new activation.
        evidence["disposable_test_reset"] = {
            "contract": CONTRACT,
            "dataset_id": manifest["reset_id"],
            "manifest_sha256": manifest_digest(manifest),
            "release_commit": commit,
            "original_state_retained": True,
            "original_journal_retained_unchanged": True,
            "diagnostic_archive_authenticated": True,
            "fresh_dataset_restore_rehearsed": True,
            "fresh_rehearsal_sha256": sha256_file(receipt_path),
            "automatic_state_reversal": True,
            "production_rollback_proven": False,
            "tested_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write(evidence_path, json.dumps(evidence, indent=2).encode())
        services.start()
        result = upgrades._post_switch_doctor(release)
        if result.get("status") != "healthy":
            raise TestResetError("reset candidate failed full settled Doctor")
        record = _json(operation.record_path)
        record.update(
            {
                "phase": "accepted",
                "commit": commit,
                "dataset_id": manifest["reset_id"],
                "doctor_status": "healthy",
                "original_health_proven": False,
            }
        )
        operation._record(record)
        clear_active_reset()
        return record
    except BaseException:
        if operation.record_path.exists():
            restore_original()
        raise
    finally:
        if activity_descriptor is not None:
            try:
                fcntl.flock(activity_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(activity_descriptor)


def _accepted_dataset(
    paths: DeploymentPaths,
    commit: str,
    *,
    selected_adapters_payload: bytes | None = None,
) -> dict[str, Any]:
    """Prove a real accepted dataset before any discovery or selection effect."""
    from adminme.deployment_readiness import ordinary_activation_backup_ready
    from bootstrap.activation_transaction import ActivationJournal
    from bootstrap.candidate_readiness import capture_deployment_snapshot

    marker = _json(paths.instance_dir / "config" / "disposable-test-dataset.json")
    if (
        marker.get("contract") != DATASET_CONTRACT
        or marker.get("acquisition_contract") != "disposable-test-acquisition-v1"
        or re.fullmatch(r"[0-9a-f]{32}", str(marker.get("dataset_id"))) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(marker.get("reset_manifest_sha256")))
        is None
    ):
        raise TestResetError("accepted reset dataset marker is invalid")
    archive = paths.rollback_dir / f"test-reset-{marker['dataset_id']}"
    _directory(archive)
    record = _json(archive / "reset.json")
    manifest = _json(archive / "manifest.json")
    if (
        record.get("contract") != CONTRACT
        or record.get("phase") != "accepted"
        or record.get("commit") != manifest.get("candidate_commit")
        or record.get("doctor_status") != "healthy"
        or record.get("manifest_sha256") != marker["reset_manifest_sha256"]
        or manifest_digest(manifest) != marker["reset_manifest_sha256"]
    ):
        raise TestResetError("dataset has no matching completed reset acceptance")
    _read_admissions(archive, manifest)
    # Restore can legitimately replace the state directory. Acceptance belongs
    # to the logical marker and authenticated activation binding, not an inode.
    _directory(paths.instance_dir)
    evidence = _json(paths.instance_dir / "config" / "deployment-readiness.json")
    binding = evidence.get("deployment_binding") or {}
    snapshot = capture_deployment_snapshot(paths)
    expected = {
        "release_commit": commit,
        "instance_fingerprint": snapshot.instance_fingerprint,
        "host_fingerprint": snapshot.host_fingerprint,
        "configuration_fingerprint": snapshot.configuration_fingerprint,
    }
    if selected_adapters_payload is not None:
        # Selection publishes the new readiness first while services are stopped.
        # Only the exact requested adapter payload may complete that interrupted
        # publication; unrelated configuration drift cannot match either proof.
        selected_snapshot = capture_deployment_snapshot(
            paths, adapters_payload_override=selected_adapters_payload
        )
        if binding.get("configuration_fingerprint") == (
            selected_snapshot.configuration_fingerprint
        ):
            expected["configuration_fingerprint"] = (
                selected_snapshot.configuration_fingerprint
            )
    if snapshot.predecessor_commit != commit or any(
        binding.get(key) != value for key, value in expected.items()
    ):
        raise TestResetError("dataset activation binding is stale or belongs elsewhere")
    reset = evidence.get("disposable_test_reset") or {}
    initial_reset = (
        commit == record["commit"]
        and reset.get("contract") == CONTRACT
        and reset.get("dataset_id") == marker["dataset_id"]
        and reset.get("manifest_sha256") == marker["reset_manifest_sha256"]
        and reset.get("release_commit") == commit
    )
    if not initial_reset:
        pointer = _json(paths.app_dir / "current-release.json")
        previous = pointer.get("previous") or {}
        history = ActivationJournal(paths).committed_activation(
            target_commit=commit, predecessor_commit=previous.get("commit", "")
        )
        activation = evidence.get("activation_evidence") or {}
        if (
            not ordinary_activation_backup_ready(evidence)
            or activation.get("contract") != "deployment-activation-evidence-v1"
            or activation.get("candidate_live_doctor") is not True
            or activation
            != (history.get("active_readiness") or {}).get("activation_evidence")
        ):
            raise TestResetError(
                "dataset has no matching completed activation evidence"
            )
    policy = (
        _json(paths.instance_dir / "config" / "adapters.json").get(
            "disposable_test_acquisition"
        )
        or {}
    )
    if any(
        policy.get(key) != manifest["acquisition"].get(key)
        for key in ("contract", "adapter_id", "member_id", "provider_account_id")
    ):
        raise TestResetError("test dataset acquisition owner changed")
    return evidence


def select_messages(upgrades: Any, selection_path: Path) -> dict[str, Any]:
    """Admit private message IDs while stopped; never acquire or modify Gmail."""

    from adminme.adapters.cursor_store import AdapterCursorStore
    from adminme.adapters.disposable_test_acquisition import (
        MAX_MESSAGES,
        DisposableTestAcquisition,
        restrict_adapter_config,
    )
    from adminme.provider_bindings import adapter_owner_coordinate_id
    from bootstrap.candidate_readiness import capture_deployment_snapshot
    from bootstrap.source_identity import verify_release_source_identity

    with upgrades._operation_lock():
        paths = upgrades.paths
        if (paths.app_dir / "disposable-test-reset-active.json").exists():
            raise TestResetError("reset must finish before selecting messages")
        if upgrades.services.status().status != "stopped":
            raise TestResetError(
                "stop managed services before changing the bounded sample"
            )
        release = upgrades._active_release_root()
        commit = upgrades._active_release_commit()
        if not upgrades.test_mode:
            if release.resolve() != upgrades.root.resolve():
                raise TestResetError(
                    "selection requires the selected immutable release CLI"
                )
            verify_release_source_identity(
                release,
                commit=commit,
                repository=upgrades.root,
                allow_legacy_tree=False,
            )
        config_path = paths.instance_dir / "config" / "adapters.json"
        adapters = _json(config_path)
        original = DisposableTestAcquisition.parse(
            adapters.get("disposable_test_acquisition")
        )
        evidence_path = paths.instance_dir / "config" / "deployment-readiness.json"
        selected = _json(selection_path)
        if set(selected) != {"message_ids"}:
            raise TestResetError("selection file must contain only message_ids")
        policy = original.private_config()
        policy["message_ids"] = selected["message_ids"]
        updated = DisposableTestAcquisition.parse(policy)
        if not set(original.message_ids).issubset(updated.message_ids):
            raise TestResetError(
                "bounded selection may add IDs but cannot recycle sample slots"
            )
        adapters["disposable_test_acquisition"] = updated.private_config()
        adapter_payload = json.dumps(adapters, indent=2).encode()
        evidence = _accepted_dataset(
            paths, commit, selected_adapters_payload=adapter_payload
        )
        effective = restrict_adapter_config(adapters)
        entry = next(
            item for item in effective["adapters"] if item["id"] == updated.adapter_id
        )
        coordinate = adapter_owner_coordinate_id("messaging-gmail-api", entry["config"])
        # Reserve outside replaceable event state before either local cursor or
        # policy publication. Restoring an older snapshot cannot recycle slots.
        lifetime_count = _reserve_admissions(paths, updated.message_ids)
        store = AdapterCursorStore(paths.instance_dir / "events" / "adapter_cursors.db")
        try:
            store.reserve_test_selection(
                coordinate, updated.message_ids, limit=MAX_MESSAGES
            )
        finally:
            store.close()
        snapshot = capture_deployment_snapshot(
            paths, adapters_payload_override=adapter_payload
        )
        evidence["deployment_binding"]["configuration_fingerprint"] = (
            snapshot.configuration_fingerprint
        )
        atomic_write(evidence_path, json.dumps(evidence, indent=2).encode())
        atomic_write(config_path, adapter_payload)
        return {
            "status": "selected",
            "selected_message_count": len(updated.message_ids),
            "lifetime_admitted_count": lifetime_count,
            "distinct_message_cap": MAX_MESSAGES,
            "mailbox_coverage_complete": False,
            "provider_calls": 0,
        }


def discover_messages(upgrades: Any, output: Path) -> dict[str, Any]:
    """Read a recent private sample only after this reset has been accepted."""
    from adminme.adapters.disposable_test_acquisition import (
        discover_test_message_selection,
    )
    from bootstrap.source_identity import verify_release_source_identity

    with upgrades._operation_lock():
        paths = upgrades.paths
        if (paths.app_dir / "disposable-test-reset-active.json").exists():
            raise TestResetError("reset must finish before discovering the sample")
        if upgrades.services.status().status != "stopped":
            raise TestResetError(
                "stop managed services before discovering the bounded sample"
            )
        release = upgrades._active_release_root()
        commit = upgrades._active_release_commit()
        if not upgrades.test_mode:
            if release.resolve() != upgrades.root.resolve():
                raise TestResetError(
                    "discovery requires the selected immutable release CLI"
                )
            verify_release_source_identity(
                release,
                commit=commit,
                repository=upgrades.root,
                allow_legacy_tree=False,
            )
        _accepted_dataset(paths, commit)
        return discover_test_message_selection(
            _json(paths.instance_dir / "config" / "adapters.json"),
            repository_root=release,
            output=output,
        )
