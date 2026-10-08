"""Durable, proposal-only interpretation of authenticated member submissions.

Capture adapters establish identity before appending a private submission. This
consumer holds no provider authority and never impersonates a member: background
work uses the existing internal device actor, with exact source scopes supplied
separately to the pinned skill seam. Recorded features and proposal identities
are reused across HTTP retries, bus redelivery, crashes, and projection replay.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from typing import Any, cast, get_args
from weakref import WeakValueDictionary

from fastapi import HTTPException

from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.schemas.triage import TriageFeatureCandidateV1, TriageSourceSurface
from adminme.lib.session import Session, build_internal_session
from adminme.lib.skill_runner import (
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillContext,
    SkillHandlerError,
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
)
from adminme.triage_intent import (
    IntentCandidate,
    extract_grounded_nlp_candidates,
    extract_intent_candidates,
    merge_intent_candidates,
)


class IncompleteSubmission(Exception):
    """Source coverage must be clarified before any candidate is proposed."""


class SubmissionContextRefused(HTTPException):
    """An HTTP refusal that durable delivery can acknowledge without retrying."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            403, {"code": "submission_context_unavailable", "reason": reason}
        )


def forwarded_date_issues(text: str) -> list[str]:
    """A forwarded relative date has no trustworthy original-time anchor."""
    if re.search(
        r"\b(?:today|tomorrow|tonight|yesterday|(?:this|next|last)\s+"
        r"(?:day|week|month|year|weekend)|(?:on|by|before|after|this|next|last)\s+"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
        text,
        flags=re.IGNORECASE,
    ):
        return ["forwarded_relative_date_requires_clarification"]
    return []


def calendar_submission_issues(text: str) -> list[str]:
    """Do not reinterpret ambiguous calendar updates as a fresh event."""
    if "BEGIN:VCALENDAR" not in text.upper():
        return []
    upper = text.upper()
    issues = []
    if re.search(r"^METHOD:CANCEL\s*$|^STATUS:CANCELLED\s*$", upper, re.MULTILINE):
        issues.append("calendar_cancellation_requires_review")
    if re.search(r"^(RECURRENCE-ID|RRULE)[;:]", upper, re.MULTILINE):
        issues.append("calendar_series_requires_review")
    for line in upper.splitlines():
        if (
            line.startswith(("DTSTART:", "DTEND:"))
            and "T" in line.split(":", 1)[-1]
            and not line.endswith("Z")
        ):
            issues.append("calendar_timezone_requires_clarification")
            break
    return issues


async def _guard_triage_write(
    runtime: Any, session: Session, action: str, payload: dict[str, Any]
) -> None:
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, action, payload)
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )


def _recorded_intent_candidates(
    feature_payload: dict[str, Any],
    *,
    deterministic_candidates: list[IntentCandidate],
) -> list[IntentCandidate]:
    """Rehydrate the one durable interpretation without invoking NLP again."""

    deterministic_by_key = {
        candidate.candidate_key: candidate for candidate in deterministic_candidates
    }
    recorded: list[IntentCandidate] = []
    for raw_candidate in feature_payload.get("candidates") or []:
        candidate = TriageFeatureCandidateV1.model_validate(raw_candidate)
        reasons = list(candidate.reasons)
        eligibility_rule_ids: list[str] = []
        deterministic = deterministic_by_key.get(candidate.candidate_key)
        if (
            deterministic is not None
            and deterministic.target_kind == candidate.target_kind
        ):
            eligibility_rule_ids.extend(deterministic.eligibility_rule_ids)
        if "exact_source_quote_grounded_v1" in reasons:
            eligibility_rule_ids.extend(
                [
                    "explicit_member_intent_v2",
                    "exact_source_quote_grounded_v1",
                    f"candidate_fields_{candidate.target_kind}_v1",
                ]
            )
        if not eligibility_rule_ids:
            eligibility_rule_ids.extend(
                [
                    "recorded_member_intent_feature_v1",
                    f"candidate_fields_{candidate.target_kind}_v1",
                ]
            )
        recorded.append(
            IntentCandidate(
                candidate_key=candidate.candidate_key,
                target_kind=candidate.target_kind,
                title=candidate.title,
                fields=dict(candidate.fields),
                evidence_spans=[
                    span.model_dump(mode="json") for span in candidate.evidence_spans
                ],
                confidence=candidate.confidence,
                reasons=reasons,
                eligibility_rule_ids=list(dict.fromkeys(eligibility_rule_ids))[:20],
            )
        )
    return recorded


