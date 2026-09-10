"""Guarded, journal-bound execution of one corrected source assessment contract.

The selected release, Gateway pin, provider configuration and durable consumer
checkpoints remain unchanged. A temporary child has only the production recovery
composition; the ordinary predecessor Doctor alone may finish the open journal.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
import yaml

from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.runtime.source_contract_recovery import (
    _ALLOWED_EVENTS,
    run_source_contract_recovery,
)
from adminme.source_contract_recovery import (
    ASSESSMENT_CONTRACT,
    ContractRecoveryTarget,
    SourceAssessmentContractRecoveryBatch,
    contract_batch_fingerprint,
    contract_recovery_terminal,
    contract_request_key,
    validate_contract_recovery_target,
)
from bootstrap.activation_transaction import (
    _assert_directory_coordinate,
    _open_private_directory,
    _read_private_at,
    _write_private_at,
    canonical_json,
)
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import (
    DeploymentPaths,
    OPENCLAW_LABEL,
    RUNTIME_LABEL,
    load_versions,
)
from bootstrap.deployment_budget import (
    DeploymentBudget,
    CONTROLLER_SECONDS,
    FINALIZATION_RESERVE_SECONDS,
    RECOVERY_SECONDS,
    deployment_clock,
)
from bootstrap.secrets import secret_store
from bootstrap.source_identity import reviewed_candidate_commit


MAINTENANCE_SECONDS = 10 * 60
_CLEANUP_SECONDS = 30
_EFFECT_COUNTS = frozenset(
    {
        "write_requests",
        "terminal_results",
        "provider_called_true",
        "provider_called_false",
        "provider_called_unknown",
        "suppressed_true",
        "suppressed_false_or_unknown",
        "missing_terminal",
        "duplicate_terminal",
        "approval_correlation_missing",
        "duplicate_request",
        "malformed_request",
        "orphan_terminal",
        "legacy_reconciled_terminal",
        "invalid_legacy_reconciliation",
        "duplicate_legacy_reconciliation",
    }
)
_SOURCE_FAILURE_CHECKS = frozenset(
    {
        "source_assurance_terminal_dispositions",
        "deployment_topology_and_recovery_readiness",
        "launchd_and_process_state",
    }
)
_SOURCE_CAPABILITIES = frozenset(
    {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
)


class ContractRecoveryControlError(RuntimeError):
    """A content-free failed prerequisite; never permission to waive health."""


class ContractRecoveryPlan(BaseModel):
    model_config = {"extra": "forbid"}
    contract: Literal["guarded-source-contract-recovery-v1"]
    transaction_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    predecessor_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    required_assessment_contract: Literal["personal-obligation-assessment-v1"]
    target: ContractRecoveryTarget


def _require(value: Any, code: str) -> None:
    if not value:
        raise ContractRecoveryControlError(code)


def expected_doctor_checks(repository: Path) -> frozenset[str]:
    tree = ast.parse((repository / "bootstrap/doctor.py").read_text("utf-8"))
    doctor = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Doctor"
    )
    method = next(
        node
        for node in doctor.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    names = {
        node.args[0].value
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "DoctorCheck"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    _require(
        len(names) == 22 and "canonical_synthetic_round_trip" in names,
        "predecessor_doctor_contract_unrecognized",
    )
    return frozenset(names)


def expected_consumer_capabilities(repository: Path, instance: Path) -> frozenset[str]:
    tree = ast.parse((repository / "bootstrap/contracts.py").read_text("utf-8"))
    projection_names = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PROJECTION_NAMES"
            for target in node.targets
        )
    )
    required = {f"projection:{name}" for name in projection_names}
    for directory in (repository / "packs/pipelines", instance / "packs/pipelines"):
        for manifest in directory.rglob("pipeline.yaml"):
            _require(not manifest.is_symlink(), "consumer_manifest_unsafe")
            document = yaml.safe_load(manifest.read_text("utf-8"))
            if (document.get("triggers") or {}).get("events"):
                required.add(f"pipeline:{document['pack']['id']}")
    _require(
        "pipeline:pipeline:noise_filtering" in required,
        "predecessor_consumer_contract_unrecognized",
    )
    return frozenset(required)


def _boot_identity() -> str:
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        identity = result.stdout.strip()
    else:
        identity = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    _require(bool(identity) and len(identity) <= 100, "boot_identity_unavailable")
    return identity


def validate_live_preflight(
    doctor: dict[str, Any],
    diagnostics: dict[str, Any],
    plan: ContractRecoveryPlan,
    *,
    doctor_checks: frozenset[str],
    required_consumers: frozenset[str],
    publication_only: bool = False,
) -> dict[str, Any]:
    checks = doctor.get("checks")
    _require(isinstance(checks, list) and bool(checks), "preflight_doctor_absent")
    assert isinstance(checks, list)
    _require(
        all(
            isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and type(row.get("ok")) is bool
            for row in checks
        ),
        "preflight_doctor_shape_invalid",
    )
    names = [row["name"] for row in checks]
    _require(
        len(names) == len(set(names))
        and set(names) == doctor_checks
        and doctor.get("schema_version") == 1
        and doctor.get("mode") == "observation",
        "preflight_doctor_incomplete",
    )
    failed = {row["name"] for row in checks if not row["ok"]}
    _require(
        (not failed and doctor.get("status") == "healthy")
        if publication_only
        else "source_assurance_terminal_dispositions" in failed
        and failed <= _SOURCE_FAILURE_CHECKS,
        "preflight_unrelated_doctor_failure",
    )
    _require(
        diagnostics.get("observation_active") is True
        and diagnostics.get("safe_reads") is True,
        "preflight_observation_or_reads_invalid",
    )
    deployment = diagnostics.get("deployment_readiness") or {}
    _require(
        deployment.get("contract") == "deployment-readiness-v1"
        and (
            deployment.get("ready") is True
            and deployment.get("blocking_dimensions") == []
            if publication_only
            else set(deployment.get("blocking_dimensions") or ())
            == {"observation_effects"}
        ),
        "preflight_unrelated_readiness_failure",
    )
    capabilities = diagnostics.get("capabilities") or {}
    _require(
        isinstance(capabilities, dict) and bool(capabilities),
        "preflight_capabilities_absent",
    )
    assert isinstance(capabilities, dict)
    _require(
        required_consumers <= capabilities.keys(),
        "preflight_consumer_inventory_incomplete",
    )
    consumers = 0
    for key, value in capabilities.items():
        _require(isinstance(value, dict), "preflight_capability_invalid")
        # The predecessor's overall_state deliberately excludes this optional
        # chat transport. Full Doctor and deployment topology still adjudicate
        # its required shared Gateway/provider boundaries.
        if key not in _SOURCE_CAPABILITIES and key != "openclaw:member-chat":
            _require(
                value.get("state") not in {"degraded", "blocked"},
                "preflight_unrelated_capability_failure",
            )
        if key.startswith(("pipeline:", "projection:")):
            consumers += 1
            details = value.get("details") or {}
            _require(
                value.get("state") == "healthy"
                and details.get("lag_count") == 0
                and details.get("degraded") is False
                and "poison_event_id" in details
                and details["poison_event_id"] is None
                and type(details.get("consecutive_failures")) is int
                and details["consecutive_failures"] == 0,
                "preflight_consumer_not_settled",
            )
    _require(consumers >= 2, "preflight_consumer_evidence_absent")
    source = (capabilities.get("policy:source-assurance") or {}).get("details") or {}
    counts = source.get("counts") or {}
    freshness = source.get("freshness") or {}
    _require(
        source.get("contract") == "source-assurance-v1"
        and source.get("content_exposed") is False
        and counts.get("pending") == counts.get("missing") == 0
        and counts.get("failed") == (0 if publication_only else 1)
        and counts.get("accounted") == counts.get("obligations")
        and freshness.get("state") == "fresh"
        and freshness.get("revision_current") is True
        and freshness.get("settled_revision") is True
        and freshness.get("pending_recheck_required") is False
        and freshness.get("refresh_error_type") is None,
        "preflight_source_proof_not_settled",
    )
    failures = source.get("failed") or []
    _require(
        not failures
        if publication_only
        else len(failures) == 1
        and failures[0].get("event_id") == plan.target.source_event_id,
        "preflight_failure_set_changed",
    )
    effects = source.get("provider_effects") or {}
    window = effects.get("observation_window") or {}
    _require(
        effects.get("state") == window.get("state") == "healthy",
        "preflight_effect_proof_absent",
    )
    for evidence, must_be_zero in (
        (effects.get("counts"), False),
        (window.get("counts"), True),
    ):
        _require(
            isinstance(evidence, dict)
            and _EFFECT_COUNTS <= evidence.keys()
            and all(
                type(evidence[key]) is int
                and evidence[key] >= 0
                and (not must_be_zero or evidence[key] == 0)
                for key in _EFFECT_COUNTS
            ),
            "preflight_effect_proof_not_zero",
        )
    watermark = source.get("watermark_event_id")
    _require(
        isinstance(watermark, str) and bool(watermark), "preflight_watermark_absent"
    )
    return {
        "watermark_event_id": watermark,
        "counts": counts,
        "failed_source_event_id": None
        if publication_only
        else plan.target.source_event_id,
        "provider_effect_counts": effects["counts"],
        "content_exposed": False,
    }


def _file_hash(path: Path, *, optional: bool = False) -> str | None:
    if not os.path.lexists(path):
        _require(optional, "protected_file_absent")
        return None
    before = path.lstat()
    _require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == os.getuid()
        and not path.is_symlink(),
        "protected_file_unsafe",
    )
    result = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        _require(
            (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino),
            "protected_file_changed",
        )
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    _require(
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        == (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns),
        "protected_file_changed",
    )
    return result.hexdigest()


def protected_snapshot(paths: DeploymentPaths) -> dict[str, Any]:
    files = [
        paths.app_dir / "current-release.json",
        paths.instance_dir / "openclaw/runtime/openclaw.json",
    ]
    files.extend(
        paths.launch_agent_dir / f"{label}.plist"
        for label in (RUNTIME_LABEL, OPENCLAW_LABEL)
    )
    config = paths.instance_dir / "config"
    _require(
        config.is_dir() and not config.is_symlink(),
        "protected_config_directory_invalid",
    )
    for item in sorted(config.rglob("*")):
        _require(not item.is_symlink(), "protected_config_symlink")
        if item.is_file() and item.suffix in {".json", ".yaml", ".yml"}:
            files.append(item)
    selector = paths.root / "bin/openclaw"
    _require(selector.is_symlink(), "protected_gateway_selector_invalid")
    selected = selector.resolve(strict=True)
    return {
        "files": {str(path): _file_hash(path) for path in files},
        "gateway_target": str(selected),
        "gateway_sha256": _file_hash(selected),
    }


def checkpoint_snapshot(paths: DeploymentPaths) -> dict[str, str | None]:
    config = load_instance_config(paths.instance_dir)
    return {
        str(Path(str(path) + suffix)): _file_hash(
            Path(str(path) + suffix), optional=True
        )
        for path in (
            config.bus_checkpoint_path,
            config.projection_checkpoint_path,
            config.adapter_cursor_path,
        )
        for suffix in ("", "-wal", "-shm")
    }


def _batch(record: dict[str, Any]) -> SourceAssessmentContractRecoveryBatch:
    plan = ContractRecoveryPlan.model_validate(record["plan"])
    return SourceAssessmentContractRecoveryBatch(
        request_id=f"guarded-contract:{record['fingerprint']}",
        required_assessment_contract=plan.required_assessment_contract,
        targets=[plan.target],
    )


def _keys(paths: DeploymentPaths, test_mode: bool) -> tuple[bytes, str]:
    references = json.loads(
        (paths.instance_dir / "config/secret-references.json").read_text()
    )["references"]
    store = secret_store(paths, test_mode=test_mode)
    return store.get(references["event_log"]), store.get(
        references["openclaw_shared"]
    ).decode("ascii")


async def validate_stopped_history(
    paths: DeploymentPaths, record: dict[str, Any], *, test_mode: bool
) -> None:
    key, _token = _keys(paths, test_mode)
    log = EventLog(load_instance_config(paths.instance_dir), key, read_only=True)
    try:
        assert isinstance(log.tenant_id, str)
        batch = _batch(record)
        target = batch.targets[0]
        source = await log.get(target.source_event_id)
        _require(
            source is not None and source.get("tenant_id") == log.tenant_id,
            "recovery_source_tenant_changed",
        )
        assert source is not None
        request = await log.get_by_idempotency(
            log.tenant_id,
            contract_request_key(batch.request_id, target.source_event_id),
        )
        if request is None:
            await validate_contract_recovery_target(
                log, target, required_tenant_id=log.tenant_id
            )
        else:
            _require(
                (request.get("payload") or {}).get("batch_fingerprint")
                == contract_batch_fingerprint(batch),
                "recovery_request_changed",
            )
            await contract_recovery_terminal(log, request["event_id"])
        # Every post-preflight append must belong to this one operation. No
        # unrelated ingress or alternate maintenance writer can reuse the proof.
        observed = 0
        preflight = (
            record.get("publication_preflight")
            if record.get("execution_mode") == "publication_only"
            else record["preflight"]
        )
        _require(isinstance(preflight, dict), "recovery_preflight_absent")
        assert isinstance(preflight, dict)
        async for event in log.read_since(preflight["watermark_event_id"]):
            observed += 1
            _require(
                observed <= 1024
                and event.get("type") in _ALLOWED_EVENTS
                and event.get("correlation_id") == batch.request_id
                and all(
                    event.get(field) == source.get(field)
                    for field in (
                        "tenant_id",
                        "owner_scope",
                        "visibility_scope",
                        "sensitivity",
                    )
                ),
                "recovery_history_outside_operation",
            )
    finally:
        await log.close()


def _save(directory: Path, name: str, record: dict[str, Any]) -> None:
    descriptor = _open_private_directory(directory)
    try:
        _write_private_at(
            descriptor,
            name,
            canonical_json(record),
            replace=os.path.lexists(directory / name),
        )
        _assert_directory_coordinate(descriptor, directory)
    finally:
        os.close(descriptor)


def _load(directory: Path, name: str) -> dict[str, Any] | None:
    if not os.path.lexists(directory / name):
        return None
    descriptor = _open_private_directory(directory)
    try:
        payload, _metadata = _read_private_at(descriptor, name)
        return json.loads(payload)
    finally:
        os.close(descriptor)


def _declared_operation(
    manager: Any, journal: dict[str, Any]
) -> tuple[Path, str, dict[str, Any]] | None:
    directory = manager.activation_journal.directory
    name = f"contract-recovery-{journal['transaction_id']}.json"
    record = _load(directory, name)
    if record is None:
        return None
    plan = ContractRecoveryPlan.model_validate(record.get("plan"))
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "controller_commit": record.get("controller_commit"),
                "plan": plan.model_dump(mode="json"),
            }
        )
    ).hexdigest()
    _require(
        record.get("contract") == "guarded-source-contract-operation-v1"
        and record.get("fingerprint") == fingerprint
        and plan.transaction_id == journal["transaction_id"]
        and plan.predecessor_commit == journal["predecessor_commit"],
        "contract_recovery_operation_changed",
    )
    return directory, name, record


async def _operation_terminal(
    paths: DeploymentPaths, record: dict[str, Any], *, test_mode: bool
) -> dict[str, Any] | None:
    key, _token = _keys(paths, test_mode)
    config = load_instance_config(paths.instance_dir)
    log = EventLog(config, key, read_only=True)
    try:
        batch = _batch(record)
        request = await log.get_by_idempotency(
            config.tenant_id,
            contract_request_key(batch.request_id, batch.targets[0].source_event_id),
        )
        if request is None:
            return None
        _require(
            (request.get("payload") or {}).get("batch_fingerprint")
            == contract_batch_fingerprint(batch),
            "recovery_request_changed",
        )
        return await contract_recovery_terminal(log, request["event_id"])
    finally:
        await log.close()


async def _operation_has_no_attempts(
    paths: DeploymentPaths, record: dict[str, Any], *, test_mode: bool
) -> bool:
    """Only an unspent exact command may obtain a fresh live preflight."""
    key, _token = _keys(paths, test_mode)
    config = load_instance_config(paths.instance_dir)
    log = EventLog(config, key, read_only=True)
    try:
        batch = _batch(record)
        target = batch.targets[0]
        request = await log.get_by_idempotency(
            config.tenant_id,
            contract_request_key(batch.request_id, target.source_event_id),
        )
        if request is not None:
            _require(
                (request.get("payload") or {}).get("batch_fingerprint")
                == contract_batch_fingerprint(batch),
                "recovery_request_changed",
            )
            terminal = await contract_recovery_terminal(log, request["event_id"])
            if terminal["status"] != "pending":
                return False
            reservations = await log.get_by_idempotency_keys(
                config.tenant_id,
                [
                    f"source-assessment-contract-attempt:{request['event_id']}:{slot}"
                    for slot in range(3)
                ],
            )
            if reservations:
                return False
        # Re-entry cannot reselect a changed source lineage even before a call.
        await validate_contract_recovery_target(
            log, target, required_tenant_id=config.tenant_id
        )
        return True
    finally:
        await log.close()


def record_contract_cleanup_intent(manager: Any, journal: dict[str, Any]) -> None:
    """Persist before captured recovery changes any selected controls."""
    declared = _declared_operation(manager, journal)
    if declared is None:
        return
    directory, name, record = declared
    record["cleanup_intent"] = True
    _save(directory, name, record)


def record_contract_predecessor_controls(manager: Any, journal: dict[str, Any]) -> None:
    """Record exact restored controls before start, without claiming health."""
    declared = _declared_operation(manager, journal)
    if declared is None:
        return
    directory, name, record = declared
    _require(record.get("cleanup_intent") is True, "contract_cleanup_intent_absent")
    restored = _expected_restored_controls(manager, journal, record)
    record["predecessor_restored_controls"] = restored
    record["predecessor_controls_restored"] = True
    _save(directory, name, record)


def _expected_restored_controls(
    manager: Any, journal: dict[str, Any], record: dict[str, Any]
) -> dict[str, Any]:
    original = record.get("initial_controls", record["controls"])
    restored = protected_snapshot(manager.paths)
    expected_files = dict(original["files"])
    restored_files = dict(restored["files"])
    for filename, field in (
        ("settings-control.json", "initial_settings_control_sha256"),
        ("adapters.json", "initial_adapter_config_sha256"),
    ):
        path = str(manager.paths.instance_dir / "config" / filename)
        if restored_files.get(path) != expected_files.get(path):
            _require(
                field in journal and restored_files.get(path) == journal[field],
                "contract_recovery_unexpected_cleanup_control",
            )
        expected_files.pop(path, None)
        restored_files.pop(path, None)
    readiness = str(manager.paths.instance_dir / "config" / "deployment-readiness.json")
    expected_files.pop(readiness, None)
    restored_files.pop(readiness, None)
    _require(
        expected_files == restored_files
        and original["gateway_target"] == restored["gateway_target"]
        and original["gateway_sha256"] == restored["gateway_sha256"],
        "contract_recovery_unexpected_cleanup_control",
    )
    return restored


def record_contract_predecessor_restoration(
    manager: Any, journal: dict[str, Any]
) -> None:
    """Called only after existing recovery's full healthy predecessor proof."""
    declared = _declared_operation(manager, journal)
    if declared is None:
        return
    directory, name, record = declared
    _require(record.get("cleanup_intent") is True, "contract_cleanup_intent_absent")
    record["predecessor_restored_controls"] = _expected_restored_controls(
        manager, journal, record
    )
    record["predecessor_restoration_proved"] = True
    _save(directory, name, record)


