"""Synthetic immutable v23 failure and predecessor v22 recovery history.

The retained records use the old producer's schemas and request namespace.
Consumers and new recovery execution are exercised through production roots.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.source_assessment_recovery import (
    SourceAssessmentRecoveryBatch,
    SourceAssessmentRecoveryTarget,
    build_recovery_request_envelopes,
)


FAILURE_REASON = "language_seam_OpenClawTimeout"


def _envelope(settings: Any, member: str, **kwargs: Any) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=time.time_ns() // 1_000_000,
        tenant_id=settings.instance.tenant_id,
        occurred_at=EventEnvelope.now_utc_iso(),
        owner_scope=f"private:{member}",
        visibility_scope=f"private:{member}",
        sensitivity="normal",
        **kwargs,
    )


async def seed_lineage(
    settings: Any,
    *,
    legacy_outcome: Literal["failed", "no_candidate"] = "failed",
    index: int = 0,
    member: str | None = None,
) -> dict[str, str]:
    """Seed before startup, without executing a runtime or language call."""
    member = member or settings.fixture_member_id
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        source_id = await log.append(
            _envelope(
                settings,
                member,
                type="messaging.received",
                schema_version=3,
                source_adapter="fixture-contract-recovery",
                source_account_id=f"fixture-contract-account:{member}",
                actor_identity=member,
                idempotency_key=f"fixture-contract-source:{member}:{index}",
                payload={
                    "member_id": member,
                    "provider": "fixture",
                    "provider_account_id": f"fixture-contract-account:{member}",
                    "workspace": "personal",
                    "source_channel": "sms",
                    "native_message_id": f"fixture-contract-message:{index}",
                    "thread_id": f"fixture-contract-thread:{member}:{index}",
                    "from_identifier": "sender@example.invalid",
                    "to_identifier": "recipient@example.invalid",
                    "subject": "Synthetic preparation request",
                    "body_text": (
                        "[fixture:attention-controls:preparation:required:1]\n"
                        "Please complete the required preparation form for activity 1."
                    ),
                    "received_at": "2034-02-01T10:00:00Z",
                    "attachments": [],
                },
            )
        )

        async def skill_terminal(version: str, identity: str, failed: bool) -> str:
            common = {
                "skill_name": "skill:classify_message_nature",
                "skill_version": version,
            }
            payload = (
                {
                    **common,
                    "failure_class": "openclaw_timeout",
                    "error_detail": "Synthetic timeout",
                }
                if failed
                else {
                    **common,
                    "inputs": {"body_text": "Synthetic legacy classifier input"},
                    "outputs": {
                        "classification": "personal",
                        "confidence": 1.0,
                        "candidates": [],
                    },
                    "provider": "fixture-provider",
                    "duration_ms": 1,
                }
            )
            return await log.append(
                _envelope(
                    settings,
                    member,
                    type="skill.call.failed" if failed else "skill.call.recorded",
                    schema_version=1 if failed else 2,
                    source_adapter="skill_runner",
                    source_account_id="system",
                    idempotency_key=(
                        f"skill:failed:{identity}:skill:classify_message_nature:{version}:openclaw_timeout"
                        if failed
                        else f"skill:{identity}:skill:classify_message_nature:{version}"
                    ),
                    payload=payload,
                ),
                causation_id=source_id,
            )

        for slot in range(3):
            suffix = f":recovery-{slot}" if slot else ""
            await skill_terminal("8.0.1", f"message-nature:{'a' * 64}{suffix}", True)
        assessment_payload = {
            "intent_id": f"source:{source_id}",
            "source_event_id": source_id,
            "skill_call_event_id": None,
            "extractor_id": "message-candidate-eligibility-v1",
            "language": None,
            "candidates": [],
            "extraction_state": "unavailable",
            "extracted_at": EventEnvelope.now_utc_iso(),
            "assessment_outcome": "failed",
            "reason_codes": [FAILURE_REASON, "no_eligible_candidate"],
        }
        failed_id = await log.append(
            _envelope(
                settings,
                member,
                type="triage.intent.features_extracted",
                schema_version=3,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                idempotency_key=f"triage:message-features:v23:{source_id}",
                payload=assessment_payload,
            ),
            causation_id=source_id,
        )
        [request] = await build_recovery_request_envelopes(
            log,
            SourceAssessmentRecoveryBatch(
                request_id=f"fixture-legacy-recovery-{member}-{index}",
                targets=[
                    SourceAssessmentRecoveryTarget(
                        source_event_id=source_id,
                        failed_assessment_event_id=failed_id,
                        expected_failure_reason=FAILURE_REASON,
                    )
                ],
            ),
            tenant_id=settings.instance.tenant_id,
            requested_by_actor=member,
            authority="member_principal",
        )
        request_id = await log.append(request)
        for slot in range(3 if legacy_outcome == "failed" else 1):
            suffix = f":recovery-{slot}" if slot else ""
            await skill_terminal(
                "7.0.0",
                f"message-nature:recovery:{request_id}:{'b' * 64}{suffix}",
                legacy_outcome == "failed",
            )
        latest_id = await log.append(
            _envelope(
                settings,
                member,
                type="triage.intent.features_extracted",
                schema_version=4,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                idempotency_key=f"triage:message-features:recovery:{request_id}:{source_id}",
                payload={
                    **assessment_payload,
                    "assessment_outcome": legacy_outcome,
                    "extraction_state": "unavailable"
                    if legacy_outcome == "failed"
                    else "complete",
                    "reason_codes": [FAILURE_REASON, "no_eligible_candidate"]
                    if legacy_outcome == "failed"
                    else ["language_assessment_completed", "no_eligible_candidate"],
                    "supersedes_assessment_event_id": failed_id,
                    "recovery_request_event_id": request_id,
                    "recovery_reason": FAILURE_REASON,
                    "recovery_policy": "source-assessment-recovery-v1",
                    "recovery_attempt_limit": 3,
                },
            ),
            causation_id=source_id,
        )
        return {
            "source_event_id": source_id,
            "latest_assessment_event_id": latest_id,
            "failed_assessment_event_id": latest_id
            if legacy_outcome == "failed"
            else failed_id,
            "expected_failure_reason": FAILURE_REASON,
            "base_assessment_event_id": failed_id,
            "legacy_request_event_id": request_id,
        }
    finally:
        await log.close()


def target_payload(lineage: dict[str, str]) -> dict[str, str]:
    return {
        "source_event_id": lineage["source_event_id"],
        "expected_latest_assessment_event_id": lineage["latest_assessment_event_id"],
        "failed_assessment_event_id": lineage["failed_assessment_event_id"],
        "expected_failure_reason": lineage["expected_failure_reason"],
    }
