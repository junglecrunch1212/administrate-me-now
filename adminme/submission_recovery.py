"""Explicit, bounded recovery of exact failed member-forwarded submissions.

Requests contain immutable identifiers and authorization digests, never source
text. They do not authorize provider effects or replay other source families.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from pydantic import BaseModel, Field

from adminme.events.envelope import EventEnvelope
from adminme.submission_receipts import (
    feature_receipt_valid,
    held_receipt_valid,
    same_source_scope,
)

RECOVERY_EVENT = "triage.intent.recovery_requested"
REFUSED_EVENT = "triage.intent.recovery_refused"
RECOVERY_POLICY = "member-forwarded-submission-recovery-v1"
MAX_ATTEMPTS = 3
MAX_CONCURRENCY = 2
RETRYABLE_REASONS = frozenset(
    {"language_seam_OpenClawTimeout", "language_seam_OpenClawUnreachable"}
)


class SubmissionRecoveryTarget(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    source_event_id: str = Field(min_length=1, max_length=240)
    failed_assessment_event_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: str = Field(min_length=1, max_length=120)


class SubmissionRecoveryRefused(ValueError):
    """Stable content-free refusal; no source payload enters diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def request_key(request_id: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{request_id}\0{source_id}".encode()).hexdigest()
    return f"submission-recovery:{digest}"


def feature_key(source_id: str, request_event_id: str) -> str:
    return f"triage-intent-recovery-features:{source_id}:{request_event_id}"


def _source_valid(source: dict[str, Any], tenant_id: str, member_id: str) -> bool:
    payload = source.get("payload") or {}
    adapter = source.get("source_adapter")
    return bool(
        source.get("type") == "triage.intent.submitted"
        and source.get("schema_version") == 2
        and source.get("tenant_id") == tenant_id
        and source.get("owner_scope") == f"private:{member_id}"
        and source.get("visibility_scope") == f"private:{member_id}"
        and source.get("sensitivity") in {"normal", "sensitive"}
        and payload.get("member_id") == member_id
        and payload.get("submission_kind") == "member_forwarded"
        and payload.get("evidence_complete") is True
        and payload.get("evidence_issues") == []
        and isinstance(payload.get("text"), str)
        and 0 < len(payload["text"]) <= 32_000
        and (
            adapter == "direct-intake"
            and source.get("actor_identity") is None
            or (
                adapter == "openclaw-submission"
                or adapter == f"member-{payload.get('source_surface')}"
            )
            and source.get("actor_identity") == member_id
        )
    )


def recovery_request_valid(source: dict[str, Any], request: dict[str, Any]) -> bool:
    payload = request.get("payload") or {}
    return bool(
        request.get("type") == RECOVERY_EVENT
        and request.get("schema_version") == 1
        and same_source_scope(source, request)
        and request.get("source_adapter") == "submission-recovery"
        and request.get("source_account_id") == "system"
        and request.get("actor_identity") == payload.get("operator_principal_id")
        and str(payload.get("operator_principal_id") or "").startswith("operator:")
        and request.get("causation_id") == source.get("event_id")
        and payload.get("source_event_id") == source.get("event_id")
        and payload.get("member_id") == (source.get("payload") or {}).get("member_id")
        and payload.get("recovery_policy") == RECOVERY_POLICY
        and payload.get("expected_failure_reason") in RETRYABLE_REASONS
        and payload.get("attempt_limit") == MAX_ATTEMPTS
        and type(payload.get("recovery_attempt")) is int
        and 1 <= payload["recovery_attempt"] <= MAX_ATTEMPTS
        and request.get("idempotency_key")
        == request_key(str(payload.get("request_id") or ""), str(source["event_id"]))
    )


async def _related(
    log: Any, source_id: str, through: str | None
) -> list[dict[str, Any]]:
    return [
        event
        for event in await log.get_by_causation(source_id)
        if through is None or str(event["event_id"]) <= through
    ]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def recovery_authorization_valid(runtime: Any, request: dict[str, Any]) -> bool:
    """Bind queued private commands to the updater's authenticated manifest."""
    binding = (
        runtime.settings.deployment_evidence.get("activation_evidence") or {}
    ).get("submission_repair") or {}
    payload = request["payload"]
    selected = (runtime.settings.host_topology.get("deployment_binding") or {}).get(
        "release_commit"
    )
    if (
        not selected
        or selected != payload.get("candidate_commit")
        or selected != binding.get("candidate_commit")
        or payload.get("request_id")
        != "guarded-submission-repair:" + str(binding.get("manifest_sha256") or "")
        or runtime.settings.member_roles.get(payload.get("owner_member_id"))
        != "principal"
    ):
        return False
    batch = []
    async for row in runtime.event_log.read_since(types=[RECOVERY_EVENT]):
        if (
            row.get("tenant_id") == request.get("tenant_id")
            and row["payload"].get("request_id") == payload["request_id"]
        ):
            source = await runtime.event_log.get(
                str(row["payload"].get("source_event_id") or "")
            )
            if (
                source is None
                or not recovery_request_valid(source, row)
                or any(
                    row["payload"].get(key) != payload.get(key)
                    for key in (
                        "operator_principal_id",
                        "owner_member_id",
                        "owner_authorization_sha256",
                        "candidate_commit",
                    )
                )
            ):
                return False
            batch.append(
                {
                    key: row["payload"][key]
                    for key in SubmissionRecoveryTarget.model_fields
                }
            )
    if (
        not 1 <= len(batch) <= 8
        or len(batch) != binding.get("target_count")
        or _digest(batch) != binding.get("targets_sha256")
    ):
        return False
    manifest = {
        "contract": "forwarded-submission-repair-v1",
        "predecessor_commit": binding.get("predecessor_commit"),
        "candidate_commit": selected,
        "operator_principal_id": payload["operator_principal_id"],
        "owner_member_id": payload["owner_member_id"],
        "owner_authorization_sha256": payload["owner_authorization_sha256"],
        "minimum_source_facts": binding.get("minimum_source_facts"),
        "targets": batch,
    }
    return _digest(manifest) == binding.get("manifest_sha256")


async def linked_recovery_feature_valid(
    log: Any, source: dict[str, Any], feature: dict[str, Any], *, depth: int = 0
) -> bool:
    """Validate the bounded immutable replacement chain without accepting a fork."""
    if feature.get("schema_version") == 3:
        return feature_receipt_valid(source, feature)
    if feature.get("schema_version") != 6 or depth >= MAX_ATTEMPTS:
        return False
    if not _source_valid(
        source,
        str(source.get("tenant_id") or ""),
        str((source.get("payload") or {}).get("member_id") or ""),
    ):
        return False
    payload = feature.get("payload") or {}
    request = await log.get(str(payload.get("recovery_request_event_id") or ""))
    previous = await log.get(str(payload.get("supersedes_assessment_event_id") or ""))
    if (
        request is None
        or previous is None
        or not recovery_request_valid(source, request)
    ):
        return False
    if not feature_receipt_valid(source, feature, recovery_request=request):
        return False
    request_payload = request["payload"]
    if not (
        str(previous["event_id"]) < str(request["event_id"]) < str(feature["event_id"])
        and request_payload.get("failed_assessment_event_id") == previous["event_id"]
        and request_payload.get("recovery_attempt") == depth_from_feature(previous) + 1
        and _failure_reason(previous) == request_payload.get("expected_failure_reason")
    ):
        return False
    earlier = [
        event
        for event in await _related(
            log, str(source["event_id"]), str(request["event_id"])
        )
        if event.get("type") == "triage.intent.features_extracted"
    ]
    if not earlier or earlier[-1]["event_id"] != previous["event_id"]:
        return False
    return await linked_recovery_feature_valid(log, source, previous, depth=depth + 1)


def depth_from_feature(feature: dict[str, Any]) -> int:
    return int((feature.get("payload") or {}).get("recovery_attempt") or 0)


def _failure_reason(feature: dict[str, Any]) -> str | None:
    payload = feature.get("payload") or {}
    reasons = payload.get("reason_codes") or []
    matches = [reason for reason in reasons if reason in RETRYABLE_REASONS]
    if (
        payload.get("assessment_outcome") != "failed"
        or payload.get("extraction_state") != "defensive_default"
        or payload.get("candidates") != []
        or len(matches) != 1
    ):
        return None
    return str(matches[0])


async def validate_submission_recovery_target(
    event_log: Any,
    target: SubmissionRecoveryTarget | dict[str, Any],
    *,
    tenant_id: str,
    through_event_id: str | None = None,
    request_event_id: str | None = None,
) -> dict[str, Any]:
    target = SubmissionRecoveryTarget.model_validate(target)
    source = await event_log.get(target.source_event_id)
    if source is None or not _source_valid(source, tenant_id, target.member_id):
        raise SubmissionRecoveryRefused("submission_identity_or_evidence_invalid")
    related = await _related(event_log, target.source_event_id, through_event_id)
    features = [
        e for e in related if e.get("type") == "triage.intent.features_extracted"
    ]
    failed = features[-1] if features else None
    if failed is None or failed["event_id"] != target.failed_assessment_event_id:
        raise SubmissionRecoveryRefused("failed_assessment_not_current")
    if not await linked_recovery_feature_valid(event_log, source, failed):
        raise SubmissionRecoveryRefused("failed_assessment_identity_invalid")
    if _failure_reason(failed) != target.expected_failure_reason:
        raise SubmissionRecoveryRefused("failure_not_retryable_or_changed")
    requests = [e for e in related if e.get("type") == RECOVERY_EVENT]
    if any(not recovery_request_valid(source, e) for e in requests):
        raise SubmissionRecoveryRefused("recovery_request_identity_invalid")
    prior_requests = [e for e in requests if e["event_id"] != request_event_id]
    if len(prior_requests) >= MAX_ATTEMPTS:
        raise SubmissionRecoveryRefused("recovery_attempt_limit")
    if any(str(e["event_id"]) > str(failed["event_id"]) for e in prior_requests):
        raise SubmissionRecoveryRefused("recovery_already_requested")
    if any(
        e.get("type")
        in {"triage.intent.publication_held", "triage.intent.interpretation_refused"}
        for e in related
    ):
        raise SubmissionRecoveryRefused("submission_already_held_or_refused")
    held = await event_log.get_by_idempotency(
        tenant_id, f"triage-intent-held:{target.source_event_id}"
    )
    if held is not None and (
        through_event_id is None or str(held["event_id"]) <= through_event_id
    ):
        raise SubmissionRecoveryRefused("submission_already_held_or_refused")
    intent_id = source["payload"]["intent_id"]
    async for event in event_log.read_since(
        owner_scope=source["owner_scope"],
        types=[
            "triage.item.proposed",
            "triage.item.enriched",
            "triage.item.managed",
            "triage.item.confirmed",
            "triage.item.dismissed",
        ],
    ):
        if through_event_id is not None and str(event["event_id"]) > through_event_id:
            continue
        payload = event.get("payload") or {}
        if event.get("tenant_id") == tenant_id and (
            payload.get("source_event_id") == target.source_event_id
            or payload.get("parent_intent_id") == intent_id
            or payload.get("proposal_id") == intent_id
        ):
            raise SubmissionRecoveryRefused("member_decision_or_proposal_preserved")
    return {
        "source": source,
        "failed_assessment": failed,
        "recovery_attempt": len(prior_requests) + 1,
    }


async def build_submission_recovery_requests(
    event_log: Any,
    *,
    request_id: str,
    targets: list[SubmissionRecoveryTarget | dict[str, Any]],
    tenant_id: str,
    operator_principal_id: str,
    owner_member_id: str,
    owner_authorization_sha256: str,
    candidate_commit: str,
) -> list[EventEnvelope]:
    from adminme.events.schemas.submission_recovery import SubmissionRecoveryRequestedV1

    if not 1 <= len(targets) <= 8:
        raise SubmissionRecoveryRefused("recovery_batch_limit")
    normalized = [SubmissionRecoveryTarget.model_validate(target) for target in targets]
    if len({target.source_event_id for target in normalized}) != len(normalized):
        raise SubmissionRecoveryRefused("duplicate_recovery_source")
    result = []
    watermark = await event_log.latest_event_id()
    for target in normalized:
        key = request_key(request_id, target.source_event_id)
        prior = await event_log.get_by_idempotency(tenant_id, key)
        identity = {
            **target.model_dump(),
            "request_id": request_id,
            "operator_principal_id": operator_principal_id,
            "owner_member_id": owner_member_id,
            "owner_authorization_sha256": owner_authorization_sha256,
            "candidate_commit": candidate_commit,
            "recovery_policy": RECOVERY_POLICY,
            "attempt_limit": MAX_ATTEMPTS,
        }
        if prior is not None:
            source = await event_log.get(target.source_event_id)
            if (
                source is None
                or not recovery_request_valid(source, prior)
                or any(prior["payload"].get(k) != v for k, v in identity.items())
            ):
                raise SubmissionRecoveryRefused("recovery_idempotency_conflict")
            result.append(
                EventEnvelope(
                    **{
                        k: v
                        for k, v in prior.items()
                        if k in EventEnvelope.model_fields
                    }
                )
            )
            continue
        validated = await validate_submission_recovery_target(
            event_log, target, tenant_id=tenant_id, through_event_id=watermark
        )
        source = validated["source"]
        payload = SubmissionRecoveryRequestedV1.model_validate(
            {
                **identity,
                "recovery_attempt": validated["recovery_attempt"],
                "requested_at": EventEnvelope.now_utc_iso(),
            }
        ).model_dump(mode="json")
        result.append(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=tenant_id,
                type=RECOVERY_EVENT,
                schema_version=1,
                occurred_at=payload["requested_at"],
                source_adapter="submission-recovery",
                source_account_id="system",
                owner_scope=source["owner_scope"],
                visibility_scope=source["visibility_scope"],
                sensitivity=source["sensitivity"],
                actor_identity=operator_principal_id,
                correlation_id=request_id,
                causation_id=target.source_event_id,
                idempotency_key=key,
                payload=payload,
            )
        )
    return result