def require_contract_publication_complete(
    manager: Any, journal: dict[str, Any]
) -> None:
    """An ordinary rollback cannot close a declared unfinished publication."""
    declared = _declared_operation(manager, journal)
    if declared is None:
        return
    terminal = asyncio.run(
        _operation_terminal(manager.paths, declared[2], test_mode=manager.test_mode)
    )
    _require(
        terminal is not None
        and terminal.get("status") == "completed"
        and terminal.get("completed_assessment_contract") == ASSESSMENT_CONTRACT
        and terminal.get("publication_complete") is True,
        "contract_publication_unfinished",
    )


def _execution_baseline(
    record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if record.get("execution_mode") == "publication_only":
        return record["publication_controls"], record["publication_checkpoints"]
    return record["controls"], record["checkpoints"]


def _worker_deadline(record: dict[str, Any]) -> float:
    publication = record.get("execution_mode") == "publication_only"
    prefix = "publication" if publication else "maintenance"
    started = record[f"{prefix}_started_at"]
    deadline = record[f"{prefix}_deadline"]
    ceiling = record["deadline"] - FINALIZATION_RESERVE_SECONDS
    if publication:
        ceiling = min(ceiling, record["recovery_deadline"] - 180)
    else:
        ceiling -= RECOVERY_SECONDS
        if "recovery_deadline" in record:
            ceiling = min(ceiling, record["recovery_deadline"] - 180)
    _require(
        type(started) in (int, float)
        and type(deadline) in (int, float)
        and math.isfinite(started)
        and math.isfinite(deadline)
        and started
        <= deployment_clock()
        < deadline
        <= min(started + MAINTENANCE_SECONDS, ceiling),
        "maintenance_deadline_exhausted",
    )
    return deadline


def _remaining(record: dict[str, Any], reserve: float = 0) -> float:
    now = deployment_clock()
    start, deadline = record["started_at"], record["deadline"]
    _require(
        all(
            type(value) in (float, int) and math.isfinite(value)
            for value in (start, deadline)
        )
        and 0 < deadline - start <= CONTROLLER_SECONDS
        and start <= now < deadline - reserve,
        "contract_recovery_deadline_exhausted",
    )
    return deadline - now - reserve


def run_maintenance_child(
    manager: Any, record_path: Path, record: dict[str, Any]
) -> dict[str, Any]:
    record["worker_terminal_confirmed"] = True
    deadline = _worker_deadline(record)
    inherited_lock = os.environ.get("ADMINME_UPGRADE_OPERATION_LOCK_FD")
    _require(
        manager.test_mode or (inherited_lock and inherited_lock.isdecimal()),
        "maintenance_requires_guarded_lock",
    )
    parent_read, parent_write = os.pipe()
    result_path = record_path.parent / f"{record['fingerprint']}.worker-result.json"
    if result_path.exists():
        _file_hash(result_path)
        result_path.unlink()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["ADMINME_MAINTENANCE_PARENT_FD"] = str(parent_read)
    descriptors = [parent_read]
    if inherited_lock is not None:
        descriptors.append(int(inherited_lock))
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bootstrap.source_contract_recovery",
                "--worker",
                str(record_path),
                "--instance-root",
                str(manager.paths.root),
                "--result",
                str(result_path),
                *(["--test-mode"] if manager.test_mode else []),
            ],
            cwd=manager.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=tuple(descriptors),
        )
        record["worker_terminal_confirmed"] = False
        os.close(parent_read)
        parent_read = -1
        child.wait(timeout=max(0.1, deadline - deployment_clock()))
        _require(
            child.returncode == 0 and deployment_clock() < deadline,
            "maintenance_child_failed",
        )
        result = _load(result_path.parent, result_path.name)
        _require(isinstance(result, dict), "maintenance_result_absent")
        assert isinstance(result, dict)
        return result
    except subprocess.TimeoutExpired as exc:
        raise ContractRecoveryControlError("maintenance_child_timed_out") from exc
    finally:
        if parent_read >= 0:
            os.close(parent_read)
        os.close(parent_write)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=_CLEANUP_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=_CLEANUP_SECONDS)
        _require(
            child is None or child.poll() is not None,
            "maintenance_child_terminality_unknown",
        )
        # This in-memory flag is set only after this invocation has reaped its
        # child. A durable flag from an earlier invocation is never trusted.
        record["worker_terminal_confirmed"] = True


