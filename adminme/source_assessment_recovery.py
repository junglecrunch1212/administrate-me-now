"""Bounded, append-only recovery for terminal source assessments.

The recovery control plane never accepts source text.  It binds an explicit
request to one immutable source event, the exact latest failed assessment, and
one reviewed transient reason.  The noise-filtering pipeline remains the only
component that reassesses the source through the pinned OpenClaw skill seam.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog


RECOVERY_POLICY = "source-assessment-recovery-v1"
RECOVERY_ATTEMPT_LIMIT = 3
RECOVERY_MAX_CONCURRENCY = 2
SUPPORTED_SOURCE_EVENT_TYPES = frozenset(
    {"messaging.received", "messaging.sent", "telephony.sms_received"}
)
RETRYABLE_FAILURE_REASONS = frozenset(
    {
        "language_seam_IdempotencyConflict",
        "language_seam_OpenClawTimeout",
        "language_seam_OpenClawUnreachable",
    }
)
RecoveryAuthority = Literal["member_principal", "guarded_release_exception"]
RecoveryRefusalReason = Literal[
    "observation_inactive",
    "source_not_found",
    "unsupported_source_type",
    "source_not_private",
    "source_privileged",
    "tenant_mismatch",
    "source_scope_mismatch",
    "request_scope_mismatch",
    "failed_assessment_not_current",
    "assessment_not_failed",
    "failure_reason_changed",
    "failure_not_retryable",
]


class SourceAssessmentRecoveryTarget(BaseModel):
    model_config = {"extra": "forbid"}

    source_event_id: str = Field(min_length=1, max_length=240)
    failed_assessment_event_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: str = Field(min_length=1, max_length=120)


class SourceAssessmentRecoveryBatch(BaseModel):
    """Principal request accepted by the trusted local runtime API."""

    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    targets: list[SourceAssessmentRecoveryTarget] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def require_unique_sources(self) -> "SourceAssessmentRecoveryBatch":
        source_ids = [target.source_event_id for target in self.targets]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(
                "recovery targets must contain unique source_event_id values"
            )
        return self


@dataclass(frozen=True, slots=True)
class ValidatedRecoveryTarget:
    source: dict[str, Any]
    failed_assessment: dict[str, Any]
    failure_reason: str
    owner_member_id: str


class SourceAssessmentRecoveryRefused(RuntimeError):
    """A content-free, stable recovery selection refusal."""

    code: RecoveryRefusalReason

    def __init__(self, code: RecoveryRefusalReason) -> None:
        self.code = code
        super().__init__(code)


def assessment_outcome(event: dict[str, Any]) -> str:
    """Normalize legacy and current terminal assessment payloads."""

    payload = event.get("payload") or {}
    outcome = str(payload.get("assessment_outcome") or "")
    if outcome in {"eligible_candidates", "no_candidate", "quarantined", "failed"}:
        return outcome
    extraction_state = str(payload.get("extraction_state") or "")
    if payload.get("candidates"):
        return "eligible_candidates"
    if extraction_state == "complete":
        return "no_candidate"
    return "failed"


def assessment_reason(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    reasons = payload.get("reason_codes") or ["assessment_failed"]
    return str(reasons[0])


async def latest_source_assessment(
    event_log: EventLog, source_event_id: str
) -> dict[str, Any] | None:
    """Return the newest valid terminal assessment for one immutable source."""

    related = await event_log.get_by_causation(source_event_id)
    assessments = [
        event
        for event in related
        if event.get("type") == "triage.intent.features_extracted"
        and str((event.get("payload") or {}).get("source_event_id") or "")
        == source_event_id
    ]
    return assessments[-1] if assessments else None


async def validate_recovery_target(
    event_log: EventLog,
    target: SourceAssessmentRecoveryTarget,
    *,
    required_owner_scope: str | None = None,
    required_tenant_id: str | None = None,
) -> ValidatedRecoveryTarget:
    """Revalidate privacy, source identity, latest failure, and retryability."""

    source = await event_log.get(target.source_event_id)
    if source is None:
        raise SourceAssessmentRecoveryRefused("source_not_found")
    source_tenant_id = str(source.get("tenant_id") or "")
    if required_tenant_id is not None and source_tenant_id != required_tenant_id:
        raise SourceAssessmentRecoveryRefused("tenant_mismatch")
    if source.get("type") not in SUPPORTED_SOURCE_EVENT_TYPES:
        raise SourceAssessmentRecoveryRefused("unsupported_source_type")
    owner_scope = str(source.get("owner_scope") or "")
    if not owner_scope.startswith("private:") or not owner_scope.removeprefix(
        "private:"
    ):
        raise SourceAssessmentRecoveryRefused("source_not_private")
    if source.get("sensitivity") == "privileged":
        raise SourceAssessmentRecoveryRefused("source_privileged")
    if required_owner_scope is not None and owner_scope != required_owner_scope:
        raise SourceAssessmentRecoveryRefused("request_scope_mismatch")
    owner_member_id = owner_scope.removeprefix("private:")
    payload_member_id = (source.get("payload") or {}).get("member_id")
    if payload_member_id is not None and str(payload_member_id) != owner_member_id:
        raise SourceAssessmentRecoveryRefused("source_scope_mismatch")

    latest = await latest_source_assessment(event_log, target.source_event_id)
    if latest is None or latest.get("event_id") != target.failed_assessment_event_id:
        raise SourceAssessmentRecoveryRefused("failed_assessment_not_current")
    if str(latest.get("tenant_id") or "") != source_tenant_id:
        raise SourceAssessmentRecoveryRefused("tenant_mismatch")
    if assessment_outcome(latest) != "failed":
        raise SourceAssessmentRecoveryRefused("assessment_not_failed")
    reason = assessment_reason(latest)
    if reason != target.expected_failure_reason:
        raise SourceAssessmentRecoveryRefused("failure_reason_changed")
    if reason not in RETRYABLE_FAILURE_REASONS:
        raise SourceAssessmentRecoveryRefused("failure_not_retryable")
    return ValidatedRecoveryTarget(
        source=source,
        failed_assessment=latest,
        failure_reason=reason,
        owner_member_id=owner_member_id,
    )


def recovery_batch_fingerprint(
    request_id: str, targets: list[SourceAssessmentRecoveryTarget]
) -> str:
    canonical = {
        "request_id": request_id,
        "targets": sorted(
            (target.model_dump(mode="json") for target in targets),
            key=lambda value: value["source_event_id"],
        ),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def build_recovery_request_envelopes(
    event_log: EventLog,
    batch: SourceAssessmentRecoveryBatch,
    *,
    tenant_id: str,
    requested_by_actor: str,
    authority: RecoveryAuthority,
    required_owner_scope: str | None = None,
) -> list[EventEnvelope]:
    """Validate a whole batch before returning any appendable request event."""

    validated = [
        await validate_recovery_target(
            event_log,
            target,
            required_owner_scope=required_owner_scope,
            required_tenant_id=tenant_id,
        )
        for target in batch.targets
    ]
    fingerprint = recovery_batch_fingerprint(batch.request_id, batch.targets)
    occurred_at = EventEnvelope.now_utc_iso()
    event_at_ms = int(time.time() * 1000)
    envelopes: list[EventEnvelope] = []
    for target, selected in zip(batch.targets, validated, strict=True):
        source = selected.source
        envelopes.append(
            EventEnvelope(
                event_at_ms=event_at_ms,
                tenant_id=tenant_id,
                type="source.assessment.recovery_requested",
                schema_version=1,
                occurred_at=occurred_at,
                source_adapter="source_assessment_recovery",
                source_account_id="operator",
                owner_scope=source["owner_scope"],
                visibility_scope=source["visibility_scope"],
                sensitivity="normal",
                actor_identity=requested_by_actor,
                idempotency_key=(
                    f"source-assessment-recovery:{batch.request_id}:"
                    f"{target.source_event_id}"
                ),
                payload={
                    "request_id": batch.request_id,
                    "batch_fingerprint": fingerprint,
                    "source_event_id": target.source_event_id,
                    "failed_assessment_event_id": target.failed_assessment_event_id,
                    "expected_failure_reason": target.expected_failure_reason,
                    "owner_member_id": selected.owner_member_id,
                    "requested_by_actor": requested_by_actor,
                    "authority": authority,
                    "policy": RECOVERY_POLICY,
                    "attempt_limit": RECOVERY_ATTEMPT_LIMIT,
                    "max_concurrency": RECOVERY_MAX_CONCURRENCY,
                },
            )
        )
    return envelopes


def target_from_request_event(event: dict[str, Any]) -> SourceAssessmentRecoveryTarget:
    payload = event.get("payload") or {}
    return SourceAssessmentRecoveryTarget(
        source_event_id=str(payload.get("source_event_id") or ""),
        failed_assessment_event_id=str(payload.get("failed_assessment_event_id") or ""),
        expected_failure_reason=str(payload.get("expected_failure_reason") or ""),
    )


__all__ = [
    "RECOVERY_ATTEMPT_LIMIT",
    "RECOVERY_MAX_CONCURRENCY",
    "RECOVERY_POLICY",
    "RETRYABLE_FAILURE_REASONS",
    "SUPPORTED_SOURCE_EVENT_TYPES",
    "RecoveryRefusalReason",
    "SourceAssessmentRecoveryBatch",
    "SourceAssessmentRecoveryRefused",
    "SourceAssessmentRecoveryTarget",
    "ValidatedRecoveryTarget",
    "assessment_outcome",
    "assessment_reason",
    "build_recovery_request_envelopes",
    "latest_source_assessment",
    "recovery_batch_fingerprint",
    "target_from_request_event",
    "validate_recovery_target",
]
