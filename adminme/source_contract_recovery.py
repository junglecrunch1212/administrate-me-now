"""Explicit recovery across a proven legacy/current assessment boundary.

This is not an automatic retry policy. A later legacy recovery can repair its
own classifier result without satisfying an already attempted current contract.
Only an exact, new member/controller command authorizes current execution.
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
from adminme.events.schemas.system import (
    SourceAssessmentRecoveryRequestedV1,
    SourceAssessmentContractRecoveryRefusedV1,
    SourceAssessmentContractRecoveryRequestedV1,
    SourceAssessmentContractRecoveryResultV1,
)
from adminme.source_assessment_recovery import (
    RECOVERY_ATTEMPT_LIMIT,
    RECOVERY_MAX_CONCURRENCY,
    RETRYABLE_FAILURE_REASONS,
    RecoveryAuthority,
    latest_source_assessment,
    recovery_assessment_idempotency_key,
    resolved_assessment_disposition,
)
from adminme.source_publication import (
    publication_completion_key,
    publication_completion_valid,
    publication_plan_key,
    source_assessment_identity_valid,
)


ASSESSMENT_CONTRACT = "personal-obligation-assessment-v1"
CONTRACT_RECOVERY_POLICY = "source-assessment-recovery-v2"
CONTRACT_REQUEST_TYPE = "source.assessment.contract_recovery_requested"
CONTRACT_REFUSAL_TYPE = "source.assessment.contract_recovery_refused"
CONTRACT_RESULT_TYPE = "source.assessment.contract_recovery_result"
CONTRACT_ATTEMPT_TYPE = "source.assessment.contract_recovery_attempt_started"
CURRENT_CLASSIFIER_VERSIONS = frozenset({"8.0.0", "8.0.1"})
_CLASSIFIER_ID = "skill:classify_message_nature"
_SCOPE_FIELDS = ("tenant_id", "owner_scope", "visibility_scope", "sensitivity")


class ContractRecoveryTarget(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1, max_length=240)
    expected_latest_assessment_event_id: str = Field(min_length=1, max_length=240)
    failed_assessment_event_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: Literal[
        "language_seam_OpenClawTimeout", "language_seam_OpenClawUnreachable"
    ]


class SourceAssessmentContractRecoveryBatch(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    required_assessment_contract: Literal["personal-obligation-assessment-v1"]
    targets: list[ContractRecoveryTarget] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def distinct_sources(self) -> "SourceAssessmentContractRecoveryBatch":
        if len({target.source_event_id for target in self.targets}) != len(
            self.targets
        ):
            raise ValueError("contract recovery targets must name distinct sources")
        return self


class ContractRecoveryRefused(RuntimeError):
    """Stable content-free refusal, never new retry authority."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AmbiguousContractAttempt(RuntimeError):
    """A reserved call has no terminal receipt; replay cannot spend it again."""


@dataclass(frozen=True)
class ContractRecoveryRequirement:
    source: dict[str, Any]
    latest_assessment: dict[str, Any]
    failed_assessment: dict[str, Any]
    failure_reason: str


def _same_scope(record: dict[str, Any], source: dict[str, Any]) -> bool:
    return all(record.get(field) == source.get(field) for field in _SCOPE_FIELDS)


def contract_result_key(request_id: str, source_id: str) -> str:
    return f"source-assessment-contract-result:{request_id}:{source_id}"


def contract_refusal_key(request_id: str) -> str:
    return f"source-assessment-contract-refused:{request_id}"


def contract_request_key(request_id: str, source_id: str) -> str:
    return f"source-assessment-contract-recovery:{request_id}:{source_id}"


