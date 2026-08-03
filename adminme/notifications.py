"""Deterministic notification policy; external delivery stays in OpenClaw."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.writer import EventSink


def notification_time(
    triggering_event: dict[str, Any] | None,
    *,
    fallback: datetime | None = None,
) -> datetime:
    """Resolve a deterministic policy clock from a standing-order event."""

    if triggering_event is not None:
        occurred_at = triggering_event.get("occurred_at")
        if isinstance(occurred_at, str):
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        event_at_ms = triggering_event.get("event_at_ms")
        if isinstance(event_at_ms, (int, float)):
            return datetime.fromtimestamp(event_at_ms / 1000, tz=timezone.utc)
    return (fallback or datetime.now(timezone.utc)).astimezone(timezone.utc)


def decide_notification(
    signal: dict[str, Any],
    *,
    profile: dict[str, Any] | None,
    now: datetime,
    observation_active: bool,
    approval_granted: bool,
    prior_failures: int = 0,
) -> dict[str, Any]:
    """Return a typed-event payload without performing an external action."""

    now = now.astimezone(timezone.utc)
    signal_id = str(signal["signal_id"])
    decision_id = f"decision:{signal_id}:{int(now.timestamp() * 1000)}:{prior_failures}"
    common = {
        "decision_id": decision_id,
        "signal_id": signal_id,
        "approval_required": bool(signal.get("requires_approval")),
        "approval_granted": approval_granted,
        "decided_at": now.isoformat(),
    }
    if observation_active:
        return {
            **common,
            "decision": "suppress",
            "route": None,
            "reasons": ["observation_mode_active"],
            "retry_at": None,
        }

    not_before = signal.get("not_before")
    if isinstance(not_before, str):
        not_before_at = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
        if not_before_at > now:
            return {
                **common,
                "decision": "retry",
                "route": None,
                "reasons": ["not_before"],
                "retry_at": not_before_at.isoformat(),
            }

    expires = signal.get("expires_at")
    if isinstance(expires, str):
        expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expires_at <= now:
            return {
                **common,
                "decision": "suppress",
                "route": None,
                "reasons": ["signal_expired"],
                "retry_at": None,
            }

    if signal.get("requires_approval") and not approval_granted:
        return {
            **common,
            "decision": "hold",
            "route": None,
            "reasons": ["explicit_approval_required"],
            "retry_at": None,
        }

    behavior = profile.get("behavior", {}) if isinstance(profile, dict) else {}
    quiet = behavior.get("quiet_hours", {}) if isinstance(behavior, dict) else {}
    start = int(quiet.get("start", 22)) if isinstance(quiet, dict) else 22
    end = int(quiet.get("end", 7)) if isinstance(quiet, dict) else 7
    in_quiet_hours = now.hour >= start or now.hour < end
    if in_quiet_hours and signal.get("urgency") != "critical":
        retry_at = now.replace(hour=end % 24, minute=0, second=0, microsecond=0)
        if retry_at <= now:
            retry_at += timedelta(days=1)
        return {
            **common,
            "decision": "retry",
            "route": None,
            "reasons": ["quiet_hours"],
            "retry_at": retry_at.isoformat(),
        }

    if prior_failures >= 3:
        return {
            **common,
            "decision": "hold",
            "route": None,
            "reasons": ["delivery_circuit_open"],
            "retry_at": None,
        }

    return {
        **common,
        "decision": "route",
        "route": "openclaw",
        "reasons": ["policy_allows_delivery"],
        "retry_at": None,
    }


async def raise_and_decide(
    event_sink: EventSink,
    signal: dict[str, Any],
    *,
    tenant_id: str,
    owner_scope: str,
    visibility_scope: str,
    sensitivity: Sensitivity,
    profile: dict[str, Any] | None,
    now: datetime,
    observation_active: bool,
    approval_granted: bool,
    prior_failures: int = 0,
    correlation_id: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Persist the canonical signal and policy decision through EventSink."""

    occurred_at = now.astimezone(timezone.utc).isoformat()
    signal_envelope = EventEnvelope(
        event_at_ms=int(now.timestamp() * 1000),
        tenant_id=tenant_id,
        type="notification.signal.raised",
        schema_version=1,
        occurred_at=occurred_at,
        source_adapter="notification-policy",
        source_account_id="administrateme-domain",
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
        idempotency_key=f"notification-signal:{signal['signal_id']}",
        payload=signal,
    )
    signal_event_id = await event_sink.write(
        signal_envelope, correlation_id=correlation_id
    )
    decision = decide_notification(
        signal,
        profile=profile,
        now=now,
        observation_active=observation_active,
        approval_granted=approval_granted,
        prior_failures=prior_failures,
    )
    decision_envelope = EventEnvelope(
        event_at_ms=int(now.timestamp() * 1000),
        tenant_id=tenant_id,
        type="notification.decision.made",
        schema_version=1,
        occurred_at=occurred_at,
        source_adapter="notification-policy",
        source_account_id="administrateme-domain",
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
        idempotency_key=f"notification-decision:{decision['decision_id']}",
        payload=decision,
    )
    decision_event_id = await event_sink.write(
        decision_envelope,
        correlation_id=correlation_id,
        causation_id=signal_event_id,
    )
    return signal_event_id, decision_event_id, decision


__all__ = ["decide_notification", "notification_time", "raise_and_decide"]
