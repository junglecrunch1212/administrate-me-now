"""Bounded, append-only recovery for terminal source assessments.

The recovery control plane never accepts source text.  It binds an explicit
request to one immutable source event, the exact latest failed assessment, and
one reviewed transient reason.  The noise-filtering pipeline remains the only
component that reassesses the source through the pinned OpenClaw skill seam.
"""

from __future__ import annotations

import hashlib
import json
import re
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
_REQUIRED_LANGUAGE_FAILURE_REASONS = RETRYABLE_FAILURE_REASONS | frozenset(
    {
        "language_seam_SkillInputInvalid",
        "language_seam_SkillOutputInvalid",
        "language_seam_SkillSensitivityRefused",
        "language_seam_SkillScopeInsufficient",
        "language_seam_SkillHandlerError",
        "language_seam_OpenClawResponseMalformed",
        "language_seam_RuntimeError",
    }
)
_LEGACY_DEFAULT_REASONS = {
    "handler_raised": "language_seam_SkillHandlerError",
    "output_invalid": "language_seam_SkillOutputInvalid",
}
_CLASSIFIER_ID = "skill:classify_message_nature"
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


def _legacy_review_complete_candidate(event: dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    if (
        event.get("type") != "triage.intent.features_extracted"
        or payload.get("assessment_outcome")
        not in {"eligible_candidates", "no_candidate"}
        or payload.get("extraction_state") != "complete"
        or payload.get("recovery_request_event_id") is not None
    ):
        return False
    reasons = payload.get("reason_codes")
    if (
        not isinstance(reasons, list)
        or not reasons
        or not all(isinstance(reason, str) for reason in reasons)
        or {
            "candidate_policy_manual_only",
            "candidate_policy_quiet",
            "candidate_language_disabled",
        }.intersection(reasons)
    ):
        return False
    policies = [
        reason.removeprefix("candidate_policy_v1:")
        for reason in reasons
        if reason.startswith("candidate_policy_v1:")
    ]
    if len(policies) != 1:
        return False
    source, separator, mode = policies[0].rpartition(":")
    return bool(source and separator and mode == "review")


def legacy_required_language_failure_candidate(event: dict[str, Any]) -> bool:
    """Select contradictory metadata; changing disposition needs source proof."""
    return bool(
        _legacy_review_complete_candidate(event)
        and event["payload"]["reason_codes"][0] in _REQUIRED_LANGUAGE_FAILURE_REASONS
    )


def legacy_default_assessment_candidate(event: dict[str, Any]) -> bool:
    return bool(
        _legacy_review_complete_candidate(event)
        and event["payload"]["reason_codes"][0] == "language_assessment_completed"
    )


def legacy_default_failure_candidate(event: dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    return bool(
        event.get("type") == "skill.call.failed"
        and payload.get("skill_name") == _CLASSIFIER_ID
        and payload.get("skill_version") == "8.0.0"
        and payload.get("failure_class") in _LEGACY_DEFAULT_REASONS
    )


def _legacy_assessment_source_matches(
    event: dict[str, Any], source_event: dict[str, Any] | None
) -> bool:
    if source_event is None:
        return False
    source_id = event["payload"].get("source_event_id")
    owner = event.get("owner_scope")
    return bool(
        isinstance(source_id, str)
        and source_id
        and event.get("schema_version") == 3
        and event.get("idempotency_key") == f"triage:message-features:v23:{source_id}"
        and event.get("causation_id") == source_id
        and event.get("source_adapter") == "deterministic_candidate_eligibility"
        and event.get("source_account_id") == "pipeline"
        and isinstance(owner, str)
        and owner.startswith("private:")
        and owner.removeprefix("private:")
        and event.get("visibility_scope") == owner
        and event.get("sensitivity") in {"normal", "sensitive"}
        and isinstance(event.get("tenant_id"), str)
        and event["tenant_id"]
        and source_event.get("event_id") == source_id
        and source_event.get("type") in SUPPORTED_SOURCE_EVENT_TYPES
        and source_event.get("tenant_id") == event["tenant_id"]
        and source_event.get("owner_scope") == owner
        and source_event.get("visibility_scope") == owner
        and source_event.get("sensitivity") == event["sensitivity"]
        and (source_event.get("payload") or {}).get(
            "member_id", owner.removeprefix("private:")
        )
        == owner.removeprefix("private:")
    )


def assessment_outcome(
    event: dict[str, Any], *, source_event: dict[str, Any] | None = None
) -> str:
    """Normalize legacy and current terminal assessment payloads."""

    payload = event.get("payload") or {}
    outcome = str(payload.get("assessment_outcome") or "")
    # CO-008 §4 requires incomplete interpretation to remain visible even
    # when deterministic candidates exist. Preserve the legacy v23 bytes;
    # CO-017 still requires an explicit exact-source recovery command before
    # any new language attempt or append-only replacement assessment.
    if legacy_required_language_failure_candidate(
        event
    ) and _legacy_assessment_source_matches(event, source_event):
        return "failed"
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


async def resolved_assessment_disposition(
    event_log: EventLog,
    assessment: dict[str, Any],
    *,
    source_event: dict[str, Any],
) -> tuple[str, str]:
    """Resolve only exact legacy paired defaults; never create retry authority."""
    ordinary = (
        assessment_outcome(assessment, source_event=source_event),
        assessment_reason(assessment),
    )
    if (
        ordinary[0] == "failed"
        or not legacy_default_assessment_candidate(assessment)
        or not _legacy_assessment_source_matches(assessment, source_event)
    ):
        return ordinary
    source_id = str(source_event["event_id"])

    def matches_scope(record: dict[str, Any]) -> bool:
        return bool(
            record.get("causation_id") == source_id
            and record.get("source_adapter") == "skill_runner"
            and record.get("source_account_id") == "system"
            and all(
                record.get(field) == source_event.get(field)
                for field in (
                    "tenant_id",
                    "owner_scope",
                    "visibility_scope",
                    "sensitivity",
                )
            )
        )

    headers = await event_log.get_causal_event_headers(
        str(source_event["tenant_id"]),
        source_id,
        types=["skill.call.recorded", "skill.call.failed"],
        through=str(assessment["event_id"]),
    )
    recorded = [
        header
        for header in headers
        if header["type"] == "skill.call.recorded"
        and header.get("schema_version") == 2
        and matches_scope(header)
        and f":{_CLASSIFIER_ID}:" in str(header.get("idempotency_key") or "")
        and header["event_id"] < assessment["event_id"]
    ]
    if not recorded:
        return ordinary
    last = recorded[-1]
    match = re.fullmatch(
        r"skill:(message-nature:([0-9a-f]{64})(?::recovery-[12])?):skill:classify_message_nature:8\.0\.0",
        str(last.get("idempotency_key") or ""),
    )
    if match is None:
        return ordinary
    identity, fingerprint = match.groups()

    failures = [
        header
        for header in headers
        if header["type"] == "skill.call.failed"
        and header.get("schema_version") == 1
        and matches_scope(header)
        and header["event_id"] < last["event_id"]
        and header.get("idempotency_key")
        in {
            f"skill:failed:{identity}:{_CLASSIFIER_ID}:8.0.0:{failure_class}"
            for failure_class in _LEGACY_DEFAULT_REASONS
        }
    ]
    if not failures:
        return ordinary
    failure_reason = None
    for header in reversed(failures):
        failure = await event_log.get(str(header["event_id"]))
        if failure is None:
            raise RuntimeError("legacy classifier failure proof unavailable")
        if legacy_default_failure_candidate(failure):
            failure_class = failure["payload"]["failure_class"]
            if (
                failure.get("idempotency_key")
                == f"skill:failed:{identity}:{_CLASSIFIER_ID}:8.0.0:{failure_class}"
            ):
                failure_reason = _LEGACY_DEFAULT_REASONS[failure_class]
                break
    if failure_reason is None:
        return ordinary
    success = await event_log.get(str(last["event_id"]))
    if success is None:
        raise RuntimeError("legacy classifier receipt proof unavailable")
    payload = success.get("payload") or {}
    # Freeze the exact 8.0.0 wrapper default. A later clean result, suppressed
    # call, or merely similar output is not this historical failure proof.
    default = {
        "classification": "personal",
        "confidence": 0.0,
        "reasons": ["skill_failure_defensive_default"],
        "reply_signal": "unknown",
        "automated": False,
        "transactional_due": False,
        "security_code": False,
        "person_to_person": False,
        "deadline": None,
        "candidates": [],
    }
    if (
        payload.get("skill_name") != _CLASSIFIER_ID
        or payload.get("skill_version") != "8.0.0"
        or payload.get("provider") in {None, "", "suppressed"}
        or payload.get("outputs") != default
    ):
        return ordinary
    inputs = payload.get("inputs")
    if isinstance(inputs, dict) and set(inputs) == {"_spilled_to"}:
        # Health must not claim a known paired default was clean when its
        # exact input proof is outside the encrypted receipt. Do not follow
        # historical filesystem paths from a read-model audit.
        raise RuntimeError("legacy classifier input identity proof unavailable")
    if (
        not isinstance(inputs, dict)
        or inputs.get("candidate_mode") != "review"
        or inputs.get("candidate_language_enabled") is not True
        or inputs.get("candidate_scope") != "primary_thread"
        or inputs.get("assessment_contract") != "personal-obligation-assessment-v1"
    ):
        return ordinary
    source_payload = source_event.get("payload") or {}
    actual_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "tenant_id": source_event.get("tenant_id"),
                "owner_scope": source_event.get("owner_scope"),
                "visibility_scope": source_event.get("visibility_scope"),
                "sensitivity": source_event.get("sensitivity", "normal"),
                "source_adapter": source_event.get("source_adapter"),
                "source_account_id": source_event.get("source_account_id"),
                "event_type": source_event.get("type"),
                "source_at": source_payload.get("received_at")
                or source_payload.get("sent_at"),
                "native_message_id": source_payload.get("native_message_id")
                or source_payload.get("carrier_message_id")
                or source_payload.get("rfc_message_id"),
                "thread_id": source_payload.get("thread_id"),
                "inputs": inputs,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if actual_fingerprint != fingerprint:
        return ordinary
    return "failed", failure_reason


def recovery_assessment_idempotency_key(
    request_event_id: str, source_event_id: str
) -> str:
    """Return the one terminal-assessment identity for a recovery command."""

    return f"triage:message-features:recovery:{request_event_id}:{source_event_id}"


def recovery_refusal_idempotency_key(request_event_id: str) -> str:
    """Return the one terminal-refusal identity for a recovery command."""

    return f"source-assessment-recovery-refused:{request_event_id}"


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
    outcome, reason = await resolved_assessment_disposition(
        event_log, latest, source_event=source
    )
    if outcome != "failed":
        raise SourceAssessmentRecoveryRefused("assessment_not_failed")
    if reason != target.expected_failure_reason:
        raise SourceAssessmentRecoveryRefused("failure_reason_changed")
    # The explicit test revision owns a finite source budget. A separate
    # ordinary recovery request cannot renew its already-spent attempts.
    if latest.get("schema_version") == 5 or reason not in RETRYABLE_FAILURE_REASONS:
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
    "legacy_required_language_failure_candidate",
    "legacy_default_assessment_candidate",
    "legacy_default_failure_candidate",
    "latest_source_assessment",
    "recovery_assessment_idempotency_key",
    "recovery_batch_fingerprint",
    "recovery_refusal_idempotency_key",
    "resolved_assessment_disposition",
    "target_from_request_event",
    "validate_recovery_target",
]
