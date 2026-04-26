"""Echo-emitter fixture pipeline. On every messaging.received, emits a
matching messaging.sent with causation_id=triggering_event_id, so the
runner integration test can verify the carry-forward from 09a's
causation-wiring contract."""

from __future__ import annotations

from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext, Triggers


class EchoEmitterPipeline:
    pack_id: str = "pipeline:echo_emitter"
    version: str = "1.0.0"
    triggers: Triggers = {"events": ["messaging.received"]}

    emitted_event_ids: list[str] = []

    @classmethod
    def reset(cls) -> None:
        cls.emitted_event_ids = []

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        envelope = EventEnvelope(
            event_at_ms=event["event_at_ms"],
            tenant_id=event["tenant_id"],
            type="messaging.sent",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="echo_emitter",
            source_account_id="test",
            owner_scope=event["owner_scope"],
            visibility_scope=event["visibility_scope"],
            sensitivity="normal",
            payload={
                "source_channel": event["payload"].get("source_channel", "test"),
                "to_identifier": event["payload"].get(
                    "from_identifier", "echo@example.com"
                ),
                "sent_at": EventEnvelope.now_utc_iso(),
                "delivery_status": "sent",
            },
        )
        emitted_id = await ctx.event_log.append(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.triggering_event_id,
        )
        type(self).emitted_event_ids.append(emitted_id)
