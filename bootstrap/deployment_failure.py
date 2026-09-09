"""Content-free, predecessor-compatible diagnostics for failed deployment proof."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from typing import Any

from bootstrap.activation_transaction import (
    ActivationJournal,
    ActivationJournalError,
    _assert_directory_coordinate,
    _read_private_at,
    _write_private_at,
    canonical_json,
)


FAILURE_CHECKS = frozenset(
    {
        "adapter_categories_and_capabilities",
        "bootstrap_sections",
        "canonical_synthetic_round_trip",
        "deployment_topology_and_recovery_readiness",
        "disk_space_and_backup_target",
        "encrypted_event_log_and_migrations",
        "fifteen_projections_and_checkpoints",
        "launchd_and_process_state",
        "member_mapping_and_bridge_status",
        "member_portal_domain_and_chat_health",
        "observation_mode_and_trust_workflow",
        "openclaw_compatibility_and_plugin_discovery",
        "openclaw_cron_reconciliation",
        "platform",
        "ports_and_loopback_binding",
        "required_reactive_pipeline_delivery",
        "reviewed_openclaw_source",
        "runtime_and_package_versions",
        "secure_secret_references",
        "source_assurance_terminal_dispositions",
        "validated_configuration",
        "workbook_forward_and_reverse_state",
        "bounded_service_settlement",
    }
)
FAILURE_REASONS = frozenset(
    {
        "consumer_stalled",
        "consumer_identity_changed",
        "consumer_evidence_invalid",
        "consumer_failed",
        "consumer_checkpoint_regressed",
        "deadline_exhausted",
        "proof_budget_exhausted",
        "structural_degradation",
        "source_assurance_failed",
        "readiness_contract_invalid",
    }
)
FAILURE_PHASES = frozenset({"doctor", "full_doctor", "readiness", "liveness"})
FAILURE_STAGES = frozenset({"candidate_doctor", "recovery_doctor"})
READINESS_DIMENSIONS = frozenset(
    {
        "topology",
        "attestation_binding",
        "service_account_recovery",
        "identity_acceptance",
        "provider_bindings",
        "service_channel_identities",
        "bridges",
        "openclaw",
        "backup_rollback",
        "observation_effects",
    }
)


def _readiness_evidence(checks: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "deployment_evidence_present": False,
        "source_evidence_present": False,
    }
    for check in checks[:64]:
        if not isinstance(check, dict) or not isinstance(check.get("evidence"), dict):
            continue
        evidence = check["evidence"]
        if check.get("name") == "deployment_topology_and_recovery_readiness":
            result["deployment_evidence_present"] = (
                evidence.get("contract") == "deployment-readiness-v1"
            )
            blocking = evidence.get("blocking_dimensions")
            if isinstance(blocking, list):
                result["blocking_dimensions"] = sorted(
                    {
                        value
                        if isinstance(value, str) and value in READINESS_DIMENSIONS
                        else "unrecognized_dimension"
                        for value in blocking[:32]
                    }
                )
        if check.get("name") == "source_assurance_terminal_dispositions":
            result["source_evidence_present"] = (
                evidence.get("contract") == "source-assurance-v1"
            )
            counts = evidence.get("counts")
            if isinstance(counts, dict):
                result["source_counts"] = {
                    name: value
                    for name in (
                        "source_facts",
                        "obligations",
                        "accounted",
                        "pending",
                        "missing",
                        "failed",
                    )
                    if type(value := counts.get(name)) is int
                    and 0 <= value <= 1_000_000_000
                }
            freshness = evidence.get("freshness")
            if isinstance(freshness, dict):
                result["source_freshness"] = {
                    name: value
                    for name in (
                        "refresh_in_progress",
                        "revision_current",
                        "pending_recheck_required",
                    )
                    if type(value := freshness.get(name)) is bool
                }
                state = freshness.get("state")
                if isinstance(state, str) and state in {
                    "fresh",
                    "stale",
                    "missing",
                    "refreshing",
                    "failed",
                }:
                    result["source_freshness"]["state"] = state
                result["source_freshness"]["refresh_error_present"] = (
                    freshness.get("refresh_error_type") is not None
                )
    return result


def failure_evidence(
    report: dict[str, Any] | None,
    *,
    allowed_consumers: frozenset[str],
    exception: Exception | None = None,
) -> dict[str, Any]:
    """Project diagnostic input onto closed names, never arbitrary evidence."""
    names: set[str] = set()
    bounded: list[dict[str, str]] = []
    checks = report.get("checks", []) if isinstance(report, dict) else []
    for check in checks[:64] if isinstance(checks, list) else []:
        if not isinstance(check, dict) or check.get("ok") is True:
            continue
        name = check.get("name")
        names.add(
            name
            if isinstance(name, str) and name in FAILURE_CHECKS
            else "unrecognized_check"
        )
        evidence = check.get("evidence")
        if name != "bounded_service_settlement" or not isinstance(evidence, dict):
            continue
        if evidence.get("contract") != "bounded-service-settlement-v1":
            continue
        reason = evidence.get("reason")
        item = {
            "reason": reason
            if isinstance(reason, str) and reason in FAILURE_REASONS
            else "unrecognized_reason"
        }
        consumer = evidence.get("consumer_id")
        if consumer is not None:
            item["consumer_id"] = (
                consumer
                if isinstance(consumer, str) and consumer in allowed_consumers
                else "unrecognized_consumer"
            )
        phase = evidence.get("phase")
        if phase is not None:
            item["phase"] = (
                phase
                if isinstance(phase, str) and phase in FAILURE_PHASES
                else "unrecognized_phase"
            )
        if item not in bounded:
            bounded.append(item)
    result: dict[str, Any] = {
        "failed_checks": sorted(names),
        "bounded": bounded,
        "readiness": _readiness_evidence(checks if isinstance(checks, list) else []),
    }
    if exception is not None:
        causes: list[Exception] = []
        current: BaseException | None = exception
        for _ in range(5):
            if not isinstance(current, Exception) or any(
                current is prior for prior in causes
            ):
                break
            causes.append(current)
            current = current.__cause__
        if any(
            isinstance(cause, (TimeoutError, subprocess.TimeoutExpired))
            for cause in causes
        ):
            category = "timeout"
        elif any(isinstance(cause, OSError) for cause in causes):
            category = "operating_system_error"
        elif any(isinstance(cause, (ValueError, TypeError)) for cause in causes):
            category = "invalid_diagnostic"
        else:
            category = "diagnostic_exception"
        result["exception_category"] = category
    return result


def failure_summary(evidence: dict[str, Any]) -> str:
    parts = [", ".join(evidence["failed_checks"]) or "unknown check"]
    for bounded in evidence["bounded"]:
        parts.append(", ".join(f"{key}={value}" for key, value in bounded.items()))
    if evidence.get("exception_category"):
        parts.append(f"exception={evidence['exception_category']}")
    readiness = evidence["readiness"]
    for check, name in (
        ("deployment_topology_and_recovery_readiness", "deployment_evidence_present"),
        ("source_assurance_terminal_dispositions", "source_evidence_present"),
    ):
        if check in evidence["failed_checks"]:
            parts.append(f"{name}={str(readiness[name]).lower()}")
    if readiness.get("blocking_dimensions"):
        parts.append(
            "blocking_dimensions=" + ",".join(readiness["blocking_dimensions"])
        )
    return "; ".join(parts)


def persist_failure(journal: ActivationJournal, document: dict[str, Any]) -> None:
    """Keep the first proof failure per transaction/stage outside the v1 journal."""
    transaction = document["transaction_id"]
    stage = document["stage"]
    if (
        not isinstance(transaction, str)
        or re.fullmatch(r"[0-9a-f]{32}", transaction) is None
        or stage not in FAILURE_STAGES
    ):
        raise ActivationJournalError("deployment failure coordinate is invalid")
    directory, history = journal._open_directories()
    failures: int | None = None
    try:
        try:
            os.mkdir("failures", mode=0o700, dir_fd=directory)
            os.fsync(directory)
        except FileExistsError:
            pass
        failures = os.open(
            "failures",
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory,
        )
        metadata = os.fstat(failures)
        public = os.stat("failures", dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
        ):
            raise ActivationJournalError("deployment failure directory is unsafe")
        name = f"{transaction}-{stage}.json"
        try:
            _write_private_at(failures, name, canonical_json(document), replace=False)
        except ActivationJournalError as exc:
            if not isinstance(exc.__cause__, FileExistsError):
                raise
            # Never overwrite the original reason during a repeated recovery.
            existing, _metadata = _read_private_at(failures, name)
            previous = json.loads(existing)
            if (
                previous.get("transaction_id") != transaction
                or previous.get("stage") != stage
            ):
                raise ActivationJournalError(
                    "deployment failure identity changed"
                ) from None
        _assert_directory_coordinate(directory, journal.directory)
        _assert_directory_coordinate(failures, journal.directory / "failures")
    finally:
        if failures is not None:
            os.close(failures)
        journal._close_directories(history, directory)
