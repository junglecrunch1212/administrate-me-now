"""Synthetic source-neutral evidence for the credential-free browser fixture."""

from __future__ import annotations

from typing import Any

from adminme.events.envelope import EventEnvelope


async def seed_attention_sources(
    runtime: Any, member_id: str, now: Any, *, future: bool = False
) -> dict[str, Any]:
    """Write sources only; the real language seam, pipeline and projections run."""
    cases = (
        [(6, "preparation", "conditional")]
        if future
        else [
            (1, "preparation", "conditional"),
            (2, "preparation", "required"),
            (3, "optional_opportunity", "conditional"),
            (4, "receipt", "informational"),
        ]
    )
    sources = []
    for index, purpose, actionability in cases:
        title = f"Fixture {purpose} source {index}."
        clause = (
            f"If you want to join activity {index}, register for the optional workshop."
            if purpose == "optional_opportunity"
            else f"If you decide to attend activity {index}, complete the preparation form."
            if actionability == "conditional"
            else f"Please complete the required preparation form for activity {index}."
            if actionability == "required"
            else f"Your payment for activity {index} was received. No action is required."
        )
        source = EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=runtime.settings.instance.tenant_id,
            occurred_at=now.isoformat(),
            source_account_id="visual-attention-account",
            owner_scope=f"private:{member_id}",
            visibility_scope=f"private:{member_id}",
            sensitivity="normal",
            actor_identity=member_id,
            type="messaging.received",
            schema_version=3,
            source_adapter="fixture-future-messages",
            idempotency_key=f"visual-attention-source:{index}",
            payload={
                "provider": "fixture",
                "provider_account_id": "visual-attention-account",
                "member_id": member_id,
                "source_channel": "sms",
                "workspace": "personal",
                "native_message_id": f"visual-attention-message-{index}",
                # Separate conversations keep independent review cases from
                # superseding one another during contextual interpretation.
                "thread_id": f"visual-attention-conversation-{index}",
                "from_identifier": "fixture-sender@example.invalid",
                "to_identifier": "fixture-member@example.invalid",
                "subject": title,
                "body_text": (
                    f"[fixture:attention-controls:{purpose}:{actionability}:{index}]\n"
                    f"{clause}"
                ),
                "received_at": now.isoformat(),
                "attachments": [],
            },
        )
        source_id = await runtime.events.write(source)
        await runtime.wait_for_subscriber(
            "pipeline:pipeline:noise_filtering", source_id
        )
        sources.append(
            {
                "index": index,
                "source_event_id": source_id,
                "title": title,
                "quote": clause,
            }
        )
    return {"sources": sources, "provider_effects": 0}
