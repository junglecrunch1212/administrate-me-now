"""Shared construction of complete adapter EventEnvelopes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from adminme.adapters.base import AdapterContext, SensitivityFloor
from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.service_identities import service_private_scope


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
    schema_version: int = 1,
    idempotency_key: str | None = None,
    raw_ref: str | None = None,
    occurred_at_override: str | None = None,
    sensitivity_override: Sensitivity | None = None,
) -> str:
    """Construct the canonical envelope and await the one EventSink.

    Adapter configuration contributes only stable provenance identifiers;
    credentials and secret values are never copied into the envelope.
    """

    tenant_id = ctx.event_sink.tenant_id
    if not tenant_id:
        raise RuntimeError("adapter EventSink is not bound to a tenant")
    member_id = config.get("member_id")
    normalized_member_id = member_id.strip() if isinstance(member_id, str) else ""
    service_principal_id = config.get("service_principal_id")
    normalized_service_id = (
        service_principal_id.strip() if isinstance(service_principal_id, str) else ""
    )
    if normalized_member_id and normalized_service_id:
        raise RuntimeError("adapter event requires exactly one owner principal")
    if not normalized_member_id and not normalized_service_id and schema_version != 1:
        raise RuntimeError("adapter event requires exactly one owner principal")
    member_bound = bool(normalized_member_id)
    source_account = (
        config.get("source_account_id")
        or config.get("provider_account_id")
        or config.get("user_email")
        or config.get("subject_email")
    )
    if not isinstance(source_account, str) or not source_account.strip():
        if schema_version != 1 and not event_type.startswith("adapter."):
            raise RuntimeError("adapter event requires an explicit provider account")
        source_account = normalized_member_id if member_bound else adapter_name
    # V1 remains readable for the bounded legacy fixture/migration path. Every
    # current provider fact is v2+ and therefore cannot use this shared scope.
    owner_scope = (
        f"private:{normalized_member_id}"
        if member_bound
        else service_private_scope(normalized_service_id)
        if normalized_service_id
        else "shared:household"
    )
    source_account_id = source_account.strip()
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
        schema_version=schema_version,
        occurred_at=occurred_at_override or occurred_at_text,
        source_adapter=adapter_name,
        source_account_id=source_account_id,
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity=(
            sensitivity_override
            or ("privileged" if sensitivity_floor == "privileged" else "normal")
        ),
        correlation_id=correlation,
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        raw_ref=raw_ref,
        payload=payload,
    )
    return await ctx.event_sink.write(
        envelope,
        correlation_id=correlation,
        causation_id=causation_id,
    )


__all__ = ["write_adapter_event"]
