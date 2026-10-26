"""Owner-authenticated, durable and revocable Books analysis controls."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from adminme.events.schemas.finance_access import FinanceBookBinding
from adminme.finance.access_control import (
    CONTROL_EVENT,
    FinanceAccessError,
    control_lock,
    refresh_control,
)
from adminme.finance.metadata_control import owner_allowed
from adminme.finance import release
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.products.member.main import (
    _guard_member_write,
    _prior_settings_receipt,
    _settings_request_fingerprint,
    _write_settings_receipt,
)

router = APIRouter(tags=["member-finance-access"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class AccessRequest(BaseModel):
    model_config = {"extra": "forbid", "hide_input_in_errors": True}
    request_id: str = Field(min_length=8, max_length=120)
    expected_revision: int = Field(ge=0, strict=True)
    binding: FinanceBookBinding
    books_enabled: StrictBool
    cos_analysis_enabled: StrictBool


def require_owner(request: Request, session: Session) -> None:
    if (
        not owner_allowed(request.app.state.runtime, session)
        or session_from_request(request) != session
    ):
        raise HTTPException(403, "finance_current_owner_required")


async def current_control(
    runtime: Any, session: Session, *, settle_change: bool = False
):
    try:
        return await refresh_control(
            runtime, session.auth_member_id, settle_change=settle_change
        )
    except FinanceAccessError:
        raise HTTPException(503, "finance_control_unavailable") from None


@router.get("/api/member/v1/settings/finance")
async def get_finance_access(request: Request, session: SessionDep) -> dict[str, Any]:
    require_owner(request, session)
    state = await current_control(request.app.state.runtime, session)
    require_owner(request, session)
    return {
        **state.authority_coordinates(),
        "advisor_available": release.ADVISOR_AVAILABLE,
        "provider_effect_count": 0,
    }


@router.put("/api/member/v1/settings/finance")
async def set_finance_access(
    body: AccessRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_owner(request, session)
    if body.cos_analysis_enabled and not body.books_enabled:
        raise HTTPException(422, "finance_analysis_requires_books")
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
        if (
            body.cos_analysis_enabled
            and not current.cos_analysis_enabled
            and not release.ADVISOR_AVAILABLE
        ):
            raise HTTPException(409, "finance_advisor_not_released")
        if prior is not None:
            require_owner(request, session)
            return {
                **current.authority_coordinates(),
                "advisor_available": release.ADVISOR_AVAILABLE,
                "idempotent": True,
                "provider_effect_count": 0,
            }
        if current.revision != body.expected_revision:
            raise HTTPException(409, "finance_control_changed")
        if current.binding is not None and current.binding != body.binding:
            raise HTTPException(409, "finance_existing_book_binding_required")
        payload = {
            "owner_id": session.auth_member_id,
            "changed_by_member_id": session.auth_member_id,
            "revision": current.revision + 1,
            "prior_event_id": current.event_id,
            "binding": body.binding.model_dump(),
            "books_enabled": body.books_enabled,
            "cos_analysis_enabled": body.cos_analysis_enabled,
            "provider_effects": False,
        }
        await _guard_member_write(runtime, session, CONTROL_EVENT, payload)
        require_owner(request, session)
        # Close all Finance delivery before the append yields. A failed or
        # ambiguous append never restores permission from the request body.
        runtime.finance_access.begin_change(session.tenant_id, session.auth_member_id)
        try:
            await _write_settings_receipt(
                runtime,
                session,
                event_type=CONTROL_EVENT,
                payload=payload,
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                sensitivity="privileged",
            )
        finally:
            # Durable read-back reconciles both success and an ambiguous result.
            # A read failure leaves this owner blocked; restart reloads the log.
            latest = await current_control(runtime, session, settle_change=True)
        require_owner(request, session)
        return {
            **latest.authority_coordinates(),
            "advisor_available": release.ADVISOR_AVAILABLE,
            "idempotent": False,
            "provider_effect_count": 0,
        }
