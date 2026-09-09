"""Exact content-free acknowledgement checks for private proposal publication.

Header-only inputs let startup skip completed draft payloads. The publishing
pipeline additionally checks the typed plan and exact draft count when it has
that payload in hand. None of these receipts grant new assessment authority.
"""

from __future__ import annotations

from typing import Any, Mapping

from adminme.events.schemas.triage import TriageSourcePublicationCompletedV1

PLAN_EVENT_TYPE = "triage.source_publication.planned"
COMPLETION_EVENT_TYPE = "triage.source_publication.completed"


def publication_plan_key(assessment_key: str) -> str:
    return f"triage:source-publication:v1:{assessment_key}"


def publication_completion_key(plan_id: str) -> str:
    return f"triage:source-publication-completed:v1:{plan_id}"


def source_assessment_identity_valid(
    *, source: Mapping[str, Any], assessment: Mapping[str, Any]
) -> bool:
    """A newer terminal receipt must belong to this exact private source."""
    source_id = str(source.get("event_id") or "")
    key = str(assessment.get("idempotency_key") or "")
    if assessment.get("schema_version") == 3:
        valid_key = key == f"triage:message-features:v23:{source_id}"
    elif assessment.get("schema_version") == 4:
        prefix, suffix = "triage:message-features:recovery:", f":{source_id}"
        request_id = key.removeprefix(prefix).removesuffix(suffix)
        valid_key = bool(request_id and key == f"{prefix}{request_id}{suffix}")
    else:
        return False
    payload = assessment.get("payload")
    if isinstance(payload, dict) and (
        payload.get("source_event_id") != source_id
        or (
            assessment.get("schema_version") == 4
            and (
                key
                != f"triage:message-features:recovery:{payload.get('recovery_request_event_id')}:{source_id}"
                or payload.get("recovery_policy") != "source-assessment-recovery-v1"
                or payload.get("recovery_attempt_limit") != 3
            )
        )
    ):
        return False
    return bool(
        source_id
        and valid_key
        and assessment.get("type") == "triage.intent.features_extracted"
        and assessment.get("causation_id") == source_id
        and assessment.get("source_adapter") == "deterministic_candidate_eligibility"
        and assessment.get("source_account_id") == "pipeline"
        and all(
            assessment.get(field) == source.get(field)
            for field in ("tenant_id", "owner_scope", "visibility_scope", "sensitivity")
        )
    )


def publication_completion_valid(
    *,
    plan: Mapping[str, Any],
    completion: Mapping[str, Any],
    assessment: Mapping[str, Any],
    source: Mapping[str, Any],
) -> bool:
    """Validate every identity without reading already-completed private drafts."""
    try:
        payload = TriageSourcePublicationCompletedV1.model_validate(
            completion.get("payload")
        )
    except (ValueError, TypeError):
        return False
    source_id = str(source.get("event_id") or "")
    owner = str(source.get("owner_scope") or "")
    assessment_key = str(assessment.get("idempotency_key") or "")
    if assessment.get("schema_version") == 3:
        expected_assessment_key = f"triage:message-features:v23:{source_id}"
    elif assessment.get("schema_version") == 4:
        prefix = "triage:message-features:recovery:"
        suffix = f":{source_id}"
        request_id = assessment_key.removeprefix(prefix).removesuffix(suffix)
        if not request_id or assessment_key != f"{prefix}{request_id}{suffix}":
            return False
        expected_assessment_key = assessment_key
    else:
        return False
    if (
        not source_id
        or not owner.startswith("private:")
        or not owner.removeprefix("private:")
        or source.get("visibility_scope") != owner
        or source.get("sensitivity") not in {"normal", "sensitive"}
        or source.get("type")
        not in {"messaging.received", "messaging.sent", "telephony.sms_received"}
        or not source.get("tenant_id")
        or assessment.get("type") != "triage.intent.features_extracted"
        or assessment_key != expected_assessment_key
        or plan.get("type") != PLAN_EVENT_TYPE
        or plan.get("schema_version") != 1
        or plan.get("idempotency_key") != publication_plan_key(assessment_key)
        or completion.get("type") != COMPLETION_EVENT_TYPE
        or completion.get("schema_version") != 1
        or completion.get("idempotency_key")
        != publication_completion_key(str(plan.get("event_id") or ""))
        or completion.get("causation_id") != plan.get("event_id")
        or assessment.get("causation_id") != source_id
        or plan.get("causation_id") != source_id
        or any(
            record.get(field) != source.get(field)
            for record in (plan, completion, assessment)
            for field in ("tenant_id", "owner_scope", "visibility_scope", "sensitivity")
        )
        or any(
            record.get("source_adapter") != "deterministic_candidate_eligibility"
            or record.get("source_account_id") != "pipeline"
            for record in (plan, completion, assessment)
        )
        or payload.plan_event_id != plan.get("event_id")
        or payload.source_event_id != source_id
        or payload.assessment_event_id != assessment.get("event_id")
    ):
        return False
    full_plan = plan.get("payload")
    if isinstance(full_plan, dict) and payload.planned_count != len(
        full_plan.get("proposals") or []
    ):
        return False
    return True
