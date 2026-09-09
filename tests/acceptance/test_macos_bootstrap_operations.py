"""Gate 5 credential-free process proof for bootstrap and recovery."""

from __future__ import annotations

import fcntl
import copy
import hashlib
import io
import json
import os
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adminme.events.bus import EventBus
from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap import backup_restore as backup_restore_module
from bootstrap import contracts as contracts_module
from bootstrap.backup_restore import BackupError, BackupManager
from bootstrap.bridge_enrollment import (
    BridgeEnrollmentError,
    BridgeEnrollmentManager,
    MemberBridgeInstaller,
    MemberBridgePaths,
)
from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.candidate_readiness import validate_pending_attestation
from bootstrap.doctor import Doctor
from bootstrap.google_oauth_contract import (
    GOOGLE_READONLY_ADAPTER_SCOPES,
    credential_member_digest,
    member_google_secret_name,
)
from bootstrap.provider_binding_transition import (
    prepare_live_provider_binding_transition,
)
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager, ServiceStatus
from bootstrap.source_identity import verify_release_source_identity
from bootstrap.upgrade import UpgradeError, UpgradeManager
from bootstrap.wizard import BootstrapWizard, install_launch_agents

REPO_ROOT = Path(__file__).resolve().parents[2]


def _reviewed_release_runtimes_available() -> bool:
    versions = load_versions(REPO_ROOT)
    python_minor = str(versions["python"]["preferred"]).rsplit(".", 1)[0]
    executables = (
        Path("/opt/homebrew/opt")
        / str(versions["python"]["formula"])
        / "bin"
        / f"python{python_minor}",
        Path("/opt/homebrew/opt") / str(versions["node"]["formula"]) / "bin" / "node",
    )
    return all(path.is_file() and os.access(path, os.X_OK) for path in executables)


def _private_acceptance_python(root: Path) -> Path:
    runtime = root / ".acceptance-runtime"
    bin_dir = runtime / "bin"
    bin_dir.mkdir(mode=0o700, parents=True)
    launcher = bin_dir / "python"
    launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    return launcher


def _extract_git_archive(repository: Path, commit: str, destination: Path) -> None:
    """Independently materialize a private release fixture through git archive."""

    payload = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repository), "archive", commit],
        check=True,
        capture_output=True,
    ).stdout
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        archive.extractall(destination, filter="data")
    destination.chmod(0o700)
    for path in destination.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
        elif path.is_file():
            path.chmod(0o700 if path.stat().st_mode & 0o100 else 0o600)


def _committed_candidate_from_worktree(source: Path, destination: Path) -> str:
    destination.mkdir(mode=0o700)
    inventory = subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=True,
        capture_output=True,
    ).stdout.split(b"\x00")
    for encoded in inventory:
        if not encoded:
            continue
        relative = Path(encoded.decode("utf-8"))
        source_path = source / relative
        if not os.path.lexists(source_path):
            continue
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if source_path.is_symlink():
            destination_path.symlink_to(os.readlink(source_path))
        else:
            shutil.copy2(source_path, destination_path)
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "add", "--all"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=Process Candidate Fixture",
            "-c",
            "user.email=process-candidate-fixture.invalid",
            "commit",
            "-qm",
            "candidate",
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_htr10_fixture(
    paths: DeploymentPaths,
    *,
    candidate: Path,
    now: datetime,
    provider_binding_transition_sha256: str | None = None,
) -> Path:
    """Run the production reducer on content-free fixtures, not human evidence."""

    historical = paths.backup_dir / "fixture-historical-attribution.json"
    source = paths.backup_dir / "fixture-source-assurance.json"
    historical.write_text(
        json.dumps(
            {
                "contract": "historical-attribution-v1",
                "state": "healthy",
                "watermark_event_id": "fixture-watermark",
                "source_event_count": 0,
                "attributed_event_count": 0,
                "unattributed_event_count": 0,
                "groups": [],
                "content_exposed": False,
                "raw_identifiers_exposed": False,
                "provider_binding_transition_sha256": (
                    provider_binding_transition_sha256
                ),
            }
        ),
        encoding="utf-8",
    )
    zero_counts = {
        "provider_called_true": 0,
        "provider_called_false": 0,
        "provider_called_unknown": 0,
        "suppressed_true": 0,
        "suppressed_false_or_unknown": 0,
        "missing_terminal": 0,
        "duplicate_terminal": 0,
        "approval_correlation_missing": 0,
        "duplicate_request": 0,
        "malformed_request": 0,
        "orphan_terminal": 0,
        "invalid_legacy_reconciliation": 0,
        "duplicate_legacy_reconciliation": 0,
    }
    source.write_text(
        json.dumps(
            {
                "contract": "source-assurance-v1",
                "state": "healthy",
                "watermark_event_id": "fixture-watermark",
                "counts": {"missing": 0, "pending": 0, "failed": 0},
                "provider_effects": {
                    "state": "healthy",
                    "counts": zero_counts,
                    "observation_window": {
                        "state": "healthy",
                        "started_at_configured": True,
                        "counts": zero_counts,
                        "content_exposed": False,
                    },
                    "content_exposed": False,
                },
                "content_exposed": False,
                "provider_binding_transition_sha256": (
                    provider_binding_transition_sha256
                ),
            }
        ),
        encoding="utf-8",
    )
    historical.chmod(0o600)
    source.chmod(0o600)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.htr10_review",
            "--instance-root",
            str(paths.root),
            "--historical-attribution",
            str(historical),
            "--source-assurance",
            str(source),
            "--observed-at",
            (now - timedelta(minutes=10)).isoformat(),
            "--confirm-reviewed",
        ],
        cwd=candidate,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("ADMINME_") and key != "PYTHONPATH"
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout)
    return paths.backup_dir / f"htr-10-review-{result['receipt_id']}.json"


def test_isolated_controller_keeps_production_service_paths_in_login_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_home = tmp_path / "account-home"
    private_home = tmp_path / "controller-home"
    account_home.mkdir()
    private_home.mkdir()
    monkeypatch.setenv("HOME", str(private_home))
    monkeypatch.setattr(
        contracts_module.pwd,
        "getpwuid",
        lambda _uid: SimpleNamespace(pw_dir=str(account_home)),
    )

    with pytest.raises(ValueError, match="broad deployment root"):
        DeploymentPaths.from_root(account_home, production=True)

    paths = DeploymentPaths.from_root(tmp_path / "instance", production=True)
    assert paths.launch_agent_dir == account_home / "Library" / "LaunchAgents"
    assert paths.log_dir == account_home / "Library" / "Logs" / "AdministrateMe"
    assert not paths.launch_agent_dir.is_relative_to(private_home)
    paths.create()
    install_launch_agents(paths, REPO_ROOT, test_mode=True)

    assert (paths.launch_agent_dir / "com.administrateme.openclaw.plist").is_file()
    assert (paths.launch_agent_dir / "com.administrateme.runtime.plist").is_file()
    assert not (private_home / "Library" / "LaunchAgents").exists()


def test_member_bridge_helpers_execute_from_the_archived_release() -> None:
    installer = (REPO_ROOT / "bootstrap" / "install-member-bridge.sh").read_text(
        "utf-8"
    )
    helper = (REPO_ROOT / "bootstrap" / "member-bridge.sh").read_text("utf-8")
    assert 'cd "$release_dir"' in installer
    assert 'cd "$release_root"' in helper
    assert ".administrateme-source-commit" in installer
    assert ".administrateme-bridge-installed-commit" in installer
    assert 'poetry_venv="$BRIDGE_ROOT/app/tooling/poetry-2.4.1"' in installer
    assert '"$poetry_venv/bin/poetry" -C bridge sync' in installer