async def audit_submission_recoveries(
    event_log: Any, *, through_event_id: str
) -> dict[str, Any]:
    """Content-free command completion; historical failures stay visible."""
    counts = {
        "requested": 0,
        "succeeded": 0,
        "failed": 0,
        "refused": 0,
        "pending": 0,
        "invalid": 0,
    }
    async for request in event_log.read_since(types=[RECOVERY_EVENT]):
        if str(request["event_id"]) > through_event_id:
            continue
        counts["requested"] += 1
        source = await event_log.get(
            str(request["payload"].get("source_event_id") or "")
        )
        if source is None or not recovery_request_valid(source, request):
            counts["invalid"] += 1
            continue
        features = [
            row
            for row in await _related(
                event_log, str(source["event_id"]), through_event_id
            )
            if row.get("type") == "triage.intent.features_extracted"
            and row["payload"].get("recovery_request_event_id") == request["event_id"]
        ]
        refusals = [
            row
            for row in await _related(
                event_log, str(request["event_id"]), through_event_id
            )
            if row.get("type") == REFUSED_EVENT
        ]
        if len(features) + len(refusals) > 1:
            counts["invalid"] += 1
        elif features:
            if not await linked_recovery_feature_valid(event_log, source, features[0]):
                counts["invalid"] += 1
            elif not await recovery_publication_complete(
                event_log, source, features[0], through_event_id=through_event_id
            ):
                counts["pending"] += 1
            else:
                counts[
                    "failed"
                    if features[0]["payload"].get("assessment_outcome") == "failed"
                    else "succeeded"
                ] += 1
        elif refusals:
            refusal = refusals[0]
            if not (
                same_source_scope(source, refusal)
                and refusal.get("schema_version") == 1
                and refusal.get("source_adapter") == "submission-interpreter"
                and refusal.get("source_account_id") == "system"
                and refusal.get("actor_identity") == "system:submission-interpreter"
                and refusal.get("idempotency_key")
                == f"submission-recovery-refused:{request['event_id']}"
                and refusal["payload"].get("source_event_id") == source["event_id"]
                and refusal["payload"].get("request_event_id") == request["event_id"]
            ):
                counts["invalid"] += 1
            else:
                counts["refused"] += 1
        else:
            counts["pending"] += 1
    return {
        "contract": RECOVERY_POLICY,
        "counts": counts,
        "attempt_limit": MAX_ATTEMPTS,
        "max_concurrency": MAX_CONCURRENCY,
        "content_exposed": False,
    }


