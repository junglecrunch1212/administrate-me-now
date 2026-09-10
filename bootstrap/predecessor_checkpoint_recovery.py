"""Restore an authenticated predecessor cursor without replacing event history.

This is a guarded rollback boundary, not an operator-supplied checkpoint. Only
the exact healthy v22 row from the activation's verified backup is eligible.
The immutable current event stream, including its entire newer suffix, remains
in place. Ordinary predecessor settlement and Doctor still decide recovery.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import yaml

from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from bootstrap.activation_transaction import (
    _assert_directory_coordinate,
    _open_private_directory,
    _read_private_at,
    _write_private_at,
    canonical_json,
)
from bootstrap.backup_restore import (
    BackupManager,
    _REHEARSAL_ACTIVITY_LOCK,
    _REHEARSAL_CONTRACT,
    _REHEARSAL_MARKER,
)
from bootstrap.candidate_readiness import (
    DeploymentSnapshot,
    _hash_private_file,
    _read_private_file,
    _validate_rehearsal,
)
from bootstrap.contracts import atomic_write, ensure_within, sha256_file
from bootstrap.deployment_budget import RECOVERY_SECONDS, deployment_clock
from bootstrap.source_contract_recovery import _boot_identity


SUBSCRIBER = "pipeline:pipeline:noise_filtering"
PREDECESSOR_VERSION = "22.0.0"
CURRENT_VERSION = "23.0.0"
CONTRACT = "guarded-predecessor-checkpoint-recovery-v1"
_COLUMNS = (
    "subscriber_id",
    "checkpoint_event_id",
    "last_success_at_ms",
    "last_failure_at_ms",
    "consecutive_failures",
    "degraded",
    "poison_event_id",
    "consumer_version",
)
_MAX_PHASE_SECONDS = 15 * 60


class PredecessorCheckpointRecoveryError(RuntimeError):
    """A bounded content-free refusal; no cursor may be invented."""


def _require(condition: Any, code: str) -> None:
    if not condition:
        raise PredecessorCheckpointRecoveryError(code)


def _remaining(deadline: float, *, reserve: float = 0) -> float:
    now = deployment_clock()
    _require(
        math.isfinite(deadline) and deadline > now + reserve,
        "checkpoint_recovery_deadline_exhausted",
    )
    return deadline - now - reserve


def _read_rows(state: Path) -> dict[str, dict[str, Any]]:
    path = state / "events/bus_checkpoints.db"
    if not path.exists():
        return {}
    coordinate = _state_file_coordinate(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(bus_consumer_offsets)")
        }
        _require(set(_COLUMNS) == columns, "checkpoint_schema_unrecognized")
        result = {
            row["subscriber_id"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM bus_consumer_offsets ORDER BY subscriber_id"
            )
        }
    _require(
        _state_file_coordinate(path) == coordinate,
        "checkpoint_database_changed_while_read",
    )
    return result


def _state_file_coordinate(path: Path) -> tuple[int, int]:
    for parent in (path.parent, path.parent.parent):
        metadata = parent.lstat()
        _require(
            not parent.is_symlink()
            and stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == os.getuid()
            and not metadata.st_mode & 0o022,
            "checkpoint_private_parent_unsafe",
        )
    metadata = path.lstat()
    _require(
        not path.is_symlink()
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
        and not metadata.st_mode & 0o022,
        "checkpoint_private_file_unsafe",
    )
    return metadata.st_dev, metadata.st_ino


def _healthy(row: dict[str, Any]) -> bool:
    return bool(
        row.get("consumer_version") == PREDECESSOR_VERSION
        and row.get("subscriber_id") == SUBSCRIBER
        and isinstance(row.get("checkpoint_event_id"), str)
        and row["checkpoint_event_id"]
        and row.get("degraded") == 0
        and row.get("consecutive_failures") == 0
        and row.get("poison_event_id") is None
    )


async def prove_prefix_and_row(
    staged_state: Path,
    current_state: Path,
    event_key: bytes,
    *,
    deadline: float,
) -> dict[str, Any]:
    """Compare the complete immutable prefix, including foreign earlier IDs."""
    captured = _read_rows(staged_state).get(SUBSCRIBER)
    _require(
        captured is not None and _healthy(captured),
        "captured_predecessor_checkpoint_not_healthy",
    )
    assert captured is not None
    checkpoint = captured["checkpoint_event_id"]
    previous = EventLog(load_instance_config(staged_state), event_key, read_only=True)
    current = EventLog(load_instance_config(current_state), event_key, read_only=True)
    try:
        _require(
            previous.tenant_id == current.tenant_id, "checkpoint_event_tenant_mismatch"
        )
        _require(
            await previous.get(checkpoint) is not None,
            "captured_checkpoint_event_absent",
        )
        _require(
            await current.get(checkpoint) is not None, "current_checkpoint_event_absent"
        )
        left = previous.read_since(through=checkpoint)
        right = current.read_since(through=checkpoint)
        digest = hashlib.sha256()
        count = 0
        while True:
            _remaining(deadline)
            old = await anext(left, None)
            new = await anext(right, None)
            _require(old == new, "checkpoint_event_prefix_changed")
            if old is None:
                break
            digest.update(canonical_json(old))
            digest.update(b"\n")
            count += 1
        _require(count > 0, "checkpoint_event_prefix_empty")
        backup_suffix = previous.read_since(cursor=checkpoint)
        live_suffix = current.read_since(cursor=checkpoint)
        new = await anext(live_suffix, None)
        backup_count = count
        async for old in backup_suffix:
            _remaining(deadline)
            while new is not None and new["event_id"] < old["event_id"]:
                new = await anext(live_suffix, None)
                _remaining(deadline)
            _require(new == old, "backup_event_suffix_missing_or_changed")
            backup_count += 1
            new = await anext(live_suffix, None)
        watermark = await current.latest_event_id()
        return {
            "captured_row": captured,
            "prefix_count": count,
            "prefix_sha256": digest.hexdigest(),
            "backup_event_count": backup_count,
            "current_watermark": watermark,
            "current_event_count": await current.count(),
            "event_log_sha256": sha256_file(staged_state / "events/events.db"),
            "content_exposed": False,
        }
    finally:
        await previous.close()
        await current.close()


def _proof_child(
    staged_state: str,
    key_pipe: Any,
    result_pipe: Any,
    *,
    current_state: str,
    deadline: float,
) -> None:
    try:
        result = asyncio.run(
            prove_prefix_and_row(
                Path(staged_state),
                Path(current_state),
                key_pipe.recv_bytes(),
                deadline=deadline,
            )
        )
        result_pipe.send({"status": "healthy", **result})
    except BaseException:
        result_pipe.send(
            {"status": "failed", "error_code": "checkpoint_prefix_proof_failed"}
        )
    finally:
        key_pipe.close()
        result_pipe.close()


def _snapshot_child(
    staged_state: str,
    key_pipe: Any,
    result_pipe: Any,
    *,
    current_state: str,
    deadline: float,
    expected_row: dict[str, Any],
) -> None:
    async def prove(key: bytes) -> dict[str, Any]:
        _require(
            _read_rows(Path(staged_state)).get(SUBSCRIBER) == expected_row,
            "failed_state_backup_row_changed",
        )
        old = EventLog(load_instance_config(Path(staged_state)), key, read_only=True)
        new = EventLog(load_instance_config(Path(current_state)), key, read_only=True)
        try:
            left, right = old.read_since(), new.read_since()
            count = 0
            while True:
                _remaining(deadline)
                before, after = await anext(left, None), await anext(right, None)
                _require(before == after, "failed_state_backup_history_changed")
                if before is None:
                    break
                count += 1
            return {
                "event_log_sha256": sha256_file(
                    Path(staged_state) / "events/events.db"
                ),
                "event_count": count,
            }
        finally:
            await old.close()
            await new.close()

    try:
        result_pipe.send(
            {"status": "healthy", **asyncio.run(prove(key_pipe.recv_bytes()))}
        )
    except BaseException:
        result_pipe.send(
            {"status": "failed", "error_code": "failed_state_backup_proof_failed"}
        )
    finally:
        key_pipe.close()
        result_pipe.close()


def _supervised_proof(
    backup: BackupManager,
    source: Path,
    artifact_hash: str,
    *,
    deadline: float,
    snapshot_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reuse authenticated extraction and its guardian-owned plaintext lease."""
    fingerprint = hashlib.sha256(str(backup.paths.root).encode()).hexdigest()
    parent = (
        Path(tempfile.gettempdir())
        / f"administrateme-backup-rehearsal-{fingerprint[:16]}"
    )
    _require(not parent.is_symlink(), "checkpoint_rehearsal_parent_unsafe")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = parent.lstat()
    _require(
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and not metadata.st_mode & 0o077,
        "checkpoint_rehearsal_parent_unsafe",
    )
    lock = os.open(
        parent / ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    stage = None
    locked = False
    try:
        lock_metadata = os.fstat(lock)
        _require(
            stat.S_ISREG(lock_metadata.st_mode)
            and lock_metadata.st_uid == os.getuid()
            and not lock_metadata.st_mode & 0o077,
            "checkpoint_rehearsal_lock_unsafe",
        )
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        backup._remove_validated_stale_stages(parent, fingerprint)
        nonce = os.urandom(16).hex()
        stage = parent / f"run-{nonce}"
        pending = parent / f".pending-{nonce}"
        pending.mkdir(mode=0o700)
        root = backup.paths.root.resolve()
        _require(
            stage.resolve() != root and root not in stage.resolve().parents,
            "checkpoint_rehearsal_inside_deployment",
        )
        atomic_write(
            pending / _REHEARSAL_MARKER,
            canonical_json(
                {
                    "contract": _REHEARSAL_CONTRACT,
                    "instance_fingerprint": fingerprint,
                    "nonce": nonce,
                }
            ),
            mode=0o600,
        )
        activity = os.open(
            pending / _REHEARSAL_ACTIVITY_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(activity)
        (pending / "payload").mkdir(mode=0o700)
        os.replace(pending, stage)
        refs = backup._references()
        keys = bytes(backup.store.get(refs["backup"])) + bytes(
            backup.store.get(refs["event_log"])
        )
        available = _remaining(deadline, reserve=60)
        phase = min(_MAX_PHASE_SECONDS, (available - 30) / 2)
        _require(phase > 0, "checkpoint_recovery_deadline_exhausted")
        pipeline, _pid = backup._run_rehearsal_guardian(
            str(source),
            str(stage),
            artifact_hash,
            phase,
            keys,
            timeout_s=phase * 2 + 30,
            replay_target=partial(
                _proof_child,
                current_state=str(backup.paths.instance_dir),
                deadline=deadline,
            )
            if snapshot_row is None
            else partial(
                _snapshot_child,
                current_state=str(backup.paths.instance_dir),
                deadline=deadline,
                expected_row=snapshot_row,
            ),
        )
        _remaining(deadline)
        _require(pipeline.get("status") == "healthy", "checkpoint_prefix_proof_failed")
        backup._remove_validated_stale_stages(parent, fingerprint)
        _require(not stage.exists(), "checkpoint_rehearsal_cleanup_unproven")
        return {
            **pipeline["replay"],
            "authenticated_manifest_sha256": pipeline["extraction"]["manifest_sha256"],
        }
    finally:
        try:
            # Existing cleanup refuses while any guardian/worker still owns its
            # lease. It never destroys payload under a live native process.
            if locked:
                backup._remove_validated_stale_stages(parent, fingerprint)
        finally:
            if locked:
                fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


def _load_record(directory: Path) -> dict[str, Any] | None:
    if not directory.exists():
        return None
    descriptor = _open_private_directory(directory)
    try:
        if not os.path.lexists(directory / "receipt.json"):
            return None
        payload, _metadata = _read_private_at(descriptor, "receipt.json")
        return json.loads(payload)
    finally:
        os.close(descriptor)


def _save_record(directory: Path, record: dict[str, Any]) -> None:
    directory.mkdir(mode=0o700, exist_ok=True)
    descriptor = _open_private_directory(directory)
    try:
        _write_private_at(
            descriptor,
            "receipt.json",
            canonical_json(record),
            replace=os.path.lexists(directory / "receipt.json"),
        )
        _assert_directory_coordinate(descriptor, directory)
    finally:
        os.close(descriptor)


def _log_files(state: Path) -> dict[str, str]:
    """Freeze both encrypted main DB and any durable WAL while quiescent."""
    result = {}
    for name in ("events.db", "events.db-wal"):
        path = state / "events" / name
        if path.exists():
            coordinate = _state_file_coordinate(path)
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                initial = os.fstat(descriptor)
                _require(
                    (initial.st_dev, initial.st_ino) == coordinate,
                    "checkpoint_event_database_changed",
                )
                digest = hashlib.sha256()
                while data := os.read(descriptor, 1024 * 1024):
                    digest.update(data)
                final = os.fstat(descriptor)
                _require(
                    _state_file_coordinate(path) == coordinate
                    and (initial.st_size, initial.st_mtime_ns)
                    == (final.st_size, final.st_mtime_ns),
                    "checkpoint_event_database_changed",
                )
                result[name] = digest.hexdigest()
            finally:
                os.close(descriptor)
    _require("events.db" in result, "checkpoint_event_database_absent")
    return result


def restore_exact_checkpoint_row(
    state: Path, *, captured: dict[str, Any], displaced: dict[str, Any], deadline: float
) -> None:
    """CAS one authenticated row, preserving every other consumer verbatim."""
    _require(
        set(captured) == set(displaced) == set(_COLUMNS) and _healthy(captured),
        "checkpoint_row_shape_invalid",
    )
    before = _read_rows(state)
    _require(before.get(SUBSCRIBER) in (captured, displaced), "checkpoint_row_changed")
    _remaining(deadline)
    path = state / "events/bus_checkpoints.db"
    coordinate = _state_file_coordinate(path)
    with sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT * FROM bus_consumer_offsets WHERE subscriber_id=?", (SUBSCRIBER,)
        ).fetchone()
        _require(
            current is not None and dict(current) in (captured, displaced),
            "checkpoint_row_changed",
        )
        _remaining(deadline)
        columns = _COLUMNS[1:]
        connection.execute(
            "UPDATE bus_consumer_offsets SET "
            + ", ".join(f"{name}=?" for name in columns)
            + " WHERE subscriber_id=?",
            (*[captured[name] for name in columns], SUBSCRIBER),
        )
        other = {
            row["subscriber_id"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM bus_consumer_offsets WHERE subscriber_id != ?",
                (SUBSCRIBER,),
            )
        }
        _require(
            other == {name: row for name, row in before.items() if name != SUBSCRIBER},
            "other_consumer_checkpoint_changed",
        )
        _remaining(deadline)
        _require(
            _state_file_coordinate(path) == coordinate,
            "checkpoint_database_changed_before_commit",
        )
        connection.commit()


def recover_predecessor_checkpoint(
    manager: Any, journal: dict[str, Any]
) -> dict[str, Any] | None:
    """Run only after the updater proves both managed services quiescent."""
    state = manager.paths.instance_dir
    live = _read_rows(state).get(SUBSCRIBER)
    if live is None:
        return None
    predecessor = Path(journal["predecessor_path"])
    manifest = yaml.safe_load(
        (predecessor / "packs/pipelines/noise_filtering/pipeline.yaml").read_text()
    )
    if manifest.get("pack", {}).get("version") != PREDECESSOR_VERSION:
        return None
    directory = (
        manager.paths.rollback_dir / f"checkpoint-recovery-{journal['transaction_id']}"
    )
    record = _load_record(directory)
    if record is None and _healthy(live):
        return None
    _require(
        live.get("consumer_version") in {PREDECESSOR_VERSION, CURRENT_VERSION},
        "checkpoint_consumer_version_unrecognized",
    )
    _require(
        manager.services.status().status == "stopped",
        "checkpoint_recovery_requires_quiescence",
    )
    fresh = journal.get("fresh_backup") or {}
    _require(
        all(
            fresh.get(key)
            for key in (
                "path",
                "artifact_sha256",
                "manifest_sha256",
                "rehearsal_receipt_path",
                "rehearsal_receipt_sha256",
            )
        ),
        "checkpoint_recovery_backup_unavailable",
    )
    backup_path = ensure_within(Path(fresh["path"]), manager.paths.backup_dir)
    rehearsal_path = ensure_within(
        Path(fresh["rehearsal_receipt_path"]), manager.paths.backup_dir
    )
    rehearsal_bytes, _metadata = _read_private_file(rehearsal_path)
    _require(
        hashlib.sha256(rehearsal_bytes).hexdigest()
        == fresh["rehearsal_receipt_sha256"],
        "checkpoint_rehearsal_identity_changed",
    )
    rehearsal = json.loads(rehearsal_bytes)
    snapshot = DeploymentSnapshot(**rehearsal["deployment_binding"])
    _require(
        snapshot.predecessor_commit == journal["predecessor_commit"]
        and snapshot.pointer_sha256 == journal["initial_pointer_sha256"],
        "checkpoint_rehearsal_predecessor_changed",
    )
    _receipt, receipt_hash, artifact_hash, _watermark_hash, _tested_at = (
        _validate_rehearsal(
            manager.paths,
            rehearsal_path,
            backup_path,
            candidate_commit=journal["candidate_commit"],
            snapshot=snapshot,
            now=datetime.now(timezone.utc),
        )
    )
    _require(
        receipt_hash == fresh["rehearsal_receipt_sha256"]
        and artifact_hash == fresh["artifact_sha256"]
        and rehearsal["artifact"]["manifest_sha256"] == fresh["manifest_sha256"],
        "checkpoint_backup_binding_changed",
    )
    deadline = manager._settlement_deadline
    _require(type(deadline) in (int, float), "checkpoint_recovery_deadline_unavailable")
    if record is None:
        started_at = deployment_clock()
        record = {
            "contract": CONTRACT,
            "transaction_id": journal["transaction_id"],
            "predecessor_commit": journal["predecessor_commit"],
            "backup_sha256": artifact_hash,
            "rehearsal_sha256": receipt_hash,
            "deadline": deadline,
            "started_at": started_at,
            "boot_identity": _boot_identity(),
            "phase": "proving",
            "displaced_row": live,
            "content_exposed": False,
        }
        _save_record(directory, record)
    else:
        _require(
            record.get("contract") == CONTRACT
            and record.get("transaction_id") == journal["transaction_id"]
            and record.get("predecessor_commit") == journal["predecessor_commit"]
            and record.get("backup_sha256") == artifact_hash
            and record.get("rehearsal_sha256") == receipt_hash,
            "checkpoint_recovery_receipt_changed",
        )
    now = deployment_clock()
    _require(
        record.get("boot_identity") == _boot_identity()
        and type(record.get("started_at")) in (int, float)
        and type(record.get("deadline")) in (int, float)
        and math.isfinite(record["started_at"])
        and math.isfinite(record["deadline"])
        and 0 <= record["started_at"] <= now
        and 0 < record["deadline"] - record["started_at"] <= RECOVERY_SECONDS,
        "checkpoint_recovery_clock_invalid",
    )
    deadline = min(deadline, record["deadline"])
    _remaining(deadline, reserve=60)
    if record.get("phase") == "applied":
        _require(
            _healthy(live)
            and live["checkpoint_event_id"]
            >= record["proof"]["captured_row"]["checkpoint_event_id"],
            "restored_predecessor_checkpoint_degraded",
        )
        return {"status": "already_restored", "content_exposed": False}
    backup = BackupManager(
        manager.paths, root=manager.root, test_mode=manager.test_mode
    )
    backup._services = manager.services
    frozen_log = _log_files(state)
    proof = _supervised_proof(backup, backup_path, artifact_hash, deadline=deadline)
    captured = proof["captured_row"]
    displaced = record["displaced_row"]
    if displaced.get("consumer_version") == PREDECESSOR_VERSION:
        _require(
            displaced.get("degraded") == 1
            and displaced.get("consecutive_failures") == 5
            and isinstance(displaced.get("poison_event_id"), str)
            and displaced["poison_event_id"] <= captured["checkpoint_event_id"],
            "poison_outside_proven_predecessor_prefix",
        )
    _require(
        _log_files(state) == frozen_log, "checkpoint_event_log_changed_during_proof"
    )
    failed_backup = record.get("failed_state_backup")
    if failed_backup is None:
        _remaining(deadline, reserve=60)
        destination = (
            manager.paths.backup_dir
            / f"checkpoint-failed-state-{journal['transaction_id']}.ambackup"
        )
        intended = record.get("failed_backup_intent")
        intent = {"path": str(destination), "event_files": frozen_log}
        if intended is None:
            _require(not destination.exists(), "checkpoint_failed_state_backup_unbound")
            record = {**record, "failed_backup_intent": intent}
            _save_record(directory, record)
        else:
            _require(intended == intent, "checkpoint_failed_backup_intent_changed")
        if not destination.exists():
            backup.create(destination)
        _remaining(deadline, reserve=30)
        artifact = _hash_private_file(destination, maximum_size=8 * 1024**3)
        snapshot_proof = _supervised_proof(
            backup, destination, artifact, deadline=deadline, snapshot_row=displaced
        )
        failed_backup = {
            "path": str(destination),
            "artifact_sha256": artifact,
            "manifest_sha256": snapshot_proof["authenticated_manifest_sha256"],
        }
    _require(
        _hash_private_file(
            ensure_within(Path(failed_backup["path"]), manager.paths.backup_dir),
            maximum_size=8 * 1024**3,
        )
        == failed_backup["artifact_sha256"],
        "checkpoint_failed_state_backup_changed",
    )
    record = {
        **record,
        "phase": "prepared",
        "proof": proof,
        "failed_state_backup": failed_backup,
        "event_files": frozen_log,
    }
    _save_record(directory, record)
    _require(
        manager.services.status().status == "stopped",
        "checkpoint_recovery_requires_quiescence",
    )
    _require(
        _log_files(state) == frozen_log, "checkpoint_event_log_changed_before_restore"
    )
    restore_exact_checkpoint_row(
        state, captured=captured, displaced=displaced, deadline=deadline
    )
    _require(
        _log_files(state) == frozen_log, "checkpoint_restore_changed_event_history"
    )
    _save_record(directory, {**record, "phase": "applied"})
    return {
        "status": "restored",
        "prefix_event_count": proof["prefix_count"],
        "content_exposed": False,
    }
