"""Append-only cutover from passive communications to member submissions.

Run before consumers start. The policy epoch is durable before any backlog
retirement, so a crash resumes the same finite selection. Member decisions are
read through the current watermark and supersessions compare the exact content
revision; neither a restart nor an overlapping member action loses member work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

from adminme.communication_intake_policy import (
    PASSIVE_COMMUNICATION_TYPES,
    PASSIVE_CONTROL_TYPES,
    POLICY_EVENT_KEY,
    POLICY_EVENT_TYPE,
    POLICY_ID,
    POLICY_REASON,
    command_retirement,
    passive_proposal_producer,
    personal_communication_source,
    record_passive_source_skipped,
    retire_passive_command,
)
from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.log import EventLog
from adminme.events.writer import EventSink


_MEMBER_DECISIONS = frozenset(
    {
        "triage.item.enriched",
        "triage.item.managed",
        "triage.item.confirmed",
        "triage.item.materialized",
        "triage.item.dismissed",
        "commitment.edited",
        "commitment.confirmed",
        "commitment.dismissed",
    }
)


async def _command_already_terminal(event_log: EventLog, event: dict[str, Any]) -> bool:
    """Keep historical successes/refusals intact; retire only unfinished work."""
    if await command_retirement(event_log, event) is not None:
        return True
    event_type = event["type"]
    payload = event.get("payload") or {}
    if event_type == "source.assessment.recovery_requested":
        from adminme.source_assessment_recovery import (
            recovery_assessment_idempotency_key,
            recovery_refusal_idempotency_key,
        )

        assessment = await event_log.get_by_idempotency(
            event["tenant_id"],
            recovery_assessment_idempotency_key(
                event["event_id"], payload["source_event_id"]
            ),
        )
        refusal = await event_log.get_by_idempotency(
            event["tenant_id"], recovery_refusal_idempotency_key(event["event_id"])
        )
        return assessment is not None or refusal is not None
    if event_type in {
        "source.assessment.revision_requested",
        "source.currentness.revision_requested",
    }:
        if event_type == "source.assessment.revision_requested":
            from adminme.source_assessment_revision import command_status
        else:
            from adminme.source_currentness_revision import command_status
        try:
            return bool((await command_status(event_log, event))["terminal"])
        except ValueError:
            # A malformed retained command cannot regain extraction authority.
            return False
    from adminme.source_publication import (
        publication_completion_key,
        publication_completion_valid,
    )

    completion = await event_log.get_by_idempotency(
        event["tenant_id"], publication_completion_key(event["event_id"])
    )
    if completion is None:
        return False
    source = await event_log.get(str(payload.get("source_event_id") or ""))
    assessment = await event_log.get_by_idempotency(
        event["tenant_id"], str(payload.get("assessment_idempotency_key") or "")
    )
    return bool(
        source is not None
        and assessment is not None
        and publication_completion_valid(
            plan=event, completion=completion, source=source, assessment=assessment
        )
    )


async def retire_passive_obligation_candidates(
    *, event_log: EventLog, event_sink: EventSink, tenant_id: str
) -> dict[str, Any]:
    """Preserve source facts and member work; retire only untouched auto drafts."""
    epoch = await event_log.get_by_idempotency(tenant_id, POLICY_EVENT_KEY)
    if epoch is None:
        source_watermark = await event_log.latest_event_id()
        now = datetime.now(timezone.utc)
        epoch_id = await event_sink.write(
            EventEnvelope(
                event_at_ms=int(now.timestamp() * 1000),
                tenant_id=tenant_id,
                type=POLICY_EVENT_TYPE,
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="communication-intake-policy",
                source_account_id="system",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                sensitivity="normal",
                actor_identity="system:communication-intake-policy",
                idempotency_key=POLICY_EVENT_KEY,
                payload={
                    "policy_id": POLICY_ID,
                    "previous_source_watermark": source_watermark,
                    "automatic_personal_obligations_enabled": False,
                },
            )
        )
        epoch = await event_log.get(epoch_id)
    if (
        epoch is None
        or epoch.get("type") != POLICY_EVENT_TYPE
        or epoch.get("tenant_id") != tenant_id
        or epoch.get("source_adapter") != "communication-intake-policy"
        or epoch.get("payload", {}).get("policy_id") != POLICY_ID
        or epoch.get("owner_scope") != "shared:household"
        or epoch.get("visibility_scope") != "shared:household"
    ):
        raise RuntimeError("communication intake policy epoch is invalid")
    cutoff = epoch["payload"].get("previous_source_watermark")
    counts: dict[str, Any] = {
        "policy_id": POLICY_ID,
        "policy_event_id": epoch["event_id"],
        "selected": 0,
        "retired": 0,
        "preserved_member_decision": 0,
        "already_retired": 0,
        "sources_not_selected": 0,
        "commands_retired": 0,
    }
    if not cutoff:
        return counts

    current_watermark = await event_log.latest_event_id()
    decided: set[tuple[str, str]] = set()
    retired: set[tuple[str, str]] = set()
    async for event in event_log.read_since(
        through=current_watermark or "",
        types=sorted(_MEMBER_DECISIONS | {"triage.item.superseded"}),
    ):
        if event.get("tenant_id") != tenant_id:
            continue
        payload = event.get("payload") or {}
        proposal_id = str(
            payload.get("proposal_id") or payload.get("commitment_id") or ""
        )
        key = (str(event.get("owner_scope") or ""), proposal_id)
        if event["type"] == "triage.item.superseded":
            if payload.get("reason") == POLICY_REASON:
                retired.add(key)
        else:
            decided.add(key)

    ctx = SimpleNamespace(
        event_sink=event_sink, session=SimpleNamespace(tenant_id=tenant_id)
    )
    async for event in event_log.read_since(
        through=str(cutoff),
        types=sorted(
            PASSIVE_COMMUNICATION_TYPES
            | PASSIVE_CONTROL_TYPES
            | {"triage.item.proposed", "commitment.proposed"}
        ),
    ):
        if event.get("tenant_id") != tenant_id:
            continue
        if event["type"] in PASSIVE_COMMUNICATION_TYPES:
            await record_passive_source_skipped(event, ctx)
            counts["sources_not_selected"] += 1
            continue
        if event["type"] in PASSIVE_CONTROL_TYPES:
            if not await _command_already_terminal(event_log, event):
                await retire_passive_command(event, ctx)
                counts["commands_retired"] += 1
            continue
        if not passive_proposal_producer(event):
            continue
        payload = event.get("payload") or {}
        source_id = str(
            payload.get("source_event_id") or event.get("causation_id") or ""
        )
        source = await event_log.get(source_id) if source_id else None
        if (
            source is None
            or source.get("tenant_id") != tenant_id
            or not personal_communication_source(source)
            or any(
                event.get(field) != source.get(field)
                for field in ("owner_scope", "visibility_scope")
            )
        ):
            continue
        proposal_id = str(
            payload.get("proposal_id") or payload.get("commitment_id") or ""
        )
        if not proposal_id:
            continue
        member_id = str(source["owner_scope"]).removeprefix("private:")
        if (
            str(
                payload.get("owner_member_id") or payload.get("owed_by_member_id") or ""
            )
            != member_id
        ):
            continue
        counts["selected"] += 1
        key = (str(event["owner_scope"]), proposal_id)
        if key in decided:
            counts["preserved_member_decision"] += 1
            continue
        if key in retired:
            counts["already_retired"] += 1
            continue
        await event_sink.write(
            EventEnvelope(
                event_at_ms=int(epoch["event_at_ms"]),
                tenant_id=tenant_id,
                type="triage.item.superseded",
                schema_version=5,
                occurred_at=str(epoch["occurred_at"]),
                source_adapter="communication-intake-policy",
                source_account_id="system",
                owner_scope=str(event["owner_scope"]),
                visibility_scope=str(event["visibility_scope"]),
                sensitivity=cast(Sensitivity, event.get("sensitivity", "normal")),
                actor_identity="system:communication-intake-policy",
                idempotency_key=f"communication-intake:retire-proposal:{event['event_id']}",
                payload={
                    "proposal_id": proposal_id,
                    "superseded_at": epoch["occurred_at"],
                    "replacement_source_event_id": epoch["event_id"],
                    "reason": POLICY_REASON,
                    "expected_content_revision_event_id": event["event_id"],
                    "expected_last_event_id": event["event_id"],
                },
            ),
            correlation_id=event.get("correlation_id"),
            causation_id=str(event["event_id"]),
        )
        counts["retired"] += 1
    return counts