def _recover_captured_predecessor(
    manager: Any,
    repository: Path,
    directory: Path,
    name: str,
    record: dict[str, Any],
    *,
    finish_journal: bool,
) -> dict[str, Any] | None:
    if "recovery_deadline" not in record:
        record["recovery_started_at"] = deployment_clock()
        record["recovery_deadline"] = min(
            record["recovery_started_at"] + RECOVERY_SECONDS,
            record["deadline"] - FINALIZATION_RESERVE_SECONDS,
        )
        _save(directory, name, record)
    _require(
        record["recovery_started_at"]
        <= deployment_clock()
        < record["recovery_deadline"]
        <= min(
            record["recovery_started_at"] + RECOVERY_SECONDS,
            record["deadline"] - FINALIZATION_RESERVE_SECONDS,
        ),
        "contract_recovery_settlement_deadline_exhausted",
    )
    record["cleanup_intent"] = True
    _save(directory, name, record)
    manager._recovery_settlement_deadline = record["recovery_deadline"]
    manager._settlement_deadline = record["recovery_deadline"]
    return manager._recover_interrupted_transaction(
        repository, finish_journal=finish_journal
    )


def _resume_frozen_publication(
    manager: Any,
    repository: Path,
    directory: Path,
    name: str,
    record: dict[str, Any],
    plan: ContractRecoveryPlan,
) -> dict[str, Any]:
    """Resume a stored draft suffix after a proved predecessor restoration."""
    _require(
        record.get("predecessor_restoration_proved") is True,
        "publication_predecessor_restoration_unproved",
    )
    expected = record["predecessor_restored_controls"]
    _require(
        protected_snapshot(manager.paths) == expected, "publication_controls_changed"
    )
    stopped = manager.services.status().status == "stopped"
    if stopped:
        _require(
            record.get("execution_mode") == "publication_only"
            and isinstance(record.get("publication_preflight"), dict)
            and checkpoint_snapshot(manager.paths)
            == record.get("publication_checkpoints"),
            "publication_stopped_preflight_unproved",
        )
    else:
        journal = manager.activation_journal.load()
        _require(journal is not None, "contract_recovery_journal_mismatch")
        assert journal is not None
        predecessor = Path(journal["predecessor_path"])
        doctor = manager._run_release_doctor(predecessor, include_synthetic_proof=True)
        diagnostics = manager.services.runtime_diagnostics()
        _require(isinstance(diagnostics, dict), "publication_live_preflight_absent")
        assert isinstance(diagnostics, dict)
        proof = validate_live_preflight(
            doctor,
            diagnostics,
            plan,
            doctor_checks=expected_doctor_checks(predecessor),
            required_consumers=expected_consumer_capabilities(
                predecessor, manager.paths.instance_dir
            ),
            publication_only=True,
        )
        _require(
            proof["provider_effect_counts"]
            == record["preflight"]["provider_effect_counts"],
            "publication_provider_effect_baseline_changed",
        )
        _require(
            protected_snapshot(manager.paths) == expected,
            "publication_controls_changed",
        )
        record["publication_preflight"] = proof
        record["publication_controls"] = expected
        record["execution_mode"] = "publication_only"
        _save(directory, name, record)
    manager.services.stop()
    recovery_started = False
    worker_started = False
    try:
        _require(
            protected_snapshot(manager.paths) == expected,
            "publication_controls_changed",
        )
        checkpoints = checkpoint_snapshot(manager.paths)
        if stopped:
            _require(
                checkpoints == record["publication_checkpoints"],
                "publication_checkpoints_changed",
            )
        else:
            record["publication_checkpoints"] = checkpoints
            _save(directory, name, record)
        asyncio.run(
            validate_stopped_history(manager.paths, record, test_mode=manager.test_mode)
        )
        if "publication_deadline" in record:
            _worker_deadline(record)
        backup = BackupManager(
            manager.paths, root=manager.root, test_mode=manager.test_mode
        )
        available = (
            min(
                _remaining(record, FINALIZATION_RESERVE_SECONDS),
                record["recovery_deadline"] - deployment_clock(),
            )
            - 180
        )
        _require(available > 30, "publication_backup_capacity_insufficient")
        artifact = backup.create()
        rehearsal = backup.rehearse(
            Path(artifact["path"]),
            candidate=repository,
            timeout_s=min(1800, (available - 30) / 2),
        )
        _require(
            rehearsal.get("receipt_written") is True
            and rehearsal.get("source_events_appended") == 0,
            "contract_recovery_backup_unverified",
        )
        record["publication_backup"] = {
            "path": artifact["path"],
            "sha256": _file_hash(Path(artifact["path"])),
            "receipt_sha256": rehearsal["receipt_sha256"],
        }
        if "publication_deadline" not in record:
            record["publication_started_at"] = deployment_clock()
            record["publication_deadline"] = min(
                record["publication_started_at"] + MAINTENANCE_SECONDS,
                record["deadline"] - FINALIZATION_RESERVE_SECONDS,
                record["recovery_deadline"] - 180,
            )
        record["execution_mode"] = "publication_only"
        record["phase"] = "publishing_retained_plan"
        _save(directory, name, record)
        _worker_deadline(record)
        _require(
            protected_snapshot(manager.paths) == expected
            and checkpoint_snapshot(manager.paths) == record["publication_checkpoints"],
            "publication_baseline_changed",
        )
        manager.services.start_maintenance_gateway()
        manager.services.assert_maintenance_boundary()
        worker_started = True
        record["worker_terminal_confirmed"] = False
        result = run_maintenance_child(manager, directory / name, record)
        manager.services.assert_maintenance_boundary()
        _require(
            protected_snapshot(manager.paths) == expected
            and checkpoint_snapshot(manager.paths) == record["publication_checkpoints"],
            "publication_mutated_protected_state",
        )
        asyncio.run(
            validate_stopped_history(manager.paths, record, test_mode=manager.test_mode)
        )
        _require(
            result.get("status") == "completed"
            and result.get("publication_complete") is True
            and result.get("completed_assessment_contract") == ASSESSMENT_CONTRACT,
            "contract_publication_unfinished",
        )
        record["terminal"] = result
        _save(directory, name, record)
        recovery_started = True
        recovery = _recover_captured_predecessor(
            manager, repository, directory, name, record, finish_journal=True
        )
        _require(
            recovery is not None and recovery.get("status") == "recovered",
            "contract_recovery_predecessor_not_healthy",
        )
        record["phase"] = "recovered"
        _save(directory, name, record)
        return {
            "status": "recovered",
            "operation_id": record["fingerprint"],
            "transaction_id": plan.transaction_id,
            "contract_recovery": result,
            "content_exposed": False,
        }
    except BaseException as primary:
        if not recovery_started and (
            not worker_started or record.get("worker_terminal_confirmed") is True
        ):
            try:
                _recover_captured_predecessor(
                    manager, repository, directory, name, record, finish_journal=False
                )
            except BaseException as recovery_error:
                primary.add_note(
                    "Captured predecessor recovery did not complete: "
                    + type(recovery_error).__name__
                )
        raise