def test_credential_free_bootstrap_doctor_backup_restore_round_trip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    wizard = BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    )
    state = wizard.run()
    assert all(item["status"] == "complete" for item in state["sections"].values())
    ServiceManager(paths, test_mode=True).start()
    before = Doctor(paths, root=REPO_ROOT, test_mode=True).run()
    assert before["status"] == "healthy"
    proof = next(
        check
        for check in before["checks"]
        if check["name"] == "canonical_synthetic_round_trip"
    )["evidence"]
    assert proof["external_side_effects"] == 0
    assert proof["review_suppressed_by_observation"] is True
    plugin_skills = paths.instance_dir / "openclaw" / "runtime" / "plugin-skills"
    plugin_skills.mkdir(parents=True, exist_ok=True)
    (plugin_skills / "generated-skill").symlink_to(REPO_ROOT / "packs" / "skills")
    transient = paths.instance_dir / "events" / "events.db-shm"
    transient.write_bytes(b"transient sqlite state")
    content_canary = paths.instance_dir / "data" / "rehearsal-content-canary.bin"
    content_canary.parent.mkdir(parents=True, exist_ok=True)
    content_canary.write_bytes(b"neutral-content-must-not-enter-rehearsal-receipt")
    original_read_bytes = Path.read_bytes

    def remove_transient_on_read(path: Path):
        if path == transient:
            path.unlink(missing_ok=True)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", remove_transient_on_read)
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    assert backup["encrypted"] is True
    candidate = tmp_path / "clean-rehearsal-candidate"
    candidate.mkdir()
    subprocess.run(["git", "init", "-q", str(candidate)], check=True)
    (candidate / "candidate.txt").write_text("reviewed predecessor\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(candidate), "add", "candidate.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Rehearsal Fixture",
            "-c",
            "user.email=rehearsal-fixture.invalid",
            "commit",
            "-qm",
            "predecessor",
        ],
        check=True,
    )
    predecessor_commit = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    predecessor_release = paths.app_dir / "releases" / predecessor_commit
    predecessor_release.parent.mkdir(parents=True, mode=0o700)
    _extract_git_archive(candidate, predecessor_commit, predecessor_release)
    predecessor_pointer = paths.app_dir / "current-release.json"
    predecessor_pointer.write_text(
        json.dumps(
            {
                "commit": predecessor_commit,
                "path": str(predecessor_release),
            }
        ),
        encoding="utf-8",
    )
    predecessor_pointer.chmod(0o600)
    (candidate / "candidate-next.txt").write_text(
        "reviewed candidate\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(candidate), "add", "candidate-next.txt"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Rehearsal Fixture",
            "-c",
            "user.email=rehearsal-fixture.invalid",
            "commit",
            "-qm",
            "candidate",
        ],
        check=True,
    )
    live_sentinel = paths.instance_dir / "rehearsal-live-sentinel"
    live_sentinel.write_text("unchanged", encoding="utf-8")

    def _service_manager_must_not_be_constructed(*args, **kwargs):
        del args, kwargs
        raise AssertionError("rehearsal constructed ServiceManager")

    monkeypatch.setattr(
        "bootstrap.backup_restore.ServiceManager",
        _service_manager_must_not_be_constructed,
    )
    rehearsal = BackupManager(paths, root=candidate, test_mode=True).rehearse(
        Path(backup["path"]),
        candidate=candidate,
    )
    assert rehearsal["status"] == "healthy"
    assert rehearsal["artifact"]["authenticated"] is True
    assert rehearsal["replay"]["projections_rebuilt"] == 15
    assert rehearsal["workbooks"]["rebuilt_count"] == 2
    assert rehearsal["workbooks"]["bidirectional_sidecar_count"] == 8
    assert rehearsal["workbooks"]["readonly_sidecar_count"] == 9
    assert rehearsal["offline_replay_checks"]["live_checks"] == "not_evaluated"
    assert rehearsal["offline_replay_checks"]["activation_eligible"] is False
    assert rehearsal["offline_replay_checks"]["event_sink_mode"] == "rejecting"
    assert rehearsal["offline_replay_checks"]["external_effects"] == "not_evaluated"
    assert rehearsal["isolation"]["active_state_replaced"] is False
    assert rehearsal["isolation"]["service_manager_constructed"] is False
    assert rehearsal["isolation"]["staging_destroyed"] is True
    assert rehearsal["isolation"]["plaintext_stage_destroyed"] is True
    assert rehearsal["source_events_appended"] == 0
    assert rehearsal["content_emitted_in_receipt"] is False
    assert rehearsal["candidate"]["controller_coordinate_matches"] is False
    assert rehearsal["candidate"]["execution_immutability"] == "not_evaluated"
    assert rehearsal["predecessor"]["source_identity_method"] == ("legacy-git-tree-v1")
    assert rehearsal["predecessor"]["source_marker_matches"] is False
    assert rehearsal["predecessor"]["tracked_source_tree_matches"] is True
    assert rehearsal["predecessor"]["immutable_release_valid"] == "not_evaluated"
    assert rehearsal["predecessor"]["rollback_compatible"] == "not_evaluated"
    assert live_sentinel.read_text("utf-8") == "unchanged"

    cli_environment = os.environ.copy()
    cli_environment["ADMINME_REPOSITORY_ROOT"] = str(candidate)
    cli_rehearsal = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "rehearse-restore",
            str(backup["path"]),
            str(candidate),
        ],
        cwd=REPO_ROOT,
        env=cli_environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    cli_receipt = json.loads(cli_rehearsal.stdout)
    assert cli_receipt["status"] == "healthy"
    assert cli_receipt["predecessor"]["source_identity_method"] == (
        "legacy-git-tree-v1"
    )
    assert cli_receipt["predecessor"]["tracked_source_tree_matches"] is True
    assert cli_receipt["isolation"]["staging_destroyed"] is True
    assert cli_receipt["offline_replay_checks"]["activation_eligible"] is False
    receipts = list(paths.backup_dir.glob("backup-rehearsal-*.json"))
    assert len(receipts) == 2
    assert all(receipt.stat().st_mode & 0o077 == 0 for receipt in receipts)
    assert all(
        b"neutral-content-must-not-enter-rehearsal-receipt" not in receipt.read_bytes()
        for receipt in receipts
    )

    tracked_predecessor = predecessor_release / "candidate.txt"
    tracked_predecessor.write_text("source drift\n", encoding="utf-8")
    with pytest.raises(BackupError, match="predecessor source identity"):
        BackupManager(paths, root=candidate, test_mode=True).rehearse(
            Path(backup["path"]),
            candidate=candidate,
        )
    tracked_predecessor.write_text("reviewed predecessor\n", encoding="utf-8")
    assert list(paths.backup_dir.glob("backup-rehearsal-*.json")) == receipts
    instance_fingerprint = hashlib.sha256(str(paths.root).encode()).hexdigest()
    rehearsal_parent = Path(tempfile.gettempdir()) / (
        f"administrateme-backup-rehearsal-{instance_fingerprint[:16]}"
    )
    assert not list(rehearsal_parent.glob("run-*"))

    tampered_path = paths.backup_dir / "tampered.ambackup"
    tampered_envelope = bytearray(Path(backup["path"]).read_bytes())
    tampered_envelope[-17] ^= 0x01
    tampered_path.write_bytes(tampered_envelope)
    tampered_path.chmod(0o600)
    with pytest.raises(BackupError, match="backup_rehearsal_validation_failed"):
        BackupManager(paths, root=candidate, test_mode=True).rehearse(
            tampered_path,
            candidate=candidate,
        )
    assert not list(rehearsal_parent.glob("run-*"))
    assert list(paths.backup_dir.glob("backup-rehearsal-*.json")) == receipts

    original_fsync_private_directory = (
        backup_restore_module._fsync_private_directory  # noqa: SLF001
    )

    def _fail_rehearsal_parent_fsync(path: Path, *, label: str) -> None:
        if path == rehearsal_parent:
            raise OSError("simulated rehearsal parent fsync failure")
        original_fsync_private_directory(path, label=label)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            backup_restore_module,
            "_fsync_private_directory",
            _fail_rehearsal_parent_fsync,
        )
        with pytest.raises(OSError, match="rehearsal parent fsync failure"):
            BackupManager(paths, root=candidate, test_mode=True).rehearse(
                Path(backup["path"]),
                candidate=candidate,
            )
    assert not list(rehearsal_parent.glob("run-*"))
    assert list(paths.backup_dir.glob("backup-rehearsal-*.json")) == receipts

    monkeypatch.setattr(
        "bootstrap.backup_restore.ServiceManager",
        ServiceManager,
    )
    bus_closed_before_live_replace = False
    original_bus_stop = EventBus.stop
    original_replace = os.replace

    async def _tracked_bus_stop(bus: EventBus) -> None:
        nonlocal bus_closed_before_live_replace
        await original_bus_stop(bus)
        bus_closed_before_live_replace = bus._closed  # noqa: SLF001

    def _assert_bus_closed_before_live_replace(source, destination) -> None:
        if Path(source) == paths.instance_dir:
            assert bus_closed_before_live_replace
        original_replace(source, destination)

    monkeypatch.setattr(EventBus, "stop", _tracked_bus_stop)
    monkeypatch.setattr(os, "replace", _assert_bus_closed_before_live_replace)
    restored = manager.restore(Path(backup["path"]))
    assert restored["projections_rebuilt"] == 15
    assert restored["workbooks_rebuilt"] is True
    assert Path(restored["rollback"]).is_dir()
    after = Doctor(
        paths,
        root=REPO_ROOT,
        test_mode=True,
    ).run(include_synthetic_proof=False)
    assert after["status"] == "healthy"
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    store = secret_store(paths, test_mode=True)
    backup_bytes = Path(backup["path"]).read_bytes()
    for reference in references.values():
        secret = store.get(reference)
        assert secret not in backup_bytes


