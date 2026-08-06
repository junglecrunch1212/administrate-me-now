"""Responsive member portal and its narrow, authenticated APIs."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from adminme.events.envelope import EventEnvelope
from adminme.lib.scope import allowed_read
from adminme.lib.session import Session
from adminme.member_chat import MemberChatError
from adminme.products.auth import session_from_request
from adminme.products.core import main as core
from adminme.products.member.surfaces import (
    authorize_surface,
    build_surface,
    crm_detail,
    evaluate_query,
    experience_mode,
    navigation,
)
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.products.openclaw.main import (
    LifecycleRequest as OpenClawLifecycleRequest,
    Provenance,
    _task_lifecycle,
)
from adminme.temporary_surfaces import (
    CHILD_ACTIONS,
    CHILD_QUERIES,
    CreateTemporarySurface,
    TemporarySurfaceError,
    TemporarySurfaceSpec,
)


router = APIRouter(tags=["member-portal"])
SessionDep = Annotated[Session, Depends(session_from_request)]
ASSET_ROOT = Path(__file__).with_name("assets")


class ChatRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message: str = Field(min_length=1, max_length=4_000)
    request_id: str = Field(min_length=8, max_length=120)


class PromotionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    audience_member_ids: list[str] = Field(min_length=1, max_length=20)


class TemporaryActionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    verb: Literal[
        "task.complete", "task.defer", "task.snooze", "task.block", "task.unblock"
    ]
    item_id: str = Field(min_length=1, max_length=160)
    expected_last_event_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class MemberLifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    item_kind: Literal["task", "commitment"]
    item_id: str = Field(min_length=1, max_length=160)
    action: Literal["complete", "defer", "snooze", "block", "unblock"]
    expected_last_event_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class ExternalWriteRequest(BaseModel):
    """One member-authorized mutation of a primary external surface."""

    model_config = {"extra": "forbid"}
    provider: Literal[
        "google_calendar", "apple_calendar", "apple_reminders", "apple_notes"
    ]
    provider_account_id: str = Field(min_length=1, max_length=500)
    operation: str = Field(min_length=1, max_length=120)
    target_external_id: str | None = Field(default=None, max_length=1000)
    expected_provider_revision: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=8, max_length=120)


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


_EXTERNAL_OPERATIONS = {
    "google_calendar": {
        "create_event",
        "update_event",
        "delete_event",
        "move_event",
        "respond_event",
    },
    "apple_calendar": {"create_event", "update_event", "delete_event", "move_event"},
    "apple_reminders": {
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_list",
        "update_list",
        "delete_list",
    },
    "apple_notes": {
        "create_note",
        "update_note",
        "append_note",
        "move_note",
        "delete_note",
        "create_folder",
        "update_folder",
        "delete_folder",
    },
}


@router.post("/api/member/v1/external-sync/commands", status_code=202)
async def request_external_write(
    body: ExternalWriteRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "external writes are unavailable while viewing another member"
        )
    if body.operation not in _EXTERNAL_OPERATIONS[body.provider]:
        raise HTTPException(422, "operation is not supported by this provider")
    runtime = _runtime(request)
    now = EventEnvelope.now_utc_iso()
    payload = {
        "command_id": body.request_id,
        "member_id": session.auth_member_id,
        "provider": body.provider,
        "provider_account_id": body.provider_account_id,
        "operation": body.operation,
        "target_external_id": body.target_external_id,
        "expected_provider_revision": body.expected_provider_revision,
        "payload": {
            **body.payload,
            **(
                {"expected_provider_revision": body.expected_provider_revision}
                if body.expected_provider_revision
                else {}
            ),
        },
        "requested_at": now,
    }
    decision = await runtime.guarded_write.check(
        session, "external_sync.write_requested", payload
    )
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type="external_sync.write_requested",
        schema_version=1,
        occurred_at=now,
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity="normal",
        correlation_id=decision.correlation_id,
        actor_identity=session.auth_member_id,
        idempotency_key=f"external-sync:{session.auth_member_id}:{body.request_id}",
        payload=payload,
    )
    event_id = await runtime.events.write(envelope)
    return {"accepted": True, "command_id": body.request_id, "event_id": event_id}


def _chat_policy(runtime: Any, session: Session) -> dict[str, Any]:
    policy = runtime.member_chat_policy(session.auth_member_id, session.auth_role)
    if session.auth_member_id != session.view_member_id:
        return {
            "state": "unauthorized",
            "enabled": False,
            "reason": "view_as_has_no_chat",
        }
    return policy


def _require_chat(runtime: Any, session: Session) -> tuple[str, str]:
    policy = _chat_policy(runtime, session)
    if not policy.get("enabled"):
        raise HTTPException(404, "chat not found")
    if policy.get("state") != "healthy":
        raise HTTPException(503, {"code": policy.get("reason", "chat_unavailable")})
    return runtime.member_chat_binding(session.auth_member_id)


def _safe_error(exc: MemberChatError, correlation_id: str) -> dict[str, Any]:
    return {
        "kind": "error",
        "code": exc.code,
        "retryable": exc.retryable,
        "correlation_id": correlation_id,
    }


def _sse(value: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(value, separators=(',', ':'))}\n\n".encode("utf-8")


async def _expire_due(runtime: Any, session: Session) -> None:
    for spec in runtime.temporary_surfaces.expire_due():
        await _audit_surface(runtime, session, spec, "expired")


async def _audit_surface(
    runtime: Any,
    session: Session,
    spec: TemporarySurfaceSpec,
    transition: Literal["created", "activated", "promoted", "expired", "archived"],
) -> str:
    now = EventEnvelope.now_utc_iso()
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=f"temporary_surface.{transition}",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-portal",
            source_account_id="temporary-surfaces",
            owner_scope=f"private:{spec.owner_member_id}",
            visibility_scope=f"private:{spec.owner_member_id}",
            sensitivity="normal",
            actor_identity=session.auth_member_id,
            payload={
                "stable_id": spec.stable_id,
                "owner_member_id": spec.owner_member_id,
                "actor_member_id": session.auth_member_id,
                "transition": transition,
                "audience_member_ids": list(spec.audience_member_ids),
                "specification_version": spec.updated_version,
                "occurred_at": now,
            },
            idempotency_key=f"temporary-surface:{spec.stable_id}:{transition}:{spec.updated_version}",
        ),
        correlation_id=session.correlation_id,
    )


def _visible_spec(
    runtime: Any, session: Session, stable_id: str
) -> TemporarySurfaceSpec:
    spec = runtime.temporary_surfaces.get(stable_id)
    if (
        spec is None
        or spec.status != "active"
        or session.auth_member_id not in spec.audience_member_ids
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(404, "temporary surface not found")
    if session.auth_role in {"device", "ambient"}:
        raise HTTPException(404, "temporary surface not found")
    if session.auth_role == "child":
        if "child" not in spec.compatible_modes:
            raise HTTPException(404, "temporary surface not found")
        if {query["name"] for query in spec.queries} - CHILD_QUERIES:
            raise HTTPException(404, "temporary surface not found")
        if set(spec.action_verbs) - CHILD_ACTIONS:
            raise HTTPException(404, "temporary surface not found")
    return spec


async def _execute_lifecycle(
    body: MemberLifecycleRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    runtime = _runtime(request)
    if body.item_kind == "task":
        actor_id, signed_session_key = runtime.member_chat_binding(
            session.auth_member_id
        )
        return await _task_lifecycle(
            OpenClawLifecycleRequest(
                provenance=Provenance(
                    actor_id=actor_id,
                    agent_id="administrateme-member-portal",
                    session_key=signed_session_key,
                    session_id=None,
                    channel_id="administrateme-portal",
                    account_id="member-portal",
                    message_id=body.request_id,
                    thread_id=None,
                    correlation_id=session.correlation_id or body.request_id,
                    call_kind="tool",
                    openclaw_version=cast(Literal["2026.7.2"], OPENCLAW_VERSION),
                    openclaw_commit=cast(
                        Literal["944cf08f94c334cff2b0fdf7e01187a9efa42715"],
                        OPENCLAW_COMMIT,
                    ),
                ),
                request_id=body.request_id,
                item_kind="task",
                item_id=body.item_id,
                action=body.action,
                expected_last_event_id=body.expected_last_event_id,
                until=body.until,
                reason=body.reason,
                note=body.note,
            ),
            request,
            session,
        )
    return await core.lifecycle(
        body.item_id,
        body.action,
        core.LifecycleRequest(
            request_id=body.request_id,
            expected_last_event_id=body.expected_last_event_id,
            until=body.until,
            reason=body.reason,
            note=body.note,
        ),
        request,
        session,
    )


@router.get("/", response_class=RedirectResponse)
async def dashboard_root(session: SessionDep) -> RedirectResponse:
    """Send an authenticated household member to the canonical portal URL."""
    return RedirectResponse(url="/member", status_code=307)


@router.get("/member")
@router.get("/member/{surface}")
async def member_page(
    request: Request,
    session: SessionDep,
    surface: str = "today",
) -> FileResponse:
    authorize_surface(session, surface)
    return FileResponse(ASSET_ROOT / "index.html", media_type="text/html")


@router.get("/member/quests/{stable_id}")
async def temporary_surface_page(
    stable_id: str, request: Request, session: SessionDep
) -> FileResponse:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    spec = runtime.temporary_surfaces.get(stable_id)
    if spec is None or session.auth_member_id != session.view_member_id:
        raise HTTPException(404, "temporary surface not found")
    if spec.status == "active":
        _visible_spec(runtime, session, stable_id)
    elif spec.owner_member_id != session.auth_member_id:
        raise HTTPException(404, "temporary surface not found")
    return FileResponse(ASSET_ROOT / "index.html", media_type="text/html")


@router.get("/member-assets/{filename}")
async def member_asset(filename: str) -> FileResponse:
    if filename not in {"member.css", "member.js"}:
        raise HTTPException(404, "asset not found")
    media = "text/css" if filename.endswith(".css") else "text/javascript"
    return FileResponse(ASSET_ROOT / filename, media_type=media)


@router.get("/api/member/v1/bootstrap")
async def bootstrap(request: Request, session: SessionDep) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    return {
        "member": {
            "id": session.view_member_id,
            "role": session.view_role,
            "auth_role": session.auth_role,
        },
        "navigation": navigation(session),
        "mode": experience_mode(runtime, session),
        "observation_mode": runtime._observation_active,
        "chat": _chat_policy(runtime, session),
        "temporary_surfaces": [
            {
                "stable_id": spec.stable_id,
                "title": spec.title,
                "purpose": spec.purpose,
                "compatible_modes": list(spec.compatible_modes),
                "href": f"/member/quests/{spec.stable_id}",
            }
            for spec in runtime.temporary_surfaces.visible_to(session.auth_member_id)
            if session.auth_member_id == session.view_member_id
            and session.auth_role not in {"device", "ambient"}
            and (session.auth_role != "child" or "child" in spec.compatible_modes)
        ],
        "trust": {
            "operator_access": False,
            "actions_as": session.auth_member_id,
            "reads_as": session.view_member_id,
        },
    }


@router.get("/api/member/v1/surfaces/{surface}")
async def surface(
    surface: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    return build_surface(_runtime(request), session, surface)


@router.get("/api/member/v1/people/{party_id}")
async def person_detail(
    party_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    return crm_detail(_runtime(request), session, party_id)


@router.get("/api/member/v1/chat/status")
async def chat_status(request: Request, session: SessionDep) -> dict[str, Any]:
    return _chat_policy(_runtime(request), session)


@router.get("/api/member/v1/chat/history")
async def chat_history(
    request: Request, session: SessionDep, limit: int = 30
) -> dict[str, Any]:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    try:
        items = await runtime.member_chat.history(
            session_key=session_key, limit=min(max(limit, 1), 50)
        )
    except MemberChatError as exc:
        raise HTTPException(
            503, _safe_error(exc, session.correlation_id or str(uuid.uuid4()))
        ) from exc
    return {"items": items, "transcript_owner": "openclaw"}


@router.post("/api/member/v1/chat/stream")
async def chat_stream(
    body: ChatRequest, request: Request, session: SessionDep
) -> StreamingResponse:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    decision = runtime.member_chat_limiter.decide(
        f"chat:{session.auth_member_id}", 60, 12
    )
    if not decision.allowed:
        raise HTTPException(
            429, {"code": "rate_limited", "retry_after_s": decision.retry_after_s}
        )
    correlation_id = session.correlation_id or str(uuid.uuid4())

    async def generate() -> AsyncIterator[bytes]:
        lock = runtime.member_chat_locks.setdefault(
            session.auth_member_id, asyncio.Lock()
        )
        try:
            async with lock:
                async for chunk in runtime.member_chat.stream(
                    session_key=session_key,
                    message=body.message,
                    idempotency_key=f"member:{session.auth_member_id}:{body.request_id}",
                ):
                    public = chunk.public()
                    public["correlation_id"] = correlation_id
                    yield _sse(public)
                    if await request.is_disconnected():
                        await runtime.member_chat.abort(session_key=session_key)
                        return
        except asyncio.CancelledError:
            await runtime.member_chat.abort(session_key=session_key)
            raise
        except MemberChatError as exc:
            yield _sse(_safe_error(exc, correlation_id))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/api/member/v1/chat/abort")
async def chat_abort(request: Request, session: SessionDep) -> dict[str, bool]:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    return {"aborted": await runtime.member_chat.abort(session_key=session_key)}


@router.get("/api/member/v1/evidence/{event_id}")
async def evidence(
    event_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    async for event in runtime.event_log.read_since():
        if event["event_id"] != event_id:
            continue
        visibility = str(event.get("visibility_scope") or "shared:household")
        if visibility not in session.allowed_scopes or not allowed_read(
            session,
            str(event.get("sensitivity") or "normal"),
            str(event.get("owner_scope") or "shared:household"),
        ):
            break
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        safe_fields = {
            key: payload[key]
            for key in (
                "task_id",
                "commitment_id",
                "title",
                "description",
                "decision",
                "outcome",
                "state",
                "reason",
                "confidence",
                "approval_state",
                "delivery_result",
                "correction_of",
            )
            if key in payload
        }
        return {
            "event_id": event["event_id"],
            "type": event["type"],
            "source": {
                "adapter": event["source_adapter"],
                "account": event["source_account_id"],
            },
            "actor": event.get("actor_identity"),
            "occurred_at": event["occurred_at"],
            "ingested_at": event["recorded_at"],
            "confidence": payload.get("confidence"),
            "scope": {
                "owner": event["owner_scope"],
                "visibility": event["visibility_scope"],
                "sensitivity": event["sensitivity"],
            },
            "why": {
                "correlation_id": event.get("correlation_id"),
                "causation_id": event.get("causation_id"),
            },
            "correction": payload.get("correction_of"),
            "approval": payload.get("approval_state") or payload.get("decision"),
            "delivery": payload.get("delivery_result") or payload.get("outcome"),
            "subject": safe_fields,
        }
    raise HTTPException(404, "evidence not found")


@router.post("/api/member/v1/lifecycle")
async def member_lifecycle(
    body: MemberLifecycleRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    result = await _execute_lifecycle(body, request, session)
    return {**result, "next_action": result.get("what_now")}


@router.post("/api/member/v1/temporary-surfaces", status_code=201)
async def create_temporary_surface(
    body: CreateTemporarySurface, request: Request, session: SessionDep
) -> dict[str, Any]:
    if (
        session.auth_role in {"child", "device", "ambient"}
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(403, "adult owner authority required")
    try:
        spec = _runtime(request).temporary_surfaces.create(session.auth_member_id, body)
    except TemporarySurfaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    runtime = _runtime(request)
    event_id = await _audit_surface(runtime, session, spec, "created")
    activation_event_id = await _audit_surface(runtime, session, spec, "activated")
    return {
        "surface": spec.public(),
        "audit_event_id": event_id,
        "activation_event_id": activation_event_id,
    }


@router.get("/api/member/v1/temporary-surfaces/{stable_id}")
async def get_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    return {"surface": _visible_spec(runtime, session, stable_id).public()}


@router.get("/api/member/v1/temporary-surfaces/{stable_id}/evaluate")
async def evaluate_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    spec = _visible_spec(runtime, session, stable_id)
    missing = [
        name
        for name in spec.required_capabilities
        if runtime.capabilities.get(name) is None
        or runtime.capabilities[name].state not in {"healthy", "observation_only"}
    ]
    if missing:
        return {
            "surface": {"stable_id": stable_id, "title": spec.title},
            "state": "degraded",
            "missing_capabilities": missing,
            "results": [],
        }
    results = [
        {
            "query": query["name"],
            "value": evaluate_query(
                runtime, session, query["name"], query.get("params", {})
            ),
        }
        for query in spec.queries
    ]
    return {
        "surface": {
            "stable_id": stable_id,
            "title": spec.title,
            "purpose": spec.purpose,
        },
        "mode": experience_mode(runtime, session),
        "state": "ready",
        "results": results,
        "actions": list(spec.action_verbs),
        "evaluated_fresh": True,
    }


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/promote")
async def promote_temporary_surface(
    stable_id: str, body: PromotionRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    spec = runtime.temporary_surfaces.get(stable_id)
    if (
        spec is None
        or spec.status != "active"
        or spec.owner_member_id != session.auth_member_id
    ):
        raise HTTPException(404, "temporary surface not found")
    if (
        session.auth_role != "principal"
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(403, "adult review required")
    known = set(runtime.settings.member_roles)
    audience = set(body.audience_member_ids)
    if not audience or audience - known:
        raise HTTPException(422, "audience contains an unknown member")
    if any(
        runtime.settings.member_roles[item] in {"device", "ambient"}
        for item in audience
    ):
        raise HTTPException(422, "shared-screen audience is not supported")
    children = {
        item for item in audience if runtime.settings.member_roles[item] == "child"
    }
    if children and (
        "child" not in spec.compatible_modes
        or {query["name"] for query in spec.queries} - CHILD_QUERIES
        or set(spec.action_verbs) - CHILD_ACTIONS
    ):
        raise HTTPException(422, "child audience requires a guardian-safe view")
    try:
        promoted = runtime.temporary_surfaces.promote(
            stable_id,
            actor_member_id=session.auth_member_id,
            audience_member_ids=tuple(audience),
        )
    except TemporarySurfaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    event_id = await _audit_surface(runtime, session, promoted, "promoted")
    return {"surface": promoted.public(), "audit_event_id": event_id}


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/archive")
async def archive_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    try:
        spec = runtime.temporary_surfaces.transition(
            stable_id, actor_member_id=session.auth_member_id, status="archived"
        )
    except TemporarySurfaceError as exc:
        raise HTTPException(404, "temporary surface not found") from exc
    event_id = await _audit_surface(runtime, session, spec, "archived")
    return {"archived": True, "audit_event_id": event_id}


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/actions")
async def temporary_surface_action(
    stable_id: str, body: TemporaryActionRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    spec = _visible_spec(runtime, session, stable_id)
    if body.verb not in spec.action_verbs:
        raise HTTPException(403, "action is not declared by this surface")
    result = await _execute_lifecycle(
        MemberLifecycleRequest(
            item_kind="task",
            item_id=body.item_id,
            action=cast(
                Literal["complete", "defer", "snooze", "block", "unblock"],
                body.verb.removeprefix("task."),
            ),
            expected_last_event_id=body.expected_last_event_id,
            request_id=body.request_id,
            until=body.until,
            reason=body.reason,
            note=body.note,
        ),
        request,
        session,
    )
    return {**result, "next_action": result.get("what_now")}


__all__ = ["router"]