def _prepare_unspent_reentry(
    manager: Any,
    predecessor: Path,
    directory: Path,
    name: str,
    record: dict[str, Any],
    plan: ContractRecoveryPlan,
) -> bool:
    if not (
        record.get("cleanup_intent") is True
        and record.get("predecessor_controls_restored") is True
        and manager.services.status().status != "stopped"
    ):
        return False
    if not asyncio.run(
        _operation_has_no_attempts(manager.paths, record, test_mode=manager.test_mode)
    ):
        return False
    expected = record["predecessor_restored_controls"]
    _require(protected_snapshot(manager.paths) == expected, "reentry_controls_changed")
    doctor = manager._run_release_doctor(predecessor, include_synthetic_proof=True)
    diagnostics = manager.services.runtime_diagnostics()
    _require(isinstance(diagnostics, dict), "contract_recovery_live_preflight_absent")
    assert isinstance(diagnostics, dict)
    proof = validate_live_preflight(
        doctor,
        diagnostics,
        plan,
        doctor_checks=expected_doctor_checks(predecessor),
        required_consumers=expected_consumer_capabilities(
            predecessor, manager.paths.instance_dir
        ),
    )
    _require(
        proof["provider_effect_counts"] == record["preflight"]["provider_effect_counts"]
        and protected_snapshot(manager.paths) == expected,
        "reentry_protected_baseline_changed",
    )
    record["reentry_preflight"] = proof
    record["reentry_controls"] = expected
    _save(directory, name, record)
    return True