@pytest.mark.parametrize("structural_failure", [False, True])
def test_encrypted_restore_settles_late_sources_and_quiesces_failed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    structural_failure: bool,
) -> None:
    """Exercise real archive/rebuild/restore with only late readiness scripted."""
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "restore-settlement-deployment")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    services = ServiceManager(paths, test_mode=True)
    services.start()
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    manager._services = services
    backup = manager.create()
    assert backup["encrypted"] is True
    marker = paths.instance_dir / "post-backup-fixture.txt"
    marker.write_text("retain this only when the restore is reverted", encoding="utf-8")
    # Use the production waiting policy with fixture services and credentials.
    # The supplied Doctor still executes every actual proof in the lab root.
    manager.test_mode = False
    # This case isolates the late-readiness contract. A separate process test
    # below proves installed-release selection and its real Doctor subprocess.
    monkeypatch.setattr(
        manager, "_selected_restore_release", lambda: (REPO_ROOT, lambda: None)
    )
    monkeypatch.setattr(
        backup_restore_module,
        "run_release_doctor",
        lambda actual_paths, release, **kwargs: kwargs["doctor_factory"](
            actual_paths, root=release, test_mode=kwargs["test_mode"]
        ).run(include_synthetic_proof=kwargs["include_synthetic_proof"]),
    )
    doctor_calls: list[bool] = []
    proof_reports: list[dict] = []
    readiness_calls = 0
    sleeps: list[float] = []
    original_diagnostics = services.runtime_diagnostics

    def readiness():
        nonlocal readiness_calls
        readiness_calls += 1
        value = original_diagnostics()
        assert value is not None
        value = copy.deepcopy(value)
        if readiness_calls <= 2:
            capability = value["capabilities"]["policy:source-assurance"]
            capability["state"] = "degraded"
            capability["details"]["state"] = "degraded"
            capability["details"]["counts"]["accounted"] = 0
            capability["details"]["counts"]["pending"] = 1
        elif structural_failure:
            value["capabilities"]["pipeline:fixture"] = {
                "kind": "reactive_pipeline",
                "state": "degraded",
            }
        return value

    class LateSourceDoctor:
        def __init__(self, actual_paths, *, root, test_mode):
            assert actual_paths == paths
            assert root == REPO_ROOT
            assert test_mode is False
            self.doctor = Doctor(actual_paths, root=root, test_mode=True)

        def run(self, *, include_synthetic_proof=True):
            doctor_calls.append(include_synthetic_proof)
            report = self.doctor.run(include_synthetic_proof=include_synthetic_proof)
            assert report["status"] == "healthy", report
            if len(doctor_calls) == 1 or structural_failure:
                failed_name = (
                    "source_assurance_terminal_dispositions"
                    if len(doctor_calls) == 1
                    else "required_reactive_pipeline_delivery"
                )
                report = copy.deepcopy(report)
                report["status"] = "failed"
                for check in report["checks"]:
                    if check["name"] == failed_name:
                        check["ok"] = False
                        break
                else:
                    raise AssertionError(
                        f"missing real Doctor predicate: {failed_name}"
                    )
            proof_reports.append(report)
            return report

    monkeypatch.setattr(services, "runtime_diagnostics", readiness)
    monkeypatch.setattr("bootstrap.backup_restore.Doctor", LateSourceDoctor)
    monkeypatch.setattr(
        "bootstrap.service_settlement.time",
        SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append),
    )
    original_replace = os.replace
    moved_selected_states = 0

    def replace(source, destination):
        nonlocal moved_selected_states
        if Path(source) == paths.instance_dir:
            assert services.status().status == "stopped"
            moved_selected_states += 1
        original_replace(source, destination)

    monkeypatch.setattr("bootstrap.backup_restore.os.replace", replace)
    if structural_failure:
        with pytest.raises(BackupError, match="required_reactive_pipeline_delivery"):
            manager.restore(Path(backup["path"]))
        assert marker.is_file()
        assert doctor_calls == [False, False]
        assert moved_selected_states == 2
        assert len(sleeps) == 2
    else:
        restored = manager.restore(Path(backup["path"]))
        assert restored["status"] == "restored"
        assert restored["projections_rebuilt"] == 15
        assert restored["workbooks_rebuilt"] is True
        assert not marker.exists()
        assert (Path(restored["rollback"]) / marker.name).is_file()
        assert doctor_calls == [False, False, True]
        assert moved_selected_states == 1
        assert len(sleeps) == 3
        proof = next(
            check["evidence"]
            for check in proof_reports[-1]["checks"]
            if check["name"] == "canonical_synthetic_round_trip"
        )
        assert proof["external_side_effects"] == 0
        assert proof["review_suppressed_by_observation"] is True
    assert readiness_calls == 3
    assert services.status().status == "healthy"
    assert services.status().observation_mode is True


