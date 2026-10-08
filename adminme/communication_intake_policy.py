"""Explicit member submission is the production boundary for obligation work.

Provider observations remain facts for Comms and reconciliation. They are not
permission to search a member's communications for new obligations. The only
compatibility switch belongs to isolated historical-contract tests; runtime
composition rejects it outside explicitly selected credential-free fixtures.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from adminme.events.envelope import EventEnvelope, Sensitivity


POLICY_ID = "explicit-member-submission-v1"
POLICY_REASON = "explicit_member_submission_required"
POLICY_EVENT_TYPE = "communication_intake.policy_applied"
POLICY_EVENT_KEY = f"communication-intake:policy:{POLICY_ID}"
SKIPPED_EVENT_TYPE = "communication_intake.source_skipped"
RETIRED_COMMAND_TYPE = "communication_intake.command_retired"
PASSIVE_COMMUNICATION_TYPES = frozenset(
    {"messaging.received", "messaging.sent", "telephony.sms_received"}
)
PASSIVE_CONTROL_TYPES = frozenset(
    {
        "source.assessment.recovery_requested",
        "source.assessment.revision_requested",
        "source.currentness.revision_requested",
        "triage.source_publication.planned",
        "triage.source_revision_publication.planned",
        "triage.currentness_publication.planned",
    }
)


def validate_policy_epoch(event: Mapping[str, Any], tenant_id: str) -> None:
    """Validate the complete authority of the irreversible household cutover."""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("communication intake policy epoch is invalid")
    cutoff = payload.get("previous_source_watermark")
    if (
        event.get("type") != POLICY_EVENT_TYPE
        or event.get("schema_version") != 1
        or event.get("tenant_id") != tenant_id
        or not isinstance(event.get("event_id"), str)
        or not event.get("event_id")
        or event.get("idempotency_key") != POLICY_EVENT_KEY
        or event.get("source_adapter") != "communication-intake-policy"
        or event.get("source_account_id") != "system"
        or event.get("actor_identity") != "system:communication-intake-policy"
        or event.get("owner_scope") != "shared:household"
        or event.get("visibility_scope") != "shared:household"
        or event.get("sensitivity") != "normal"
        or event.get("causation_id") is not None
        or set(payload)
        != {
            "policy_id",
            "previous_source_watermark",
            "automatic_personal_obligations_enabled",
        }
        or payload.get("policy_id") != POLICY_ID
        or payload.get("automatic_personal_obligations_enabled") is not False
        or (
            cutoff is not None
            and (not isinstance(cutoff, str) or not cutoff or len(cutoff) > 240)
        )
    ):
        raise RuntimeError("communication intake policy epoch is invalid")


async def read_policy_epoch(event_log: Any, tenant_id: str) -> dict[str, Any] | None:
    """Fail closed on a forged key or a mis-keyed/contradictory epoch fact.

    This function performs only reads and can also be used by the guarded
    updater with a read-only encrypted log. A source-specific skipped receipt
    is deliberately not a household cutover epoch.
    """
    epoch = await event_log.get_by_idempotency(tenant_id, POLICY_EVENT_KEY)
    if epoch is not None:
        validate_policy_epoch(epoch, tenant_id)
    async for event in event_log.read_since(types=[POLICY_EVENT_TYPE]):
        if event.get("tenant_id") != tenant_id:
            continue
        validate_policy_epoch(event, tenant_id)
        if epoch is None or event["event_id"] != epoch["event_id"]:
            raise RuntimeError("communication intake policy epoch is inconsistent")
    if epoch is not None and epoch["payload"]["previous_source_watermark"] is not None:
        cutoff = await event_log.get(epoch["payload"]["previous_source_watermark"])
        if (
            cutoff is None
            or cutoff.get("tenant_id") != tenant_id
            or cutoff.get("event_id") == epoch["event_id"]
        ):
            raise RuntimeError("communication intake policy epoch watermark is invalid")
    return cast("dict[str, Any] | None", epoch)


def passive_obligation_source(event: Mapping[str, Any]) -> bool:
    """A provider communication is never an explicit member submission."""
    return event.get("type") in PASSIVE_COMMUNICATION_TYPES


def personal_communication_source(event: Mapping[str, Any]) -> bool:
    owner = str(event.get("owner_scope") or "")
    return bool(
        passive_obligation_source(event)
        and owner.startswith("private:")
        and owner.removeprefix("private:")
        and event.get("visibility_scope") == owner
        and not (event.get("payload") or {}).get("service_principal_id")
        and (event.get("payload") or {}).get("member_id", owner[8:]) == owner[8:]
    )


def passive_obligations_disabled(event: Mapping[str, Any], ctx: Any) -> bool:
    return passive_obligation_source(event) and not bool(
        getattr(ctx, "legacy_personal_obligation_compatibility", False)
    )


def passive_proposal_producer(event: Mapping[str, Any]) -> bool:
    """Exact built-in producer identities; explicit-intent children are excluded."""
    producer = str(event.get("source_adapter") or "")
    if event.get("type") == "commitment.proposed":
        return producer in {"commitment_extraction", "thank_you_detection"}
    return bool(
        event.get("type") == "triage.item.proposed"
        and producer == "deterministic_candidate_eligibility"
    )


def retired_command_key(event_id: str) -> str:
    return f"communication-intake:retired:{event_id}"


def source_skip_valid(source: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    payload = receipt.get("payload") or {}
    return bool(
        passive_obligation_source(source)
        and receipt.get("type") == SKIPPED_EVENT_TYPE
        and receipt.get("source_adapter") == "communication-intake-policy"
        and receipt.get("source_account_id") == "system"
        and receipt.get("idempotency_key")
        == f"communication-intake:skipped:{source.get('event_id')}"
        and receipt.get("causation_id") == source.get("event_id")
        and payload.get("source_event_id") == source.get("event_id")
        and payload.get("policy_id") == POLICY_ID
        and payload.get("reason") == POLICY_REASON
        and payload.get("disposition") == "not_selected"
        and payload.get("language_called") is False
        and payload.get("candidate_count") == 0
        and all(
            receipt.get(field) == source.get(field)
            for field in ("tenant_id", "owner_scope", "visibility_scope", "sensitivity")
        )
    )


async def command_retirement(
    event_log: Any, event: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Accept only a terminal receipt for this exact command and scope."""
    record = await event_log.get_by_idempotency(
        str(event["tenant_id"]), retired_command_key(str(event["event_id"]))
    )
    if record is None:
        return None
    payload = record.get("payload") or {}
    if (
        record.get("type") != RETIRED_COMMAND_TYPE
        or record.get("source_adapter") != "communication-intake-policy"
        or record.get("source_account_id") != "system"
        or record.get("causation_id") != event["event_id"]
        or payload.get("policy_id") != POLICY_ID
        or payload.get("reason") != POLICY_REASON
        or payload.get("command_event_id") != event["event_id"]
        or payload.get("command_type") != event["type"]
        or any(
            record.get(key) != event.get(key)
            for key in ("tenant_id", "owner_scope", "visibility_scope", "sensitivity")
        )
    ):
        raise RuntimeError("communication intake retirement receipt identity mismatch")
    return record