async def recovery_publication_complete(
    event_log: Any,
    source: dict[str, Any],
    feature: dict[str, Any],
    *,
    through_event_id: str | None = None,
) -> bool:
    """Distinguish a completed assessment from interrupted child publication."""
    candidates = feature["payload"].get("candidates") or []
    if not candidates:
        return True
    member = source["payload"]["member_id"]
    intent_id = source["payload"]["intent_id"]
    async for decision in event_log.read_since(
        owner_scope=source["owner_scope"],
        types=[
            "triage.item.enriched",
            "triage.item.managed",
            "triage.item.confirmed",
            "triage.item.dismissed",
        ],
    ):
        if (
            (through_event_id is None or decision["event_id"] <= through_event_id)
            and same_source_scope(source, decision)
            and decision.get("actor_identity") == member
            and decision["event_id"] > source["event_id"]
            and decision["payload"].get("proposal_id") == intent_id
        ):
            return True
    held = await event_log.get_by_idempotency(
        source["tenant_id"], f"triage-intent-held:{source['event_id']}"
    )
    if held is not None and (
        through_event_id is None or held["event_id"] <= through_event_id
    ):
        request = await event_log.get(feature["payload"]["recovery_request_event_id"])
        if held_receipt_valid(source, held, feature, recovery_request=request):
            return True
    for candidate in candidates:
        fingerprint = _digest(candidate)
        seed = (
            f"{source['tenant_id']}|{intent_id}|{candidate['candidate_key']}|"
            f"{candidate['target_kind']}|{fingerprint}"
        )
        proposal_id = "proposal_" + hashlib.sha256(seed.encode()).hexdigest()[:28]
        proposal = await event_log.get_by_idempotency(
            source["tenant_id"], f"triage-intent-proposal:{proposal_id}"
        )
        if proposal is None or not (
            (through_event_id is None or proposal["event_id"] <= through_event_id)
            and proposal.get("type") == "triage.item.proposed"
            and proposal.get("schema_version") == 2
            and same_source_scope(source, proposal)
            and proposal.get("source_adapter") == "member-intent-hybrid-v2"
            and proposal.get("source_account_id") == member
            and proposal.get("actor_identity") == "pipeline:member-intent-hybrid-v2"
            and proposal.get("causation_id") == feature["event_id"]
            and proposal.get("raw_ref") == f"candidate-sha256:{fingerprint}"
            and all(
                proposal["payload"].get(key) == value
                for key, value in {
                    "proposal_id": proposal_id,
                    "source_event_id": source["event_id"],
                    "parent_intent_id": intent_id,
                    "extraction_event_id": feature["event_id"],
                    "owner_member_id": member,
                    "route": {"member_id": member},
                    "candidate_key": candidate["candidate_key"],
                    "target_kind": candidate["target_kind"],
                    "title": candidate["title"],
                    "candidate": candidate["fields"],
                    "confidence": candidate["confidence"],
                    "reasons": candidate["reasons"],
                }.items()
            )
        ):
            return False
    return True