def test_newer_restore_controller_proves_selected_predecessor_in_its_real_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact older release owns Doctor even when a newer checkout restores."""
    from bootstrap.source_identity import write_source_marker

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "selected-predecessor-restore")
    predecessor = "890a0b5b5a86650806fcda54ac319f00a0d636cb"
    # Installed release archives have no Git metadata; the reviewed installer
    # supplies this exact source identity to bridge-package preparation.
    monkeypatch.setenv("ADMINME_REVIEWED_COMMIT", predecessor)
    release = paths.app_dir / "releases" / predecessor
    release.parent.mkdir(parents=True, mode=0o700)
    _extract_git_archive(REPO_ROOT, predecessor, release)
    assert (
        "version: 22.0.0"
        in (release / "packs/pipelines/noise_filtering/pipeline.yaml").read_text()
    )
    assert (
        "version: 23.0.0"
        in (REPO_ROOT / "packs/pipelines/noise_filtering/pipeline.yaml").read_text()
    )
    python = release / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, mode=0o700)
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    python.chmod(0o700)
    write_source_marker(release, predecessor)
    pointer = paths.app_dir / "current-release.json"
    pointer.write_text(json.dumps({"commit": predecessor, "path": str(release)}))
    pointer.chmod(0o600)
    pointer_before = pointer.read_bytes()
    # The predecessor must initialize its own runtime and packs. Calling the
    # current Wizard with an older root creates a hybrid composition that no
    # installed release runs, and cannot prove predecessor compatibility.
    predecessor_environment = dict(os.environ)
    predecessor_environment.pop("PYTHONPATH", None)
    predecessor_environment["ADMINME_REPOSITORY_ROOT"] = str(release)
    predecessor_bootstrap = subprocess.run(
        [
            str(python),
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "bootstrap",
            "--dry-run",
            "--fixture",
            "two-adult-one-child",
        ],
        cwd=release,
        env=predecessor_environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert predecessor_bootstrap.returncode == 0, (
        predecessor_bootstrap.stdout,
        predecessor_bootstrap.stderr,
    )
    assert json.loads(predecessor_bootstrap.stdout)["status"] == "healthy"
    services = ServiceManager(paths, test_mode=True)
    services.start()
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    manager._services = services
    backup = manager.create()
    manager.test_mode = False
    # No Doctor/report mock: both invocations below execute bootstrap.cli from
    # the selected archived predecessor using its installed interpreter path.
    original_run = subprocess.run
    doctor_calls = []

    def run(command, **kwargs):
        completed = original_run(command, **kwargs)
        if command[0] == str(python):
            doctor_calls.append((command, kwargs, json.loads(completed.stdout)))
        return completed

    monkeypatch.setattr("bootstrap.service_settlement.subprocess.run", run)
    restored = manager.restore(Path(backup["path"]))
    assert restored["status"] == "restored"
    assert restored["projections_rebuilt"] == 15
    assert len(doctor_calls) == 2
    for command, options, report in doctor_calls:
        assert command[:3] == [str(python), "-m", "bootstrap.cli"]
        assert options["cwd"] == release
        assert options["env"]["ADMINME_REPOSITORY_ROOT"] == str(release)
        assert "PYTHONPATH" not in options["env"]
        assert report["status"] == "healthy", report
    assert doctor_calls[0][0][-1] == "--no-synthetic-proof"
    assert doctor_calls[1][0][-1] == "doctor"
    proof = next(
        row["evidence"]
        for row in doctor_calls[-1][2]["checks"]
        if row["name"] == "canonical_synthetic_round_trip"
    )
    assert proof["external_side_effects"] == 0
    assert proof["review_suppressed_by_observation"] is True
    assert pointer.read_bytes() == pointer_before
    assert services.status().observation_mode is True


def test_candidate_pending_is_nonmutating_until_journaled_cli_consumes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production command composition records only immutable pending evidence."""

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "pending-deployment")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    paths.root.chmod(0o700)
    paths.app_dir.chmod(0o700)
    openclaw_activation = paths.instance_dir / "config" / "openclaw-activation.json"
    openclaw_activation.write_text(
        json.dumps({"configured": True, "fixture_only": True}), encoding="utf-8"
    )
    openclaw_activation.chmod(0o600)
    openclaw_runtime = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    openclaw_runtime.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    openclaw_runtime.write_text(
        json.dumps({"agents": {}, "bindings": []}), encoding="utf-8"
    )
    openclaw_runtime.chmod(0o600)
    mapping = json.loads(
        (paths.instance_dir / "config" / "member-mapping.json").read_text("utf-8")
    )
    principal_ids = [
        str(row["member_id"])
        for row in mapping["members"]
        if row.get("role") == "principal"
    ]
    assert len(principal_ids) == 2
    principal_id, enrollment_principal_id = principal_ids
    adapter_path = paths.instance_dir / "config" / "adapters.json"
    legacy_adapters = json.loads(adapter_path.read_text("utf-8"))
    reference = "keychain://fixture/provider-binding-transition"
    account = "principal-a@invalid.example"
    legacy_adapters["adapters"] = [
        {
            "kind": "messaging-gmail-api",
            "config": {
                "member_id": principal_id,
                "user_email": account,
                "workspace": "personal",
                "oauth_credentials_ref": reference,
                "enable_writes": False,
            },
        },
        {
            "kind": "calendaring-google-calendar",
            "config": {
                "member_id": principal_id,
                "provider_account_id": account,
                "oauth_credentials_ref": reference,
                "calendar_id": "primary",
                "discover_calendars": True,
            },
        },
        {
            "kind": "tasks-google-tasks",
            "config": {
                "member_id": principal_id,
                "user_email": account,
                "oauth_credentials_ref": reference,
                "tasklist_id": "@default",
            },
        },
    ]
    legacy_adapter_bytes = json.dumps(legacy_adapters, indent=2, sort_keys=True).encode(
        "utf-8"
    )
    adapter_path.write_bytes(legacy_adapter_bytes)
    adapter_path.chmod(0o600)
    enrollment_account = "principal-b@invalid.example"
    secret_store(paths, test_mode=True).put(
        member_google_secret_name(enrollment_principal_id),
        json.dumps(
            {
                "client_id": "fixture-client",
                "client_secret": "fixture-secret",
                "owner_member_digest": credential_member_digest(
                    enrollment_principal_id
                ),
                "provider_account_id": enrollment_account,
                "refresh_token": "fixture-refresh",
                "token_uri": "https://oauth2.invalid/token",
                "scopes": sorted(GOOGLE_READONLY_ADAPTER_SCOPES),
            },
            sort_keys=True,
        ).encode("utf-8"),
    )
    transition = prepare_live_provider_binding_transition(
        paths,
        legacy_adapters,
        household_mapping=mapping,
        ownership_reviewed=True,
        source_payload=legacy_adapter_bytes,
        test_mode=True,
    )
    assert transition.public()["contract"] == "provider-binding-config-transition-v2"
    transition_sha256 = hashlib.sha256(
        json.dumps(transition.public(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    candidate = tmp_path / "pending-candidate"
    predecessor_commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    predecessor_release = paths.app_dir / "releases" / predecessor_commit
    predecessor_release.parent.mkdir(parents=True, mode=0o700)
    _extract_git_archive(candidate, predecessor_commit, predecessor_release)
    pointer = paths.app_dir / "current-release.json"
    pointer.write_text(
        json.dumps({"commit": predecessor_commit, "path": str(predecessor_release)}),
        encoding="utf-8",
    )
    pointer.chmod(0o600)
    (candidate / "candidate-pending-boundary.txt").write_text(
        "candidate pending boundary\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(candidate), "add", "candidate-pending-boundary.txt"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Pending Candidate Fixture",
            "-c",
            "user.email=pending-candidate-fixture.invalid",
            "commit",
            "-qm",
            "candidate",
        ],
        check=True,
    )
    candidate_commit = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manager = BackupManager(paths, root=candidate, test_mode=True)
    backup = manager.create()
    receipt_path = paths.backup_dir / "pending-preliminary-rehearsal.json"
    rehearsal_environment = {
        **os.environ,
        "ADMINME_TEST_MODE": "1",
        "ADMINME_REPOSITORY_ROOT": str(candidate),
    }
    rehearsal_process = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "rehearse-restore",
            str(backup["path"]),
            str(candidate),
            "--receipt",
            str(receipt_path),
        ],
        cwd=candidate,
        env=rehearsal_environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        json.loads(rehearsal_process.stdout)["candidate"][
            "controller_coordinate_matches"
        ]
        is True
    )
    now = datetime.now(timezone.utc)
    active_readiness = paths.instance_dir / "config" / "deployment-readiness.json"
    active_readiness.write_text(
        json.dumps(
            {
                "contract": "deployment-readiness-v1",
                "sentinel": True,
                "provider_effect_window": {
                    "started_at": (now - timedelta(days=1)).isoformat()
                },
            }
        ),
        encoding="utf-8",
    )
    active_readiness.chmod(0o600)
    htr10 = _write_htr10_fixture(
        paths,
        candidate=candidate,
        now=now,
        provider_binding_transition_sha256=transition_sha256,
    )
    active_before = (active_readiness.read_bytes(), active_readiness.stat().st_ino)
    pointer_before = pointer.read_bytes()
    services_before = (
        paths.service_state_path.read_bytes()
        if paths.service_state_path.is_file()
        else None
    )
    backup_before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths.backup_dir.iterdir()
        if path.is_file()
    }
    observed_at = (now - timedelta(minutes=20)).isoformat()
    command = [
        sys.executable,
        "-m",
        "bootstrap.candidate_readiness",
        "--instance-root",
        str(paths.root),
        "--candidate",
        str(candidate),
        "--expected-candidate-commit",
        candidate_commit,
        "--preliminary-rehearsal-receipt",
        str(receipt_path),
        "--preliminary-backup-artifact",
        str(backup["path"]),
        "--htr-10-review",
        str(htr10),
        "--approve-provider-binding-migration",
    ]
    for htr in ("01", "02", "03", "04", "06", "07"):
        command.extend(
            [f"--confirm-htr-{htr}", f"--htr-{htr}-observed-at", observed_at]
        )
    command.append("--confirm-htr-10")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ADMINME_") and key != "PYTHONPATH"
    }
    environment["ADMINME_TEST_MODE"] = "1"
    completed = subprocess.run(
        command,
        cwd=candidate,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "candidate_pending_recorded"
    assert result["activation_authorized"] is False
    pending = (
        paths.app_dir
        / "readiness"
        / "candidate-pending"
        / f"{result['attestation_id']}.json"
    )
    stored = validate_pending_attestation(json.loads(pending.read_text("utf-8")))
    assert stored["deployment_binding"]["candidate_commit"] == candidate_commit
    assert stored["activation_boundary"]["candidate_active"] is False
    assert stored["deployment_binding"]["provider_binding_transition"] == (
        transition.public()
    )
    assert adapter_path.read_bytes() == legacy_adapter_bytes
    assert (
        active_readiness.read_bytes(),
        active_readiness.stat().st_ino,
    ) == active_before
    assert pointer.read_bytes() == pointer_before
    assert (
        paths.service_state_path.read_bytes()
        if paths.service_state_path.is_file()
        else None
    ) == services_before
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths.backup_dir.iterdir()
        if path.is_file()
    } == backup_before
    assert not paths.rollback_dir.exists() or not any(paths.rollback_dir.iterdir())

    # The guarded CLI is the only consumer. It creates and rehearses a second,
    # exact fresh artifact before any selector, then durably commits the active
    # pointer/readiness pair and archives the transaction journal.
    activation = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "upgrade",
            str(candidate),
            "--pending-attestation-id",
            result["attestation_id"],
            "--approve-provider-binding-migration",
        ],
        cwd=candidate,
        env={
            **environment,
            "ADMINME_TEST_MODE": "1",
            "ADMINME_REPOSITORY_ROOT": str(candidate),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert activation.returncode == 0, activation.stdout + activation.stderr
    activated = json.loads(activation.stdout)
    assert activated["status"] == "upgraded"
    assert activated["commit"] == candidate_commit
    assert activated["pending_attestation_id"] == result["attestation_id"]
    assert activated["fresh_backup_sha256"]
    assert activated["fresh_rehearsal_receipt_sha256"]
    selected = json.loads(pointer.read_text("utf-8"))
    assert selected["commit"] == candidate_commit
    assert selected["previous"]["commit"] == predecessor_commit
    promoted = json.loads(active_readiness.read_text("utf-8"))
    assert promoted["contract"] == "deployment-readiness-v1"
    assert promoted["deployment_binding"]["release_commit"] == candidate_commit
    assert promoted["activation_evidence"]["candidate_live_doctor"] is True
    assert promoted["activation_evidence"]["post_switch_acceptance"] == (
        "pending_post_switch"
    )
    assert adapter_path.read_bytes() == transition.prepared_bytes
    journal_root = paths.app_dir / "activation"
    assert not (journal_root / "active.json").exists()
    histories = list((journal_root / "history").glob("*.json"))
    assert len(histories) == 1
    assert json.loads(histories[0].read_text("utf-8"))["phase"] == "committed"
    assert len(list(paths.backup_dir.glob("activation-*.ambackup"))) == 1
    assert len(list(paths.backup_dir.glob("activation-rehearsal-*.json"))) == 1
    promoted_bytes = active_readiness.read_bytes()

    interrupted = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "rollback",
        ],
        cwd=candidate,
        env={
            **environment,
            "ADMINME_TEST_MODE": "1",
            "ADMINME_REPOSITORY_ROOT": str(candidate),
            "ADMINME_TEST_ACTIVATION_CRASH_AFTER": "pointer_selected",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert interrupted.returncode == -signal.SIGKILL
    assert (journal_root / "active.json").is_file()
    assert json.loads(pointer.read_text("utf-8"))["commit"] == predecessor_commit
    assert active_readiness.read_bytes() == promoted_bytes
    assert adapter_path.read_bytes() == legacy_adapter_bytes

    recovery = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "rollback",
        ],
        cwd=candidate,
        env={
            **environment,
            "ADMINME_TEST_MODE": "1",
            "ADMINME_REPOSITORY_ROOT": str(candidate),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert recovery.returncode == 0, recovery.stdout + recovery.stderr
    recovered = json.loads(recovery.stdout)
    assert recovered["status"] == "recovered"
    assert json.loads(pointer.read_text("utf-8"))["commit"] == candidate_commit
    assert active_readiness.read_bytes() == promoted_bytes
    assert adapter_path.read_bytes() == transition.prepared_bytes
    assert not (journal_root / "active.json").exists()

    rollback = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "rollback",
        ],
        cwd=candidate,
        env={
            **environment,
            "ADMINME_TEST_MODE": "1",
            "ADMINME_REPOSITORY_ROOT": str(candidate),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    rolled_back = json.loads(rollback.stdout)
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["commit"] == predecessor_commit
    assert pointer.read_bytes() == pointer_before
    assert active_readiness.read_bytes() == active_before[0]
    assert adapter_path.read_bytes() == legacy_adapter_bytes
    histories = list((journal_root / "history").glob("*.json"))
    assert len(histories) == 3
    assert {json.loads(path.read_text("utf-8"))["operation"] for path in histories} == {
        "activate",
        "rollback",
    }


def test_reviewed_candidate_controller_completes_guarded_upgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    candidate = tmp_path / "candidate-in-process"
    commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-upgrade")
    BootstrapWizard(
        paths,
        root=candidate,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    reconciled: list[tuple[Path, bool]] = []

    def reconcile(_paths, release: Path, *, test_mode: bool = False):
        reconciled.append((release, test_mode))
        return {"ok": True}

    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        reconcile,
    )
    monkeypatch.setattr(
        UpgradeManager,
        "_attest_controller_provenance",
        lambda self, candidate, commit: None,
    )

    result = UpgradeManager(
        paths,
        root=candidate,
        test_mode=True,
    ).activate(candidate)

    assert result["status"] == "upgraded"
    assert result["commit"] == commit
    assert result["upgrade_controller_attested"] is True
    pointer = json.loads((paths.app_dir / "current-release.json").read_text("utf-8"))
    assert pointer["commit"] == commit
    release = Path(pointer["path"])
    assert release.is_dir()
    marker = release / ".administrateme-source-commit"
    assert marker.read_text("ascii").strip() == commit
    assert marker.stat().st_mode & 0o077 == 0
    assert reconciled == [(Path(pointer["path"]), True)]


def test_cli_upgrade_publishes_fixed_source_before_backup_and_pointer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    candidate = tmp_path / "candidate"
    commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-cli-upgrade")
    BootstrapWizard(
        paths,
        root=candidate,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir(mode=0o700)
    subprocess.run(["git", "init", "-q", str(unrelated)], check=True)
    controller = tmp_path / "fixed-controller"
    _extract_git_archive(candidate, commit, controller)
    controller_marker = controller / ".administrateme-source-commit"
    controller_marker.write_text(f"{commit}\n", encoding="ascii")
    controller_marker.chmod(0o600)
    environment = os.environ.copy()
    environment.update(
        {
            "ADMINME_TEST_MODE": "1",
            "ADMINME_REPOSITORY_ROOT": str(candidate),
            "ADMINME_UPGRADE_CONTROLLER_COMMIT": commit,
            "ADMINME_UPGRADE_CONTROLLER_ROOT": str(controller),
            "PYTHONPATH": f"{controller / 'bridge'}:{controller}",
            "GIT_DIR": str(unrelated / ".git"),
            "GIT_WORK_TREE": str(unrelated),
            "GIT_COMMON_DIR": str(unrelated / ".git"),
            "GIT_OBJECT_DIRECTORY": str(unrelated / ".git" / "objects"),
            "GIT_INDEX_FILE": str(unrelated / ".git" / "index"),
        }
    )
    backups_before = set(paths.backup_dir.glob("*.ambackup"))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "upgrade",
            str(candidate),
        ],
        cwd=controller,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["status"] == "upgraded"
    assert result["commit"] == commit
    assert result["upgrade_controller_attested"] is True
    backups_after = set(paths.backup_dir.glob("*.ambackup"))
    assert len(backups_after - backups_before) == 1
    pointer_path = paths.app_dir / "current-release.json"
    pointer = json.loads(pointer_path.read_text("utf-8"))
    release = paths.app_dir / "releases" / commit
    assert pointer["path"] == str(release)
    assert pointer["commit"] == commit
    marker = release / ".administrateme-source-commit"
    assert marker.read_bytes() == f"{commit}\n".encode("ascii")
    assert marker.stat().st_mode & 0o777 == 0o600
    assert (
        verify_release_source_identity(
            release,
            repository=candidate,
            commit=commit,
            allow_legacy_tree=False,
            require_tracked_tree=True,
            retained_by_commit=commit,
        )["tracked_source_tree_matches"]
        is True
    )
    assert not list((paths.app_dir / "releases").glob(".staging-*"))

    unsupported = candidate / "unsupported-link"
    unsupported.symlink_to("poetry.lock")
    subprocess.run(["git", "-C", str(candidate), "add", "unsupported-link"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Process Candidate Fixture",
            "-c",
            "user.email=process-candidate-fixture.invalid",
            "commit",
            "-qm",
            "unsupported candidate",
        ],
        check=True,
    )
    unsupported_commit = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    unsupported_controller = tmp_path / "fixed-controller-unsupported"
    _extract_git_archive(candidate, unsupported_commit, unsupported_controller)
    unsupported_controller_marker = (
        unsupported_controller / ".administrateme-source-commit"
    )
    unsupported_controller_marker.write_text(
        f"{unsupported_commit}\n", encoding="ascii"
    )
    unsupported_controller_marker.chmod(0o600)
    environment["ADMINME_UPGRADE_CONTROLLER_COMMIT"] = unsupported_commit
    environment["ADMINME_UPGRADE_CONTROLLER_ROOT"] = str(unsupported_controller)
    environment["PYTHONPATH"] = (
        f"{unsupported_controller / 'bridge'}:{unsupported_controller}"
    )
    backups_before_failure = set(paths.backup_dir.glob("*.ambackup"))
    pointer_before_failure = pointer_path.read_bytes()
    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cli",
            "--instance-root",
            str(paths.root),
            "upgrade",
            str(candidate),
        ],
        cwd=unsupported_controller,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert failed.returncode == 1
    failure = json.loads(failed.stdout)
    assert failure["status"] == "failed"
    assert set(paths.backup_dir.glob("*.ambackup")) == backups_before_failure
    assert pointer_path.read_bytes() == pointer_before_failure
    assert not (paths.app_dir / "releases" / unsupported_commit).exists()
    assert not list((paths.app_dir / "releases").glob(".staging-*"))


