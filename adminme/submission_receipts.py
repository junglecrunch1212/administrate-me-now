"""Exact private lineage checks for durable submission receipts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


REFUSAL_REASONS = frozenset(
    {
        "member_principal_not_active",
        "submission_identity_invalid",
        "retained_receipt_identity_invalid",
        "member_decision_preserved",
        "legacy_submission_evidence_incomplete",
    }
)


def refusal_key(source_id: str, conflicting_event_id: str | None = None) -> str:
    base = f"triage-intent-refused:{source_id}"
    if conflicting_event_id:
        return (
            base
            + ":collision:"
            + hashlib.sha256(conflicting_event_id.encode()).hexdigest()[:24]
        )
    return base


def same_source_scope(source: dict[str, Any], receipt: dict[str, Any]) -> bool:
    return all(
        receipt.get(key) == source.get(key)
        for key in (
            "tenant_id",
            "owner_scope",
            "visibility_scope",
            "sensitivity",
        )
    )


def submission_refusal_valid(source: dict[str, Any], receipt: dict[str, Any]) -> bool:
    payload = receipt.get("payload") or {}
    return bool(
        source.get("type") == "triage.intent.submitted"
        and receipt.get("type") == "triage.intent.interpretation_refused"
        and receipt.get("schema_version") == 1
        and same_source_scope(source, receipt)
        and receipt.get("source_adapter") == "submission-interpreter"
        and receipt.get("source_account_id") == "system"
        and receipt.get("actor_identity") == "system:submission-interpreter"
        and receipt.get("causation_id") == source.get("event_id")
        and payload.get("source_event_id") == source.get("event_id")
        and payload.get("reason") in REFUSAL_REASONS
        and receipt.get("idempotency_key")
        == refusal_key(
            str(source["event_id"]), payload.get("conflicting_receipt_event_id")
        )
    )


def feature_receipt_valid(
    source: dict[str, Any],
    feature: dict[str, Any],
    *,
    recovery_request: dict[str, Any] | None = None,
) -> bool:
    payload = feature.get("payload") or {}
    member = (source.get("payload") or {}).get("member_id")
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    expected_key = (
        f"triage-intent-features:{source['event_id']}:member-intent-hybrid-v2"
    )
    if recovery_request is not None:
        from adminme.submission_recovery import feature_key, recovery_request_valid

        request_payload = recovery_request.get("payload") or {}
        if not (
            recovery_request_valid(source, recovery_request)
            and feature.get("schema_version") == 6
            and payload.get("recovery_request_event_id")
            == recovery_request.get("event_id")
            and payload.get("supersedes_assessment_event_id")
            == request_payload.get("failed_assessment_event_id")
            and payload.get("recovery_policy") == request_payload.get("recovery_policy")
            and payload.get("recovery_reason")
            == request_payload.get("expected_failure_reason")
            and payload.get("recovery_attempt")
            == request_payload.get("recovery_attempt")
        ):
            return False
        expected_key = feature_key(
            str(source["event_id"]), str(recovery_request["event_id"])
        )
    elif feature.get("schema_version") != 3:
        return False
    if not (
        feature.get("type") == "triage.intent.features_extracted"
        and same_source_scope(source, feature)
        and feature.get("source_adapter") == "member-intent-hybrid-v2"
        and feature.get("source_account_id") == member
        and feature.get("actor_identity") == "pipeline:member-intent-hybrid-v2"
        and feature.get("causation_id") == source.get("event_id")
        and feature.get("idempotency_key") == expected_key
        and feature.get("raw_ref") == f"features-sha256:{fingerprint}"
        and payload.get("intent_id") == (source.get("payload") or {}).get("intent_id")
        and payload.get("source_event_id") == source.get("event_id")
        and payload.get("extractor_id") == "member-intent-hybrid-v2"
    ):
        return False
    text = str((source.get("payload") or {}).get("text") or "")
    for candidate in payload.get("candidates") or []:
        spans = candidate.get("evidence_spans") or []
        if not any(
            span.get("field") == "title"
            and isinstance(span.get("start"), int)
            and isinstance(span.get("end"), int)
            and 0 <= span["start"] < span["end"] <= len(text)
            and text[span["start"] : span["end"]].startswith(
                str(candidate.get("title") or "")
            )
            for span in spans
        ):
            return False
    return True


def held_receipt_valid(
    source: dict[str, Any],
    held: dict[str, Any],
    feature: dict[str, Any] | None,
    *,
    recovery_request: dict[str, Any] | None = None,
) -> bool:
    payload = held.get("payload") or {}
    return bool(
        feature is not None
        and feature_receipt_valid(source, feature, recovery_request=recovery_request)
        and held.get("type") == "triage.intent.publication_held"
        and held.get("schema_version") == 1
        and same_source_scope(source, held)
        and held.get("source_adapter") == "member-intent-hybrid-v2"
        and held.get("source_account_id")
        == (source.get("payload") or {}).get("member_id")
        and held.get("actor_identity") == "pipeline:member-intent-hybrid-v2"
        and held.get("idempotency_key") == f"triage-intent-held:{source['event_id']}"
        and held.get("causation_id") == feature.get("event_id")
        and payload.get("feature_event_id") == feature.get("event_id")
        and payload.get("source_event_id") == source.get("event_id")
        and payload.get("intent_id") == (source.get("payload") or {}).get("intent_id")
    )