async def validate_submission_recovery_lineage(
    event_log: Any,
    target: SubmissionRecoveryTarget | dict[str, Any],
    *,
    tenant_id: str,
    request_id: str,
    candidate_commit: str,
    owner_member_id: str,
    operator_principal_id: str,
    owner_authorization_sha256: str,
    through_event_id: str | None = None,
) -> dict[str, Any]:
    """Validate retained repair facts without erasing a completed or failed retry."""
    target = SubmissionRecoveryTarget.model_validate(target)
    source = await event_log.get(target.source_event_id)
    failed = await event_log.get(target.failed_assessment_event_id)
    if (
        source is None
        or failed is None
        or (
            through_event_id is not None
            and (
                str(source["event_id"]) > through_event_id
                or str(failed["event_id"]) > through_event_id
            )
        )
        or not _source_valid(source, tenant_id, target.member_id)
        or not await linked_recovery_feature_valid(event_log, source, failed)
        or _failure_reason(failed) != target.expected_failure_reason
    ):
        raise SubmissionRecoveryRefused("original_recovery_target_invalid")
    related = await _related(event_log, target.source_event_id, through_event_id)
    descendants = [
        row
        for row in related
        if str(row["event_id"]) > target.failed_assessment_event_id
    ]
    requests = [row for row in descendants if row.get("type") == RECOVERY_EVENT]
    if not requests:
        return await validate_submission_recovery_target(
            event_log, target, tenant_id=tenant_id, through_event_id=through_event_id
        )
    if len(requests) != 1:
        raise SubmissionRecoveryRefused("recovery_lineage_fork")
    request = requests[0]
    identity = {
        **target.model_dump(),
        "request_id": request_id,
        "candidate_commit": candidate_commit,
        "owner_member_id": owner_member_id,
        "operator_principal_id": operator_principal_id,
        "owner_authorization_sha256": owner_authorization_sha256,
    }
    if not recovery_request_valid(source, request) or any(
        request["payload"].get(k) != v for k, v in identity.items()
    ):
        raise SubmissionRecoveryRefused("recovery_lineage_identity_invalid")
    features = [
        row
        for row in descendants
        if row.get("type") == "triage.intent.features_extracted"
    ]
    children = await _related(event_log, str(request["event_id"]), through_event_id)
    refusals = [row for row in children if row.get("type") == REFUSED_EVENT]
    if len(features) + len(refusals) > 1 or any(
        row.get("type")
        not in {REFUSED_EVENT, "skill.call.recorded", "skill.call.failed"}
        for row in children
    ):
        raise SubmissionRecoveryRefused("recovery_lineage_terminal_invalid")
    if features and (
        features[0]["payload"].get("recovery_request_event_id") != request["event_id"]
        or not await linked_recovery_feature_valid(event_log, source, features[0])
    ):
        raise SubmissionRecoveryRefused("recovery_lineage_terminal_invalid")
    if refusals:
        refused = refusals[0]
        if not (
            same_source_scope(source, refused)
            and refused.get("schema_version") == 1
            and refused.get("source_adapter") == "submission-interpreter"
            and refused.get("source_account_id") == "system"
            and refused.get("actor_identity") == "system:submission-interpreter"
            and refused.get("idempotency_key")
            == f"submission-recovery-refused:{request['event_id']}"
            and refused["payload"].get("source_event_id") == source["event_id"]
            and refused["payload"].get("request_event_id") == request["event_id"]
        ):
            raise SubmissionRecoveryRefused("recovery_lineage_terminal_invalid")
    return {
        "source": source,
        "failed_assessment": failed,
        "request": request,
        "feature": features[0] if features else None,
        "refusal": refusals[0] if refusals else None,
        "recovery_attempt": request["payload"]["recovery_attempt"],
    }