@pytest.mark.skipif(
    sys.platform != "darwin"
    or not Path("/opt/homebrew/bin/brew").is_file()
    or not _reviewed_release_runtimes_available(),
    reason=(
        "the supported updater process proof requires the reviewed side-by-side "
        "macOS Python and Node toolchain"
    ),
)
def test_supported_update_launcher_holds_fixed_controller_lock_through_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    candidate = tmp_path / "candidate-supported-update"
    predecessor_commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-supported-update")
    BootstrapWizard(
        paths,
        root=candidate,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    releases = paths.app_dir / "releases"
    releases.mkdir(mode=0o700)
    predecessor = releases / predecessor_commit
    _extract_git_archive(candidate, predecessor_commit, predecessor)
    marker = predecessor / ".administrateme-source-commit"
    marker.write_text(f"{predecessor_commit}\n", encoding="ascii")
    marker.chmod(0o600)
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", predecessor / ".venv"],
        check=True,
        timeout=60,
    )
    pointer_path = paths.app_dir / "current-release.json"
    pointer_path.write_text(
        json.dumps({"path": str(predecessor), "commit": predecessor_commit}),
        encoding="utf-8",
    )
    pointer_path.chmod(0o600)

    candidate_canary = candidate / "supported-update-candidate.txt"
    candidate_canary.write_text("fixed candidate\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(candidate), "add", candidate_canary.name],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Supported Update Fixture",
            "-c",
            "user.email=supported-update-fixture.invalid",
            "commit",
            "-qm",
            "supported update candidate",
        ],
        check=True,
    )
    candidate_commit = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bash_canary = tmp_path / "ambient-bash-code-ran"
    bash_environment = tmp_path / "ambient-bash-environment"
    bash_environment.write_text(
        f"printf ran > {shlex.quote(str(bash_canary))}\n",
        encoding="utf-8",
    )
    python_canary = tmp_path / "ambient-python-code-ran"
    python_path = tmp_path / "ambient-python-path"
    python_path.mkdir(mode=0o700)
    (python_path / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(python_canary)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "ADMINME_INSTANCE_ROOT": str(paths.root),
            "ADMINME_TEST_MODE": "1",
            "BASH_ENV": str(bash_environment),
            "GIT_DIR": str(tmp_path / "poisoned-git-dir"),
            "GIT_WORK_TREE": str(tmp_path / "poisoned-worktree"),
            "PYTHONPATH": str(python_path),
        }
    )
    command = [
        str(candidate / "bootstrap" / "update.sh"),
        f"--pending-attestation-id={'0' * 32}",
        str(candidate),
    ]
    lock_path = paths.app_dir / ".upgrade-operation.lock"
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(lock_descriptor, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused = subprocess.run(
            command,
            cwd=candidate,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
    assert refused.returncode == 1
    assert "already running" in refused.stderr
    assert not (releases / candidate_commit).exists()
    assert not list(paths.backup_dir.glob("*.ambackup"))
    assert not list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))
    assert not bash_canary.exists()
    assert not python_canary.exists()

    # With the cooperative lock released, the real public shell, bounded
    # launcher, fixed supervisor, guardian, and fixed controller all run. The
    # production controller must reject the deliberately absent exact pending
    # artifact before backup or selector effects; the internal harness below is
    # the separate credential-free legacy-composition proof.
    public_controller_attempt = subprocess.run(
        command,
        cwd=candidate,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    public_output = public_controller_attempt.stdout + public_controller_attempt.stderr
    assert public_controller_attempt.returncode == 1, public_output
    assert "candidate-pending readiness is not eligible" in public_output
    assert json.loads(pointer_path.read_text("utf-8"))["commit"] == predecessor_commit
    assert not list(paths.backup_dir.glob("*.ambackup"))
    assert not list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))
    assert not bash_canary.exists()
    assert not python_canary.exists()

    supervisor_source = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(candidate),
            "show",
            f"{candidate_commit}:bootstrap/update_supervisor.py",
        ],
        check=True,
        capture_output=True,
    ).stdout
    internal_environment = environment.copy()
    internal_environment["ADMINME_ACCEPTANCE_PYTHON"] = str(
        _private_acceptance_python(tmp_path / "supported-update-acceptance")
    )
    completed = subprocess.run(
        [
            "/usr/bin/python3",
            "-I",
            "-",
            str(paths.root),
            str(candidate),
            candidate_commit,
            "--test-mode",
            str(candidate),
        ],
        cwd=candidate,
        env=internal_environment,
        input=supervisor_source.decode("utf-8"),
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    pointer = json.loads(pointer_path.read_text("utf-8"))
    release = releases / candidate_commit
    assert pointer["commit"] == candidate_commit
    assert pointer["path"] == str(release)
    assert release.is_dir() and not release.is_symlink()
    assert (release / candidate_canary.name).read_text("utf-8") == "fixed candidate\n"
    release_marker = release / ".administrateme-source-commit"
    assert release_marker.read_text("ascii").strip() == candidate_commit
    assert stat.S_IMODE(release_marker.stat().st_mode) == 0o600
    assert len(list(paths.backup_dir.glob("*.ambackup"))) == 1
    assert not list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))
    assert not list(releases.glob(".staging-*"))
    assert not bash_canary.exists()
    assert not python_canary.exists()