def contract_batch_fingerprint(batch: SourceAssessmentContractRecoveryBatch) -> str:
    payload = batch.model_dump(mode="json")
    payload["targets"] = sorted(
        payload["targets"], key=lambda item: item["source_event_id"]
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _request_identity_valid(request: dict[str, Any], source: dict[str, Any]) -> bool:
    try:
        payload = SourceAssessmentContractRecoveryRequestedV1.model_validate(
            request.get("payload")
        )
    except (ValueError, TypeError):
        return False
    return bool(
        request.get("type") == CONTRACT_REQUEST_TYPE
        and request.get("schema_version") == 1
        and request.get("source_adapter") == "source_assessment_contract_recovery"
        and request.get("source_account_id") == "operator"
        and request.get("actor_identity") == payload.requested_by_actor
        and request.get("idempotency_key")
        == contract_request_key(payload.request_id, source["event_id"])
        and payload.source_event_id == source["event_id"]
        and payload.owner_member_id
        == str(source.get("owner_scope")).removeprefix("private:")
        and _same_scope(request, source)
    )


async def validate_contract_language_receipt(
    log: Any,
    source: dict[str, Any],
    request_id: str,
    receipt_id: str,
    version: str,
) -> None:
    skill = await log.get(receipt_id)
    key = str((skill or {}).get("idempotency_key") or "")
    match = re.fullmatch(
        rf"skill:(message-nature:recovery:{re.escape(request_id)}:[0-9a-f]{{64}}(?::recovery-[12])?):skill:classify_message_nature:{re.escape(version)}",
        key,
    )
    if (
        skill is None
        or match is None
        or version not in CURRENT_CLASSIFIER_VERSIONS
        or skill.get("type") != "skill.call.recorded"
        or skill.get("schema_version") != 2
        or skill.get("source_adapter") != "skill_runner"
        or skill.get("source_account_id") != "system"
        or skill.get("causation_id") != source["event_id"]
        or not _same_scope(skill, source)
        or (skill.get("payload") or {}).get("skill_name") != _CLASSIFIER_ID
        or (skill.get("payload") or {}).get("skill_version") != version
        or ((skill.get("payload") or {}).get("outputs") or {}).get(
            "assessment_contract"
        )
        != ASSESSMENT_CONTRACT
    ):
        raise RuntimeError("contract recovery language provenance is invalid")
    failure_prefix = f"skill:failed:{match.group(1)}:{_CLASSIFIER_ID}:{version}:"
    if any(
        event.get("type") == "skill.call.failed"
        and event.get("source_adapter") == "skill_runner"
        and event.get("source_account_id") == "system"
        and _same_scope(event, source)
        and str(event.get("idempotency_key") or "").startswith(failure_prefix)
        for event in await log.get_by_causation(source["event_id"])
    ):
        raise RuntimeError("contract recovery language result has a paired failure")


def deterministic_contract_terminal(assessment_payload: dict[str, Any]) -> bool:
    reasons = set(assessment_payload.get("reason_codes") or [])
    return bool(
        assessment_payload.get("assessment_outcome") == "no_candidate"
        and assessment_payload.get("extraction_state") == "complete"
        and not assessment_payload.get("candidates")
        and assessment_payload.get("extractor_id") == "thread-anchor-deferred-v1"
        and {
            "language_assessment_deferred_to_latest_thread_message",
            "no_eligible_candidate",
            "deferred_to_latest_thread_message",
        }
        <= reasons
    )


async def current_contract_result(
    log: Any, source: dict[str, Any], assessment: dict[str, Any]
) -> dict[str, Any] | None:
    """Validate the atomic contract receipt without treating a v1 result as v2."""
    request_id = (assessment.get("payload") or {}).get("recovery_request_event_id")
    if not isinstance(request_id, str):
        return None
    receipt = await log.get_by_idempotency(
        source["tenant_id"], contract_result_key(request_id, source["event_id"])
    )
    if receipt is None:
        return None
    request = await log.get(request_id)
    payload = receipt.get("payload") or {}
    SourceAssessmentContractRecoveryResultV1.model_validate(payload)
    request_payload = (request or {}).get("payload") or {}
    SourceAssessmentContractRecoveryRequestedV1.model_validate(request_payload)
    if (
        request is None
        or not _request_identity_valid(request, source)
        or request.get("type") != CONTRACT_REQUEST_TYPE
        or request.get("schema_version") != 1
        or request.get("source_adapter") != "source_assessment_contract_recovery"
        or request.get("source_account_id") != "operator"
        or request.get("actor_identity") != request_payload.get("requested_by_actor")
        or request.get("idempotency_key")
        != contract_request_key(
            str(request_payload.get("request_id")), source["event_id"]
        )
        or request_payload.get("source_event_id") != source["event_id"]
        or request_payload.get("owner_member_id")
        != str(source.get("owner_scope")).removeprefix("private:")
        or not _same_scope(request, source)
        or not source_assessment_identity_valid(source=source, assessment=assessment)
        or receipt.get("type") != CONTRACT_RESULT_TYPE
        or receipt.get("schema_version") != 1
        or receipt.get("source_adapter") != "deterministic_candidate_eligibility"
        or receipt.get("source_account_id") != "pipeline"
        or receipt.get("causation_id") != source["event_id"]
        or not _same_scope(receipt, source)
        or payload.get("source_event_id") != source["event_id"]
        or payload.get("request_event_id") != request_id
        or payload.get("assessment_idempotency_key")
        != assessment.get("idempotency_key")
        or payload.get("required_assessment_contract") != ASSESSMENT_CONTRACT
        or (request.get("payload") or {}).get("required_assessment_contract")
        != ASSESSMENT_CONTRACT
    ):
        raise RuntimeError("contract recovery result identity is invalid")
    assessment_payload = assessment.get("payload") or {}
    if not request_id < assessment["event_id"] < receipt[
        "event_id"
    ] or assessment_payload.get(
        "supersedes_assessment_event_id"
    ) != request_payload.get("expected_latest_assessment_event_id"):
        raise RuntimeError("contract recovery result lineage is invalid")
    if payload.get("completion_kind") == "language":
        if (
            assessment_payload.get("assessment_outcome")
            not in {"eligible_candidates", "no_candidate"}
            or assessment_payload.get("extraction_state") != "complete"
            or payload.get("completed_assessment_contract") != ASSESSMENT_CONTRACT
            or not request_id
            < str(payload.get("skill_call_event_id") or "")
            < assessment["event_id"]
        ):
            raise RuntimeError("contract recovery language provenance is invalid")
        await validate_contract_language_receipt(
            log,
            source,
            request_id,
            str(payload.get("skill_call_event_id") or ""),
            str(payload.get("classifier_version") or ""),
        )
    elif payload.get("completion_kind") == "failed":
        if (
            payload.get("completed_assessment_contract") is not None
            or assessment_payload.get("assessment_outcome") != "failed"
            or assessment_payload.get("extraction_state") != "unavailable"
            or payload.get("skill_call_event_id") is not None
        ):
            raise RuntimeError("failed contract recovery claims completion")
    elif payload.get("completion_kind") == "deterministic_terminal":
        if (
            not deterministic_contract_terminal(assessment_payload)
            or payload.get("completed_assessment_contract") != ASSESSMENT_CONTRACT
            or payload.get("skill_call_event_id") is not None
            or payload.get("classifier_version") is not None
        ):
            raise RuntimeError("deterministic contract recovery is not proven")
    else:
        raise RuntimeError("contract recovery completion is invalid")
    return receipt


async def contract_recovery_requirement(
    log: Any,
    source: dict[str, Any],
    latest: dict[str, Any],
) -> ContractRecoveryRequirement | None:
    """Prove only occupied-v23 failure -> later incompatible linked recovery.

    Ordinary historical successes and deterministic deferrals are unaffected.
    Scope is checked before choosing the last classifier receipt. Unknown or
    incomplete provenance never grants a new command's attempt budget.
    """
    if latest.get("schema_version") != 4 or not source_assessment_identity_valid(
        source=source, assessment=latest
    ):
        return None
    if await current_contract_result(log, source, latest) is not None:
        return None
    source_id = source["event_id"]
    base = await log.get_by_idempotency(
        source["tenant_id"], f"triage:message-features:v23:{source_id}"
    )
    if base is None or not source_assessment_identity_valid(
        source=source, assessment=base
    ):
        return None
    base_outcome, base_reason = await resolved_assessment_disposition(
        log, base, source_event=source
    )
    if base_outcome != "failed" or base_reason not in RETRYABLE_FAILURE_REASONS:
        return None
    cursor = latest
    seen: set[str] = set()
    latest_request: dict[str, Any] | None = None
    for _ in range(32):
        if cursor["event_id"] == base["event_id"]:
            break
        if cursor["event_id"] in seen or not source_assessment_identity_valid(
            source=source, assessment=cursor
        ):
            return None
        seen.add(cursor["event_id"])
        payload = cursor.get("payload") or {}
        request = await log.get(str(payload.get("recovery_request_event_id") or ""))
        prior = await log.get(str(payload.get("supersedes_assessment_event_id") or ""))
        try:
            legacy_request = SourceAssessmentRecoveryRequestedV1.model_validate(
                (request or {}).get("payload")
            )
        except (ValueError, TypeError):
            return None
        if (
            request is None
            or prior is None
            or request.get("type") != "source.assessment.recovery_requested"
            or request.get("source_adapter") != "source_assessment_recovery"
            or request.get("schema_version") != 1
            or request.get("source_account_id") != "operator"
            or request.get("actor_identity") != legacy_request.requested_by_actor
            or request.get("idempotency_key")
            != f"source-assessment-recovery:{legacy_request.request_id}:{source_id}"
            or legacy_request.owner_member_id
            != str(source.get("owner_scope")).removeprefix("private:")
            or not _same_scope(request, {**source, "sensitivity": "normal"})
            or (request.get("payload") or {}).get("source_event_id") != source_id
            or (request.get("payload") or {}).get("failed_assessment_event_id")
            != prior["event_id"]
            or not prior["event_id"] < request["event_id"] < cursor["event_id"]
        ):
            return None
        latest_request = latest_request or request
        cursor = prior
    else:
        return None
    if latest_request is None:
        return None
    related = await log.get_by_causation(source_id)
    classifier = [
        record
        for record in related
        if record.get("type") in {"skill.call.recorded", "skill.call.failed"}
        and record.get("source_adapter") == "skill_runner"
        and record.get("source_account_id") == "system"
        and _same_scope(record, source)
        and latest_request["event_id"] < record["event_id"] < latest["event_id"]
        and (record.get("payload") or {}).get("skill_name") == _CLASSIFIER_ID
    ]
    if not classifier:
        return None
    terminal = classifier[-1]
    version = str((terminal.get("payload") or {}).get("skill_version") or "")
    request_id = re.escape(str(latest_request["event_id"]))
    if version != "7.0.0" or not re.fullmatch(
        rf"skill:(?:failed:)?message-nature:recovery:{request_id}:[0-9a-f]{{64}}"
        rf"(?::recovery-[12])?:skill:classify_message_nature:7\.0\.0(?::[a-z_]+)?",
        str(terminal.get("idempotency_key") or ""),
    ):
        return None
    outcome, reason = await resolved_assessment_disposition(
        log, latest, source_event=source
    )
    if outcome == "failed":
        if (
            terminal.get("type") != "skill.call.failed"
            or terminal.get("schema_version") != 1
        ):
            return None
        failed, failure_reason = latest, reason
    elif outcome in {"eligible_candidates", "no_candidate"}:
        if (
            terminal.get("type") != "skill.call.recorded"
            or terminal.get("schema_version") != 2
        ):
            return None
        failed, failure_reason = base, base_reason
    else:
        return None
    return ContractRecoveryRequirement(source, latest, failed, failure_reason)


async def validate_contract_recovery_target(
    log: Any,
    target: ContractRecoveryTarget,
    *,
    required_tenant_id: str,
    required_owner_scope: str | None = None,
) -> ContractRecoveryRequirement:
    source = await log.get(target.source_event_id)
    if (
        source is None
        or source.get("tenant_id") != required_tenant_id
        or source.get("type")
        not in {"messaging.received", "messaging.sent", "telephony.sms_received"}
        or not str(source.get("owner_scope") or "").startswith("private:")
        or not str(source.get("owner_scope") or "").removeprefix("private:")
        or source.get("visibility_scope") != source.get("owner_scope")
        or source.get("sensitivity") not in {"normal", "sensitive"}
        or (
            required_owner_scope is not None
            and source.get("owner_scope") != required_owner_scope
        )
        or (source.get("payload") or {}).get(
            "member_id", str(source.get("owner_scope")).removeprefix("private:")
        )
        != str(source.get("owner_scope")).removeprefix("private:")
    ):
        raise ContractRecoveryRefused("source_scope_mismatch")
    latest = await latest_source_assessment(log, target.source_event_id)
    if (
        latest is None
        or latest["event_id"] != target.expected_latest_assessment_event_id
    ):
        raise ContractRecoveryRefused("assessment_not_current")
    requirement = await contract_recovery_requirement(log, source, latest)
    if requirement is None:
        raise ContractRecoveryRefused("contract_boundary_not_proven")
    if (
        requirement.failed_assessment["event_id"] != target.failed_assessment_event_id
        or requirement.failure_reason != target.expected_failure_reason
    ):
        raise ContractRecoveryRefused("failure_identity_changed")
    return requirement


async def build_contract_recovery_request_envelopes(
    log: Any,
    batch: SourceAssessmentContractRecoveryBatch,
    *,
    tenant_id: str,
    requested_by_actor: str,
    authority: RecoveryAuthority,
    required_owner_scope: str | None = None,
) -> list[EventEnvelope]:
    selected = [
        await validate_contract_recovery_target(
            log,
            target,
            required_tenant_id=tenant_id,
            required_owner_scope=required_owner_scope,
        )
        for target in batch.targets
    ]
    fingerprint = contract_batch_fingerprint(batch)
    return [
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=tenant_id,
            type=CONTRACT_REQUEST_TYPE,
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="source_assessment_contract_recovery",
            source_account_id="operator",
            owner_scope=item.source["owner_scope"],
            visibility_scope=item.source["visibility_scope"],
            sensitivity=item.source["sensitivity"],
            actor_identity=requested_by_actor,
            idempotency_key=contract_request_key(
                batch.request_id, target.source_event_id
            ),
            payload={
                **target.model_dump(mode="json"),
                "request_id": batch.request_id,
                "batch_fingerprint": fingerprint,
                "required_assessment_contract": batch.required_assessment_contract,
                "owner_member_id": item.source["owner_scope"].removeprefix("private:"),
                "requested_by_actor": requested_by_actor,
                "authority": authority,
                "policy": CONTRACT_RECOVERY_POLICY,
                "attempt_limit": RECOVERY_ATTEMPT_LIMIT,
                "max_concurrency": RECOVERY_MAX_CONCURRENCY,
            },
        )
        for target, item in zip(batch.targets, selected, strict=True)
    ]