async def _write_receipt(
    event: Mapping[str, Any],
    ctx: Any,
    *,
    event_type: str,
    key: str,
    payload: dict[str, Any],
) -> str:
    if str(event.get("tenant_id") or "") != ctx.session.tenant_id:
        raise ValueError("communication intake policy tenant mismatch")
    return str(
        await ctx.event_sink.write(
            EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type=event_type,
                schema_version=1,
                occurred_at=str(event["occurred_at"]),
                source_adapter="communication-intake-policy",
                source_account_id="system",
                owner_scope=str(event["owner_scope"]),
                visibility_scope=str(event["visibility_scope"]),
                sensitivity=cast(Sensitivity, event.get("sensitivity", "normal")),
                idempotency_key=key,
                payload=payload,
            ),
            correlation_id=event.get("correlation_id"),
            causation_id=str(event["event_id"]),
        )
    )


async def record_passive_source_skipped(event: Mapping[str, Any], ctx: Any) -> str:
    """One deterministic receipt shared by all former obligation producers."""
    return await _write_receipt(
        event,
        ctx,
        event_type=SKIPPED_EVENT_TYPE,
        key=f"communication-intake:skipped:{event['event_id']}",
        payload={
            "policy_id": POLICY_ID,
            "source_event_id": str(event["event_id"]),
            "reason": POLICY_REASON,
            "disposition": "not_selected",
            "language_called": False,
            "candidate_count": 0,
        },
    )


async def retire_passive_command(event: Mapping[str, Any], ctx: Any) -> str:
    if event.get("type") not in PASSIVE_CONTROL_TYPES:
        raise ValueError("unsupported passive extraction command")
    return await _write_receipt(
        event,
        ctx,
        event_type=RETIRED_COMMAND_TYPE,
        key=retired_command_key(str(event["event_id"])),
        payload={
            "policy_id": POLICY_ID,
            "command_event_id": str(event["event_id"]),
            "command_type": str(event["type"]),
            "reason": POLICY_REASON,
            "language_called": False,
            "candidate_count": 0,
        },
    )
