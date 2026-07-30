"""Shared construction of complete adapter EventEnvelopes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from adminme.adapters.base import AdapterContext, SensitivityFloor
from adminme.events.envelope import EventEnvelope


async def write_adapter_event(
    ctx: AdapterContext,
    *,
    adapter_name: str,
    sensitivity_floor: SensitivityFloor,
    config: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    causation_id: str | None = None,
    correlation_id: str | None = None,
) -> str:
    """Construct the canonical envelope and await the one EventSink.

    Adapter configuration contributes only stable provenance identifiers;
    credentials and secret values are never copied into the envelope.
    """

    tenant_id = ctx.event_sink.tenant_id
    if not tenant_id:
        raise RuntimeError("adapter EventSink is not bound to a tenant")
    member_id = str(config.get("member_id") or "")
    owner_scope = f"private:{member_id}" if member_id else "shared:household"
    source_account_id = str(
        config.get("source_account_id")
        or config.get("user_email")
        or member_id
        or adapter_name
    )
    correlation = correlation_id or ctx.correlation_id_factory()
    occurred_at = ctx.now_factory()
    if isinstance(occurred_at, datetime):
        occurred_at_text = occurred_at.isoformat().replace("+00:00", "Z")
        event_at_ms = int(occurred_at.timestamp() * 1000)
    else:  # defensive for test clocks that satisfy the old loose contract
        occurred_at_text = EventEnvelope.now_utc_iso()
        event_at_ms = 0
    envelope = EventEnvelope(
        event_at_ms=event_at_ms,
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=occurred_at_text,
        source_adapter=adapter_name,
        source_account_id=source_account_id,
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity=("privileged" if sensitivity_floor == "privileged" else "normal"),
        correlation_id=correlation,
        causation_id=causation_id,
        payload=payload,
    )
    return await ctx.event_sink.write(
        envelope,
        correlation_id=correlation,
        causation_id=causation_id,
    )


__all__ = ["write_adapter_event"]
