"""Deterministic, provider-neutral direct-to-CoS intake.

Only exact authenticated metadata selects a reviewed opaque route.  Source
content starts and remains in the service-private source fact; a resolved
member route copies only the bounded message text needed for that member's
primary Triage cue.  This component has no provider client or outbound seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, cast

from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.writer import EventSink
from adminme.service_identities import (
    ServiceChannelAccount,
    resolve_service_channel_route,
    service_private_scope,
)


DIRECT_CHANNEL_READINESS: tuple[dict[str, Any], ...] = (
    {
        "channel": "gmail",
        "state": "supported_not_enrolled",
        "production_adapter": True,
    },
    {
        "channel": "apple_calendar",
        "state": "planned_not_enrolled",
        "production_adapter": False,
    },
    {
        "channel": "apple_reminders",
        "state": "planned_not_enrolled",
        "production_adapter": False,
    },
    {
        "channel": "imessage",
        "state": "planned_not_enrolled",
        "production_adapter": False,
    },
    {
        "channel": "sms",
        "state": "not_provisioned",
        "production_adapter": False,
    },
    {
        "channel": "phone_voicemail_transcripts",
        "state": "not_provisioned",
        "production_adapter": False,
    },
)


def public_direct_channel_readiness(
    channels: tuple[ServiceChannelAccount, ...],
) -> dict[str, Any]:
    configured = {channel.provider: channel for channel in channels}
    rows: list[dict[str, Any]] = []
    for base in DIRECT_CHANNEL_READINESS:
        row = dict(base)
        configured_channel = configured.get(str(base["channel"]))
        if configured_channel is not None:
            row.update(
                {
                    "state": configured_channel.state,
                    "enrolled": configured_channel.enrolled,
                    "observation_active": configured_channel.observation_active,
                    "sending_enabled": configured_channel.sending_enabled,
                }
            )
        else:
            row.update(
                {
                    "enrolled": False,
                    "observation_active": False,
                    "sending_enabled": False,
                }
            )
        row["content_exposed"] = False
        rows.append(row)
    return {
        "contract": "direct-channel-readiness-v1",
        "channels": rows,
        "provider_effect_permitted": False,
        "content_exposed": False,
    }


class DirectIntakeProcessor:
    CONSUMER_VERSION = "direct-intake-v1"

    def __init__(
        self,
        *,
        event_sink: EventSink,
        channels: tuple[ServiceChannelAccount, ...],
    ) -> None:
        self._event_sink = event_sink
        self._channels = {channel.channel_id: channel for channel in channels}

    @staticmethod
    def _bounded_text(payload: dict[str, Any]) -> str:
        parts = [payload.get("subject"), payload.get("body_text")]
        text = "\n\n".join(
            value.strip() for value in parts if isinstance(value, str) and value.strip()
        )
        text = re.sub(r"\s+", " ", text).strip()
        return text[:2000] or "Direct message received for review."

    @staticmethod
    def _now(envelope: dict[str, Any]) -> tuple[str, int]:
        occurred_at = str(envelope.get("occurred_at") or "")
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
            occurred_at = parsed.isoformat().replace("+00:00", "Z")
        return occurred_at, int(parsed.timestamp() * 1000)

    async def handle(self, envelope: dict[str, Any]) -> None:
        if (
            envelope.get("type") != "messaging.received"
            or int(envelope.get("schema_version") or 0) < 6
        ):
            return
        payload = envelope.get("payload") or {}
        service_id = payload.get("service_principal_id")
        if not isinstance(service_id, str) or not service_id:
            return
        expected_service_scope = service_private_scope(service_id)
        if (
            envelope.get("owner_scope") != expected_service_scope
            or envelope.get("visibility_scope") != expected_service_scope
        ):
            raise ValueError("service intake source scope contradicts its owner")

        channel_id = str(payload.get("channel_id") or "")
        channel = self._channels.get(channel_id)
        route_key = payload.get("trusted_route_key")
        provider = str(payload.get("provider") or payload.get("source_channel") or "")
        if (
            channel is None
            or channel.service_principal_id != service_id
            or channel.provider != provider
            or not channel.observation_active
            or not isinstance(route_key, str)
        ):
            route = {
                "state": "unresolved",
                "route_scope": "unresolved",
                "routing_target_id": None,
                "owner_scope": expected_service_scope,
                "visibility_scope": expected_service_scope,
                "content_exposed": False,
            }
        else:
            route = resolve_service_channel_route(channel, route_key)
            if route["state"] == "unresolved":
                route["route_scope"] = "unresolved"
                route["routing_target_id"] = None
                route["owner_scope"] = expected_service_scope
                route["visibility_scope"] = expected_service_scope

        occurred_at, event_at_ms = self._now(envelope)
        source_event_id = str(envelope["event_id"])
        route_scope = str(route["route_scope"])
        target_id = route.get("routing_target_id")
        raw_sensitivity = envelope.get("sensitivity") or "normal"
        if raw_sensitivity not in {"normal", "sensitive", "privileged"}:
            raise ValueError("direct intake source has invalid sensitivity")
        sensitivity = cast(Sensitivity, raw_sensitivity)
        cue_envelope: EventEnvelope | None = None
        cue_event_id: str | None = None
        if route_scope == "member_private" and isinstance(target_id, str):
            cue_intent_id = f"direct-intake-cue:{source_event_id}"
            text = self._bounded_text(payload)
            cue_envelope = EventEnvelope(
                event_at_ms=event_at_ms,
                tenant_id=str(envelope["tenant_id"]),
                type="triage.intent.submitted",
                schema_version=1,
                occurred_at=occurred_at,
                source_adapter="direct-intake",
                source_account_id=str(envelope.get("source_account_id") or channel_id),
                owner_scope=f"private:{target_id}",
                visibility_scope=f"private:{target_id}",
                sensitivity=sensitivity,
                correlation_id=str(envelope.get("correlation_id") or source_event_id),
                causation_id=source_event_id,
                idempotency_key=cue_intent_id,
                payload={
                    "intent_id": cue_intent_id,
                    "member_id": target_id,
                    "text": text,
                    "source_surface": "openclaw",
                    "kind_hint": None,
                    "source_event_id": source_event_id,
                    "submitted_at": occurred_at,
                    "client_request_id": hashlib.sha256(
                        f"direct-intake:{source_event_id}".encode("utf-8")
                    ).hexdigest()[:32],
                },
            )
            cue_event_id = await self._event_sink.write(
                cue_envelope,
                correlation_id=cue_envelope.correlation_id,
                causation_id=source_event_id,
            )

        authority = (
            "proposal_only"
            if route_scope == "member_private"
            else "external_contact_not_instruction"
            if route_scope == "external_contact"
            else "none"
        )
        routed = EventEnvelope(
            event_at_ms=event_at_ms,
            tenant_id=str(envelope["tenant_id"]),
            type="direct_intake.routed",
            schema_version=1,
            occurred_at=occurred_at,
            source_adapter="direct-intake",
            source_account_id=str(envelope.get("source_account_id") or channel_id),
            owner_scope=str(route["owner_scope"]),
            visibility_scope=str(route["visibility_scope"]),
            sensitivity=sensitivity,
            correlation_id=str(envelope.get("correlation_id") or source_event_id),
            causation_id=source_event_id,
            idempotency_key=f"direct-intake-route:{source_event_id}",
            payload={
                "source_event_id": source_event_id,
                "channel_id": channel_id or "unresolved-channel",
                "service_principal_id": service_id,
                "route_source": "exact_authenticated_metadata",
                "route_state": str(route["state"]),
                "routing_scope": route_scope,
                "routing_target_id": target_id,
                "cue_kind": "primary_triage_cue" if cue_envelope else "none",
                "cue_event_id": cue_event_id,
                "authority": authority,
                "provider_effect_permitted": False,
                "source_content_exposed": False,
            },
        )
        await self._event_sink.write(
            routed,
            correlation_id=routed.correlation_id,
            causation_id=source_event_id,
        )


__all__ = [
    "DIRECT_CHANNEL_READINESS",
    "DirectIntakeProcessor",
    "public_direct_channel_readiness",
]