def recover_source_contract(
    manager: Any, repository: Path, plan_path: Path
) -> dict[str, Any]:
    with manager._operation_lock():
        budget = manager._begin_deployment_budget()
        commit = reviewed_candidate_commit(repository)
        manager._attest_controller_provenance(repository, commit)
        _payload, document = manager._read_private_json_payload(plan_path)
        plan = ContractRecoveryPlan.model_validate(document)
        journal = manager.activation_journal.load()
        _require(
            journal is not None
            and journal["phase"] == "recovering"
            and journal["transaction_id"] == plan.transaction_id
            and journal["predecessor_commit"] == plan.predecessor_commit
            and journal.get("was_running") is True,
            "contract_recovery_journal_mismatch",
        )
        predecessor = Path(journal["predecessor_path"])
        _pointer_bytes, pointer = manager._read_private_json_payload(
            manager.paths.app_dir / "current-release.json"
        )
        _require(
            pointer
            and pointer.get("commit") == plan.predecessor_commit
            and pointer.get("path") == str(predecessor),
            "contract_recovery_predecessor_not_selected",
        )
        _require(
            load_versions(manager.root) == load_versions(predecessor),
            "contract_recovery_runtime_or_gateway_migration_refused",
        )
        fingerprint = hashlib.sha256(
            canonical_json(
                {"controller_commit": commit, "plan": plan.model_dump(mode="json")}
            )
        ).hexdigest()
        directory = manager.activation_journal.directory
        name = f"contract-recovery-{plan.transaction_id}.json"
        record = _load(directory, name)
        existing_operation = record is not None
        if record is None:
            _require(
                budget.deadline - deployment_clock()
                > RECOVERY_SECONDS + MAINTENANCE_SECONDS + FINALIZATION_RESERVE_SECONDS,
                "contract_recovery_capacity_insufficient",
            )
            doctor = manager._run_release_doctor(
                predecessor, include_synthetic_proof=True
            )
            diagnostics = manager.services.runtime_diagnostics()
            _require(
                isinstance(diagnostics, dict), "contract_recovery_live_preflight_absent"
            )
            preflight = validate_live_preflight(
                doctor,
                diagnostics,
                plan,
                doctor_checks=expected_doctor_checks(predecessor),
                required_consumers=expected_consumer_capabilities(
                    predecessor, manager.paths.instance_dir
                ),
            )
            record = {
                "contract": "guarded-source-contract-operation-v1",
                "fingerprint": fingerprint,
                "controller_commit": commit,
                "plan": plan.model_dump(mode="json"),
                "boot_identity": _boot_identity(),
                "started_at": budget.started_at,
                "deadline": budget.deadline,
                "phase": "preflight",
                "preflight": preflight,
                "controls": protected_snapshot(manager.paths),
            }
            _save(directory, name, record)
        _require(
            record.get("fingerprint") == fingerprint
            and record.get("plan") == plan.model_dump(mode="json")
            and record.get("controller_commit") == commit,
            "contract_recovery_operation_changed",
        )
        _require(
            record.get("boot_identity") == _boot_identity(),
            "contract_recovery_boot_changed",
        )
        _remaining(record, FINALIZATION_RESERVE_SECONDS)
        record["deadline"] = min(record["deadline"], budget.deadline)
        _save(directory, name, record)
        manager._deployment_budget = DeploymentBudget(
            record["started_at"], record["deadline"]
        )
        refresh_unspent = False
        if existing_operation:
            terminal = asyncio.run(
                _operation_terminal(manager.paths, record, test_mode=manager.test_mode)
            )
            if (
                terminal is not None
                and terminal.get("status") == "completed"
                and terminal.get("completed_assessment_contract") == ASSESSMENT_CONTRACT
                and terminal.get("publication_complete") is True
            ):
                # A completed suffix needs only the existing bounded predecessor
                # recovery, never another maintenance child or model seam.
                recovery = _recover_captured_predecessor(
                    manager, repository, directory, name, record, finish_journal=True
                )
                _require(
                    recovery is not None and recovery.get("status") == "recovered",
                    "contract_recovery_predecessor_not_healthy",
                )
                return {
                    "status": "recovered",
                    "operation_id": fingerprint,
                    "transaction_id": plan.transaction_id,
                    "contract_recovery": terminal,
                    "content_exposed": False,
                }
            if (
                terminal is not None
                and terminal.get("status") == "completed"
                and terminal.get("publication_complete") is False
                and record.get("predecessor_restoration_proved") is True
            ):
                return _resume_frozen_publication(
                    manager, repository, directory, name, record, plan
                )
            refresh_unspent = _prepare_unspent_reentry(
                manager, predecessor, directory, name, record, plan
            )
        expected_controls = (
            record["reentry_controls"] if refresh_unspent else record["controls"]
        )
        _require(
            protected_snapshot(manager.paths) == expected_controls,
            "contract_recovery_controls_changed",
        )
        manager.services.stop()
        recovery_started = False
        worker_started = False
        try:
            _require(
                protected_snapshot(manager.paths) == expected_controls,
                "contract_recovery_controls_changed",
            )
            checkpoints = checkpoint_snapshot(manager.paths)
            if refresh_unspent:
                _require(
                    asyncio.run(
                        _operation_has_no_attempts(
                            manager.paths, record, test_mode=manager.test_mode
                        )
                    ),
                    "reentry_attempt_started",
                )
                record.setdefault("initial_controls", record["controls"])
                record.setdefault("initial_preflight", record["preflight"])
                record["preflight"] = record["reentry_preflight"]
                record["checkpoints"] = checkpoints
                record["controls"] = expected_controls
                _save(directory, name, record)
            elif "checkpoints" in record:
                _require(
                    checkpoints == record["checkpoints"],
                    "contract_recovery_checkpoints_changed",
                )
            else:
                record["checkpoints"] = checkpoints
                _save(directory, name, record)
            asyncio.run(
                validate_stopped_history(
                    manager.paths, record, test_mode=manager.test_mode
                )
            )
            backup = BackupManager(
                manager.paths, root=manager.root, test_mode=manager.test_mode
            )
            # A new stopped backup/rehearsal is required on each controller entry.
            # It does not alter request identity or restore any execution allowance.
            available = _remaining(
                record,
                RECOVERY_SECONDS + MAINTENANCE_SECONDS + FINALIZATION_RESERVE_SECONDS,
            )
            if "recovery_deadline" in record:
                available = min(
                    available,
                    record["recovery_deadline"]
                    - deployment_clock()
                    - MAINTENANCE_SECONDS
                    - 180,
                )
            if "maintenance_deadline" in record:
                _worker_deadline(record)
                available = min(
                    available, record["maintenance_deadline"] - deployment_clock()
                )
            _require(available > 30, "contract_recovery_backup_capacity_insufficient")
            artifact = backup.create()
            rehearsal = backup.rehearse(
                Path(artifact["path"]),
                candidate=repository,
                timeout_s=min(1800, (available - 30) / 2),
            )
            _require(
                rehearsal.get("receipt_written") is True
                and rehearsal.get("source_events_appended") == 0,
                "contract_recovery_backup_unverified",
            )
            record["backup"] = {
                "path": artifact["path"],
                "sha256": _file_hash(Path(artifact["path"])),
                "receipt_sha256": rehearsal["receipt_sha256"],
            }
            if "maintenance_deadline" not in record:
                record["maintenance_started_at"] = deployment_clock()
                record["maintenance_deadline"] = min(
                    record["maintenance_started_at"] + MAINTENANCE_SECONDS,
                    record["deadline"]
                    - RECOVERY_SECONDS
                    - FINALIZATION_RESERVE_SECONDS,
                    record.get("recovery_deadline", record["deadline"]) - 180,
                )
            _worker_deadline(record)
            record["phase"] = "maintenance"
            _save(directory, name, record)
            _require(
                checkpoint_snapshot(manager.paths) == record["checkpoints"]
                and protected_snapshot(manager.paths) == expected_controls,
                "contract_recovery_baseline_changed",
            )
            manager.services.start_maintenance_gateway()
            manager.services.assert_maintenance_boundary()
            worker_started = True
            record["worker_terminal_confirmed"] = False
            result = run_maintenance_child(manager, directory / name, record)
            manager.services.assert_maintenance_boundary()
            _require(
                protected_snapshot(manager.paths) == expected_controls
                and checkpoint_snapshot(manager.paths) == record["checkpoints"],
                "contract_recovery_mutated_protected_state",
            )
            asyncio.run(
                validate_stopped_history(
                    manager.paths, record, test_mode=manager.test_mode
                )
            )
            record["terminal"] = result
            record["phase"] = "maintenance_terminal"
            _save(directory, name, record)
            _require(
                result.get("status") == "completed"
                and result.get("publication_complete") is True
                and result.get("completed_assessment_contract") == ASSESSMENT_CONTRACT,
                "contract_recovery_not_completed",
            )
            # The worker has been reaped and all encrypted handles closed. Existing
            # recovery restores only the captured predecessor and requires full Doctor.
            recovery_started = True
            recovery = _recover_captured_predecessor(
                manager, repository, directory, name, record, finish_journal=True
            )
            _require(
                recovery is not None and recovery.get("status") == "recovered",
                "contract_recovery_predecessor_not_healthy",
            )
            record["phase"] = "recovered"
            _save(directory, name, record)
            return {
                "status": "recovered",
                "operation_id": fingerprint,
                "transaction_id": plan.transaction_id,
                "contract_recovery": result,
                "content_exposed": False,
            }
        except BaseException as primary:
            # A failed operation must not strand the captured predecessor off.
            # Unknown worker terminality is the one boundary at which starting
            # another runtime would be unsafe. The guardian retains its lease.
            if not recovery_started and (
                not worker_started or record.get("worker_terminal_confirmed") is True
            ):
                try:
                    _recover_captured_predecessor(
                        manager,
                        repository,
                        directory,
                        name,
                        record,
                        finish_journal=False,
                    )
                except BaseException as recovery_error:
                    primary.add_note(
                        "Captured predecessor recovery did not complete: "
                        + type(recovery_error).__name__
                    )
            raise


