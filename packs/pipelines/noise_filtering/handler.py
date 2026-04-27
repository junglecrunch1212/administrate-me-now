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
(and dropping it from the inbox) is a worse failure than mis-classifying
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
from adminme.pipelines.base import PipelineContext, Triggers

_log = logging.getLogger(__name__)

SKILL_ID = "skill:classify_message_nature"
SKILL_NAME = "classify_message_nature"
SKILL_VERSION = "2.0.0"
DEFAULT_CLASSIFICATION = "personal"


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
    version: str = "1.0.0"
    triggers: Triggers = {
        "events": ["messaging.received", "telephony.sms_received"]
    }

    async def handle(
        self, event: dict[str, Any], ctx: PipelineContext
    ) -> None:
        event_type = event.get("type", "")
        if event_type not in (
            "messaging.received",
            "telephony.sms_received",
        ):
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
            await self._emit_classified(
                event=event,
                ctx=ctx,
                source_event_id=source_event_id,
                classification=DEFAULT_CLASSIFICATION,
                confidence=0.0,
            )
            return

        output = result.output if hasattr(result, "output") else result
        classification = str(output.get("classification", DEFAULT_CLASSIFICATION))
        confidence = float(output.get("confidence", 0.0))
        await self._emit_classified(
            event=event,
            ctx=ctx,
            source_event_id=source_event_id,
            classification=classification,
            confidence=confidence,
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
            sensitivity="normal",
            payload={
                "source_event_id": source_event_id,
                "classification": classification,
                "confidence": round(confidence, 4),
                "skill_name": SKILL_NAME,
                "skill_version": SKILL_VERSION,
            },
        )
        await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
