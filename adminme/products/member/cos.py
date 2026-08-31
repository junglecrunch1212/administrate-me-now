"""Member-controlled authority API for the non-human household CoS."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field

from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.lib.session import Session
from adminme.products.auth import require_principal, session_from_request
from adminme.projections.parties.queries import list_service_delegations
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID


router = APIRouter(prefix="/api/member/v1/cos", tags=["member-cos"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class DelegationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared", "external_contact"]
    context_member_id: str | None = Field(default=None, min_length=1)
    owner_party_id: str | None = Field(default=None, min_length=1)
    permissions: list[
        Literal["read_context", "create_internal_work", "manage_internal_work"]
    ] = Field(min_length=1, max_length=3)
    expires_at: AwareDatetime | None = None


class DelegationDecision(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    reason: str | None = Field(default=None, max_length=500)


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


def _require_self_principal(session: Session) -> None:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "CoS authority cannot be changed while viewing-as")


def _envelope(
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    owner_scope: str,
) -> EventEnvelope:
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="member-cos-authority",
        source_account_id=session.auth_member_id,
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity="normal",
        correlation_id=session.correlation_id,
        actor_identity=session.auth_member_id,
        idempotency_key=(
            f"member-cos:{event_type}:{session.auth_member_id}:{request_id}"
        ),
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )


@router.get("/delegations")
async def delegations(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "CoS delegation projection is unavailable")
    return {
        "contract": "household-cos-delegation-v1",
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "service_party_id": COS_PARTY_ID,
        "items": list_service_delegations(
            runtime.projections.connection("parties"),
            session,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        ),
        "provider_authority": False,
        "external_write_requests": 0,
    }


@router.post("/delegations", status_code=201)
async def request_delegation(
    body: DelegationRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "CoS delegation authority is unavailable")
    context_member_id = body.context_member_id
    owner_party_id = body.owner_party_id
    principals = sorted(
        member_id
        for member_id, role in runtime.settings.member_roles.items()
        if role == "principal"
    )
    if body.context_scope == "member_private":
        if context_member_id not in {None, session.auth_member_id} or owner_party_id:
            raise HTTPException(403, "member-private authority is self-owned")
        context_member_id = session.auth_member_id
        required = [session.auth_member_id]
        quorum = "member_owner"
        owner_scope = f"private:{session.auth_member_id}"
        resource_owner = session.auth_member_id
    elif body.context_scope == "household_shared":
        if context_member_id or owner_party_id:
            raise HTTPException(400, "household-shared authority has no private owner")
        if len(principals) < 2:
            raise HTTPException(409, "household authority requires multiple principals")
        required = principals
        quorum = "all_adult_principals"
        owner_scope = "shared:household"
        resource_owner = None
    else:
        if (
            context_member_id not in {None, session.auth_member_id}
            or not owner_party_id
        ):
            raise HTTPException(403, "external-contact authority requires a self owner")
        context_member_id = session.auth_member_id
        party = (
            runtime.projections.connection("parties")
            .execute(
                "SELECT party_id, qualification FROM parties "
                "WHERE tenant_id=? AND party_id=?",
                (session.tenant_id, owner_party_id),
            )
            .fetchone()
        )
        if (
            party is None
            or party["qualification"] != "qualified"
            or owner_party_id in set(runtime.settings.member_roles) | {COS_PARTY_ID}
        ):
            raise HTTPException(
                409, "external contact must be a reviewed non-member Party"
            )
        required = [session.auth_member_id]
        quorum = "member_owner"
        owner_scope = f"private:{session.auth_member_id}"
        resource_owner = session.auth_member_id

    grant_digest = hashlib.sha256(
        f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
    ).hexdigest()[:32]
    grant_id = f"cos-grant:{grant_digest}"
    now = EventEnvelope.now_utc_iso()
    payload = {
        "grant_id": grant_id,
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "service_party_id": COS_PARTY_ID,
        "context_scope": body.context_scope,
        "context_member_id": context_member_id,
        "resource_owner_principal_id": resource_owner,
        "owner_party_id": owner_party_id,
        "requested_by_member_id": session.auth_member_id,
        "required_approving_member_ids": required,
        "approval_quorum": quorum,
        "permissions": sorted(set(body.permissions)),
        "audience": (
            required
            if body.context_scope == "household_shared"
            else [session.auth_member_id]
        ),
        "visibility": [owner_scope],
        "requested_at": now,
        "expires_at": body.expires_at.isoformat() if body.expires_at else None,
    }
    envelopes = [
        _envelope(
            session,
            event_type="service.delegation.requested",
            payload=payload,
            request_id=body.request_id,
            owner_scope=owner_scope,
        ),
        _envelope(
            session,
            event_type="service.delegation.approved",
            payload={
                "grant_id": grant_id,
                "approved_by_member_id": session.auth_member_id,
                "approved_at": now,
            },
            request_id=f"{body.request_id}:self-approval",
            owner_scope=owner_scope,
        ),
    ]
    try:
        event_ids = await runtime.events.write_batch(envelopes)
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", max(event_ids))
    projected = next(
        item
        for item in list_service_delegations(
            runtime.projections.connection("parties"),
            session,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        )
        if item["grant_id"] == grant_id
    )
    return {
        "grant_id": grant_id,
        "event_ids": event_ids,
        "active": projected["active"],
        "required_approving_member_ids": required,
        "provider_authority": False,
        "external_write_requests": 0,
    }


def _grant_for_decision(runtime: Any, session: Session, grant_id: str) -> Any:
    if runtime.projections is None:
        raise HTTPException(503, "CoS delegation projection is unavailable")
    row = (
        runtime.projections.connection("parties")
        .execute(
            "SELECT * FROM service_delegation_grants WHERE tenant_id=? AND grant_id=?",
            (session.tenant_id, grant_id),
        )
        .fetchone()
    )
    if row is None:
        raise HTTPException(404, "CoS delegation not found")
    return row


@router.post("/delegations/{grant_id}/approvals")
async def approve_delegation(
    grant_id: str,
    body: DelegationDecision,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None:
        raise HTTPException(503, "CoS delegation authority is unavailable")
    grant = _grant_for_decision(runtime, session, grant_id)
    required = set(json.loads(str(grant["required_approvals_json"])))
    if session.auth_member_id not in required or grant["revoked_at"] is not None:
        raise HTTPException(403, "member is not an active required approver")
    payload = {
        "grant_id": grant_id,
        "approved_by_member_id": session.auth_member_id,
        "approved_at": EventEnvelope.now_utc_iso(),
    }
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type="service.delegation.approved",
                payload=payload,
                request_id=body.request_id,
                owner_scope=str(grant["owner_scope"]),
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", event_id)
    return {"grant_id": grant_id, "event_id": event_id, "approved": True}


@router.delete("/delegations/{grant_id}")
async def revoke_delegation(
    grant_id: str,
    body: DelegationDecision,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None:
        raise HTTPException(503, "CoS delegation authority is unavailable")
    grant = _grant_for_decision(runtime, session, grant_id)
    authorized = set(json.loads(str(grant["required_approvals_json"]))) | {
        str(grant["requested_by_member_id"])
    }
    if session.auth_member_id not in authorized:
        raise HTTPException(403, "member cannot revoke this CoS delegation")
    payload = {
        "grant_id": grant_id,
        "revoked_by_member_id": session.auth_member_id,
        "revoked_at": EventEnvelope.now_utc_iso(),
        "reason": body.reason,
    }
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type="service.delegation.revoked",
                payload=payload,
                request_id=body.request_id,
                owner_scope=str(grant["owner_scope"]),
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", event_id)
    return {"grant_id": grant_id, "event_id": event_id, "revoked": True}


__all__ = ["router"]