class SubmissionInterpreter:
    CONSUMER_VERSION = "member-submission-interpreter-v2"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def handle(self, event: dict[str, Any]) -> None:
        if (
            event.get("type") != "triage.intent.submitted"
            or event.get("tenant_id") != self.runtime.settings.instance.tenant_id
        ):
            return
        # The historical direct-intake cue discarded evidence after 2,000
        # characters. Never silently treat that old partial cue as a full forward.
        if (
            event.get("source_adapter") == "direct-intake"
            and int(event.get("schema_version") or 1) < 2
        ):
            return
        # An in-flight member request owns its original caller's authorization.
        # Let it finish before the durable callback attempts recovery.
        payload = event["payload"]
        request_key = f"{event['tenant_id']}:{payload['member_id']}:{payload['client_request_id']}"
        request_lock = self.runtime.triage_intent_command_locks.get(request_key)
        if request_lock is not None:
            async with request_lock:
                pass
        held = await self.runtime.event_log.get_by_idempotency(
            str(event["tenant_id"]), f"triage-intent-held:{event['event_id']}"
        )
        if held is not None:
            return
        refused_key = f"triage-intent-refused:{event['event_id']}"
        if (
            await self.runtime.event_log.get_by_idempotency(
                str(event["tenant_id"]), refused_key
            )
            is not None
        ):
            return
        try:
            await self.interpret(event)
        except SubmissionContextRefused as exc:
            await self.runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=str(event["tenant_id"]),
                    type="triage.intent.interpretation_refused",
                    schema_version=1,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter="submission-interpreter",
                    source_account_id="system",
                    owner_scope=str(event["owner_scope"]),
                    visibility_scope=str(event["visibility_scope"]),
                    sensitivity=cast(Sensitivity, event.get("sensitivity") or "normal"),
                    actor_identity="system:submission-interpreter",
                    correlation_id=event.get("correlation_id"),
                    causation_id=str(event["event_id"]),
                    idempotency_key=refused_key,
                    payload={
                        "source_event_id": str(event["event_id"]),
                        "reason": exc.reason,
                        "consumer_version": self.CONSUMER_VERSION,
                        "refused_at": EventEnvelope.now_utc_iso(),
                    },
                )
            )
        except HTTPException:
            # A recorded governance refusal is terminal for background delivery.
            # The owner may explicitly retry after the condition is resolved.
            held = await self.runtime.event_log.get_by_idempotency(
                str(event["tenant_id"]), f"triage-intent-held:{event['event_id']}"
            )
            if held is None:
                raise

    async def interpret(
        self, event: dict[str, Any], *, session: Session | None = None
    ) -> dict[str, Any]:
        runtime = self.runtime
        payload = event["payload"]
        member_id = str(payload["member_id"])
        scope = f"private:{member_id}"
        source = str(event.get("source_adapter") or "")
        member_sources = {
            f"member-{surface}" for surface in get_args(TriageSourceSurface)
        }
        if (
            event.get("type") != "triage.intent.submitted"
            or event.get("tenant_id") != runtime.settings.instance.tenant_id
            or event.get("owner_scope") != scope
            or event.get("visibility_scope") != scope
            or source not in member_sources | {"direct-intake", "openclaw-submission"}
            or (source != "direct-intake" and event.get("actor_identity") != member_id)
            or (
                source == "direct-intake"
                and (
                    int(event.get("schema_version") or 1) < 2
                    or payload.get("submission_kind") != "member_forwarded"
                )
            )
        ):
            raise SubmissionContextRefused("submission_identity_invalid")
        if runtime.settings.member_roles.get(member_id) != "principal":
            raise SubmissionContextRefused("member_principal_not_active")
        if session is not None and (
            session.tenant_id != event["tenant_id"]
            or session.auth_member_id != member_id
            or session.view_member_id != member_id
        ):
            raise SubmissionContextRefused("submission_identity_invalid")
        session = session or build_internal_session(
            "submission_interpreter",
            "device",
            str(event["tenant_id"]),
            correlation_id=event.get("correlation_id"),
        )
        key = str(event["event_id"])
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._interpret_locked(event, session)

    async def _member_decision_outcome(
        self, event: dict[str, Any]
    ) -> dict[str, Any] | None:
        payload = event["payload"]
        member_id = str(payload["member_id"])
        tenant_id = str(event["tenant_id"])
        intent_event_id = str(event["event_id"])
        intent_id = str(payload["intent_id"])
        async for decision in self.runtime.event_log.read_since(
            owner_scope=f"private:{member_id}",
            types=[
                "triage.item.enriched",
                "triage.item.managed",
                "triage.item.confirmed",
                "triage.item.dismissed",
            ],
        ):
            if (
                decision.get("tenant_id") == tenant_id
                and (decision.get("payload") or {}).get("proposal_id") == intent_id
            ):
                return {
                    "accepted": True,
                    "duplicate": True,
                    "intent_id": intent_id,
                    "intent_event_id": intent_event_id,
                    "proposal_ids": [],
                    "proposal_event_ids": [],
                    "canonical_event_ids": [],
                    "state": "member_decision_preserved",
                }
        return None

    async def _interpret_locked(
        self, event: dict[str, Any], session: Session
    ) -> dict[str, Any]:
        runtime = self.runtime
        payload = event["payload"]
        member_id = str(payload["member_id"])
        tenant_id = str(event["tenant_id"])
        correlation_id = event.get("correlation_id")
        text = str(payload["text"])
        kind_hint = payload.get("kind_hint")
        intent_event_id = str(event["event_id"])
        intent_id = str(payload["intent_id"])
        submitted_at = datetime.fromisoformat(
            str(payload["submitted_at"]).replace("Z", "+00:00")
        )
        sensitivity = cast(Sensitivity, event.get("sensitivity") or "normal")
        member_decision = await self._member_decision_outcome(event)
        if member_decision is not None:
            return member_decision
        evidence_issues = list(payload.get("evidence_issues") or [])
        evidence_complete = bool(payload.get("evidence_complete", True))
        if payload.get("submission_kind") == "member_forwarded":
            evidence_issues.extend(calendar_submission_issues(text))
            evidence_issues.extend(forwarded_date_issues(text))
            evidence_complete = evidence_complete and not evidence_issues
        deterministic_candidates = (
            extract_intent_candidates(
                text, submitted_at=submitted_at, kind_hint=kind_hint
            )
            if evidence_complete
            else []
        )
        feature_key = (
            f"triage-intent-features:{intent_event_id}:member-intent-hybrid-v2"
        )
        prior_feature = await runtime.event_log.get_by_idempotency(
            tenant_id, feature_key
        )
        if prior_feature is not None:
            feature_event_id = str(prior_feature["event_id"])
            feature_payload = dict(prior_feature["payload"])
        else:
            language_state = "complete"
            language_reason = "bounded_language_assessment_completed"
            skill_call_event_id: str | None = None
            grounded_candidates = []
            try:
                if not evidence_complete:
                    raise IncompleteSubmission("submission_requires_clarification")
                skill_result = await runtime.run_skill_fn(
                    "skill:extract_note_semantics",
                    {
                        "title": None,
                        "body_text": text,
                        "body_format": "plain",
                        "source_kind": "member_submission"
                        if int(event.get("schema_version") or 1) >= 2
                        else "member_intent",
                        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    },
                    SkillContext(
                        session=session,
                        correlation_id=correlation_id,
                        triggering_event_id=intent_event_id,
                        invocation_idempotency_key=f"triage-intent:v2:{intent_event_id}",
                        require_clean_result=int(event.get("schema_version") or 1) >= 2,
                        owner_scope=f"private:{member_id}",
                        visibility_scope=f"private:{member_id}",
                        sensitivity=sensitivity,
                    ),
                    input_sensitivities={"body_text": sensitivity},
                )
                raw_output = getattr(skill_result, "output", skill_result)
                if not isinstance(raw_output, dict):
                    raise SkillOutputInvalid(
                        "submission extraction must return an object"
                    )
                if (
                    len(raw_output.get("candidates") or []) >= 20
                    and payload.get("submission_kind") == "member_forwarded"
                ):
                    evidence_complete = False
                    evidence_issues.append("submission_candidate_limit_requires_review")
                    raise IncompleteSubmission("submission_requires_clarification")
                if isinstance(raw_output, dict):
                    grounded_candidates = extract_grounded_nlp_candidates(
                        text,
                        raw_output.get("candidates"),
                        source_rule_id="explicit_member_intent_v2",
                    )
            except (
                IncompleteSubmission,
                RuntimeError,
                SkillInputInvalid,
                SkillOutputInvalid,
                SkillSensitivityRefused,
                SkillScopeInsufficient,
                SkillHandlerError,
                OpenClawTimeout,
                OpenClawUnreachable,
                OpenClawResponseMalformed,
            ) as exc:
                language_state = (
                    "unavailable" if not evidence_complete else "defensive_default"
                )
                language_reason = (
                    "submission_requires_clarification"
                    if not evidence_complete
                    else f"language_seam_{type(exc).__name__}"
                )
            if kind_hint is not None:
                grounded_candidates = [
                    candidate
                    for candidate in grounded_candidates
                    if candidate.target_kind == kind_hint
                ]
            extracted_candidates = merge_intent_candidates(
                deterministic_candidates,
                grounded_candidates,
            )
            if (
                payload.get("submission_kind") == "member_forwarded"
                and len(extracted_candidates) >= 20
            ):
                evidence_complete = False
                evidence_issues.append("submission_candidate_limit_requires_review")
                language_state = "unavailable"
                language_reason = "submission_requires_clarification"
            if payload.get("submission_kind") == "member_forwarded" and (
                not evidence_complete or language_state != "complete"
            ):
                # A failed full-forward interpretation cannot silently publish
                # only whatever a bounded deterministic prefix happened to find.
                extracted_candidates = []
            related_skill_events = await runtime.event_log.get_by_causation(
                intent_event_id
            )
            skill_call = next(
                (
                    event
                    for event in reversed(related_skill_events)
                    if event.get("type") == "skill.call.recorded"
                    and (event.get("payload") or {}).get("skill_name")
                    == "skill:extract_note_semantics"
                ),
                None,
            )
            if skill_call is not None:
                skill_call_event_id = str(skill_call["event_id"])
            extracted_at = EventEnvelope.now_utc_iso()
            feature_payload = {
                "intent_id": intent_id,
                "source_event_id": intent_event_id,
                "skill_call_event_id": skill_call_event_id,
                "extractor_id": "member-intent-hybrid-v2",
                "language": "en",
                "candidates": [
                    candidate.feature_payload() for candidate in extracted_candidates
                ],
                # The deterministic assessor completed even when it could not derive
                # a typed candidate. The exact text remains in the Triage holding
                # state for human interpretation; this is not a model failure.
                "extraction_state": language_state,
                "extracted_at": extracted_at,
                "assessment_outcome": (
                    "eligible_candidates"
                    if extracted_candidates
                    else "quarantined"
                    if not evidence_complete
                    else "failed"
                    if language_state != "complete"
                    else "no_candidate"
                ),
                "reason_codes": [
                    "member_kind_or_text_rule_matched"
                    if deterministic_candidates
                    else "bounded_language_candidate_found"
                    if grounded_candidates
                    else "member_text_requires_human_interpretation",
                    language_reason,
                    *evidence_issues[:18],
                ],
            }
            feature_fingerprint = hashlib.sha256(
                json.dumps(
                    feature_payload, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            feature_event_id = await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=tenant_id,
                    type="triage.intent.features_extracted",
                    schema_version=3,
                    occurred_at=extracted_at,
                    source_adapter="member-intent-hybrid-v2",
                    source_account_id=member_id,
                    owner_scope=f"private:{member_id}",
                    visibility_scope=f"private:{member_id}",
                    sensitivity=sensitivity,
                    correlation_id=correlation_id,
                    causation_id=intent_event_id,
                    actor_identity="pipeline:member-intent-hybrid-v2",
                    idempotency_key=feature_key,
                    raw_ref=f"features-sha256:{feature_fingerprint}",
                    payload=feature_payload,
                )
            )

        extracted_at = str(
            feature_payload.get("extracted_at") or submitted_at.isoformat()
        )
        candidates = _recorded_intent_candidates(
            feature_payload,
            deterministic_candidates=deterministic_candidates,
        )

        proposal_ids: list[str] = []
        proposal_event_by_id: dict[str, str] = {}
        missing_proposals: list[tuple[str, dict[str, Any], EventEnvelope]] = []
        for candidate in candidates:
            candidate_values = candidate.feature_payload()
            candidate_fingerprint = hashlib.sha256(
                json.dumps(
                    candidate_values, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            proposal_seed = (
                f"{tenant_id}|{intent_id}|{candidate.candidate_key}|"
                f"{candidate.target_kind}|{candidate_fingerprint}"
            )
            proposal_id = (
                "proposal_" + hashlib.sha256(proposal_seed.encode()).hexdigest()[:28]
            )
            proposal_ids.append(proposal_id)
            proposal_payload = {
                "proposal_id": proposal_id,
                "target_kind": candidate.target_kind,
                "owner_member_id": member_id,
                "title": candidate.title,
                "candidate": candidate.fields,
                "route": {"member_id": member_id},
                "source_event_id": intent_event_id,
                "confidence": candidate.confidence,
                "reasons": candidate.reasons,
                "parent_intent_id": intent_id,
                "candidate_key": candidate.candidate_key,
                "extraction_event_id": feature_event_id,
                "eligibility_rule_ids": candidate.eligibility_rule_ids,
            }
            proposal_key = f"triage-intent-proposal:{proposal_id}"
            prior_proposal = await runtime.event_log.get_by_idempotency(
                tenant_id, proposal_key
            )
            if prior_proposal is not None:
                proposal_event_by_id[proposal_id] = str(prior_proposal["event_id"])
                continue
            missing_proposals.append(
                (
                    proposal_id,
                    proposal_payload,
                    EventEnvelope(
                        event_at_ms=int(time.time() * 1000),
                        tenant_id=tenant_id,
                        type="triage.item.proposed",
                        schema_version=2,
                        occurred_at=extracted_at,
                        source_adapter="member-intent-hybrid-v2",
                        source_account_id=member_id,
                        owner_scope=f"private:{member_id}",
                        visibility_scope=f"private:{member_id}",
                        sensitivity=sensitivity,
                        correlation_id=correlation_id,
                        causation_id=feature_event_id,
                        actor_identity="pipeline:member-intent-hybrid-v2",
                        idempotency_key=proposal_key,
                        raw_ref=f"candidate-sha256:{candidate_fingerprint}",
                        payload=proposal_payload,
                    ),
                )
            )

        # Preflight the complete recorded family before appending any child. A
        # denial must not leave a partial family after the first projected child
        # has dismissed the durable member-intent holding row.
        try:
            for _proposal_id, proposal_payload, _envelope in missing_proposals:
                await _guard_triage_write(
                    runtime, session, "triage.item.proposed", proposal_payload
                )
        except HTTPException as exc:
            detail = exc.detail
            reason = (
                detail
                if isinstance(detail, str)
                else str(detail.get("reason") or detail.get("code") or "write denied")
            )
            held_key = f"triage-intent-held:{intent_event_id}"
            if await runtime.event_log.get_by_idempotency(tenant_id, held_key) is None:
                held_event_id = await runtime.events.write(
                    EventEnvelope(
                        event_at_ms=int(time.time() * 1000),
                        tenant_id=tenant_id,
                        type="triage.intent.publication_held",
                        schema_version=1,
                        occurred_at=EventEnvelope.now_utc_iso(),
                        source_adapter="member-intent-hybrid-v2",
                        source_account_id=member_id,
                        owner_scope=f"private:{member_id}",
                        visibility_scope=f"private:{member_id}",
                        sensitivity=sensitivity,
                        correlation_id=correlation_id,
                        causation_id=feature_event_id,
                        actor_identity="pipeline:member-intent-hybrid-v2",
                        idempotency_key=held_key,
                        payload={
                            "intent_id": intent_id,
                            "source_event_id": intent_event_id,
                            "feature_event_id": feature_event_id,
                            "reason": reason[:500],
                            "held_at": EventEnvelope.now_utc_iso(),
                        },
                    )
                )
                await runtime.wait_for_projection("triage", held_event_id)
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": "triage_intent_post_capture",
                    "capture_state": "intent_and_features_recorded",
                    "reason": reason,
                    "recovery": {
                        "intent_id": intent_id,
                        "href": (
                            f"/member/triage?view=needs_interpretation&item={intent_id}"
                        ),
                    },
                },
                headers=exc.headers,
            ) from exc

        # A member can decide the holding item while the model is running.
        # Recheck immutable decisions immediately before append; the projection
        # independently rejects late children ordered after a member decision.
        member_decision = await self._member_decision_outcome(event)
        if member_decision is not None:
            return member_decision
        if missing_proposals:
            appended_ids = await runtime.events.write_batch(
                [envelope for _proposal_id, _payload, envelope in missing_proposals]
            )
            proposal_event_by_id.update(
                {
                    proposal_id: event_id
                    for (proposal_id, _payload, _envelope), event_id in zip(
                        missing_proposals, appended_ids, strict=True
                    )
                }
            )
        proposal_event_ids = [
            proposal_event_by_id[proposal_id] for proposal_id in proposal_ids
        ]
        if proposal_event_ids:
            await runtime.wait_for_projection("triage", proposal_event_ids[-1])
        else:
            await runtime.wait_for_projection("triage", feature_event_id)
        return {
            "accepted": True,
            "duplicate": prior_feature is not None,
            "intent_id": intent_id,
            "intent_event_id": intent_event_id,
            "feature_event_id": feature_event_id,
            "proposal_ids": proposal_ids,
            "proposal_event_ids": proposal_event_ids,
            "state": "pending_review" if proposal_ids else "pending_interpretation",
            "canonical_event_ids": [],
            "evidence_complete": evidence_complete,
            "evidence_issues": evidence_issues,
            "extraction_state": feature_payload.get("extraction_state"),
            "assessment_outcome": feature_payload.get("assessment_outcome"),
        }
