"""Capture/Knowledge router.

Quick-prefix capture, direct voice ingestion, and triage are intentionally not
routes in v2.  Member-owned knowledge arrives through the Cat-D bridge.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from adminme.domain_search import domain_vector
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.projections.member_knowledge.queries import get_visible, list_visible
from adminme.projections.vector_search.queries import nearest

router = APIRouter(prefix="/api/capture", tags=["capture-knowledge"])
SessionDep = Annotated[Session, Depends(session_from_request)]


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.product_status("capture")


def _connections(request: Request) -> tuple[Any, Any]:
    projections = request.app.state.runtime.projections
    if projections is None:
        raise HTTPException(503, "knowledge projections unavailable")
    return (
        projections.connection("member_knowledge"),
        projections.connection("vector_search"),
    )


def _require_private_surface(session: Session) -> None:
    if session.auth_role in {"ambient", "device"}:
        raise HTTPException(403, "member knowledge has no ambient/device surface")
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "private member knowledge cannot be viewed-as")


@router.get("/v1/knowledge")
async def knowledge_list(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_private_surface(session)
    knowledge_conn, _ = _connections(request)
    return {
        "member_id": session.auth_member_id,
        "items": list_visible(knowledge_conn, session),
    }


@router.get("/v1/knowledge/{knowledge_id}")
async def knowledge_item(
    knowledge_id: str,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_private_surface(session)
    knowledge_conn, _ = _connections(request)
    item = get_visible(knowledge_conn, session, knowledge_id=knowledge_id)
    if item is None:
        raise HTTPException(404, "knowledge item not found")
    return item


@router.get("/v1/search")
async def domain_search(
    request: Request,
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> dict[str, Any]:
    """Search only the authenticated member's non-privileged domain index."""

    _require_private_surface(session)
    knowledge_conn, vector_conn = _connections(request)
    matches = nearest(vector_conn, session, query_vector=domain_vector(q), k=limit)
    items: list[dict[str, Any]] = []
    for match in matches:
        if match.get("linked_kind") != "member_knowledge":
            continue
        item = get_visible(
            knowledge_conn,
            session,
            knowledge_id=str(match["linked_id"]),
        )
        if item is None or item.get("sensitivity") == "privileged":
            continue
        items.append(
            {
                "knowledge": item,
                "distance": match["distance"],
                "provenance": {
                    key: match.get(key)
                    for key in (
                        "source_event_id",
                        "source_adapter",
                        "source_account_id",
                        "correlation_id",
                        "causation_id",
                    )
                },
            }
        )
    return {"member_id": session.auth_member_id, "query": q, "items": items}


__all__ = ["router"]
