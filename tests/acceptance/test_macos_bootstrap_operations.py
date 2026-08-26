"""Gate 5 credential-free process proof for bootstrap and recovery."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.events.bus import EventBus
from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap import backup_restore as backup_restore_module
from bootstrap.backup_restore import BackupError, BackupManager
from bootstrap.bridge_enrollment import (
    BridgeEnrollmentManager,
    MemberBridgeInstaller,
    MemberBridgePaths,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.doctor import Doctor
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager, ServiceStatus
from bootstrap.upgrade import UpgradeError, UpgradeManager
from bootstrap.wizard import BootstrapWizard

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    predecessor_commit = "1" * 40
    predecessor_release = paths.app_dir / "releases" / predecessor_commit
    predecessor_release.mkdir(parents=True)
    (predecessor_release / ".administrateme-source-commit").write_text(
        predecessor_commit,
        encoding="utf-8",
    )
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
    candidate = tmp_path / "clean-rehearsal-candidate"
    candidate.mkdir()
    (candidate / "candidate.txt").write_text("reviewed candidate\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(candidate)], check=True)
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
    rehearsal = BackupManager(paths, root=REPO_ROOT, test_mode=True).rehearse(
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
    assert live_sentinel.read_text("utf-8") == "unchanged"

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
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    cli_receipt = json.loads(cli_rehearsal.stdout)
    assert cli_receipt["status"] == "healthy"
    assert cli_receipt["isolation"]["staging_destroyed"] is True
    assert cli_receipt["offline_replay_checks"]["activation_eligible"] is False
    receipts = list(paths.backup_dir.glob("backup-rehearsal-*.json"))
    assert len(receipts) == 2
    assert all(receipt.stat().st_mode & 0o077 == 0 for receipt in receipts)
    assert all(
        b"neutral-content-must-not-enter-rehearsal-receipt" not in receipt.read_bytes()
        for receipt in receipts
    )

    tampered_path = paths.backup_dir / "tampered.ambackup"
    tampered_envelope = json.loads(Path(backup["path"]).read_text("utf-8"))
    tampered_ciphertext = bytearray(
        base64.b64decode(tampered_envelope["ciphertext"], validate=True)
    )
    tampered_ciphertext[-1] ^= 0x01
    tampered_envelope["ciphertext"] = base64.b64encode(tampered_ciphertext).decode()
    tampered_path.write_text(
        json.dumps(tampered_envelope, sort_keys=True),
        encoding="utf-8",
    )
    tampered_path.chmod(0o600)
    with pytest.raises(BackupError, match="backup_rehearsal_validation_failed"):
        BackupManager(paths, root=REPO_ROOT, test_mode=True).rehearse(
            tampered_path,
            candidate=candidate,
        )
    instance_fingerprint = hashlib.sha256(str(paths.root).encode()).hexdigest()
    rehearsal_parent = Path(tempfile.gettempdir()) / (
        f"administrateme-backup-rehearsal-{instance_fingerprint[:16]}"
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
            BackupManager(paths, root=REPO_ROOT, test_mode=True).rehearse(
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


def test_reviewed_candidate_controller_completes_guarded_upgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-upgrade")
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
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", commit)
    reconciled: list[tuple[Path, bool]] = []

    def reconcile(_paths, release: Path, *, test_mode: bool = False):
        reconciled.append((release, test_mode))
        return {"ok": True}

    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        reconcile,
    )

    result = UpgradeManager(
        paths,
        root=REPO_ROOT,
        test_mode=True,
    ).activate(REPO_ROOT)

    assert result["status"] == "upgraded"
    assert result["commit"] == commit
    assert result["upgrade_controller_attested"] is True
    pointer = json.loads((paths.app_dir / "current-release.json").read_text("utf-8"))
    assert pointer["commit"] == commit
    assert Path(pointer["path"]).is_dir()
    assert reconciled == [(Path(pointer["path"]), True)]


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
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", commit)
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
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", commit)
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: {"ok": True},
    )
    # Establish an immutable predecessor inside the reviewed releases root.
    UpgradeManager(paths, root=REPO_ROOT, test_mode=True).activate(REPO_ROOT)
    previous = json.loads((paths.app_dir / "current-release.json").read_text("utf-8"))
    ServiceManager(paths, test_mode=True).start()
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
            root=REPO_ROOT,
            test_mode=True,
            approve_source_assessment_repair=True,
        ).activate(REPO_ROOT)

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
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", commit)
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


def test_declared_source_repair_restarts_predecessor_when_both_stop_attempts_fail(
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
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", commit)
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

    with pytest.raises(UpgradeError, match="cleanup stop also failed") as error:
        manager.activate(REPO_ROOT)

    assert "quiescence failed after runtime stopped" in str(error.value)
    assert "previous release restart did not become healthy" in str(error.value)
    assert "status=failed, runtime=failed, openclaw=running" in str(error.value)
    assert stop_attempts == 2
    assert restart_attempts == 1
    assert ServiceManager(paths, test_mode=True).status().status == "healthy"
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
                headers={"Tailscale-User-Login": transfer["tailscale_identity"]},
            )
            assert denied.status_code == 403, denied.text
            assert denied.json()["detail"]["error"] == (
                "provisional_enrollment_contract_denied"
            )
            response = client.post(
                "/api/bridge/ingest",
                json=envelope,
                headers={"Tailscale-User-Login": transfer["tailscale_identity"]},
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
            headers={"Tailscale-User-Login": "principal-a@example.invalid"},
        )
    assert active_response.status_code == 202, active_response.text
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
