"""Guarded cursor CAS, immutable budgets, and publication crash boundaries."""

from __future__ import annotations

import hashlib
import fcntl
import json
import sqlite3
from types import SimpleNamespace

import pytest

import bootstrap.predecessor_checkpoint_recovery as recovery
from bootstrap.contracts import DeploymentPaths


class SyntheticCrash(BaseException):
    pass


@pytest.fixture
def case(tmp_path, monkeypatch):
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    events = paths.instance_dir / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "events.db").write_bytes(b"synthetic encrypted database boundary")
    captured = {
        "subscriber_id": recovery.SUBSCRIBER,
        "checkpoint_event_id": "fixture-checkpoint-captured",
        "last_success_at_ms": 1,
        "last_failure_at_ms": None,
        "consecutive_failures": 0,
        "degraded": 0,
        "poison_event_id": None,
        "consumer_version": "22.0.0",
    }
    displaced = {
        **captured,
        "checkpoint_event_id": "fixture-before",
        "poison_event_id": "fixture-before-poison",
        "degraded": 1,
        "consecutive_failures": 5,
        "last_failure_at_ms": 2,
    }
    other = {
        **captured,
        "subscriber_id": "projection:fixture-other",
        "consumer_version": "1",
    }
    with sqlite3.connect(events / "bus_checkpoints.db") as db:
        db.execute(
            "CREATE TABLE bus_consumer_offsets (subscriber_id TEXT PRIMARY KEY, checkpoint_event_id TEXT, last_success_at_ms INTEGER, last_failure_at_ms INTEGER, consecutive_failures INTEGER, degraded INTEGER, poison_event_id TEXT, consumer_version TEXT)"
        )
        for row in (displaced, other):
            db.execute(
                "INSERT INTO bus_consumer_offsets VALUES(?,?,?,?,?,?,?,?)",
                tuple(row[name] for name in recovery._COLUMNS),
            )
    predecessor = tmp_path / "predecessor"
    manifest = predecessor / "packs/pipelines/noise_filtering/pipeline.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("pack:\n  version: 22.0.0\n")
    artifact = paths.backup_dir / "captured.ambackup"
    artifact.write_bytes(b"synthetic authenticated artifact boundary")
    artifact.chmod(0o600)
    receipt_path = paths.backup_dir / "captured-rehearsal.json"
    binding = {
        "predecessor_commit": "a" * 40,
        "pointer_sha256": "b" * 64,
        "pointer_chain_sha256": "c" * 64,
        "instance_fingerprint": "d" * 64,
        "host_fingerprint": "e" * 64,
        "service_account_fingerprint": "f" * 64,
        "configuration_fingerprint": "1" * 64,
    }
    receipt = {"deployment_binding": binding, "artifact": {"manifest_sha256": "2" * 64}}
    receipt_path.write_text(json.dumps(receipt))
    receipt_path.chmod(0o600)
    artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    journal = {
        "transaction_id": "3" * 32,
        "predecessor_path": str(predecessor),
        "predecessor_commit": "a" * 40,
        "candidate_commit": "4" * 40,
        "initial_pointer_sha256": "b" * 64,
        "fresh_backup": {
            "path": str(artifact),
            "artifact_sha256": artifact_hash,
            "manifest_sha256": "2" * 64,
            "rehearsal_receipt_path": str(receipt_path),
            "rehearsal_receipt_sha256": receipt_hash,
        },
    }
    clock = [100.0]
    boot = ["fixture-boot-a"]
    monkeypatch.setattr(recovery, "deployment_clock", lambda: clock[0])
    monkeypatch.setattr(recovery, "_boot_identity", lambda: boot[0])
    monkeypatch.setattr(
        recovery,
        "_validate_rehearsal",
        lambda *_args, **_kwargs: (
            receipt,
            receipt_hash,
            artifact_hash,
            "watermark",
            None,
        ),
    )
    calls = {"create": 0, "snapshot_proof": 0}

    class Backup:
        def __init__(self, *_args, **_kwargs):
            self.paths = paths

        def create(self, destination):
            calls["create"] += 1
            destination.write_bytes(b"synthetic full failed state")
            destination.chmod(0o600)
            return {"manifest_sha256": "5" * 64}

    monkeypatch.setattr(recovery, "BackupManager", Backup)
    proof = {
        "captured_row": captured,
        "prefix_count": 2,
        "prefix_sha256": "6" * 64,
        "current_watermark": "fixture-tail",
        "current_event_count": 3,
        "content_exposed": False,
    }

    def supervised(_backup, _source, _hash, *, deadline, snapshot_row=None):
        assert deadline <= 600
        if snapshot_row is not None:
            assert snapshot_row == displaced
            calls["snapshot_proof"] += 1
            return {"authenticated_manifest_sha256": "5" * 64}
        return proof

    monkeypatch.setattr(recovery, "_supervised_proof", supervised)
    manager = SimpleNamespace(
        paths=paths,
        root=predecessor,
        test_mode=True,
        _settlement_deadline=600.0,
        services=SimpleNamespace(status=lambda: SimpleNamespace(status="stopped")),
    )
    return SimpleNamespace(
        paths=paths,
        manager=manager,
        journal=journal,
        captured=captured,
        displaced=displaced,
        other=other,
        clock=clock,
        boot=boot,
        calls=calls,
        Backup=Backup,
        proof=proof,
    )