def _parent_watch(fd: int) -> None:
    try:
        os.read(fd, 1)
    finally:
        # The parent holds this pipe until the child is reaped. EOF means the
        # controlling process vanished; do not outlive its exclusive lease.
        os._exit(74)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--test-mode", action="store_true")
    args = parser.parse_args()
    parent_fd = int(os.environ["ADMINME_MAINTENANCE_PARENT_FD"])
    threading.Thread(target=_parent_watch, args=(parent_fd,), daemon=True).start()
    record = _load(args.worker.parent, args.worker.name)
    _require(record is not None, "maintenance_operation_absent")
    assert record is not None
    paths = DeploymentPaths.from_root(args.instance_root, production=not args.test_mode)
    _require(
        args.worker.parent == paths.app_dir / "activation"
        and args.worker.name
        == f"contract-recovery-{record['plan']['transaction_id']}.json",
        "maintenance_operation_coordinate_invalid",
    )
    _require(
        args.result
        == args.worker.parent / f"{record['fingerprint']}.worker-result.json",
        "maintenance_result_coordinate_invalid",
    )
    if not args.test_mode:
        lock_fd = int(os.environ["ADMINME_UPGRADE_OPERATION_LOCK_FD"])
        inherited = os.fstat(lock_fd)
        named = (paths.app_dir / ".upgrade-operation.lock").lstat()
        _require(
            stat.S_ISREG(inherited.st_mode)
            and inherited.st_uid == os.getuid()
            and stat.S_IMODE(inherited.st_mode) == 0o600
            and inherited.st_nlink == 1
            and (inherited.st_dev, inherited.st_ino) == (named.st_dev, named.st_ino),
            "maintenance_guarded_lease_invalid",
        )
    from bootstrap.activation_transaction import ActivationJournal
    from bootstrap.services import ServiceManager

    journal = ActivationJournal(paths).load()
    _require(
        journal is not None
        and journal["phase"] == "recovering"
        and journal["transaction_id"] == record["plan"]["transaction_id"],
        "maintenance_journal_changed",
    )
    _require(
        record.get("boot_identity") == _boot_identity(), "maintenance_boot_changed"
    )
    publication_only = record.get("execution_mode") == "publication_only"
    _remaining(
        record,
        FINALIZATION_RESERVE_SECONDS + (180 if publication_only else RECOVERY_SECONDS),
    )
    _worker_deadline(record)
    expected_controls, expected_checkpoints = _execution_baseline(record)
    _require(
        protected_snapshot(paths) == expected_controls
        and checkpoint_snapshot(paths) == expected_checkpoints,
        "maintenance_protected_baseline_changed",
    )
    ServiceManager(paths, test_mode=args.test_mode).assert_maintenance_boundary()
    key, token = _keys(paths, args.test_mode)
    operation = args.worker.parent / f"contract-worker-{record['fingerprint']}"
    operation.mkdir(mode=0o700, exist_ok=True)
    _require(
        not operation.is_symlink()
        and operation.stat().st_uid == os.getuid()
        and stat.S_IMODE(operation.stat().st_mode) == 0o700,
        "maintenance_directory_unsafe",
    )
    result = asyncio.run(
        run_source_contract_recovery(
            instance_dir=paths.instance_dir,
            repository_root=Path(__file__).resolve().parents[1],
            operation_dir=operation,
            encryption_key=key,
            gateway_token=token,
            batch=_batch(record),
            publication_only=publication_only,
            requested_by_actor=f"guarded-updater:{record['controller_commit']}",
        )
    )
    _save(args.result.parent, args.result.name, result)


if __name__ == "__main__":
    main()
