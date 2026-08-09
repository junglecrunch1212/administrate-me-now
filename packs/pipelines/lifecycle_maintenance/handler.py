"""Clock-driven, deterministic lifecycle transitions.

OpenClaw owns the clock.  The existing reminder-dispatch standing-order tick is
only a typed time signal; reviewed code reconstructs commitment state from the
append-only log and expires unconfirmed proposals after fourteen days.  No LLM,
projection mutation, or member-facing decision is involved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext, Triggers


_COMMITMENT_EVENTS = [
    "commitment.proposed",
    "commitment.confirmed",
    "commitment.dismissed",
    "commitment.edited",
    "commitment.snoozed",
    "commitment.deferred",
    "commitment.blocked",
    "commitment.unblocked",
    "commitment.completed",
    "commitment.cancelled",
    "commitment.delegated",
    "commitment.expired",
]
_CLOSED = {
    "commitment.confirmed",
    "commitment.dismissed",
    "commitment.completed",
    "commitment.cancelled",
    "commitment.delegated",
    "commitment.expired",
}


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class LifecycleMaintenancePipeline:
    pack_id = "pipeline:lifecycle_maintenance"
    version = "1.0.0"
    triggers: Triggers = {"events": ["openclaw.standing_order.triggered"]}

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        payload = event.get("payload") or {}
        if (
            event.get("type") != "openclaw.standing_order.triggered"
            or payload.get("program_id") != "reminder_dispatch"
        ):
            return
        owner_scope = str(event.get("owner_scope") or "")
        if not owner_scope.startswith("private:"):
            return
        member_id = owner_scope.removeprefix("private:")
        now = _instant(payload.get("triggered_at"))
        if now is None:
            return
        cutoff = now - timedelta(days=14)

        proposals: dict[str, dict[str, Any]] = {}
        async for commitment_event in ctx.event_log.read_since(
            types=_COMMITMENT_EVENTS
        ):
            if (
                commitment_event.get("tenant_id") != event.get("tenant_id")
                or commitment_event.get("owner_scope") != owner_scope
            ):
                continue
            commitment_payload = commitment_event.get("payload") or {}
            commitment_id = str(commitment_payload.get("commitment_id") or "")
            if not commitment_id:
                continue
            event_type = str(commitment_event.get("type") or "")
            if event_type == "commitment.proposed":
                if commitment_payload.get("owed_by_member_id") == member_id:
                    proposals[commitment_id] = commitment_event
                continue
            if event_type in _CLOSED:
                proposals.pop(commitment_id, None)
            elif commitment_id in proposals:
                proposals[commitment_id] = {
                    **proposals[commitment_id],
                    "last_event_id": commitment_event["event_id"],
                }

        for commitment_id, proposal in sorted(proposals.items()):
            proposed_at = _instant(proposal.get("occurred_at"))
            if proposed_at is None or proposed_at > cutoff:
                continue
            causation_id = str(
                proposal.get("last_event_id") or proposal.get("event_id") or ""
            )
            await ctx.event_sink.write(
                EventEnvelope(
                    event_at_ms=int(event["event_at_ms"]),
                    tenant_id=str(event["tenant_id"]),
                    type="commitment.expired",
                    schema_version=1,
                    occurred_at=now.isoformat(),
                    source_adapter="lifecycle_maintenance",
                    source_account_id="pipeline",
                    owner_scope=owner_scope,
                    visibility_scope=str(event.get("visibility_scope") or owner_scope),
                    sensitivity=proposal.get("sensitivity", "normal"),
                    correlation_id=event.get("correlation_id"),
                    causation_id=causation_id,
                    actor_identity="system:lifecycle-maintenance",
                    idempotency_key=f"commitment-expiry:v1:{commitment_id}",
                    payload={
                        "commitment_id": commitment_id,
                        "expired_at": now.isoformat(),
                    },
                ),
                correlation_id=event.get("correlation_id"),
                causation_id=causation_id,
            )


__all__ = ["LifecycleMaintenancePipeline"]
