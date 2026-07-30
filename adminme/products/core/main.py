"""Chief-of-Staff ranking and commitment lifecycle API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from adminme.events.envelope import EventEnvelope
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.products.commitment_service import (
    command_fingerprint,
    emit_command,
    owned_commitment,
    prior_command,
)
from adminme.projections.commitments.queries import active_for_member, pending_approval
from adminme.projections.tasks.queries import open_for_member as open_tasks

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
    candidates: list[dict[str, Any]] = []
    for row in commitments:
        if row["status"] != "pending":
            continue
        candidates.append(
            {
                "id": row["commitment_id"],
                "kind": "commitment",
                "title": row["description"],
                "due": row["due_at"],
                "score": 1000,
                "explanation": {
                    "obligation": "confirmed promise to another party",
                    "urgency": "due date is near",
                    "owner": row["owed_by_party"],
                    "dependency": "none",
                    "energy": "unknown",
                    "effort": "reply",
                },
            }
        )
    for row in tasks:
        candidates.append(
            {
                "id": row["task_id"],
                "kind": "task",
                "title": row["title"],
                "due": row["due_date"],
                "score": 500,
                "explanation": {
                    "obligation": "household task",
                    "urgency": "scheduled fallback",
                    "owner": row["assignee_party"],
                    "dependency": "none",
                    "energy": row["energy"] or "unknown",
                    "effort": row["effort"] or "small",
                },
            }
        )
    candidates.sort(
        key=lambda item: (-item["score"], item["due"] or "9999", item["id"])
    )
    ordered = [{k: v for k, v in item.items() if k != "score"} for item in candidates]
    horizons = ["right_now", "next", "today", "this_week", "this_month", "someday"]
    return {
        "member_id": session.view_member_id,
        "generated_by": "deterministic-ranker-v1",
        "horizons": {
            horizon: (ordered[index] if index < len(ordered) else None)
            for index, horizon in enumerate(horizons)
        },
    }


@router.get("/v1/what-now")
async def what_now(request: Request, session: SessionDep) -> dict[str, Any]:
    return _what_now(request.app.state.runtime, session)


@router.get("/v1/inbox")
async def inbox(request: Request, session: SessionDep) -> dict[str, Any]:
    """Merge proposal review and household work at the authorized read seam."""
    runtime = request.app.state.runtime
    if runtime.projections is None:
        raise HTTPException(503, "inbox projections unavailable")
    proposals = [
        {
            "id": row["commitment_id"],
            "kind": "commitment_proposal",
            "state": "pending_review",
            "title": row["description"],
            "source_interaction_id": row["source_interaction_id"],
            "last_event_id": row["last_event_id"],
            "actions": ["edit", "approve", "dismiss"],
        }
        for row in pending_approval(
            runtime.projections.connection("commitments"), session
        )
        if row["owed_by_party"] == session.view_member_id
    ]
    tasks = [
        {
            "id": row["task_id"],
            "kind": "task",
            "state": row["status"],
            "title": row["title"],
            "source_interaction_id": None,
            "last_event_id": row["last_event_id"],
            "actions": [],
        }
        for row in open_tasks(
            runtime.projections.connection("tasks"),
            session,
            member_party_id=session.view_member_id,
        )
    ]
    return {
        "member_id": session.view_member_id,
        "items": sorted(proposals + tasks, key=lambda item: (item["kind"], item["id"])),
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