def _record(case):
    directory = (
        case.paths.rollback_dir
        / f"checkpoint-recovery-{case.journal['transaction_id']}"
    )
    return recovery._load_record(directory)


def test_exact_captured_row_restores_once_preserving_other_consumers_and_log(case):
    before = recovery._log_files(case.paths.instance_dir)
    result = recovery.recover_predecessor_checkpoint(case.manager, case.journal)
    assert result["status"] == "restored"
    rows = recovery._read_rows(case.paths.instance_dir)
    assert rows[recovery.SUBSCRIBER] == case.captured
    assert rows["projection:fixture-other"] == case.other
    assert recovery._log_files(case.paths.instance_dir) == before
    assert _record(case)["displaced_row"] == case.displaced
    case.manager._settlement_deadline = 900
    assert (
        recovery.recover_predecessor_checkpoint(case.manager, case.journal)["status"]
        == "already_restored"
    )
    assert _record(case)["deadline"] == 600
    assert case.calls == {"create": 1, "snapshot_proof": 1}


@pytest.mark.parametrize(
    "boundary", ["before_backup", "after_backup", "after_row_commit"]
)
def test_durable_crash_boundaries_resume_without_inventing_checkpoint_or_replacing_backup(
    case, monkeypatch, boundary
):
    original_create = case.Backup.create
    original_save = recovery._save_record
    interrupted = False

    def create(backup, destination):
        nonlocal interrupted
        if not interrupted and boundary == "before_backup":
            interrupted = True
            raise SyntheticCrash
        result = original_create(backup, destination)
        if not interrupted and boundary == "after_backup":
            interrupted = True
            raise SyntheticCrash
        return result

    def save(directory, record):
        nonlocal interrupted
        if (
            not interrupted
            and boundary == "after_row_commit"
            and record["phase"] == "applied"
        ):
            interrupted = True
            raise SyntheticCrash
        return original_save(directory, record)

    monkeypatch.setattr(case.Backup, "create", create)
    monkeypatch.setattr(recovery, "_save_record", save)
    with pytest.raises(SyntheticCrash):
        recovery.recover_predecessor_checkpoint(case.manager, case.journal)
    assert _record(case)["failed_backup_intent"]["event_files"] == recovery._log_files(
        case.paths.instance_dir
    )
    assert (
        recovery.recover_predecessor_checkpoint(case.manager, case.journal)["status"]
        == "restored"
    )
    assert (
        recovery._read_rows(case.paths.instance_dir)[recovery.SUBSCRIBER]
        == case.captured
    )
    assert case.calls["create"] == 1
    assert _record(case)["phase"] == "applied"


