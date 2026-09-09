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
    if not future:
        sources.extend(await _seed_retained_messages_account(runtime, member_id, now))
    return {"sources": sources, "provider_effects": 0}


async def _seed_retained_messages_account(
    runtime: Any, member_id: str, now: Any
) -> list[dict[str, Any]]:
    """Exercise real Messages provenance without enrolling a provider or bridge."""
    account = "provider-account:" + "a" * 64
    sources = []
    for index in (11, 12, 13):
        # A replaced local source can retain the same account and enrollment.
        # Its historical candidates must not acquire another source's rules.
        protected_source = "protected-source:" + ("d" if index == 13 else "b") * 64
        chat = f"visual-messages-conversation-{index}"
        sender = f"visual-messages-sender-{index}"
        local_endpoint = f"self:{account}"
        clause = (
            f"If you decide to attend activity {index}, complete the preparation form."
        )
        source = EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=runtime.settings.instance.tenant_id,
            occurred_at=now.isoformat(),
            source_account_id=account,
            owner_scope=f"private:{member_id}",
            visibility_scope=f"private:{member_id}",
            sensitivity="normal",
            actor_identity=member_id,
            type="messaging.received",
            schema_version=7,
            source_adapter="messaging-bluebubbles",
            idempotency_key=f"visual-attention-messages-source:{index}",
            payload={
                "member_id": member_id,
                "provider": "bluebubbles",
                "provider_account_id": account,
                "workspace": "personal",
                "routing_scope": "member_private",
                "routing_target_id": member_id,
                "source_channel": "imessage",
                "from_identifier": sender,
                "to_identifier": local_endpoint,
                "to_identifiers": [local_endpoint],
                "thread_id": chat,
                "native_message_id": f"visual-messages-message-{index}",
                "subject": f"Fixture Messages preparation {index}",
                "body_text": (
                    f"[fixture:attention-controls:preparation:conditional:{index}]\n"
                    f"{clause}"
                ),
                "received_at": now.isoformat(),
                "bridge_enrollment_id": "visual-messages-retained-enrollment",
                "device_identity_sha256": "c" * 64,
                "connector_id": "messaging-bluebubbles",
                "protected_source_id": protected_source,
                "native_chat_id": chat,
                "native_revision": "1:fixture",
                "native_revision_ms": int(now.timestamp() * 1000),
                "direction": "incoming",
                "participants": [
                    {"participant_id": sender, "role": "sender", "is_self": False},
                    {
                        "participant_id": local_endpoint,
                        "role": "self",
                        "is_self": True,
                    },
                ],
                "sender_id": sender,
                "observed_at": now.isoformat(),
                "transport_class": "imessage",
                "is_group": False,
                "message_state": "active",
                "attachments": [],
                "transport_version": "1.9.9",
                "transport_commit": "ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1",
                "correlation_id": f"visual-messages-observation-{index}",
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
                "quote": clause,
                "provider_account_id": account,
                "native_chat_id": chat,
                "protected_source_id": protected_source,
            }
        )
    return sources