def test_public_update_refuses_symlinked_instance_before_deployment_writes(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate-symlink-instance"
    _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / "symlink-instance-target")
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    instance_link = tmp_path / "instance-link"
    instance_link.symlink_to(paths.root, target_is_directory=True)
    environment = os.environ.copy()
    environment.update(
        {
            "ADMINME_INSTANCE_ROOT": str(instance_link),
            "ADMINME_TEST_MODE": "1",
        }
    )

    completed = subprocess.run(
        [
            str(candidate / "bootstrap" / "update.sh"),
            f"--pending-attestation-id={'0' * 32}",
            str(candidate),
        ],
        cwd=candidate,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 1
    assert "instance root is unsafe" in completed.stderr
    assert not (paths.app_dir / ".upgrade-operation.lock").exists()
    assert not (paths.app_dir / "tooling").exists()


def test_public_bootstrap_ignores_stale_internal_candidate_coordinates(
    tmp_path: Path,
) -> None:
    candidate_a = tmp_path / "candidate-a"
    candidate_a_commit = _committed_candidate_from_worktree(REPO_ROOT, candidate_a)
    candidate_b = tmp_path / "candidate-b"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", candidate_a, candidate_b],
        check=True,
    )
    stale_supervisor = candidate_b / "bootstrap" / "update_supervisor.py"
    stale_supervisor.write_text(
        "import sys\n"
        "def main():\n"
        "    print('STALE_CANDIDATE_EXECUTED', file=sys.stderr)\n"
        "    return 91\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(candidate_b), "add", "--all"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate_b),
            "-c",
            "user.name=Stale Candidate Fixture",
            "-c",
            "user.email=stale-candidate-fixture.invalid",
            "commit",
            "-qm",
            "stale candidate",
        ],
        check=True,
    )
    paths = DeploymentPaths.from_root(tmp_path / "bootstrap-stale-coordinate")
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    lock_path = paths.app_dir / ".upgrade-operation.lock"
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(lock_descriptor, 0o600)
    environment = os.environ.copy()
    environment.update(
        {
            "ADMINME_SUPERVISED_CANDIDATE_ROOT": str(candidate_b),
            "ADMINME_FIXED_CONTROLLER_ROOT": str(candidate_b),
            "ADMINME_INSTANCE_ROOT": str(paths.root),
            "ADMINME_TEST_MODE": "1",
        }
    )
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = subprocess.run(
            [
                str(candidate_a / "bootstrap" / "bootstrap-macos.sh"),
                "--instance-root",
                str(paths.root),
            ],
            cwd=candidate_a,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)

    assert completed.returncode == 1
    assert "STALE_CANDIDATE_EXECUTED" not in completed.stderr
    assert "already running" in completed.stderr
    assert not (paths.app_dir / "tooling").exists()

    # A wrong reviewed commit is refused by the public launcher before the
    # supervisor can create even the private instance root.
    mismatch_root = tmp_path / "bootstrap-commit-mismatch"
    mismatch_environment = environment.copy()
    mismatch_environment["ADMINME_INSTANCE_ROOT"] = str(mismatch_root)
    mismatch_attempt = subprocess.run(
        [
            str(candidate_a / "bootstrap" / "bootstrap-macos.sh"),
            "--instance-root",
            str(mismatch_root),
            "--expected-commit",
            "f" * 40,
        ],
        cwd=candidate_a,
        env=mismatch_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert mismatch_attempt.returncode == 1
    assert "STALE_CANDIDATE_EXECUTED" not in mismatch_attempt.stderr
    assert "does not match reviewed commit" in mismatch_attempt.stderr
    assert not mismatch_root.exists()

    # With the lock released and the correct reviewed commit, cross the whole
    # public shell -> launcher -> fixed supervisor -> guardian -> fixed
    # controller boundary. The explicit internal test-mode marker is first in
    # the forwarded argument list; the fixed controller then refuses because
    # the public entry did not authorize the dry-run harness.
    controller_environment = environment.copy()
    controller_environment["ADMINME_ACCEPTANCE_PYTHON"] = str(
        _private_acceptance_python(tmp_path / "bootstrap-acceptance")
    )
    controller_attempt = subprocess.run(
        [
            str(candidate_a / "bootstrap" / "bootstrap-macos.sh"),
            "--test-mode",
            "--expected-commit",
            candidate_a_commit,
        ],
        cwd=candidate_a,
        env=controller_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert controller_attempt.returncode == 1
    assert "STALE_CANDIDATE_EXECUTED" not in controller_attempt.stderr
    assert "fixed bootstrap test mode requires the explicit dry-run harness" in (
        controller_attempt.stderr
    )
    assert not (paths.app_dir / "current-release.json").exists()
    assert not (paths.app_dir / "releases").exists()
    assert not paths.backup_dir.exists()
    assert not list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("poetry") is None
    or not _reviewed_release_runtimes_available(),
    reason=(
        "stable-coordinate dependency preparation requires the reviewed side-by-side "
        "macOS Python, Node, and Poetry toolchain"
    ),
)
def test_macos_non_test_release_preparation_uses_stable_coordinate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate-real-prepare"
    commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-real-prepare")
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    poetry = Path(shutil.which("poetry") or "")
    tooling = paths.app_dir / "tooling" / "poetry-2.4.1" / "bin"
    tooling.mkdir(mode=0o700, parents=True)
    wrapper = tooling / "poetry"
    wrapper.write_text(
        f'#!/bin/sh\nexec {shlex.quote(str(poetry))} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", commit)
    lifecycle = paths.app_dir / ".test-controller-lifecycle.lock"
    lifecycle.write_text("\n", encoding="ascii")
    lifecycle.chmod(0o600)
    lifecycle_metadata = lifecycle.stat()
    operation_lock_path = paths.app_dir / ".upgrade-operation.lock"
    operation_lock_path.write_text("\n", encoding="ascii")
    operation_lock_path.chmod(0o600)
    operation_lock = os.open(operation_lock_path, os.O_RDWR)
    fcntl.flock(operation_lock, fcntl.LOCK_EX)
    monkeypatch.setenv("ADMINME_CONTROLLER_LIFECYCLE_PATH", str(lifecycle))
    monkeypatch.setenv(
        "ADMINME_CONTROLLER_LIFECYCLE_DEVICE",
        str(lifecycle_metadata.st_dev),
    )
    monkeypatch.setenv(
        "ADMINME_CONTROLLER_LIFECYCLE_INODE",
        str(lifecycle_metadata.st_ino),
    )
    monkeypatch.setenv("ADMINME_UPGRADE_OPERATION_LOCK_FD", str(operation_lock))

    try:
        release = UpgradeManager(
            paths,
            root=candidate,
            test_mode=False,
        )._publish_release(candidate, commit)  # noqa: SLF001
    finally:
        fcntl.flock(operation_lock, fcntl.LOCK_UN)
        os.close(operation_lock)

    assert release == paths.app_dir / "releases" / commit
    python = release / ".venv" / "bin" / "python"
    expected_python = json.loads(
        (candidate / "bootstrap" / "versions.json").read_text("utf-8")
    )["python"]["preferred"]
    assert (
        subprocess.run(
            [str(python), "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == f"Python {expected_python}"
    )
    console_scripts = [
        path
        for path in (release / ".venv" / "bin").iterdir()
        if path.is_file() and os.access(path, os.X_OK)
    ]
    assert console_scripts
    for script in console_scripts:
        payload = script.read_bytes()
        assert b".staging-" not in payload
    assert (release / "openclaw" / "administrateme" / "node_modules").is_dir()
    assert (release / "openclaw" / "administrateme" / "dist").is_dir()
    assert (
        verify_release_source_identity(
            release,
            repository=candidate,
            commit=commit,
            allow_legacy_tree=False,
            require_tracked_tree=True,
            retained_by_commit=commit,
        )["tracked_source_tree_matches"]
        is True
    )
    assert not list((paths.app_dir / "releases").glob(".staging-*"))


@pytest.mark.parametrize("operation", ("upgrade", "rollback"))
def test_cli_deployment_operation_lock_rejects_competing_process_before_effects(
    tmp_path: Path,
    operation: str,
) -> None:
    candidate = tmp_path / f"candidate-operation-lock-{operation}"
    commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / f"deployment-lock-{operation}")
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    lock_path = paths.app_dir / ".upgrade-operation.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys; "
                "fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600); "
                "os.fchmod(fd,0o600); fcntl.flock(fd,fcntl.LOCK_EX); "
                "print('ready',flush=True); sys.stdin.buffer.read(1)"
            ),
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    environment = os.environ.copy()
    environment.update(
        {
            "ADMINME_TEST_MODE": "1",
            "ADMINME_REPOSITORY_ROOT": str(candidate),
            "ADMINME_UPGRADE_CONTROLLER_COMMIT": commit,
            "PYTHONPATH": str(candidate / "bridge"),
        }
    )
    command = [
        sys.executable,
        "-m",
        "bootstrap.cli",
        "--instance-root",
        str(paths.root),
        operation,
    ]
    if operation == "upgrade":
        command.append(str(candidate))
    try:
        completed = subprocess.run(
            command,
            cwd=candidate,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        assert holder.stdin is not None
        holder.stdin.write("x")
        holder.stdin.flush()
        holder.wait(timeout=5)

    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert report["status"] == "failed"
    assert "already running" in report["message"]
    assert not list(paths.backup_dir.glob("*.ambackup"))
    assert not (paths.app_dir / "current-release.json").exists()
    assert not (paths.app_dir / "releases" / commit).exists()


def test_declared_source_repair_runs_stopped_and_completes_guarded_upgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-repair-upgrade")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        UpgradeManager, "_candidate_commit", lambda _self, _candidate: commit
    )
    policy = object()
    online_validations: list[object] = []
    offline_states: list[str] = []
    monkeypatch.setattr(
        "bootstrap.upgrade.load_repair_policy", lambda _candidate: policy
    )

    def validate_online(self, selected_policy):
        assert selected_policy is policy
        online_validations.append(selected_policy)
        return {"capabilities": {}, "observation_active": True}

    monkeypatch.setattr(
        UpgradeManager, "_validate_source_repair_online", validate_online
    )

    def activate_repair(selected_paths, **kwargs):
        assert selected_paths == paths
        assert kwargs["policy"] is policy
        assert kwargs["gateway_live_at_quiescence"] is True
        assert kwargs["append_requests"] is True
        offline_states.append(ServiceManager(paths, test_mode=True).status().status)
        return {
            "contract": "source-assessment-degraded-baseline-v2",
            "request_event_ids": ["ev_recovery_request"],
            "requested": 1,
            "stopped_log_snapshot": {
                "minimum_obligations": 1,
                "counts": {
                    "obligations": 1,
                    "accounted": 1,
                    "pending": 0,
                    "missing": 0,
                    "failed": 1,
                },
                "failed_target_count": 1,
                "content_exposed": False,
                "watermark_event_id": "ev_stopped_watermark",
                "consumer_health": {
                    "pipeline_count": 10,
                    "projection_count": 15,
                    "unhealthy_count": 0,
                    "content_exposed": False,
                },
                "gateway_live_at_quiescence": True,
            },
            "content_exposed": False,
            "expires_after_activation": True,
        }

    monkeypatch.setattr(
        "bootstrap.upgrade.offline_validate_repair_baseline", activate_repair
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: {"ok": True},
    )

    manager = UpgradeManager(
        paths,
        root=REPO_ROOT,
        test_mode=True,
        approve_source_assessment_repair=True,
    )
    quiescence_requirements: list[bool] = []
    original_stop = manager.services.stop

    def stop_services(*, require_openclaw_live_before_stop: bool = False):
        quiescence_requirements.append(require_openclaw_live_before_stop)
        return original_stop(
            require_openclaw_live_before_stop=require_openclaw_live_before_stop
        )

    monkeypatch.setattr(manager.services, "stop", stop_services)
    result = manager.activate(REPO_ROOT)

    assert len(online_validations) == 2
    assert offline_states == ["stopped"]
    assert quiescence_requirements == [True]
    assert result["source_assessment_repair"] == {
        "contract": "source-assessment-degraded-baseline-v2",
        "request_event_ids": ["ev_recovery_request"],
        "requested": 1,
        "stopped_log_snapshot": {
            "minimum_obligations": 1,
            "counts": {
                "obligations": 1,
                "accounted": 1,
                "pending": 0,
                "missing": 0,
                "failed": 1,
            },
            "failed_target_count": 1,
            "content_exposed": False,
            "watermark_event_id": "ev_stopped_watermark",
            "consumer_health": {
                "pipeline_count": 10,
                "projection_count": 15,
                "unhealthy_count": 0,
                "content_exposed": False,
            },
            "gateway_live_at_quiescence": True,
        },
        "content_exposed": False,
        "expires_after_activation": True,
    }
    assert ServiceManager(paths, test_mode=True).status().status == "healthy"


def test_declared_source_repair_rolls_back_on_new_post_switch_degradation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-repair-rollback")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        UpgradeManager, "_candidate_commit", lambda _self, _candidate: commit
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: {"ok": True},
    )
    # Establish an immutable predecessor inside the reviewed releases root.
    UpgradeManager(paths, root=REPO_ROOT, test_mode=True).activate(REPO_ROOT)
    previous = json.loads((paths.app_dir / "current-release.json").read_text("utf-8"))
    ServiceManager(paths, test_mode=True).start()
    candidate = tmp_path / "candidate-degraded-successor"
    candidate_commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", candidate_commit)
    monkeypatch.setattr(
        UpgradeManager,
        "_candidate_commit",
        lambda _self, _candidate: candidate_commit,
    )
    monkeypatch.setattr(
        UpgradeManager,
        "_attest_controller_provenance",
        lambda _self, _candidate, _commit: None,
    )
    policy = object()
    monkeypatch.setattr(
        "bootstrap.upgrade.load_repair_policy", lambda _candidate: policy
    )
    monkeypatch.setattr(
        UpgradeManager,
        "_validate_source_repair_online",
        lambda _self, _policy: {
            "capabilities": {},
            "observation_active": True,
        },
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.offline_validate_repair_baseline",
        lambda *_args, **_kwargs: {
            "contract": "source-assessment-degraded-baseline-v2",
            "request_event_ids": ["ev_recovery_request"],
            "requested": 1,
            "content_exposed": False,
            "expires_after_activation": True,
        },
    )
    monkeypatch.setattr(
        UpgradeManager,
        "_post_switch_doctor",
        lambda _self, _release: {
            "status": "failed",
            "checks": [{"name": "unexpected_capability", "ok": False}],
        },
    )

    with pytest.raises(UpgradeError, match="candidate failed post-switch Doctor"):
        UpgradeManager(
            paths,
            root=candidate,
            test_mode=True,
            approve_source_assessment_repair=True,
        ).activate(candidate)

    restored = json.loads((paths.app_dir / "current-release.json").read_text("utf-8"))
    assert restored == previous
    assert ServiceManager(paths, test_mode=True).status().status == "healthy"


def test_declared_source_repair_stops_before_selection_on_stopped_receipt_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-repair-stopped-drift")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        UpgradeManager, "_candidate_commit", lambda _self, _candidate: commit
    )
    policy = object()
    monkeypatch.setattr(
        "bootstrap.upgrade.load_repair_policy", lambda _candidate: policy
    )
    monkeypatch.setattr(
        UpgradeManager,
        "_validate_source_repair_online",
        lambda _self, _policy: {
            "capabilities": {},
            "observation_active": True,
        },
    )
    stopped_states: list[str] = []

    def reject_stopped_receipt(selected_paths, **kwargs):
        assert selected_paths == paths
        assert kwargs["gateway_live_at_quiescence"] is True
        stopped_states.append(ServiceManager(paths, test_mode=True).status().status)
        raise UpgradeError("event log advanced after final live health validation")

    monkeypatch.setattr(
        "bootstrap.upgrade.offline_validate_repair_baseline",
        reject_stopped_receipt,
    )
    reconciled: list[Path] = []
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda _paths, release, **_kwargs: reconciled.append(release),
    )

    with pytest.raises(UpgradeError, match="event log advanced"):
        UpgradeManager(
            paths,
            root=REPO_ROOT,
            test_mode=True,
            approve_source_assessment_repair=True,
        ).activate(REPO_ROOT)

    assert stopped_states == ["stopped"]
    assert reconciled == []
    assert not (paths.app_dir / "current-release.json").exists()
    assert ServiceManager(paths, test_mode=True).status().status == "healthy"


