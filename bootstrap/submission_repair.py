"""Exact, private maintenance authorization for failed forwarded submissions.

This exception preserves the degraded assessment and all historical facts. It
permits containment and a guarded upgrade, never a healthy readiness claim.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.source_assurance import audit_source_assurance
from adminme.submission_recovery import (
    RETRYABLE_REASONS,
    SubmissionRecoveryTarget,
    build_submission_recovery_requests,
    validate_submission_recovery_target,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.deployment_budget import deployment_clock
from bootstrap.services import RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS
from bootstrap.service_settlement import RELEASE_DOCTOR_TIMEOUT_SECONDS
from bootstrap.secrets import secret_store
from bootstrap.source_identity import _run_git, reviewed_candidate_commit


REPAIR_READINESS_DIMENSIONS = frozenset(
    {
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
REPAIR_DOCTOR_CHECKS = frozenset(
    {
        "platform",
        "runtime_and_package_versions",
        "reviewed_openclaw_source",
        "validated_configuration",
        "bootstrap_sections",
        "secure_secret_references",
        "encrypted_event_log_and_migrations",
        "fifteen_projections_and_checkpoints",
        "required_reactive_pipeline_delivery",
        "workbook_forward_and_reverse_state",
        "adapter_categories_and_capabilities",
        "openclaw_compatibility_and_plugin_discovery",
        "openclaw_cron_reconciliation",
        "member_portal_domain_and_chat_health",
        "launchd_and_process_state",
        "source_assurance_terminal_dispositions",
        "deployment_topology_and_recovery_readiness",
        "ports_and_loopback_binding",
        "member_mapping_and_bridge_status",
        "observation_mode_and_trust_workflow",
        "disk_space_and_backup_target",
    }
)


class SubmissionRepairError(ValueError):
    """Content-free refusal of the declared repair boundary."""


class SubmissionRepairManifest(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    contract: Literal["forwarded-submission-repair-v1"]
    predecessor_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_source_facts: int = Field(ge=1)
    targets: list[SubmissionRecoveryTarget] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def exact_targets(self) -> SubmissionRepairManifest:
        if self.candidate_commit == self.predecessor_commit:
            raise ValueError("repair requires a distinct successor")
        if len({t.source_event_id for t in self.targets}) != len(self.targets):
            raise ValueError("repair sources must be unique")
        if len({t.failed_assessment_event_id for t in self.targets}) != len(
            self.targets
        ):
            raise ValueError("repair assessments must be unique")
        if any(
            t.expected_failure_reason not in RETRYABLE_REASONS for t in self.targets
        ):
            raise ValueError("repair failure is not a transient language failure")
        return self

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))

    def targets_digest(self) -> str:
        return _digest([t.model_dump(mode="json") for t in self.targets])

    def public_binding(self) -> dict[str, Any]:
        return repair_binding(self)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def repair_binding(manifest: SubmissionRepairManifest) -> dict[str, Any]:
    return {
        "contract": "forwarded-submission-repair-binding-v1",
        "manifest_sha256": manifest.digest(),
        "candidate_commit": manifest.candidate_commit,
        "predecessor_commit": manifest.predecessor_commit,
        "targets_sha256": manifest.targets_digest(),
        "target_count": len(manifest.targets),
        "minimum_source_facts": manifest.minimum_source_facts,
    }


def valid_repair_binding(value: Any) -> bool:
    keys = {
        "contract",
        "manifest_sha256",
        "candidate_commit",
        "predecessor_commit",
        "targets_sha256",
        "target_count",
        "minimum_source_facts",
    }
    return bool(
        isinstance(value, dict)
        and set(value) == keys
        and value["contract"] == "forwarded-submission-repair-binding-v1"
        and all(
            isinstance(value[k], str) and re.fullmatch(r"[0-9a-f]{64}", value[k])
            for k in ("manifest_sha256", "targets_sha256")
        )
        and all(
            isinstance(value[k], str) and re.fullmatch(r"[0-9a-f]{40}", value[k])
            for k in ("candidate_commit", "predecessor_commit")
        )
        and value["candidate_commit"] != value["predecessor_commit"]
        and type(value["target_count"]) is int
        and 1 <= value["target_count"] <= 8
        and type(value["minimum_source_facts"]) is int
        and value["minimum_source_facts"] >= 1
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionRepairError("repair document has duplicate keys")
        result[key] = value
    return result


def load_submission_repair_manifest(
    path: Path,
    *,
    candidate: Path | None = None,
    active_commit: str | None = None,
    candidate_commit: str | None = None,
    predecessor_commit: str | None = None,
    test_mode: bool = False,
) -> SubmissionRepairManifest:
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise SubmissionRepairError("repair manifest parent must be private")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or metadata.st_size > 32768
        ):
            raise SubmissionRepairError(
                "repair manifest must be an owner-only regular file"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(32769)
        after = os.fstat(descriptor)
        public = path.lstat()
        if (
            len(data) != metadata.st_size
            or len(data) > 32768
            or any(
                getattr(metadata, key) != getattr(after, key)
                or getattr(metadata, key) != getattr(public, key)
                for key in (
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                    "st_mode",
                    "st_uid",
                    "st_nlink",
                )
            )
        ):
            raise SubmissionRepairError("repair manifest changed during private read")
        manifest = SubmissionRepairManifest.model_validate(
            json.loads(data, object_pairs_hook=_unique_object)
        )
    finally:
        os.close(descriptor)
    if candidate is not None:
        actual = reviewed_candidate_commit(candidate)
        if (
            not test_mode
            and _run_git(candidate, ["rev-parse", "refs/remotes/origin/main"])
            .stdout.decode("ascii")
            .strip()
            != actual
        ):
            raise SubmissionRepairError(
                "repair controller must be the merged protected-main commit"
            )
        if candidate_commit is not None and actual != candidate_commit:
            raise SubmissionRepairError("repair candidate source differs")
        candidate_commit = actual
    if candidate_commit is not None and manifest.candidate_commit != candidate_commit:
        raise SubmissionRepairError("repair candidate binding differs")
    if (
        active_commit or predecessor_commit
    ) is not None and manifest.predecessor_commit != (
        active_commit or predecessor_commit
    ):
        raise SubmissionRepairError("repair predecessor binding differs")
    return manifest


def validate_submission_repair_audit(
    manifest: SubmissionRepairManifest, assurance: dict[str, Any]
) -> None:
    counts = assurance.get("counts") or {}
    required = (
        "source_facts",
        "obligations",
        "accounted",
        "pending",
        "missing",
        "failed",
    )
    if (
        assurance.get("contract") != "source-assurance-v1"
        or assurance.get("state") != "degraded"
        or assurance.get("content_exposed") is not False
        or any(type(counts.get(key)) is not int for key in required)
        or counts["source_facts"] < manifest.minimum_source_facts
        or counts["obligations"] < counts["source_facts"]
        or counts["accounted"] != counts["obligations"]
        or counts["pending"] != 0
        or counts["missing"] != 0
        or counts["failed"] != len(manifest.targets)
        or not isinstance(assurance.get("watermark_event_id"), str)
        or not assurance["watermark_event_id"]
    ):
        raise SubmissionRepairError(
            "source accounting differs from the exact repair baseline"
        )
    failures = assurance.get("failed")
    if not isinstance(failures, list) or len(failures) != len(manifest.targets):
        raise SubmissionRepairError("repair failure inventory is incomplete")
    expected = {t.source_event_id: t for t in manifest.targets}
    seen = set()
    for failure in failures:
        if not isinstance(failure, dict) or failure.get("event_id") not in expected:
            raise SubmissionRepairError("repair contains an undeclared failed source")
        source_id = failure["event_id"]
        if (
            source_id in seen
            or failure.get("event_type") != "triage.intent.submitted"
            or failure.get("reason")
            not in {
                "member_text_requires_human_interpretation",
                expected[source_id].expected_failure_reason,
            }
        ):
            raise SubmissionRepairError("repair failure summary differs")
        seen.add(source_id)
    recovery = assurance.get("submission_recovery")
    if recovery is not None and (
        not isinstance(recovery, dict)
        or any(
            type((recovery.get("counts") or {}).get(k)) is not int
            or recovery["counts"][k] != 0
            for k in ("pending", "invalid")
        )
    ):
        raise SubmissionRepairError("repair has unresolved recovery commands")
    effects = assurance.get("provider_effects") or {}
    effect_counts = effects.get("counts") or {}
    from bootstrap.htr10_export import _INTEGRITY_ZERO_COUNTS

    if any(
        type(effect_counts.get(key)) is not int or effect_counts[key] != 0
        for key in _INTEGRITY_ZERO_COUNTS
    ):
        raise SubmissionRepairError("repair provider-effect integrity counters differ")
    if (
        effects.get("state") != "healthy"
        or effects.get("zero_effect_state") != "healthy"
        or any(
            type(effect_counts.get(key)) is not int or effect_counts[key] != 0
            for key in ("provider_called_true", "provider_called_unknown")
        )
    ):
        raise SubmissionRepairError("repair requires complete zero-effect accounting")


async def audit_submission_repair(
    event_log: EventLog,
    manifest: SubmissionRepairManifest,
    *,
    repository_root: Path,
    through_event_id: str | None = None,
) -> dict[str, Any]:
    watermark = through_event_id or await event_log.latest_event_id()
    if not watermark:
        raise SubmissionRepairError("repair event watermark is absent")
    tenants = set()
    for target in manifest.targets:
        source = await event_log.get(target.source_event_id)
        if source is None or str(source["event_id"]) > watermark:
            raise SubmissionRepairError(
                "repair source is outside the audited watermark"
            )
        tenants.add(source["tenant_id"])
        await validate_submission_recovery_target(
            event_log, target, tenant_id=source["tenant_id"], through_event_id=watermark
        )
    if len(tenants) != 1:
        raise SubmissionRepairError("repair cannot cross tenants")
    assurance = await audit_source_assurance(
        event_log,
        repository_root=repository_root,
        capabilities={},
        through_event_id=watermark,
    )
    validate_submission_repair_audit(manifest, assurance)
    return {
        "status": "repair_pending",
        "binding": repair_binding(manifest),
        "watermark_event_id": watermark,
        "counts": assurance["counts"],
        "provider_effects": assurance["provider_effects"],
        "content_exposed": False,
    }


async def validate_submission_repair_sources(
    manifest: SubmissionRepairManifest,
    event_log: EventLog,
    *,
    through_event_id: str | None = None,
) -> None:
    watermark = through_event_id or await event_log.latest_event_id()
    tenants = set()
    for target in manifest.targets:
        source = await event_log.get(target.source_event_id)
        if source is None or watermark is None or str(source["event_id"]) > watermark:
            raise SubmissionRepairError("repair source watermark is invalid")
        tenants.add(source["tenant_id"])
        await validate_submission_recovery_target(
            event_log, target, tenant_id=source["tenant_id"], through_event_id=watermark
        )
    if len(tenants) != 1:
        raise SubmissionRepairError("repair cannot cross tenants")


def validate_submission_repair_diagnostics(
    manifest: SubmissionRepairManifest,
    diagnostics: dict[str, Any],
    *,
    observation_active: bool,
    doctor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _validate_operational_diagnostics(
        manifest, diagnostics, observation_active=observation_active, doctor=doctor
    )


def _validate_operational_diagnostics(
    manifest: SubmissionRepairManifest,
    diagnostics: dict[str, Any],
    *,
    observation_active: bool,
    doctor: dict[str, Any] | None = None,
    rollback: bool = False,
) -> dict[str, Any]:
    if (
        diagnostics.get("observation_active") is not observation_active
        or (
            diagnostics.get("ready") is not False
            and not (rollback and diagnostics.get("ready") is True)
        )
        or any(
            diagnostics.get(key) is not True
            for key in ("service_ready", "safe_reads", "event_ingest_ready")
        )
        or diagnostics.get("startup_error") is not None
        or diagnostics.get("write_admission")
        not in ({"blocked", "observation_suppressed"} if rollback else {"blocked"})
    ):
        raise SubmissionRepairError(
            "repair requires its exact degraded observation state"
        )
    capabilities = diagnostics.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        raise SubmissionRepairError("repair capability inventory is absent")
    permitted = {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
    for key, capability in capabilities.items():
        if not isinstance(capability, dict) or (
            capability.get("state") not in {"healthy", "observation_only", "disabled"}
            and not (key in permitted and capability.get("state") == "degraded")
        ):
            raise SubmissionRepairError("repair has unrelated capability degradation")
        if (
            capability.get("kind") in {"projection", "reactive_pipeline"}
            and capability.get("state") != "disabled"
        ):
            details = capability.get("details") or {}
            if (
                details.get("lag_count") != 0
                or details.get("poison_event_id")
                or details.get("consecutive_failures", 0) != 0
            ):
                raise SubmissionRepairError("repair consumers are not settled")
    from bootstrap.recovery_readiness import recovery_consumers_settled
    from bootstrap.contracts import PROJECTION_NAMES
    from bootstrap.doctor import _required_reactive_pipeline_ids

    expected_consumers = {f"projection:{name}" for name in PROJECTION_NAMES} | {
        f"pipeline:{name}"
        for name in _required_reactive_pipeline_ids(Path(__file__).resolve().parents[1])
    }
    if not expected_consumers <= set(capabilities):
        raise SubmissionRepairError("repair required consumer inventory is incomplete")

    if not recovery_consumers_settled(diagnostics):
        raise SubmissionRepairError(
            "repair requires complete settled consumer inventory"
        )
    source = capabilities.get("policy:source-assurance") or {}
    assurance = source.get("details") or {}
    if rollback:
        _validate_lineage_audit(manifest, assurance)
    else:
        validate_submission_repair_audit(manifest, assurance)
    freshness = assurance.get("freshness") or {}
    if source.get("state") not in (
        {"healthy", "degraded"} if rollback else {"degraded"}
    ) or not (
        freshness.get("state") == "fresh"
        and freshness.get("revision_current") is True
        and freshness.get("settled_revision") is True
        and freshness.get("pending_recheck_required") is False
        and freshness.get("refresh_in_progress") is False
        and freshness.get("refresh_error_type") is None
    ):
        raise SubmissionRepairError("repair audit is not fresh and settled")
    readiness = diagnostics.get("deployment_readiness") or {}
    if readiness.get("contract") != "deployment-readiness-v1" or set(
        readiness.get("blocking_dimensions") or []
    ) not in (
        [set(), {"observation_effects"}] if rollback else [{"observation_effects"}]
    ):
        raise SubmissionRepairError("repair readiness has unrelated blockers")
    dimensions = readiness.get("dimensions") or {}
    if not REPAIR_READINESS_DIMENSIONS <= set(dimensions) or any(
        not isinstance(value, dict)
        or (
            value.get("state") != "healthy"
            and not (
                value.get("required") is False
                and value.get("state") == "not_applicable"
            )
        )
        for key, value in dimensions.items()
        if key != "observation_effects"
    ):
        raise SubmissionRepairError("repair readiness inventory is incomplete")
    if doctor is not None:
        checks = doctor.get("checks")
        if (
            not isinstance(checks, list)
            or any(
                not isinstance(check, dict)
                or type(check.get("ok")) is not bool
                or not isinstance(check.get("name"), str)
                for check in checks
            )
            or len({check["name"] for check in checks}) != len(checks)
            or not REPAIR_DOCTOR_CHECKS <= {check["name"] for check in checks}
        ):
            raise SubmissionRepairError(
                "repair Doctor inventory is absent or incomplete"
            )
        failures = {
            check.get("name") for check in checks if check.get("ok") is not True
        }
        if (not failures and not rollback) or not failures <= {
            "source_assurance_terminal_dispositions",
            "launchd_and_process_state",
            "deployment_topology_and_recovery_readiness",
        }:
            raise SubmissionRepairError("repair Doctor has unrelated failures")
    return {
        "status": "contained_degraded" if observation_active else "repair_pending",
        "binding": repair_binding(manifest),
        "counts": assurance["counts"],
        "watermark_event_id": assurance["watermark_event_id"],
        "content_exposed": False,
    }


def offline_submission_repair(
    paths: DeploymentPaths,
    manifest: SubmissionRepairManifest,
    *,
    repository_root: Path,
    candidate_commit: str,
    append_requests: bool = False,
    test_mode: bool = False,
    allow_recovery_lineage: bool = False,
) -> dict[str, Any]:
    if manifest.candidate_commit != candidate_commit:
        raise SubmissionRepairError("stopped repair candidate differs")
    return asyncio.run(
        _offline(
            paths,
            manifest,
            repository_root,
            append_requests,
            test_mode,
            allow_recovery_lineage,
        )
    )


async def _offline(
    paths: DeploymentPaths,
    manifest: SubmissionRepairManifest,
    repository_root: Path,
    append_requests: bool,
    test_mode: bool,
    allow_recovery_lineage: bool,
) -> dict[str, Any]:
    config = paths.instance_dir / "config"
    members = json.loads((config / "member-mapping.json").read_bytes())["members"]
    if not any(
        m.get("member_id") == manifest.owner_member_id and m.get("role") == "principal"
        for m in members
    ):
        raise SubmissionRepairError("repair owner is not an enrolled principal")
    known_members = {m["member_id"] for m in members}
    if any(t.member_id not in known_members for t in manifest.targets):
        raise SubmissionRepairError("repair target is not an enrolled member")
    references = json.loads((config / "secret-references.json").read_bytes())[
        "references"
    ]
    instance = load_instance_config(paths.instance_dir)
    log = EventLog(
        instance,
        secret_store(paths, test_mode=test_mode).get(references["event_log"]),
        read_only=not append_requests,
    )
    try:
        if allow_recovery_lineage:
            if append_requests:
                raise SubmissionRepairError(
                    "rollback cannot request another extraction"
                )
            proof = await audit_submission_repair_lineage(
                log, manifest, repository_root=repository_root
            )
        else:
            proof = await audit_submission_repair(
                log, manifest, repository_root=repository_root
            )
        if append_requests:
            events = await build_submission_recovery_requests(
                log,
                request_id=f"guarded-submission-repair:{manifest.digest()}",
                targets=list(manifest.targets),
                tenant_id=instance.tenant_id,
                operator_principal_id=manifest.operator_principal_id,
                owner_authorization_sha256=manifest.owner_authorization_sha256,
                owner_member_id=manifest.owner_member_id,
                candidate_commit=manifest.candidate_commit,
            )
            proof["recovery_request_event_ids"] = await log.append_batch(events)
            proof["recovery_request_count"] = len(events)
        return proof
    finally:
        await log.close()


def wait_for_submission_repair(
    manifest: SubmissionRepairManifest,
    *,
    diagnostics: Callable[[], dict[str, Any] | None],
    doctor: Callable[[], dict[str, Any]],
    deadline: float,
    rollback: bool = False,
) -> dict[str, Any]:
    """Require two settled exact-baseline observations around full Doctor."""

    def validate(
        value: dict[str, Any], report: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if rollback:
            return validate_submission_repair_rollback_diagnostics(
                manifest, value, doctor=report
            )
        return validate_submission_repair_diagnostics(
            manifest, value, observation_active=True, doctor=report
        )

    full_budget = (
        RELEASE_DOCTOR_TIMEOUT_SECONDS + 2 * RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS
    )
    while deployment_clock() + full_budget <= deadline:
        try:
            validate(diagnostics() or {})
        except SubmissionRepairError:
            time.sleep(min(2.0, max(0.0, deadline - deployment_clock())))
            continue
        if (
            deployment_clock()
            + RELEASE_DOCTOR_TIMEOUT_SECONDS
            + RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS
            > deadline
        ):
            break
        report = doctor()
        if deployment_clock() + RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS > deadline:
            break
        if not any(
            row.get("name") == "canonical_synthetic_round_trip"
            and row.get("ok") is True
            for row in report.get("checks", [])
        ):
            raise SubmissionRepairError(
                "repair containment requires the full Doctor proof"
            )
        proof = validate(diagnostics() or {}, report)
        if deployment_clock() >= deadline:
            break
        return {**report, "status": "contained_degraded", "submission_repair": proof}
    raise SubmissionRepairError(
        "exact repair baseline did not settle within the deadline"
    )


async def audit_submission_repair_lineage(
    event_log: EventLog,
    manifest: SubmissionRepairManifest,
    *,
    repository_root: Path,
) -> dict[str, Any]:
    """Explain a rolled-back predecessor without erasing candidate facts."""
    from adminme.submission_recovery import (
        validate_submission_recovery_lineage,
        recovery_publication_complete,
    )

    watermark = await event_log.latest_event_id()
    outcomes = []
    tenants = set()
    for target in manifest.targets:
        source = await event_log.get(target.source_event_id)
        if source is None:
            raise SubmissionRepairError("rollback source is absent")
        tenants.add(source["tenant_id"])
        outcomes.append(
            await validate_submission_recovery_lineage(
                event_log,
                target,
                tenant_id=source["tenant_id"],
                request_id=f"guarded-submission-repair:{manifest.digest()}",
                candidate_commit=manifest.candidate_commit,
                owner_member_id=manifest.owner_member_id,
                operator_principal_id=manifest.operator_principal_id,
                owner_authorization_sha256=manifest.owner_authorization_sha256,
                through_event_id=watermark,
            )
        )
    if len(tenants) != 1:
        raise SubmissionRepairError("rollback repair cannot cross tenants")
    assurance = await audit_source_assurance(
        event_log,
        repository_root=repository_root,
        capabilities={},
        through_event_id=watermark,
    )
    _validate_lineage_audit(manifest, assurance)
    requests = [
        value["request"] for value in outcomes if value.get("request") is not None
    ]
    if requests and len(requests) != len(manifest.targets):
        raise SubmissionRepairError("rollback repair batch is incomplete")
    pending = 0
    for value in outcomes:
        if value.get("request") is not None and value.get("refusal") is None:
            feature = value.get("feature")
            if feature is None or not await recovery_publication_complete(
                event_log, value["source"], feature, through_event_id=watermark
            ):
                pending += 1
    recovery_counts = (assurance.get("submission_recovery") or {}).get("counts") or {}
    if recovery_counts.get("invalid") != 0 or recovery_counts.get("pending") != pending:
        raise SubmissionRepairError("rollback has unrelated recovery commands")
    return {
        "status": "contained_degraded",
        "recovery_request_event_ids": [value["event_id"] for value in requests],
        "recovery_pending_count": pending,
        "source_state": assurance["state"],
        "binding": repair_binding(manifest),
        "watermark_event_id": watermark,
        "counts": assurance["counts"],
        "provider_effects": assurance["provider_effects"],
        "target_count": len(outcomes),
        "content_exposed": False,
    }


def _validate_lineage_audit(
    manifest: SubmissionRepairManifest, assurance: dict[str, Any]
) -> None:
    from bootstrap.htr10_export import _INTEGRITY_ZERO_COUNTS

    counts = assurance.get("counts") or {}
    expected = {target.source_event_id for target in manifest.targets}
    if (
        assurance.get("contract") != "source-assurance-v1"
        or assurance.get("state") not in {"healthy", "degraded"}
        or assurance.get("content_exposed") is not False
        or any(
            type(counts.get(k)) is not int or counts[k] < 0
            for k in (
                "source_facts",
                "obligations",
                "accounted",
                "pending",
                "missing",
                "failed",
            )
        )
        or counts["source_facts"] < manifest.minimum_source_facts
        or counts["obligations"] < counts["source_facts"]
        or counts["accounted"] + counts["missing"] != counts["obligations"]
        or counts["pending"] != 0
    ):
        raise SubmissionRepairError("rollback source accounting is incomplete")
    for key in ("failed", "missing"):
        rows = assurance.get(key)
        if (
            not isinstance(rows, list)
            or len(rows) != counts[key]
            or any(
                not isinstance(row, dict)
                or row.get("event_id") not in expected
                or row.get("event_type") != "triage.intent.submitted"
                for row in rows
            )
        ):
            raise SubmissionRepairError("rollback has unrelated source failures")
        if len({row["event_id"] for row in rows}) != len(rows):
            raise SubmissionRepairError(
                "rollback source accounting duplicates a target"
            )
    effects = assurance.get("provider_effects") or {}
    effect_counts = effects.get("counts") or {}
    if (
        effects.get("state") != "healthy"
        or effects.get("zero_effect_state") != "healthy"
        or any(
            type(effect_counts.get(k)) is not int or effect_counts[k] != 0
            for k in (*_INTEGRITY_ZERO_COUNTS, "provider_called_true")
        )
    ):
        raise SubmissionRepairError(
            "rollback provider effects are not zero and complete"
        )


def validate_submission_repair_rollback_diagnostics(
    manifest: SubmissionRepairManifest,
    diagnostics: dict[str, Any],
    *,
    doctor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep old-runtime schema limitations visible after exact offline proof."""
    capabilities = diagnostics.get("capabilities") or {}
    source = (capabilities.get("policy:source-assurance") or {}).get("details") or {}
    _validate_lineage_audit(manifest, source)
    # Reuse operational/freshness checks without substituting source history.
    _validate_operational_diagnostics(
        manifest, diagnostics, observation_active=True, doctor=doctor, rollback=True
    )
    return {
        "status": "contained_degraded",
        "binding": repair_binding(manifest),
        "content_exposed": False,
    }