async def contract_recovery_terminal(log: Any, request_event_id: str) -> dict[str, Any]:
    """Read an exact command's validated terminal without renewing its selection."""
    request = await log.get(request_event_id)
    if request is None or request.get("type") != CONTRACT_REQUEST_TYPE:
        raise ContractRecoveryRefused("request_scope_mismatch")
    payload = request.get("payload") or {}
    source_id = str(payload.get("source_event_id") or "")
    source = await log.get(source_id)
    if source is None or not _request_identity_valid(request, source):
        raise ContractRecoveryRefused("source_scope_mismatch")
    assessment = await log.get_by_idempotency(
        request["tenant_id"],
        recovery_assessment_idempotency_key(request_event_id, source_id),
    )
    refusal = await log.get_by_idempotency(
        request["tenant_id"], contract_refusal_key(request_event_id)
    )
    base = {
        "request_event_id": request_event_id,
        "source_event_id": source_id,
        "content_exposed": False,
    }
    if assessment is not None:
        receipt = await current_contract_result(log, source, assessment)
        if receipt is None:
            raise RuntimeError("contract recovery lost its atomic provenance receipt")
        failed = (assessment.get("payload") or {}).get("assessment_outcome") == "failed"
        expected_plan_key = receipt["payload"].get("publication_plan_idempotency_key")
        plan = await log.get_by_idempotency(
            request["tenant_id"], publication_plan_key(assessment["idempotency_key"])
        )
        publication_complete = expected_plan_key is None and plan is None
        if expected_plan_key is not None:
            if (
                expected_plan_key != publication_plan_key(assessment["idempotency_key"])
                or plan is None
            ):
                raise RuntimeError("contract recovery lost its atomic publication plan")
            completion = await log.get_by_idempotency(
                request["tenant_id"], publication_completion_key(plan["event_id"])
            )
            publication_complete = (
                completion is not None
                and publication_completion_valid(
                    source=source,
                    assessment=assessment,
                    plan=plan,
                    completion=completion,
                )
            )
            if completion is not None and not publication_complete:
                raise RuntimeError(
                    "contract recovery publication completion is invalid"
                )
        elif plan is not None:
            raise RuntimeError("contract recovery publication plan is not declared")
        return {
            **base,
            "status": "failed" if failed else "completed",
            "assessment_event_id": assessment["event_id"],
            "contract_receipt_event_id": receipt["event_id"],
            "completed_assessment_contract": receipt["payload"][
                "completed_assessment_contract"
            ],
            "publication_complete": publication_complete,
        }
    if refusal is not None:
        SourceAssessmentContractRecoveryRefusedV1.model_validate(refusal.get("payload"))
        if (
            refusal.get("type") != CONTRACT_REFUSAL_TYPE
            or refusal.get("schema_version") != 1
            or refusal.get("source_adapter") != "noise_filtering"
            or refusal.get("source_account_id") != "pipeline"
            or refusal.get("payload", {}).get("source_event_id") != source_id
            or refusal["event_id"] <= request_event_id
            or not _same_scope(refusal, source)
            or (refusal.get("payload") or {}).get("request_event_id")
            != request_event_id
            or refusal.get("causation_id") != request_event_id
        ):
            raise RuntimeError("contract recovery refusal identity is invalid")
        return {
            **base,
            "status": "refused",
            "refusal_event_id": refusal["event_id"],
            "reason": refusal["payload"]["reason"],
        }
    return {**base, "status": "pending"}
