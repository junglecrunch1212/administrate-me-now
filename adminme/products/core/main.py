"""Chief-of-Staff ranking and commitment lifecycle API."""

from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from adminme.events.envelope import EventEnvelope
from adminme.lib.scope import allowed_read
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.products.commitment_service import (
    command_fingerprint,
    emit_command,
    owned_commitment,
    prior_command,
)
from adminme.projections.commitments.queries import active_for_member
from adminme.projections.lists.queries import open_items_for_member
from adminme.projections.recurrences.queries import for_member as recurrences_for_member
from adminme.projections.recurrences.queries import routine_occurrences_at
from adminme.projections.tasks.queries import open_for_member as open_tasks
from adminme.projections.triage.queries import pending_for_member as pending_triage
from adminme.what_now import rank_what_now

router = APIRouter(prefix="/api/core", tags=["core"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class LifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=1, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.product_status("core")


def _what_now(runtime: Any, session: Session) -> dict[str, Any]:
    if runtime.projections is None:
        raise HTTPException(503, "work projections unavailable")
    commitments = active_for_member(
        runtime.projections.connection("commitments"),
        session,
        member_party_id=session.view_member_id,
    )
    tasks = open_tasks(
        runtime.projections.connection("tasks"),
        session,
        member_party_id=session.view_member_id,
    )
    list_items = open_items_for_member(
        runtime.projections.connection("lists"),
        session,
        member_id=session.view_member_id,
    )
    recurrences = recurrences_for_member(
        runtime.projections.connection("recurrences"),
        session,
        member_party_id=session.view_member_id,
    )
    as_of = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    routines = routine_occurrences_at(
        runtime.projections.connection("recurrences"),
        session,
        member_party_id=session.view_member_id,
        as_of_iso=as_of.isoformat(),
    )
    profile = runtime.extensions.profile_for(session.view_member_id)
    return rank_what_now(
        tasks,
        commitments,
        member_id=session.view_member_id,
        profile=profile,
        as_of=as_of,
        list_items=list_items,
        recurrences=recurrences,
        routines=routines,
    )


@router.get("/v1/what-now")
async def what_now(request: Request, session: SessionDep) -> dict[str, Any]:
    return _what_now(request.app.state.runtime, session)


@router.get("/v1/triage")
async def triage(request: Request, session: SessionDep) -> dict[str, Any]:
    """Return only derived candidates awaiting this member's adjudication."""
    runtime = request.app.state.runtime
    if runtime.projections is None:
        raise HTTPException(503, "triage projection unavailable")
    return {
        "member_id": session.view_member_id,
        "surface": "triage",
        "items": pending_triage(
            runtime.projections.connection("triage"),
            session,
            member_id=session.view_member_id,
        ),
    }


@router.get("/v1/inbox", deprecated=True)
async def inbox(request: Request, session: SessionDep) -> dict[str, Any]:
    """Deprecated compatibility alias for pre-D26 clients."""
    result = await triage(request, session)
    return {
        **result,
        "deprecated_alias": "inbox",
        "canonical_path": "/api/core/v1/triage",
    }


@router.get("/v1/activity")
async def activity(
    request: Request,
    session: SessionDep,
    limit: int = 50,
) -> dict[str, Any]:
    """Evidence-rich, scope-filtered activity without exposing event bodies."""

    if limit < 1 or limit > 200:
        raise HTTPException(422, "limit must be between 1 and 200")
    runtime = request.app.state.runtime
    if runtime.event_log is None:
        raise HTTPException(503, "event log unavailable")
    visible: deque[dict[str, Any]] = deque(maxlen=limit)
    safe_payload_fields = (
        "commitment_id",
        "task_id",
        "knowledge_id",
        "related_kind",
        "related_id",
        "title",
        "text_summary",
        "decision",
        "outcome",
        "state",
        "reason",
    )
    async for event in runtime.event_log.read_since():
        visibility = str(event.get("visibility_scope") or "shared:household")
        if not (
            visibility in session.allowed_scopes
            or (visibility.startswith("org:") and "org:*" in session.allowed_scopes)
        ):
            continue
        if not allowed_read(
            session,
            str(event.get("sensitivity") or "normal"),
            str(event.get("owner_scope") or "shared:household"),
        ):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        visible.append(
            {
                "event_id": event["event_id"],
                "type": event["type"],
                "occurred_at": event["occurred_at"],
                "recorded_at": event["recorded_at"],
                "source": {
                    "adapter": event["source_adapter"],
                    "account_id": event["source_account_id"],
                },
                "scope": {
                    "owner": event["owner_scope"],
                    "visibility": event["visibility_scope"],
                    "sensitivity": event["sensitivity"],
                },
                "provenance": {
                    "correlation_id": event.get("correlation_id"),
                    "causation_id": event.get("causation_id"),
                },
                "subject": {
                    field: payload[field]
                    for field in safe_payload_fields
                    if field in payload
                },
            }
        )
    return {
        "member_id": session.view_member_id,
        "items": list(reversed(visible)),
        "payload_policy": "identifier-and-state-fields-only",
    }


@router.get("/v1/commitments/active")
async def active_commitments(request: Request, session: SessionDep) -> dict[str, Any]:
    runtime = request.app.state.runtime
    if runtime.projections is None:
        raise HTTPException(503, "commitments projection unavailable")
    return {
        "items": active_for_member(
            runtime.projections.connection("commitments"),
            session,
            member_party_id=session.view_member_id,
        )
    }


@router.get("/v1/commitments/{commitment_id}")
async def commitment(
    commitment_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    return owned_commitment(request.app.state.runtime, session, commitment_id)


@router.post("/v1/commitments/{commitment_id}/{action}")
async def lifecycle(
    commitment_id: str,
    action: str,
    body: LifecycleRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = request.app.state.runtime
    request_fingerprint = command_fingerprint(body.model_dump(mode="json"))
    now = EventEnvelope.now_utc_iso()
    actor = session.auth_member_id
    event_types = {
        "complete": "commitment.completed",
        "block": "commitment.blocked",
        "unblock": "commitment.unblocked",
        "snooze": "commitment.snoozed",
        "defer": "commitment.deferred",
    }
    event_type = event_types.get(action)
    if event_type is None:
        raise HTTPException(404, "unknown lifecycle action")
    prior = await prior_command(
        runtime,
        session,
        event_type=event_type,
        commitment_id=commitment_id,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        return {
            "event_id": prior[0],
            "commitment": prior[1],
            "duplicate": True,
            "what_now": _what_now(runtime, session),
        }
    row = owned_commitment(runtime, session, commitment_id)
    if row["confirmed_at"] is None:
        raise HTTPException(409, "commitment must be approved first")
    payload: dict[str, Any]
    if action == "complete":
        if row["status"] in {"done", "cancelled", "delegated"}:
            raise HTTPException(409, "commitment is terminal")
        payload = {
            "commitment_id": commitment_id,
            "completed_at": now,
            "completed_by_party_id": actor,
            "completion_note": body.note,
        }
    elif action == "block":
        if row["status"] != "pending" or not body.reason:
            raise HTTPException(
                409, "only active commitments with a reason may be blocked"
            )
        payload = {
            "commitment_id": commitment_id,
            "blocked_at": now,
            "blocked_by_party_id": actor,
            "reason": body.reason,
        }
    elif action == "unblock":
        if row["status"] != "blocked":
            raise HTTPException(409, "commitment is not blocked")
        payload = {
            "commitment_id": commitment_id,
            "unblocked_at": now,
            "unblocked_by_party_id": actor,
            "note": body.note,
        }
    elif action == "snooze":
        if row["status"] != "pending" or not body.until:
            raise HTTPException(409, "active commitment and until are required")
        payload = {
            "commitment_id": commitment_id,
            "snoozed_at": now,
            "snoozed_until": body.until,
            "snoozed_by_party_id": actor,
        }
    elif action == "defer":
        if row["status"] not in {"pending", "snoozed"} or not body.until:
            raise HTTPException(
                409, "active or snoozed commitment and until are required"
            )
        payload = {
            "commitment_id": commitment_id,
            "deferred_at": now,
            "deferred_until": body.until,
            "deferred_by_party_id": actor,
            "reason": body.reason,
        }
    else:  # event_type validation above keeps this unreachable at runtime.
        raise AssertionError(f"unhandled lifecycle action: {action}")
    event_id, updated, duplicate = await emit_command(
        runtime,
        session,
        commitment_id=commitment_id,
        event_type=event_type,
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        expected_last_event_id=body.expected_last_event_id,
    )
    if action == "complete" and runtime.xlsx_projection is not None:
        await runtime.xlsx_projection.regenerate_now("adminme-ops.xlsx")
    return {
        "event_id": event_id,
        "commitment": updated,
        "duplicate": duplicate,
        "what_now": _what_now(runtime, session),
    }


__all__ = ["router"]