def submission_repair_wait_budget(
    manifest: SubmissionRepairManifest, repository_root: Path
) -> float:
    """One bounded extraction per target, at the production semaphore width."""
    from adminme.lib.skill_runner.pack_loader import load_pack
    from adminme.submission_recovery import MAX_CONCURRENCY

    pack = load_pack(repository_root / "packs/skills/extract_note_semantics")
    timeout = pack.skill_frontmatter.get("timeout_seconds")
    if type(timeout) is not int or not 0 < timeout <= 120:
        raise SubmissionRepairError(
            "submission repair skill deadline exceeds its reviewed bound"
        )
    waves = (len(manifest.targets) + MAX_CONCURRENCY - 1) // MAX_CONCURRENCY
    # The wrapper's outer/network deadline is the skill deadline plus two
    # seconds. The fixed margin covers startup and durable child publication.
    return float(60 + waves * (timeout + 2))


def validate_submission_repair_progress(diagnostics: dict[str, Any]) -> None:
    """Permit bounded checkpoint/refresh lag, never a new structural failure."""
    from bootstrap.service_settlement import has_new_structural_degradation
    from bootstrap.recovery_readiness import _recovery_consumers
    from bootstrap.contracts import PROJECTION_NAMES
    from bootstrap.doctor import _required_reactive_pipeline_ids

    if (
        any(
            diagnostics.get(key) is not True
            for key in (
                "service_ready",
                "safe_reads",
                "event_ingest_ready",
                "observation_active",
            )
        )
        or diagnostics.get("startup_error") is not None
        or diagnostics.get("write_admission")
        not in {"blocked", "observation_suppressed"}
        or has_new_structural_degradation(diagnostics)
        or _recovery_consumers(diagnostics) is None
    ):
        raise SubmissionRepairError(
            "pending repair runtime is unavailable or structurally degraded"
        )
    capabilities = diagnostics["capabilities"]
    expected = {f"projection:{name}" for name in PROJECTION_NAMES} | {
        f"pipeline:{name}"
        for name in _required_reactive_pipeline_ids(Path(__file__).resolve().parents[1])
    }
    if not expected <= set(capabilities):
        raise SubmissionRepairError("pending repair consumer inventory is incomplete")
    readiness = diagnostics["deployment_readiness"]
    dimensions = readiness.get("dimensions") or {}
    if not REPAIR_READINESS_DIMENSIONS <= set(dimensions):
        raise SubmissionRepairError("pending repair readiness inventory is incomplete")
    for key, value in dimensions.items():
        if not isinstance(value, dict) or (
            key not in {"observation_effects", "openclaw"}
            and value.get("state") != "healthy"
            and not (
                value.get("required") is False
                and value.get("state") == "not_applicable"
            )
        ):
            raise SubmissionRepairError("pending repair has an unrelated readiness gap")
    for key, value in capabilities.items():
        if not isinstance(value, dict) or value.get("state") not in {
            "healthy",
            "observation_only",
            "disabled",
            "degraded",
            "blocked",
        }:
            raise SubmissionRepairError("pending repair capability state is invalid")
    source = (capabilities.get("policy:source-assurance") or {}).get("details") or {}
    if (source.get("freshness") or {}).get("refresh_error_type") is not None:
        raise SubmissionRepairError("pending repair audit refresh failed")