def test_declared_source_repair_aborts_restoration_when_both_stop_attempts_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-repair-stop-failure")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        UpgradeManager, "_candidate_commit", lambda _self, _candidate: commit
    )
    policy = object()
    monkeypatch.setattr(
        "bootstrap.upgrade.load_repair_policy", lambda _candidate: policy
    )
    monkeypatch.setattr(
        UpgradeManager,
        "_validate_source_repair_online",
        lambda _self, _policy: {
            "capabilities": {},
            "observation_active": True,
        },
    )
    manager = UpgradeManager(
        paths,
        root=REPO_ROOT,
        test_mode=True,
        approve_source_assessment_repair=True,
    )
    original_stop = manager.services.stop
    original_start = manager.services.start
    stop_attempts = 0
    restart_attempts = 0

    def fail_stop(*, require_openclaw_live_before_stop: bool = False):
        nonlocal stop_attempts
        stop_attempts += 1
        if stop_attempts == 1:
            original_stop(
                require_openclaw_live_before_stop=require_openclaw_live_before_stop
            )
            raise RuntimeError("fixture quiescence failed after runtime stopped")
        raise RuntimeError("fixture cleanup stop also failed")

    def restart_predecessor():
        nonlocal restart_attempts
        restart_attempts += 1
        original_start()
        return ServiceStatus(
            "failed",
            "failed",
            "running",
            True,
            "fixture blocked readiness",
        )

    monkeypatch.setattr(manager.services, "stop", fail_stop)
    monkeypatch.setattr(manager.services, "start", restart_predecessor)

    with pytest.raises(UpgradeError, match="service quiescence is unproven") as error:
        manager.activate(REPO_ROOT)

    assert "quiescence failed after runtime stopped" in str(error.value)
    assert str(error.value.__cause__) == "fixture cleanup stop also failed"
    assert stop_attempts == 2
    assert restart_attempts == 0
    assert ServiceManager(paths, test_mode=True).status().status == "stopped"
    assert not (paths.app_dir / "current-release.json").exists()