@pytest.mark.parametrize(
    "failure",
    ["deadline", "reboot", "prefix_race", "poison_after_checkpoint", "not_quiescent"],
)
def test_failed_prerequisite_preserves_every_checkpoint_row(case, monkeypatch, failure):
    original = recovery._read_rows(case.paths.instance_dir)
    if failure in {"deadline", "reboot"}:
        create = case.Backup.create

        def interrupt(backup, destination):
            create(backup, destination)
            raise SyntheticCrash

        monkeypatch.setattr(case.Backup, "create", interrupt)
        with pytest.raises(SyntheticCrash):
            recovery.recover_predecessor_checkpoint(case.manager, case.journal)
        monkeypatch.setattr(case.Backup, "create", create)
        if failure == "deadline":
            case.clock[0] = 601
            case.manager._settlement_deadline = 2000
        else:
            case.boot[0] = "fixture-boot-b"
    elif failure == "prefix_race":
        original_proof = recovery._supervised_proof

        def changed(*args, **kwargs):
            result = original_proof(*args, **kwargs)
            (case.paths.instance_dir / "events/events.db").write_bytes(
                b"changed event history"
            )
            return result

        monkeypatch.setattr(recovery, "_supervised_proof", changed)
    elif failure == "poison_after_checkpoint":
        case.proof["captured_row"] = {
            **case.captured,
            "checkpoint_event_id": "fixture-a",
        }
    else:
        case.manager.services.status = lambda: SimpleNamespace(status="healthy")
    with pytest.raises(recovery.PredecessorCheckpointRecoveryError):
        recovery.recover_predecessor_checkpoint(case.manager, case.journal)
    assert recovery._read_rows(case.paths.instance_dir) == original


def test_row_restore_rejects_changed_displaced_row_and_expired_deadline(case):
    before = recovery._read_rows(case.paths.instance_dir)
    with pytest.raises(
        recovery.PredecessorCheckpointRecoveryError, match="row_changed"
    ):
        recovery.restore_exact_checkpoint_row(
            case.paths.instance_dir,
            captured=case.captured,
            displaced={**case.displaced, "checkpoint_event_id": "unknown"},
            deadline=600,
        )
    case.clock[0] = 601
    with pytest.raises(recovery.PredecessorCheckpointRecoveryError, match="deadline"):
        recovery.restore_exact_checkpoint_row(
            case.paths.instance_dir,
            captured=case.captured,
            displaced=case.displaced,
            deadline=600,
        )
    assert recovery._read_rows(case.paths.instance_dir) == before


def test_busy_rehearsal_parent_never_cleans_another_controllers_pending_stage(
    monkeypatch, tmp_path
):
    # The broader recovery fixture replaces _supervised_proof. Exercise the real
    # lock boundary here, before any extraction or source-history access.
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    monkeypatch.setattr(recovery.tempfile, "gettempdir", lambda: str(tmp_path))
    fingerprint = hashlib.sha256(str(paths.root).encode()).hexdigest()
    parent = tmp_path / f"administrateme-backup-rehearsal-{fingerprint[:16]}"
    parent.mkdir(mode=0o700)
    pending = parent / (".pending-" + "a" * 32)
    pending.mkdir(mode=0o700)
    calls = []
    backup = SimpleNamespace(
        paths=paths,
        _remove_validated_stale_stages=lambda *_args: calls.append("cleanup"),
    )
    with (parent / ".lock").open("wb") as lease:
        (parent / ".lock").chmod(0o600)
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            recovery._supervised_proof(
                backup,
                paths.backup_dir / "captured.ambackup",
                "a" * 64,
                deadline=600,
            )
    assert calls == []
    assert pending.is_dir()
