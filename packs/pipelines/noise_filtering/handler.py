"""noise_filtering reactive pipeline.

Per [BUILD.md §1132-1138] and [§7.4] / [§8] / [D6]. Calls the
``classify_message_nature`` skill once per inbound messaging event,
emits a ``messaging.classified`` event with the classification +
confidence + skill provenance. Downstream (a future ``interactions``
projection update) decides whether to suppress the row.

Defensive-default discipline ([§7.7]): if the skill raises (timeout,
unreachable, input/output schema reject), the pipeline emits a
classified event with classification=``personal`` / confidence 0.0
rather than letting the exception propagate up to the runner — which
would non-advance the bus checkpoint and block subsequent events on
every classification failure. Mis-classifying a real message as noise
(and dropping it from authorized interactions) is a worse failure than mis-classifying
noise as personal.

Per [ADR-0002], skill calls go through ``ctx.run_skill_fn`` (bound to
``adminme.lib.skill_runner.run_skill``) and never through provider
SDKs.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from adminme.events.envelope import EventEnvelope
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
from adminme.pipelines.base import PipelineContext, Triggers, skill_output
from adminme.triage_intent import (
    EXTRACTOR_ID,
    GROUNDED_NLP_EXTRACTOR_ID,
    extract_grounded_nlp_candidates,
    extract_intent_candidates,
    merge_intent_candidates,
)

_log = logging.getLogger(__name__)

SKILL_ID = "skill:classify_message_nature"
SKILL_NAME = "classify_message_nature"
SKILL_VERSION = "4.0.0"
DEFAULT_CLASSIFICATION = "personal"

_SKILL_FAILURE_TYPES: tuple[type[BaseException], ...] = (
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillSensitivityRefused,
    SkillScopeInsufficient,
    SkillHandlerError,
    OpenClawTimeout,
    OpenClawUnreachable,
    OpenClawResponseMalformed,
    # The language seam is non-authoritative.  A configuration or transport
    # RuntimeError must not poison durable message ingestion; deterministic
    # extraction and a defensive classification can still proceed safely.
    RuntimeError,
)


def _extract_text_and_inputs(
    event_type: str, payload: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """Build the skill input payload, or return None if there's nothing
    to classify (empty body)."""
    outbound_message = False
    if event_type == "telephony.sms_received":
        text = (payload.get("body") or "").strip()
        from_id = str(payload.get("from_number", ""))
        source_channel = "sms"
    else:
        text = (payload.get("body_text") or "").strip()
        provider_labels = {
            str(label).upper() for label in payload.get("provider_labels") or []
        }
        outbound_message = event_type == "messaging.sent" or "SENT" in provider_labels
        from_id = str(
            payload.get("to_identifier" if outbound_message else "from_identifier", "")
        )
        source_channel = str(payload.get("source_channel", ""))
    if not text:
        return None
    inputs: dict[str, Any] = {
        "body_text": text,
        "source_channel": source_channel,
        "from_identifier": from_id,
        "counterparty_identifier": from_id,
        "message_direction": "outbound" if outbound_message else "inbound",
        # Conservative — the runner does not yet expose the parties DB
        # to the pipeline (see identity_resolution module docstring), so
        # we cannot cheaply check whether the sender is resolved. The
        # skill prompt is robust to this hint being conservative.
        "from_party_known": False,
    }
    subject = payload.get("subject")
    if subject:
        inputs["subject"] = str(subject)
    grounded_source = f"Subject: {subject}\n\n{text}" if subject else text
    return grounded_source, inputs


class NoiseFilteringPipeline:
    pack_id: str = "pipeline:noise_filtering"
    version: str = "4.0.0"
    triggers: Triggers = {
        "events": [
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
        ]
    }

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        event_type = event.get("type", "")
        if event_type not in (
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
        ):
            return
        if event.get("sensitivity") == "privileged":
            return

        payload = event.get("payload") or {}
        prepared = _extract_text_and_inputs(event_type, payload)
        if prepared is None:
            return
        source_text, inputs = prepared

        source_event_id = event.get("event_id", "")
        skill_ctx = SkillContext(
            session=ctx.session,
            correlation_id=ctx.correlation_id,
            triggering_event_id=str(source_event_id),
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity=event.get("sensitivity", "normal"),
        )

        try:
            result = await ctx.run_skill_fn(SKILL_ID, inputs, skill_ctx)
        except _SKILL_FAILURE_TYPES as exc:
            _log.warning(
                "noise_filtering skill failure (%s); emitting defensive default",
                type(exc).__name__,
            )
            await self._emit_results(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                output={
                    "classification": DEFAULT_CLASSIFICATION,
                    "confidence": 0.0,
                    "reasons": ["skill_failure_defensive_default"],
                    "reply_signal": "unknown",
                    "automated": False,
                    "transactional_due": False,
                    "security_code": False,
                    "person_to_person": False,
                    "deadline": None,
                    "candidates": [],
                },
                source_text=source_text,
            )
            return

        output = skill_output(result)
        await self._emit_results(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            output=output,
            source_text=source_text,
        )

    async def _emit_results(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        output: dict[str, Any],
        source_text: str,
    ) -> None:
        await self._emit_classified(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            classification=str(output.get("classification", DEFAULT_CLASSIFICATION)),
            confidence=float(output.get("confidence", 0.0)),
        )
        await self._emit_semantic_features(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            output=output,
        )
        await self._emit_triage_candidates(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            source_text=source_text,
            raw_candidates=output.get("candidates"),
        )

    async def _emit_classified(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        classification: str,
        confidence: float,
    ) -> None:
        payload = {
            "source_event_id": source_event_id,
            "classification": classification,
            "confidence": round(confidence, 4),
            "skill_name": SKILL_NAME,
            "skill_version": SKILL_VERSION,
        }
        idempotency_key = f"messaging:classified:{source_event_id}"
        prior = await ctx.event_log.get_by_idempotency(
            event["tenant_id"], idempotency_key
        )
        if prior is not None and prior.get("payload") != payload:
            idempotency_key = f"messaging:classified:v4:{source_event_id}"
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="messaging.classified",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="noise_filtering",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _emit_semantic_features(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        output: dict[str, Any],
    ) -> None:
        payload = {
            "source_event_id": source_event_id,
            "reply_signal": str(output.get("reply_signal") or "unknown"),
            "automated": bool(output.get("automated", False)),
            "transactional_due": bool(output.get("transactional_due", False)),
            "security_code": bool(output.get("security_code", False)),
            "person_to_person": bool(output.get("person_to_person", False)),
            "deadline": output.get("deadline"),
            "confidence": round(float(output.get("confidence", 0.0)), 4),
            "reasons": [str(value) for value in output.get("reasons") or []][:20],
            "skill_name": SKILL_NAME,
            "skill_version": SKILL_VERSION,
        }
        idempotency_key = f"messaging:semantic-features:{source_event_id}"
        prior = await ctx.event_log.get_by_idempotency(
            event["tenant_id"], idempotency_key
        )
        if prior is not None and prior.get("payload") != payload:
            idempotency_key = f"messaging:semantic-features:v4:{source_event_id}"
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="messaging.semantic_features_extracted",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="noise_filtering",
            source_account_id="pipeline",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )

    async def _emit_triage_candidates(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        source_text: str,
        raw_candidates: Any,
    ) -> None:
        payload = event.get("payload") or {}
        owner_scope = str(event.get("owner_scope") or "")
        if not owner_scope.startswith("private:"):
            return
        member_id = owner_scope.removeprefix("private:")
        payload_member = payload.get("member_id")
        if payload_member is not None and payload_member != member_id:
            return
        if not member_id:
            return
        try:
            submitted_at = datetime.fromisoformat(
                str(event["occurred_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            submitted_at = datetime.fromtimestamp(
                event["event_at_ms"] / 1000, timezone.utc
            )
        candidates = merge_intent_candidates(
            extract_intent_candidates(
                source_text,
                submitted_at=submitted_at,
            ),
            extract_grounded_nlp_candidates(source_text, raw_candidates),
        )
        if not candidates:
            return
        intent_id = f"source:{source_event_id}"
        # Replay must produce byte-identical event identities.  Never place a
        # wall-clock retry time in an idempotent payload.
        extracted_at = str(
            event.get("recorded_at")
            or event.get("occurred_at")
            or datetime.fromtimestamp(
                event["event_at_ms"] / 1000, timezone.utc
            ).isoformat()
        )
        baseline_idempotency = f"triage:message-features:{source_event_id}"
        prior_extraction = await ctx.event_log.get_by_idempotency(
            event["tenant_id"], baseline_idempotency
        )
        idempotency_generation = ""
        if prior_extraction is not None:
            prior_proposals = await ctx.event_log.get_by_causation(
                prior_extraction["event_id"]
            )
            existing = {
                (
                    str(proposal["payload"].get("target_kind") or ""),
                    str(proposal["payload"].get("candidate_key") or ""),
                )
                for proposal in prior_proposals
                if proposal.get("type") == "triage.item.proposed"
            }
            candidates = [
                candidate
                for candidate in candidates
                if (candidate.target_kind, candidate.candidate_key) not in existing
            ]
            if not candidates:
                return
            # A later reviewed language-seam repair may discover additional
            # exact-source candidates after a defensive deterministic pass.
            # Append only those additions under a new idempotency generation;
            # never rewrite or duplicate proposals already reviewed in Triage.
            idempotency_generation = "v4:"
        extraction_event_id = await ctx.event_sink.write(
            EventEnvelope(
                event_at_ms=event["event_at_ms"],
                tenant_id=event["tenant_id"],
                type="triage.intent.features_extracted",
                schema_version=1,
                occurred_at=extracted_at,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=owner_scope,
                visibility_scope=event["visibility_scope"],
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=(
                    f"triage:message-features:{idempotency_generation}{source_event_id}"
                ),
                payload={
                    "intent_id": intent_id,
                    "source_event_id": source_event_id,
                    "skill_call_event_id": None,
                    "extractor_id": f"{EXTRACTOR_ID}+{GROUNDED_NLP_EXTRACTOR_ID}",
                    "language": None,
                    "candidates": [
                        candidate.feature_payload() for candidate in candidates
                    ],
                    "extraction_state": "complete",
                    "extracted_at": extracted_at,
                },
            ),
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
        for candidate in candidates:
            target_kind = candidate.target_kind
            fingerprint = hashlib.sha256(
                (
                    f"{source_event_id}:{candidate.candidate_key}:"
                    f"{target_kind}:{candidate.title}"
                ).encode()
            ).hexdigest()[:24]
            proposal_id = f"triage:message:{fingerprint}"
            envelope = EventEnvelope(
                event_at_ms=event["event_at_ms"],
                tenant_id=event["tenant_id"],
                type="triage.item.proposed",
                schema_version=2,
                occurred_at=extracted_at,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=owner_scope,
                visibility_scope=owner_scope,
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=(
                    f"triage:message-candidate:{idempotency_generation}"
                    f"{source_event_id}:"
                    f"{candidate.candidate_key}:{target_kind}"
                ),
                payload={
                    "proposal_id": proposal_id,
                    "target_kind": target_kind,
                    "owner_member_id": member_id,
                    "title": candidate.title,
                    "candidate": candidate.fields,
                    "route": {"member_id": member_id},
                    "source_event_id": source_event_id,
                    "parent_intent_id": None,
                    "candidate_key": candidate.candidate_key,
                    "extraction_event_id": extraction_event_id,
                    "eligibility_rule_ids": candidate.eligibility_rule_ids,
                    "confidence": candidate.confidence,
                    "reasons": candidate.reasons,
                },
            )
            await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=extraction_event_id,
            )