def test_doctor_fails_when_required_triage_pipeline_is_poisoned(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-poisoned")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    checkpoint_path = paths.instance_dir / "events" / "bus_checkpoints.db"
    connection = sqlite3.connect(str(checkpoint_path))
    try:
        connection.execute(
            "UPDATE bus_consumer_offsets SET degraded=1, "
            "consecutive_failures=5, poison_event_id='fixture-poison' "
            "WHERE subscriber_id='pipeline:pipeline:noise_filtering'"
        )
        connection.commit()
    finally:
        connection.close()

    report = Doctor(paths, root=REPO_ROOT, test_mode=True).run(
        include_synthetic_proof=False
    )
    assert report["status"] == "failed"
    check = next(
        item
        for item in report["checks"]
        if item["name"] == "required_reactive_pipeline_delivery"
    )
    assert check["ok"] is False
    assert check["evidence"]["unhealthy"] == ["pipeline:pipeline:noise_filtering"]


def test_member_bridge_export_accept_finalize_uses_production_ingest_composition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "central")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    manager = BridgeEnrollmentManager(paths, root=REPO_ROOT, test_mode=True)
    transfer_path = tmp_path / "principal-a.adminmebridge"
    exported = manager.export(
        "member_fixture_principal_a",
        transfer_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"p" * 32,
    )
    report = json.loads(paths.public_config_path.read_text("utf-8"))
    assert report["bootstrap_complete"] is False
    assert report["bridge_enrollment"]["credential_free_fixture"] is True
    submitted_enrollment: dict[str, object] = {}

    def submitter(transfer: dict, envelope: dict) -> str:
        submitted_enrollment.update(envelope)
        references = json.loads(
            (paths.instance_dir / "config" / "secret-references.json").read_text(
                "utf-8"
            )
        )["references"]
        bridge_records = json.loads(manager.index_path.read_text("utf-8"))
        settings = RuntimeSettings.credential_free(
            paths.instance_dir,
            encryption_key=secret_store(paths, test_mode=True).get(
                references["event_log"]
            ),
            repository_root=REPO_ROOT,
            member_identities={
                "principal-a@example.invalid": "member_fixture_principal_a"
            },
            bridge_enrollments=bridge_records,
            member_roles={"member_fixture_principal_a": "principal"},
        )
        with TestClient(create_application(settings)) as client:
            denied = client.post(
                "/api/bridge/ingest",
                json={
                    **envelope,
                    "source_adapter": "ordinary-bridge-adapter",
                    "idempotency_key": "ordinary-before-finalize",
                },
                headers={
                    "Tailscale-User-Login": transfer["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": transfer["enrollment_id"],
                },
            )
            assert denied.status_code == 403, denied.text
            assert denied.json()["detail"]["error"] == (
                "provisional_enrollment_contract_denied"
            )
            response = client.post(
                "/api/bridge/ingest",
                json=envelope,
                headers={
                    "Tailscale-User-Login": transfer["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": transfer["enrollment_id"],
                },
            )
        assert response.status_code == 202, response.text
        return response.json()["event_id"]

    receipt = MemberBridgeInstaller(
        MemberBridgePaths.from_root(tmp_path / "member"),
        root=REPO_ROOT,
        test_mode=True,
        submitter=submitter,
    ).accept(
        transfer_path,
        exported.transfer_code,
        identity_override="principal-a@example.invalid",
        node_override="node-principal-a",
    )
    assert receipt["status"] == "active"
    finalized = manager.finalize("member_fixture_principal_a")
    assert finalized["status"] == "active"
    assert finalized["bootstrap_material_revoked"] is True
    index = json.loads(manager.index_path.read_text("utf-8"))
    assert index["principal-a@example.invalid"]["status"] == "active"
    active_settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=secret_store(paths, test_mode=True).get(
            json.loads(
                (paths.instance_dir / "config" / "secret-references.json").read_text(
                    "utf-8"
                )
            )["references"]["event_log"]
        ),
        repository_root=REPO_ROOT,
        member_identities={"principal-a@example.invalid": "member_fixture_principal_a"},
        bridge_enrollments=index,
        member_roles={"member_fixture_principal_a": "principal"},
    )
    active_event = {
        **submitted_enrollment,
        "event_at_ms": int(time.time() * 1000),
        "source_adapter": "ordinary-bridge-adapter",
        "source_account_id": "ordinary-bridge-source",
        "idempotency_key": "ordinary-after-finalize",
    }
    with TestClient(create_application(active_settings)) as client:
        active_response = client.post(
            "/api/bridge/ingest",
            json=active_event,
            headers={
                "Tailscale-User-Login": "principal-a@example.invalid",
                "X-AdministrateMe-Bridge-Enrollment": index[
                    "principal-a@example.invalid"
                ]["enrollment_id"],
            },
        )
    assert active_response.status_code == 403, active_response.text
    assert active_response.json()["detail"] == {
        "error": "bridge_pack_contract_denied",
        "violations": ["source_account_id", "source_adapter"],
    }
    assert (
        json.loads(paths.public_config_path.read_text("utf-8"))["bootstrap_complete"]
        is False
    )

    manager.revoke("member_fixture_principal_b")
    manager.revoke("member_fixture_child")
    assert (
        json.loads(paths.public_config_path.read_text("utf-8"))["bootstrap_complete"]
        is True
    )


def test_member_bridge_provider_capability_requires_enable_evidence_and_attestation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Transport enrollment alone never grants Apple provider authority."""

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "central")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    manager = BridgeEnrollmentManager(paths, root=REPO_ROOT, test_mode=True)
    member_id = "member_fixture_principal_a"
    identity = "principal-a@example.invalid"
    pack = "calendaring-apple-calendar-bridge"
    account = "provider-account:" + "a" * 32
    calendar_id = "calendar-private"
    configured = {
        pack: {
            "member_id": member_id,
            "provider_account_id": account,
            "deployment_target": "bridge",
            "workspace": "personal",
            "permission_scope": [
                "eventkit.calendar.read",
                "eventkit.calendar.write",
            ],
            "calendar_identifier": calendar_id,
            "allowed_container_ids": [calendar_id],
            "shared_container_ids": [],
            "binding_provenance": "member-bridge-enrollment",
            "ownership_attested": True,
        }
    }
    manager.configure(
        member_id,
        {"configured_packs": [pack], "pack_config": configured},
    )
    transfer_path = tmp_path / "provider-capability.adminmebridge"
    exported = manager.export(
        member_id,
        transfer_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"q" * 32,
    )
    submitted_enrollment: dict[str, object] = {}

    def settings_for(index: dict[str, dict]) -> RuntimeSettings:
        references = json.loads(
            (paths.instance_dir / "config" / "secret-references.json").read_text(
                "utf-8"
            )
        )["references"]
        return RuntimeSettings.credential_free(
            paths.instance_dir,
            encryption_key=secret_store(paths, test_mode=True).get(
                references["event_log"]
            ),
            repository_root=REPO_ROOT,
            member_identities={identity: member_id},
            bridge_enrollments=index,
            member_roles={member_id: "principal"},
        )

    def submitter(transfer: dict, envelope: dict) -> str:
        submitted_enrollment.update(envelope)
        with TestClient(
            create_application(
                settings_for(json.loads(manager.index_path.read_text("utf-8")))
            )
        ) as client:
            response = client.post(
                "/api/bridge/ingest",
                json=envelope,
                headers={
                    "Tailscale-User-Login": transfer["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": transfer["enrollment_id"],
                },
            )
        assert response.status_code == 202, response.text
        return response.json()["event_id"]

    member_paths = MemberBridgePaths.from_root(tmp_path / "member")
    installer = MemberBridgeInstaller(
        member_paths,
        root=REPO_ROOT,
        test_mode=True,
        submitter=submitter,
    )
    installer.accept(
        transfer_path,
        exported.transfer_code,
        identity_override=identity,
        node_override="node-principal-a",
    )
    manager.finalize(member_id)
    transport_index = json.loads(manager.index_path.read_text("utf-8"))
    assert transport_index[identity]["provider_capability_state"] == "not_attested"
    assert transport_index[identity]["authorized_packs"] == []

    ordinary = {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": submitted_enrollment["tenant_id"],
        "type": "calendar.event_added",
        "schema_version": 2,
        "occurred_at": "2026-08-27T12:00:00+00:00",
        "source_adapter": "calendaring-apple-calendar",
        "source_account_id": account,
        "owner_scope": f"private:{member_id}",
        "visibility_scope": f"private:{member_id}",
        "sensitivity": "normal",
        "payload": {
            "member_id": member_id,
            "provider": "apple_calendar",
            "provider_account_id": account,
            "calendar_external_id": calendar_id,
            "external_event_id": "event-private",
            "summary": "Private fixture",
            "start": "2026-08-27T12:00:00+00:00",
            "end": "2026-08-27T13:00:00+00:00",
            "updated_at": "2026-08-27T11:00:00+00:00",
        },
    }
    headers = {
        "Tailscale-User-Login": identity,
        "X-AdministrateMe-Bridge-Enrollment": exported.public["enrollment_id"],
    }
    with TestClient(create_application(settings_for(transport_index))) as client:
        denied = client.post("/api/bridge/ingest", json=ordinary, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"] == {
        "error": "bridge_pack_contract_denied",
        "violations": ["source_account_id", "source_adapter"],
    }

    enabled = installer.enable_pack(pack)
    assert enabled["central_provider_authority"] is False
    configuration_sha256 = hashlib.sha256(
        json.dumps(configured, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    evidence = {
        **ordinary,
        "type": "adapter.authenticated",
        "idempotency_key": "calendar-capability-evidence",
        "payload": {
            "adapter_name": "calendaring-apple-calendar",
            "adapter_family": "calendaring",
            "token_storage_ref": "keychain://device-permissions/calendar",
            "authenticated_at": "2026-08-27T12:01:00+00:00",
            "scopes": [
                "eventkit.calendar.read",
                "eventkit.calendar.write",
            ],
            "bridge_enrollment_id": exported.public["enrollment_id"],
            "configuration_sha256": configuration_sha256,
            "provider_account_id": account,
        },
    }
    with TestClient(create_application(settings_for(transport_index))) as client:
        evidence_response = client.post(
            "/api/bridge/ingest", json=evidence, headers=headers
        )
    assert evidence_response.status_code == 202, evidence_response.text

    attested = manager.attest(member_id, [pack])
    assert attested["provider_capability_state"] == "attested"
    attested_index = json.loads(manager.index_path.read_text("utf-8"))
    assert attested_index[identity]["authorized_packs"] == [pack]
    with TestClient(create_application(settings_for(attested_index))) as client:
        accepted = client.post("/api/bridge/ingest", json=ordinary, headers=headers)
    assert accepted.status_code == 202, accepted.text

    prior_index = manager.index_path.read_bytes()

    def fail_restart() -> bool:
        raise BridgeEnrollmentError("injected attestation restart failure")

    monkeypatch.setattr(manager, "_restart_if_running", fail_restart)
    with pytest.raises(BridgeEnrollmentError, match="injected attestation"):
        manager.attest(member_id, [pack])
    assert manager.index_path.read_bytes() == prior_index
