"""Versioned communication-to-commitment product API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from adminme.demo import (
    FIXTURE_MEMBER_EMAIL,
    FIXTURE_SENDER_EMAIL,
    canonical_seed_events,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.lib.scope import filter_rows
from adminme.lib.session import Session
from adminme.products.auth import require_principal, session_from_request
from adminme.products.commitment_service import (
    command_fingerprint,
    emit_command,
    owned_commitment,
    prior_command,
)
from adminme.projections.commitments.queries import pending_approval
from adminme.projections.interactions.queries import get_interaction, recent
from adminme.projections.triage.queries import get_item as get_triage_item

router = APIRouter(prefix="/api/comms", tags=["comms"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class CanonicalMessageRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=1, max_length=120)
    native_message_id: str = Field(min_length=1, max_length=120)
    body: str = Field(
        default="Could you reply today with the fixture household update?",
        min_length=1,
        max_length=2000,
    )
    received_at: datetime = datetime.fromisoformat("2026-07-30T12:05:00+00:00")
    sensitivity: Literal["normal", "sensitive", "privileged"] = "normal"

    @field_validator("received_at")
    @classmethod
    def require_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone offset")
        return value


class CommandRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=1, max_length=120)
    expected_last_event_id: str = Field(min_length=1)


class EditRequest(CommandRequest):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    due_at: str | None = None


class ApproveRequest(CommandRequest):
    send_acknowledgement: bool = True


class DismissRequest(CommandRequest):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.product_status("comms")


@router.post("/v1/demo/canonical-message")
async def canonical_message(
    body: CanonicalMessageRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = request.app.state.runtime
    require_principal(session)
    if (
        not runtime.settings.deterministic_openclaw_fixture
        or runtime.fixture_adapter is None
    ):
        raise HTTPException(404, "credential-free fixture is disabled")
    if session.auth_member_id != (
        runtime.settings.fixture_member_id or session.auth_member_id
    ):
        raise HTTPException(403, "fixture member is not configured")

    seed_ids = await runtime.events.write_batch(
        canonical_seed_events(session.tenant_id),
        correlation_id=f"demo:{body.request_id}",
    )
    await runtime.wait_for_projection("parties", seed_ids[5])
    await runtime.wait_for_projection("tasks", seed_ids[-1])
    try:
        source_id = await runtime.fixture_adapter.emit_canonical_message(
            native_message_id=body.native_message_id,
            sender=FIXTURE_SENDER_EMAIL,
            recipient=FIXTURE_MEMBER_EMAIL,
            body=body.body,
            received_at=body.received_at.isoformat().replace("+00:00", "Z"),
            sensitivity=body.sensitivity,
            correlation_id=f"demo:{body.request_id}",
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "native message id conflicts with durable input"
        ) from exc
    await runtime.wait_for_projection("interactions", source_id)
    await runtime.wait_for_subscriber(
        "pipeline:pipeline:identity_resolution", source_id
    )
    await runtime.wait_for_subscriber(
        "pipeline:pipeline:commitment_extraction", source_id
    )
    # The fixture response is a production-composition settlement boundary.
    # Noise filtering can append interaction semantic features after the source
    # projection and commitment extraction complete; returning earlier lets a
    # member command race that later interaction revision.
    await runtime.wait_for_subscriber("pipeline:pipeline:noise_filtering", source_id)
    proposal = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"commitment:proposal:{source_id}"
    )
    derived = await runtime.event_log.get_by_causation(source_id)
    if body.sensitivity != "privileged" and proposal is None:
        raise HTTPException(503, "commitment proposal pipeline did not produce output")
    interaction_events = [
        e
        for e in derived
        if e["type"] in {"interaction.participant_resolved", "messaging.classified"}
    ]
    if interaction_events:
        await runtime.wait_for_projection(
            "interactions", max(e["event_id"] for e in interaction_events)
        )
    if proposal is not None:
        await runtime.wait_for_projection("commitments", proposal["event_id"])
    return {
        "input": {
            "adapter": "fixture-messaging",
            "native_message_id": body.native_message_id,
            "from": FIXTURE_SENDER_EMAIL,
            "to": FIXTURE_MEMBER_EMAIL,
            "body": body.body,
        },
        "output": {
            "source_event_id": source_id,
            "interaction_id": f"ix_{source_id}",
            "commitment_id": proposal["payload"]["commitment_id"] if proposal else None,
            "state": "proposal_only" if proposal else "suppressed",
        },
    }


@router.get("/v1/interactions")
async def interactions(request: Request, session: SessionDep) -> dict[str, Any]:
    runtime = request.app.state.runtime
    if runtime.projections is None:
        raise HTTPException(503, "interactions projection unavailable")
    return {"items": recent(runtime.projections.connection("interactions"), session)}


@router.get("/v1/commitments/pending")
async def pending(request: Request, session: SessionDep) -> dict[str, Any]:
    runtime = request.app.state.runtime
    if runtime.projections is None:
        raise HTTPException(503, "commitments projection unavailable")
    return {
        "items": pending_approval(
            runtime.projections.connection("commitments"), session
        )
    }


@router.get("/v1/interactions/{interaction_id}/evidence")
async def evidence(
    interaction_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = request.app.state.runtime
    interaction = get_interaction(
        runtime.projections.connection("interactions"),
        session,
        interaction_id=interaction_id,
    )
    if interaction is None:
        raise HTTPException(404, "interaction not found")
    source_id = json.loads(interaction["raw_event_ids"])[0]
    source = await runtime.event_log.get(source_id)
    related = await runtime.event_log.get_by_causation(source_id)
    visible = filter_rows(session, [e for e in [source, *related] if e is not None])
    return {
        "interaction": interaction,
        "events": [
            {
                "event_id": e["event_id"],
                "type": e["type"],
                "causation_id": e["causation_id"],
                "correlation_id": e["correlation_id"],
                "source_adapter": e["source_adapter"],
                "payload": e["payload"],
            }
            for e in visible
        ],
    }


@router.post("/v1/commitments/{commitment_id}/edit")
async def edit_commitment(
    commitment_id: str, body: EditRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_principal(session)
    runtime = request.app.state.runtime
    request_fingerprint = command_fingerprint(body.model_dump(mode="json"))
    prior = await prior_command(
        runtime,
        session,
        event_type="commitment.edited",
        commitment_id=commitment_id,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        return {"event_id": prior[0], "commitment": prior[1], "duplicate": True}
    row = owned_commitment(runtime, session, commitment_id)
    if row["confirmed_at"] is not None or row["status"] != "pending":
        raise HTTPException(409, "only unconfirmed proposals may be edited")
    updates = {
        k: v
        for k, v in {"description": body.description, "due_at": body.due_at}.items()
        if v is not None
    }
    if not updates:
        raise HTTPException(422, "at least one editable field is required")
    now = EventEnvelope.now_utc_iso()
    event_id, updated, duplicate = await emit_command(
        runtime,
        session,
        commitment_id=commitment_id,
        event_type="commitment.edited",
        payload={
            "commitment_id": commitment_id,
            "edited_at": now,
            "edited_by_party_id": session.auth_member_id,
            "field_updates": updates,
        },
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        expected_last_event_id=body.expected_last_event_id,
    )
    return {"event_id": event_id, "commitment": updated, "duplicate": duplicate}


@router.post("/v1/commitments/{commitment_id}/approve")
async def approve_commitment(
    commitment_id: str, body: ApproveRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_principal(session)
    runtime = request.app.state.runtime
    request_fingerprint = command_fingerprint(body.model_dump(mode="json"))
    prior = await prior_command(
        runtime,
        session,
        event_type="commitment.confirmed",
        commitment_id=commitment_id,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        return {
            "event_id": prior[0],
            "commitment": prior[1],
            "duplicate": True,
            "delivery": None,
        }
    row = owned_commitment(runtime, session, commitment_id)
    if runtime.projections is None:
        raise HTTPException(503, "triage projection unavailable")
    await runtime.wait_for_projection("triage", row["last_event_id"])
    triage_item = get_triage_item(
        runtime.projections.connection("triage"),
        session,
        proposal_id=commitment_id,
    )
    if (
        triage_item is None
        or triage_item.get("source_kind") != "commitment_proposal"
        or triage_item.get("decision_disposition") != "decision"
    ):
        raise HTTPException(
            409,
            "supporting commitment evidence cannot create duplicate household truth",
        )
    if row["confirmed_at"] is not None or row["status"] != "pending":
        raise HTTPException(409, "proposal is no longer awaiting approval")
    now = EventEnvelope.now_utc_iso()
    event_id, updated, duplicate = await emit_command(
        runtime,
        session,
        commitment_id=commitment_id,
        event_type="commitment.confirmed",
        payload={
            "commitment_id": commitment_id,
            "confirmed_by_member_id": session.auth_member_id,
            "confirmed_at": now,
            "note": None,
        },
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        expected_last_event_id=body.expected_last_event_id,
    )
    delivery = None
    pending_provider_action_event_id = None
    if body.send_acknowledgement:
        command_payload = {
            "command_id": f"commitment-ack:{commitment_id}:{body.request_id}",
            "member_id": session.auth_member_id,
            "provider": "fixture",
            "provider_account_id": "fixture-cat-a",
            "operation": "reply",
            "target_external_id": row.get("source_interaction_id"),
            "expected_provider_revision": None,
            "payload": {
                "interaction_id": row.get("source_interaction_id"),
                "recipient": FIXTURE_SENDER_EMAIL,
                "body": "Acknowledgement approved by the household principal.",
                "commitment_id": commitment_id,
                "content_privacy_guard": {
                    "member_bound": True,
                    "source_scope": f"private:{session.auth_member_id}",
                    "template": "fixture-commitment-ack-v1",
                },
            },
            "requested_at": now,
        }
        action_fingerprint = command_fingerprint(command_payload)
        action_key = (
            f"provider-action:{session.auth_member_id}:"
            f"commitment-ack:{commitment_id}:{body.request_id}"
        )
        prior_action = await runtime.event_log.get_by_idempotency(
            session.tenant_id, action_key
        )
        if prior_action is not None:
            if prior_action.get("raw_ref") != (f"request-sha256:{action_fingerprint}"):
                raise HTTPException(409, "acknowledgement request id conflict")
            pending_provider_action_event_id = prior_action["event_id"]
        else:
            pending_provider_action_event_id = await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(datetime.now().timestamp() * 1000),
                    tenant_id=session.tenant_id,
                    type="member.provider_action.confirmed",
                    schema_version=1,
                    occurred_at=now,
                    source_adapter="member-comms",
                    source_account_id=session.auth_member_id,
                    owner_scope=f"private:{session.auth_member_id}",
                    visibility_scope=f"private:{session.auth_member_id}",
                    sensitivity="normal",
                    correlation_id=session.correlation_id,
                    causation_id=event_id,
                    actor_identity=session.auth_member_id,
                    idempotency_key=action_key,
                    raw_ref=f"request-sha256:{action_fingerprint}",
                    payload={
                        "action_id": command_payload["command_id"],
                        "member_id": session.auth_member_id,
                        "provider": command_payload["provider"],
                        "provider_account_id": command_payload["provider_account_id"],
                        "operation": command_payload["operation"],
                        "target_external_id": command_payload["target_external_id"],
                        "expected_provider_revision": None,
                        "command_payload": command_payload["payload"],
                        "request_fingerprint": action_fingerprint,
                        "confirmed_at": now,
                    },
                )
            )
        delivery = {
            "delivered": False,
            "suppressed": False,
            "held": True,
            "reason": "openclaw_approval_required",
            "domain_approval_event_id": pending_provider_action_event_id,
        }
    if runtime.xlsx_projection is not None:
        await runtime.xlsx_projection.regenerate_now("adminme-ops.xlsx")
    return {
        "event_id": event_id,
        "commitment": updated,
        "duplicate": duplicate,
        "delivery": delivery,
        "pending_provider_action_event_id": pending_provider_action_event_id,
    }


@router.post("/v1/commitments/{commitment_id}/dismiss")
async def dismiss_commitment(
    commitment_id: str, body: DismissRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_principal(session)
    runtime = request.app.state.runtime
    request_fingerprint = command_fingerprint(body.model_dump(mode="json"))
    now = EventEnvelope.now_utc_iso()
    prior = await prior_command(
        runtime,
        session,
        event_type="commitment.dismissed",
        commitment_id=commitment_id,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        return {"event_id": prior[0], "commitment": prior[1], "duplicate": True}
    row = owned_commitment(runtime, session, commitment_id)
    if row["confirmed_at"] is not None or row["status"] != "pending":
        raise HTTPException(409, "only unconfirmed proposals may be dismissed")
    event_id, updated, duplicate = await emit_command(
        runtime,
        session,
        commitment_id=commitment_id,
        event_type="commitment.dismissed",
        payload={
            "commitment_id": commitment_id,
            "dismissed_at": now,
            "dismissed_by_party_id": session.auth_member_id,
            "reason": body.reason,
        },
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        expected_last_event_id=body.expected_last_event_id,
    )
    return {"event_id": event_id, "commitment": updated, "duplicate": duplicate}


__all__ = ["router"]
