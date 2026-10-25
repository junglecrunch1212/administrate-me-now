"""Direct-owner Books metadata discovery and reversible, encrypted access controls."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from adminme.events.envelope import EventEnvelope
from adminme.finance.metadata import inspect_metadata
from adminme.finance.metadata_control import (
    CONTROL_EVENT,
    control_lock,
    control_state,
    owner_allowed,
)
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.products.member.main import (
    _guard_member_write,
    _prior_settings_receipt,
    _settings_request_fingerprint,
    _write_settings_receipt,
)

router = APIRouter(tags=["member-finance-metadata"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class MetadataSelection(BaseModel):
    model_config = {"extra": "forbid"}
    book_id: str | None = Field(default=None, min_length=1, max_length=120)
    household_party_id: str | None = Field(default=None, min_length=1, max_length=120)


class MetadataControlRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_revision: int = Field(ge=0, strict=True)
    owner_read_enabled: StrictBool
    cos_read_enabled: StrictBool


def require_current_owner(request: Request, session: Session) -> None:
    if (
        not owner_allowed(request.app.state.runtime, session)
        or session_from_request(request) != session
    ):
        raise HTTPException(403, "finance_metadata_current_owner_required")


async def current_control(runtime: Any, session: Session) -> dict[str, Any]:
    try:
        return await control_state(runtime, session)
    except RuntimeError:
        raise HTTPException(503, "finance_metadata_control_unavailable") from None


@router.get("/api/member/v1/settings/finance-metadata")
async def get_metadata_control(request: Request, session: SessionDep) -> dict[str, Any]:
    require_current_owner(request, session)
    result = await current_control(request.app.state.runtime, session)
    require_current_owner(request, session)
    return {**result, "provider_effect_count": 0}


@router.put("/api/member/v1/settings/finance-metadata")
async def set_metadata_control(
    body: MetadataControlRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_current_owner(request, session)
    runtime = request.app.state.runtime
    fingerprint = _settings_request_fingerprint(
        CONTROL_EVENT, body.model_dump(exclude={"request_id"})
    )
    async with control_lock(runtime, session):
        prior = await _prior_settings_receipt(
            runtime,
            session,
            event_type=CONTROL_EVENT,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        current = await current_control(runtime, session)
        if prior is not None:
            require_current_owner(request, session)
            return {**current, "idempotent": True, "provider_effect_count": 0}
        if current["revision"] != body.expected_revision:
            raise HTTPException(409, "finance_metadata_control_changed")
        payload = {
            "owner_id": session.auth_member_id,
            "changed_by_member_id": session.auth_member_id,
            "revision": current["revision"] + 1,
            "prior_event_id": current["event_id"],
            "owner_read_enabled": body.owner_read_enabled,
            "cos_read_enabled": body.cos_read_enabled,
            "provider_effects": False,
        }
        await _guard_member_write(runtime, session, CONTROL_EVENT, payload)
        require_current_owner(request, session)
        event_id = await _write_settings_receipt(
            runtime,
            session,
            event_type=CONTROL_EVENT,
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            owner_member_id=session.auth_member_id,
            sensitivity="privileged",
        )
        require_current_owner(request, session)
        return {
            "revision": payload["revision"],
            "event_id": event_id,
            "owner_read_enabled": body.owner_read_enabled,
            "cos_read_enabled": body.cos_read_enabled,
            "provider_effect_count": 0,
        }


async def audit_metadata_read(
    runtime: Any, session: Session, policy: dict[str, Any], result: dict[str, Any]
) -> str:
    completeness = result.get("completeness", {})
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="finance.metadata_inspected",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="member-portal",
            source_account_id=session.auth_member_id,
            actor_identity=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="privileged",
            payload={
                "owner_id": session.auth_member_id,
                "access_path": "authenticated_member",
                "policy_revision": policy["revision"],
                "state": result["state"],
                "complete": completeness.get("complete") is True,
                "through_event_id": completeness.get("through_event_id"),
                "history_sha256": completeness.get("history_sha256"),
                "provider_effects": False,
            },
        ),
        correlation_id=session.correlation_id,
    )


@router.post("/api/member/v1/finance/bindings")
async def owner_finance_bindings(
    body: MetadataSelection, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_current_owner(request, session)
    runtime = request.app.state.runtime
    async with control_lock(runtime, session):
        policy = await current_control(runtime, session)
        result: dict[str, Any] = {
            "state": "access_unavailable",
            "bindings": [],
            "completeness": {"complete": False},
        }
        if policy["owner_read_enabled"]:
            result = await inspect_metadata(
                runtime, session, body.book_id, body.household_party_id
            )
        await audit_metadata_read(runtime, session, policy, result)
        latest_policy = await current_control(runtime, session)
        require_current_owner(request, session)
        if latest_policy != policy:
            raise HTTPException(403, "finance_metadata_control_changed")
        # No await after final policy/identity checks; the supported transition
        # holds this same lock, so disable cannot overtake a disclosed result.
        return {**result, "provider_effect_count": 0}
