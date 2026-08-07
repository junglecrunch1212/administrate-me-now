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

import logging
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import (
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillContext,
    SkillInputInvalid,
    SkillOutputInvalid,
)
from adminme.pipelines.base import PipelineContext, Triggers, skill_output

_log = logging.getLogger(__name__)

SKILL_ID = "skill:classify_message_nature"
SKILL_NAME = "classify_message_nature"
SKILL_VERSION = "3.0.0"
DEFAULT_CLASSIFICATION = "personal"
_CANDIDATE_KINDS = {
    "task",
    "calendar_event",
    "reminder",
    "list",
    "list_item",
    "party_identifier",
}
_CANDIDATE_FIELDS = {
    "task": {"description", "due", "energy", "effort_min"},
    "calendar_event": {"start", "end", "all_day", "event_type", "location"},
    "reminder": {"due", "notes", "priority"},
    "list": {"notes"},
    "list_item": {"due", "notes", "priority"},
    "party_identifier": {
        "surviving_party_id",
        "candidate_kind",
        "candidate_value",
        "candidate_value_normalized",
    },
}


def _extract_text_and_inputs(
    event_type: str, payload: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """Build the skill input payload, or return None if there's nothing
    to classify (empty body)."""
    if event_type == "telephony.sms_received":
        text = (payload.get("body") or "").strip()
        from_id = str(payload.get("from_number", ""))
        source_channel = "sms"
    else:
        text = (payload.get("body_text") or "").strip()
        from_id = str(payload.get("from_identifier", ""))
        source_channel = str(payload.get("source_channel", ""))
    if not text:
        return None
    inputs: dict[str, Any] = {
        "body_text": text,
        "source_channel": source_channel,
        "from_identifier": from_id,
        # Conservative — the runner does not yet expose the parties DB
        # to the pipeline (see identity_resolution module docstring), so
        # we cannot cheaply check whether the sender is resolved. The
        # skill prompt is robust to this hint being conservative.
        "from_party_known": False,
    }
    subject = payload.get("subject")
    if subject:
        inputs["subject"] = str(subject)
    return text, inputs


class NoiseFilteringPipeline:
    pack_id: str = "pipeline:noise_filtering"
    version: str = "2.0.0"
    triggers: Triggers = {"events": ["messaging.received", "telephony.sms_received"]}

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        event_type = event.get("type", "")
        if event_type not in (
            "messaging.received",
            "telephony.sms_received",
        ):
            return
        if event.get("sensitivity") == "privileged":
            return

        payload = event.get("payload") or {}
        prepared = _extract_text_and_inputs(event_type, payload)
        if prepared is None:
            return
        _, inputs = prepared

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
        except (
            SkillInputInvalid,
            SkillOutputInvalid,
            OpenClawTimeout,
            OpenClawUnreachable,
            OpenClawResponseMalformed,
        ) as exc:
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
            )
            return

        output = skill_output(result)
        await self._emit_results(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            output=output,
        )

    async def _emit_results(
        self,
        *,
        event: dict[str, Any],
        ctx: PipelineContext,
        source_event_id: str,
        output: dict[str, Any],
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
            candidates=output.get("candidates"),
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
            idempotency_key=f"messaging:classified:{source_event_id}",
            payload={
                "source_event_id": source_event_id,
                "classification": classification,
                "confidence": round(confidence, 4),
                "skill_name": SKILL_NAME,
                "skill_version": SKILL_VERSION,
            },
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
            idempotency_key=f"messaging:semantic-features:{source_event_id}",
            payload={
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
            },
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
        candidates: Any,
    ) -> None:
        payload = event.get("payload") or {}
        owner_scope = str(event.get("owner_scope") or "")
        if not owner_scope.startswith("private:"):
            return
        member_id = owner_scope.removeprefix("private:")
        payload_member = payload.get("member_id")
        if payload_member is not None and payload_member != member_id:
            return
        if not member_id or not isinstance(candidates, list):
            return
        for index, candidate in enumerate(candidates[:10]):
            if not isinstance(candidate, dict):
                continue
            target_kind = str(candidate.get("target_kind") or "")
            confidence = float(candidate.get("confidence") or 0.0)
            title = str(candidate.get("title") or "").strip()
            if target_kind not in _CANDIDATE_KINDS or confidence < 0.65 or not title:
                continue
            raw_candidate = candidate.get("candidate")
            candidate_fields = raw_candidate if isinstance(raw_candidate, dict) else {}
            sanitized_candidate = {
                key: value
                for key, value in candidate_fields.items()
                if key in _CANDIDATE_FIELDS[target_kind]
            }
            proposal_id = f"triage:message:{source_event_id}:{index}:{target_kind}"
            envelope = EventEnvelope(
                event_at_ms=event["event_at_ms"],
                tenant_id=event["tenant_id"],
                type="triage.item.proposed",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="noise_filtering",
                source_account_id="pipeline",
                owner_scope=owner_scope,
                visibility_scope=owner_scope,
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=f"triage:message-candidate:{source_event_id}:{index}",
                payload={
                    "proposal_id": proposal_id,
                    "target_kind": target_kind,
                    "owner_member_id": member_id,
                    "title": title[:1000],
                    "candidate": sanitized_candidate,
                    "route": {"member_id": member_id},
                    "source_event_id": source_event_id,
                    "confidence": round(confidence, 4),
                    "reasons": [str(value) for value in candidate.get("reasons") or []][
                        :20
                    ],
                },
            )
            await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=ctx.triggering_event_id,
            )
