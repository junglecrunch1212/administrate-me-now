"""Small, read-only contracts shared by the constitutional member surfaces.

These helpers describe navigation, attention, provider actions, and freshness.
They never authorize a domain mutation and never create a second truth store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode


_SURFACE_PATHS = {
    "today": "/member/today",
    "triage": "/member/triage",
    "comms": "/member/comms",
    "reminders": "/member/reminders",
    "calendar": "/member/calendar",
    "people": "/member/people",
    "notes": "/member/notes",
    "finance": "/member/finance",
    "scoreboard": "/member/scoreboard",
    "settings": "/member/settings",
}


def surface_link(
    surface: str,
    *,
    object_kind: str,
    object_id: str,
    evidence_event_ids: list[str] | None = None,
    params: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Return an exact, versioned link without changing domain semantics."""

    if surface not in _SURFACE_PATHS:
        raise ValueError(f"unsupported member surface: {surface}")
    query = {
        key: value
        for key, value in (params or {}).items()
        if isinstance(value, str) and value
    }
    query.setdefault(object_kind, object_id)
    href = _SURFACE_PATHS[surface]
    if query:
        href = f"{href}?{urlencode(sorted(query.items()))}"
    return {
        "contract": "surface-link-v1",
        "surface": surface,
        "object_kind": object_kind,
        "object_id": object_id,
        "href": href,
        "evidence_event_ids": [value for value in (evidence_event_ids or []) if value],
    }


def attention_explanation(
    *, rule_id: str, reasons: list[str], signals: dict[str, Any]
) -> dict[str, Any]:
    """Explain a deterministic attention decision in a stable shape."""

    return {
        "contract": "attention-explanation-v1",
        "decision_maker": "rules_not_model",
        "rule_id": rule_id,
        "reasons": list(reasons),
        "signals": dict(signals),
    }


def provider_action_state(
    *,
    provider: str,
    provider_account_id: str,
    action: str,
    available: bool,
    reason: str,
) -> dict[str, Any]:
    """Describe capability only; governance still decides every real write."""

    return {
        "contract": "provider-action-state-v1",
        "provider": provider,
        "provider_account_id": provider_account_id,
        "action": action,
        "available": bool(available),
        "reason": reason,
        "authorization": "not_granted_by_this_contract",
    }


def surface_freshness(
    *,
    generated_at: datetime,
    latest_source_at: datetime | None,
    stale_after_seconds: int,
    row_count: int,
    degraded_reason: str | None = None,
    configured: bool = True,
    checkpoint_event_id: str | None = None,
    checkpoint_required: bool = False,
    lag_count: int | None = None,
) -> dict[str, Any]:
    """Distinguish proven empty state from unprocessed, stale, or degraded state."""

    generated = generated_at.astimezone(timezone.utc)
    latest = latest_source_at.astimezone(timezone.utc) if latest_source_at else None
    if not configured:
        state = "not_configured"
    elif degraded_reason:
        state = "degraded"
    elif checkpoint_required and checkpoint_event_id is None:
        state = "uninitialized"
    elif lag_count is not None and lag_count > 0:
        state = "processing"
    elif (
        latest is not None
        and (generated - latest).total_seconds() > stale_after_seconds
    ):
        state = "stale"
    elif row_count == 0:
        state = "healthy_empty"
    elif latest is None:
        state = "unknown"
    else:
        state = "fresh"
    return {
        "contract": "surface-freshness-v1",
        "state": state,
        "generated_at": generated.isoformat(),
        "latest_source_at": latest.isoformat() if latest else None,
        "stale_after_seconds": stale_after_seconds,
        "row_count": row_count,
        "degraded_reason": degraded_reason,
        "checkpoint_event_id": checkpoint_event_id,
        "checkpoint_required": checkpoint_required,
        "lag_count": lag_count,
        "safe_read_functions_available": configured,
    }


__all__ = [
    "attention_explanation",
    "provider_action_state",
    "surface_freshness",
    "surface_link",
]
