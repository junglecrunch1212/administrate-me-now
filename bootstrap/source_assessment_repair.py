"""Release-scoped validation and activation for one degraded source baseline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.log import EventLog
from adminme.source_assessment_recovery import (
    SourceAssessmentRecoveryBatch,
    SourceAssessmentRecoveryTarget,
    build_recovery_request_envelopes,
    latest_source_assessment,
)
from adminme.source_assurance import audit_source_assurance
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store


POLICY_RELATIVE_PATH = Path(
    "bootstrap/release-exceptions/pr-334-source-assessment-recovery.json"
)
_SOURCE_DERIVED_CAPABILITIES = frozenset(
    {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
)


class BaselineCounts(BaseModel):
    model_config = {"extra": "forbid"}

    obligations: int = Field(ge=1)
    accounted: int = Field(ge=1)
    pending: Literal[0]
    missing: Literal[0]
    failed: int = Field(ge=1, le=20)


class BaselineTarget(BaseModel):
    model_config = {"extra": "forbid"}

    source_event_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: Literal[
        "language_seam_OpenClawTimeout",
        "language_seam_OpenClawUnreachable",
    ]


class SourceAssessmentRepairPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    contract: Literal["source-assessment-degraded-baseline-v1"]
    required_active_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    observation_mode_required: Literal[True]
    expected_counts: BaselineCounts
    targets: list[BaselineTarget] = Field(min_length=1, max_length=20)
    expires_after_activation: Literal[True]


class SourceAssessmentRepairError(RuntimeError):
    pass


def _noise_filtering_checkpoint_token(paths: DeploymentPaths) -> str:
    """Bind a guarded request to the stopped consumer's read-only position.

    A failed candidate can roll back to v19, whose event filter does not include
    recovery requests. If a later message advances that older checkpoint past
    an unprocessed request, the next guarded attempt must append a new request
    beyond it instead of resolving to the skipped idempotent event.
    """

    checkpoint_db = paths.instance_dir / "events" / "bus_checkpoints.db"
    checkpoint = "absent"
    if checkpoint_db.is_file():
        try:
            connection = sqlite3.connect(
                f"file:{checkpoint_db}?mode=ro",
                uri=True,
            )
            try:
                row = connection.execute(
                    "SELECT checkpoint_event_id FROM bus_consumer_offsets "
                    "WHERE subscriber_id=?",
                    ("pipeline:pipeline:noise_filtering",),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SourceAssessmentRepairError(
                "noise-filtering recovery checkpoint is unreadable"
            ) from exc
        checkpoint = str(row[0] or "none") if row is not None else "none"
    return hashlib.sha256(checkpoint.encode("utf-8")).hexdigest()[:12]


def load_repair_policy(candidate: Path) -> SourceAssessmentRepairPolicy:
    path = candidate / POLICY_RELATIVE_PATH
    if not path.is_file():
        raise SourceAssessmentRepairError(
            "approved source-assessment repair policy is missing"
        )
    try:
        document = json.loads(path.read_text("utf-8"))
        policy = SourceAssessmentRepairPolicy.model_validate(document)
    except Exception as exc:
        raise SourceAssessmentRepairError(
            "approved source-assessment repair policy is invalid"
        ) from exc
    source_ids = [target.source_event_id for target in policy.targets]
    if len(source_ids) != len(set(source_ids)):
        raise SourceAssessmentRepairError("repair policy repeats a source event id")
    if policy.expected_counts.failed != len(policy.targets):
        raise SourceAssessmentRepairError(
            "repair policy failed count does not equal its exact target set"
        )
    if policy.expected_counts.accounted != policy.expected_counts.obligations:
        raise SourceAssessmentRepairError(
            "repair policy requires complete source accounting before recovery"
        )
    return policy


def _failed_doctor_checks(doctor: dict[str, Any]) -> set[str]:
    return {
        str(check.get("name"))
        for check in doctor.get("checks", [])
        if isinstance(check, dict) and not check.get("ok")
    }


def _assurance_details(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return (
        (diagnostics.get("capabilities") or {}).get("policy:source-assurance") or {}
    ).get("details") or {}


def validate_declared_baseline(
    policy: SourceAssessmentRepairPolicy,
    *,
    active_commit: str,
    doctor: dict[str, Any],
    diagnostics: dict[str, Any],
) -> None:
    """Fail unless the active machine differs from healthy in exactly one way."""

    if active_commit != policy.required_active_commit:
        raise SourceAssessmentRepairError(
            "active release does not match the declared repair predecessor"
        )
    if diagnostics.get("observation_active") is not True:
        raise SourceAssessmentRepairError("repair requires active observation mode")
    if _failed_doctor_checks(doctor) != {"source_assurance_terminal_dispositions"}:
        raise SourceAssessmentRepairError(
            "active Doctor differs from the declared source-only degradation"
        )
    capabilities = diagnostics.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        raise SourceAssessmentRepairError("runtime capabilities are unavailable")
    unexpected = sorted(
        capability_id
        for capability_id, capability in capabilities.items()
        if capability_id not in _SOURCE_DERIVED_CAPABILITIES
        and isinstance(capability, dict)
        and capability.get("state") in {"degraded", "blocked"}
    )
    if unexpected:
        raise SourceAssessmentRepairError(
            "unexpected capability degradation is present: " + ", ".join(unexpected)
        )
    details = _assurance_details(diagnostics)
    counts = details.get("counts") or {}
    expected = policy.expected_counts.model_dump(mode="json")
    if any(int(counts.get(name, -1)) != value for name, value in expected.items()):
        raise SourceAssessmentRepairError(
            "source-assurance counts differ from the declared repair baseline"
        )
    if details.get("content_exposed") is not False:
        raise SourceAssessmentRepairError("source diagnostics exposed content")
    actual_failures = {
        (str(item.get("event_id") or ""), str(item.get("reason") or ""))
        for item in details.get("failed") or []
        if isinstance(item, dict)
    }
    expected_failures = {
        (target.source_event_id, target.expected_failure_reason)
        for target in policy.targets
    }
    if actual_failures != expected_failures:
        raise SourceAssessmentRepairError(
            "failed source ids or reasons differ from the declared repair baseline"
        )


def _capability_snapshot(diagnostics: dict[str, Any]) -> dict[str, Any]:
    capabilities = diagnostics.get("capabilities") or {}
    return {
        str(key): value
        for key, value in capabilities.items()
        if str(key).startswith(("pipeline:", "projection:"))
    }


async def _offline_validate_and_append(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    candidate_commit: str,
    policy: SourceAssessmentRepairPolicy,
    diagnostics: dict[str, Any],
    append_requests: bool,
    test_mode: bool,
) -> dict[str, Any]:
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    event_key = secret_store(paths, test_mode=test_mode).get(references["event_log"])
    event_log = EventLog(paths.instance_dir / "events" / "events.db", event_key)
    try:
        audit = await audit_source_assurance(
            event_log,
            repository_root=repository_root,
            capabilities=_capability_snapshot(diagnostics),
        )
        synthetic_diagnostics = {
            "observation_active": True,
            "capabilities": {
                **(diagnostics.get("capabilities") or {}),
                "policy:source-assurance": {
                    "state": audit["state"],
                    "details": audit,
                },
            },
        }
        validate_declared_baseline(
            policy,
            active_commit=policy.required_active_commit,
            doctor={
                "checks": [
                    {
                        "name": "source_assurance_terminal_dispositions",
                        "ok": False,
                    }
                ]
            },
            diagnostics=synthetic_diagnostics,
        )
        targets: list[SourceAssessmentRecoveryTarget] = []
        for declared in policy.targets:
            assessment = await latest_source_assessment(
                event_log, declared.source_event_id
            )
            if assessment is None:
                raise SourceAssessmentRepairError(
                    "declared source has no terminal assessment"
                )
            targets.append(
                SourceAssessmentRecoveryTarget(
                    source_event_id=declared.source_event_id,
                    failed_assessment_event_id=str(assessment["event_id"]),
                    expected_failure_reason=declared.expected_failure_reason,
                )
            )
        if not append_requests:
            return {
                "contract": policy.contract,
                "validated_targets": len(targets),
                "content_exposed": False,
            }
        target_digest = hashlib.sha256(
            json.dumps(
                [target.model_dump(mode="json") for target in targets],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        checkpoint_token = _noise_filtering_checkpoint_token(paths)
        batch = SourceAssessmentRecoveryBatch(
            request_id=(
                f"guarded-release:{candidate_commit}:{target_digest}:{checkpoint_token}"
            ),
            targets=targets,
        )
        # A raw path does not carry a tenant hint; bind requests to the exact
        # tenant already present on their retained source events.
        tenant_ids = {
            str((await event_log.get(target.source_event_id) or {}).get("tenant_id"))
            for target in targets
        }
        if len(tenant_ids) != 1 or "None" in tenant_ids:
            raise SourceAssessmentRepairError(
                "declared recovery targets do not share one tenant"
            )
        tenant_id = next(iter(tenant_ids))
        envelopes = await build_recovery_request_envelopes(
            event_log,
            batch,
            tenant_id=tenant_id,
            requested_by_actor=f"guarded-updater:{candidate_commit}",
            authority="guarded_release_exception",
        )
        event_ids = await event_log.append_batch(
            envelopes,
            correlation_id=f"guarded-release:{candidate_commit}",
        )
        return {
            "contract": policy.contract,
            "request_event_ids": event_ids,
            "requested": len(event_ids),
            "content_exposed": False,
            "expires_after_activation": True,
        }
    finally:
        await event_log.close()


def offline_validate_repair_baseline(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    candidate_commit: str,
    policy: SourceAssessmentRepairPolicy,
    diagnostics: dict[str, Any],
    append_requests: bool,
    test_mode: bool,
) -> dict[str, Any]:
    return asyncio.run(
        _offline_validate_and_append(
            paths,
            repository_root=repository_root,
            candidate_commit=candidate_commit,
            policy=policy,
            diagnostics=diagnostics,
            append_requests=append_requests,
            test_mode=test_mode,
        )
    )


__all__ = [
    "POLICY_RELATIVE_PATH",
    "SourceAssessmentRepairError",
    "SourceAssessmentRepairPolicy",
    "load_repair_policy",
    "offline_validate_repair_baseline",
    "validate_declared_baseline",
]
