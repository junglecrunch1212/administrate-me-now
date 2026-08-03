"""Authenticated loopback transport for the one native OpenClaw plugin.

OpenClaw authenticates the transport; AdministrateMe independently maps the
OpenClaw actor/channel/account tuple to a household member.  A gateway bearer
token, operator scope, or ``senderIsOwner`` value never authorizes household
data access.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.log import IdempotencyConflict
from adminme.lib.scope import filter_rows
from adminme.lib.session import Session, Source
from adminme.lib.skill_runner.wrapper import SkillContext
from adminme.products.comms import main as comms
from adminme.products.core import main as core
from adminme.products.openclaw.constants import (
    BRIDGE_EVENT_KINDS,
    OPENCLAW_COMMIT,
    OPENCLAW_VERSION,
    PHASE3_SKILL_IDS,
    PLUGIN_ID,
)
from adminme.projections.tasks.queries import get_task

router = APIRouter(prefix="/api/openclaw/v1", tags=["openclaw"])


class Provenance(BaseModel):
    model_config = {"extra": "forbid"}
    actor_id: str = Field(min_length=1, max_length=300)
    agent_id: str | None = Field(default=None, max_length=200)
    session_key: str | None = Field(default=None, max_length=300)
    session_id: str | None = Field(default=None, max_length=300)
    channel_id: str = Field(min_length=1, max_length=200)
    account_id: str | None = Field(default=None, max_length=200)
    message_id: str | None = Field(default=None, max_length=300)
    thread_id: str | None = Field(default=None, max_length=300)
    correlation_id: str = Field(min_length=1, max_length=300)
    call_kind: Literal["tool", "dashboard", "slash_command", "standing_order", "bridge"]
    openclaw_version: Literal["2026.7.2"]
    openclaw_commit: Literal["944cf08f94c334cff2b0fdf7e01187a9efa42715"]

    def audit_payload(self, *, member_id: str) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "member_id": member_id,
            "agent_id": self.agent_id,
            "session_key": self.session_key,
            "session_id": self.session_id,
            "channel_id": self.channel_id,
            "account_id": self.account_id,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "correlation_id": self.correlation_id,
            "call_kind": self.call_kind,
            "openclaw_version": self.openclaw_version,
            "openclaw_commit": self.openclaw_commit,
        }


class ContextRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provenance: Provenance


class ActionRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    mode: Literal["propose", "capture"]
    kind: Literal["task", "commitment"]
    title: str = Field(min_length=1, max_length=500)
    due: str | None = None
    owed_to_party_id: str | None = Field(default=None, min_length=1)


class ReviewRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    action: Literal["inspect", "approve", "dismiss", "edit"]
    commitment_id: str | None = Field(default=None, min_length=1)
    expected_last_event_id: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    due_at: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    send_acknowledgement: bool = False
    openclaw_approved: bool = False


class LifecycleRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    item_kind: Literal["task", "commitment"]
    item_id: str = Field(min_length=1)
    action: Literal["complete", "defer", "snooze", "block", "unblock"]
    expected_last_event_id: str = Field(min_length=1)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class CorrectionRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    item_kind: Literal["task", "commitment"]
    item_id: str = Field(min_length=1)
    expected_last_event_id: str = Field(min_length=1)
    fields: dict[str, Any]


class SkillRequest(ContextRequest):
    skill_id: str = Field(min_length=1, max_length=120)
    inputs: dict[str, Any]
    input_sensitivities: dict[str, Literal["normal", "sensitive", "privileged"]] = (
        Field(default_factory=dict)
    )
    triggering_event_id: str | None = None


class BridgeRequest(ContextRequest):
    event_kind: Literal["commitment_candidate", "household_action", "delivery_outcome"]
    content: str = Field(min_length=1, max_length=2000)
    consent: Literal[True]
    sensitivity: Literal["normal", "sensitive"] = "normal"


class StandingOrderRequest(ContextRequest):
    program_id: str = Field(min_length=1, max_length=120)
    automation_id: str = Field(min_length=1, max_length=200)
    trigger_id: str = Field(min_length=1, max_length=300)


class ModelCallRequest(ContextRequest):
    phase: Literal["started", "ended"]
    run_id: str = Field(min_length=1, max_length=300)
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=300)


class NotificationResultRequest(ContextRequest):
    attempt_id: str = Field(min_length=1, max_length=240)
    decision_id: str = Field(min_length=1, max_length=240)
    signal_id: str = Field(min_length=1, max_length=240)
    outcome: Literal["delivered", "failed", "retrying"]
    attempted_at: str = Field(min_length=1)
    provider_receipt_id: str | None = Field(default=None, max_length=500)
    error_code: str | None = Field(default=None, max_length=120)
    next_retry_at: str | None = None


class HealthReport(BaseModel):
    model_config = {"extra": "forbid"}
    state: Literal["healthy", "degraded", "unavailable", "incompatible"]
    reason: str | None = Field(default=None, max_length=500)
    openclaw_version: str = Field(min_length=1)
    openclaw_commit: str = Field(min_length=7)


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


def _authenticate(request: Request) -> None:
    runtime = _runtime(request)
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "OpenClaw transport is loopback-only")
    expected = runtime.settings.openclaw_shared_secret
    supplied = request.headers.get("Authorization", "")
    if not expected or not supplied.startswith("Bearer "):
        raise HTTPException(401, "OpenClaw transport credential required")
    if not hmac.compare_digest(supplied[7:], expected):
        raise HTTPException(401, "OpenClaw transport credential rejected")


def _session(request: Request, provenance: Provenance) -> Session:
    _authenticate(request)
    runtime = _runtime(request)
    account = provenance.account_id or "-"
    key = f"{provenance.channel_id}|{account}|{provenance.actor_id}"
    member_id = runtime.settings.openclaw_actor_identities.get(key)
    if not member_id:
        raise HTTPException(403, "OpenClaw actor is not mapped to a household member")
    role = runtime.settings.member_roles.get(member_id)
    if role not in {"principal", "child", "device"}:
        raise HTTPException(
            403, "mapped OpenClaw actor has no interactive household role"
        )
    source: Source
    if provenance.call_kind == "slash_command":
        source = "openclaw_slash_command"
    elif provenance.call_kind == "standing_order":
        source = "openclaw_standing_order"
    else:
        source = "openclaw_plugin"
    return Session(
        tenant_id=runtime.settings.instance.tenant_id,
        auth_member_id=member_id,
        auth_role=role,
        view_member_id=member_id,
        view_role=role,
        dm_scope="per_channel_peer",
        source=source,
        correlation_id=provenance.correlation_id,
    )


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _append(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    source_account_id: str,
    owner_scope: str,
    visibility_scope: str,
    sensitivity: Sensitivity = "normal",
    request_id: str | None = None,
    causation_id: str | None = None,
    request_fingerprint: str | None = None,
) -> tuple[str, bool]:
    key = (
        f"openclaw:{session.auth_member_id}:{event_type}:{request_id}"
        if request_id
        else None
    )
    fingerprint = request_fingerprint or _fingerprint(payload)
    if key:
        prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
        if prior is not None:
            if prior["raw_ref"] != f"request-sha256:{fingerprint}":
                raise HTTPException(409, "idempotency key conflicts with prior body")
            return prior["event_id"], True
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="openclaw-plugin",
        source_account_id=source_account_id,
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
        actor_identity=session.auth_member_id,
        idempotency_key=key,
        raw_ref=f"request-sha256:{fingerprint}" if key else None,
        payload=payload,
    )
    try:
        event_id = await runtime.events.write(
            envelope,
            correlation_id=session.correlation_id,
            causation_id=causation_id,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, "idempotency key conflicts with prior body") from exc
    return event_id, False


async def _prior_append(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    request_fingerprint: str,
) -> str | None:
    key = f"openclaw:{session.auth_member_id}:{event_type}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{request_fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior body")
    return prior["event_id"]


async def _record_tool_call(
    request: Request,
    session: Session,
    provenance: Provenance,
    *,
    operation: str,
    result_event_ids: list[str] | None = None,
) -> str:
    payload = {
        "operation": operation,
        "result_event_ids": list(result_event_ids or []),
        **provenance.audit_payload(member_id=session.auth_member_id),
    }
    event_id, _ = await _append(
        _runtime(request),
        session,
        event_type="openclaw.tool_call.recorded",
        source_account_id=provenance.account_id or provenance.channel_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload=payload,
        request_id=f"{operation}:{provenance.correlation_id}",
        causation_id=result_event_ids[-1] if result_event_ids else None,
    )
    return event_id


def _result_event_ids(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    value = result.get("event_id")
    return [value] if isinstance(value, str) and value else []


async def _guard(runtime: Any, session: Session, action: str, payload: dict) -> None:
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, action, payload)
    if not decision.pass_:
        raise HTTPException(
            403, {"code": "write_denied", "layer": decision.layer_failed}
        )


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    _authenticate(request)
    runtime = _runtime(request)
    return {
        "plugin_id": PLUGIN_ID,
        "expected_openclaw_version": OPENCLAW_VERSION,
        "expected_openclaw_commit": OPENCLAW_COMMIT,
        "compatible": True,
        "runtime": await runtime.diagnostics(),
    }


@router.post("/health/report")
async def report_health(body: HealthReport, request: Request) -> dict[str, Any]:
    _authenticate(request)
    runtime = _runtime(request)
    compatible = (
        body.openclaw_version == OPENCLAW_VERSION
        and body.openclaw_commit == OPENCLAW_COMMIT
    )
    state = body.state if compatible else "incompatible"
    session = Session(
        tenant_id=runtime.settings.instance.tenant_id,
        auth_member_id="openclaw-plugin",
        auth_role="device",
        view_member_id="openclaw-plugin",
        view_role="device",
        dm_scope="shared",
        source="openclaw_plugin",
        correlation_id=f"openclaw-health:{int(time.time())}",
    )
    event_id, _ = await _append(
        runtime,
        session,
        event_type="openclaw.integration.state_changed",
        source_account_id="health",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        payload={
            "state": state,
            "observed_at": EventEnvelope.now_utc_iso(),
            "openclaw_version": body.openclaw_version,
            "openclaw_commit": body.openclaw_commit,
            "reason": body.reason,
        },
    )
    runtime.openclaw_state = state
    await runtime._refresh_capability_registry()
    if not compatible:
        raise HTTPException(
            409, {"code": "incompatible_openclaw", "event_id": event_id}
        )
    return {"event_id": event_id, "state": state}


@router.post("/tools/state")
async def inspect_state(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = _runtime(request)
    result = {
        "what_now": core._what_now(runtime, session),
        "inbox": await core.inbox(request, session),
        "commitments": await core.active_commitments(request, session),
    }
    await _record_tool_call(request, session, body.provenance, operation="tools.state")
    return result


@router.post("/tools/what-now")
async def what_now(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    result = core._what_now(_runtime(request), session)
    await _record_tool_call(
        request, session, body.provenance, operation="tools.what-now"
    )
    return result


@router.post("/tools/action")
async def propose_or_capture(body: ActionRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = _runtime(request)
    observation_active = await runtime.observation.is_active()
    payload: dict[str, Any]
    if body.mode == "propose" or observation_active:
        payload = {
            "proposal_id": f"ocp_{body.request_id}",
            "kind": body.kind,
            "title": body.title,
            "owner_member_id": session.auth_member_id,
            "due": body.due,
            "reason": (
                "observation_mode_active" if observation_active else "explicit_proposal"
            ),
            "provenance": body.provenance.model_dump(mode="json"),
        }
        await _guard(runtime, session, "household.action.proposed", payload)
        event_id, duplicate = await _append(
            runtime,
            session,
            event_type="household.action.proposed",
            source_account_id="actions",
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=_fingerprint(
                body.model_dump(mode="json", exclude={"provenance"})
            ),
        )
        await _record_tool_call(
            request,
            session,
            body.provenance,
            operation="tools.action",
            result_event_ids=[event_id],
        )
        return {
            "state": "proposal_only",
            "observation_active": observation_active,
            "event_id": event_id,
            "duplicate": duplicate,
        }

    if body.kind == "task":
        event_type = "task.created"
        item_id = f"oct_{body.request_id}"
        payload = {
            "task_id": item_id,
            "title": body.title,
            "description": None,
            "owner_member_id": session.auth_member_id,
            "due": body.due,
            "energy": None,
            "effort_min": None,
            "source_commitment_id": None,
        }
        projection = "tasks"
    else:
        if not body.owed_to_party_id:
            raise HTTPException(422, "owed_to_party_id is required for a commitment")
        event_type = "commitment.proposed"
        item_id = f"occ_{body.request_id}"
        payload = {
            "commitment_id": item_id,
            "kind": "task",
            "owed_by_member_id": session.auth_member_id,
            "owed_to_party_id": body.owed_to_party_id,
            "text_summary": body.title,
            "suggested_due": body.due,
            "urgency": "this_week",
            "confidence": 1.0,
            "strength": "confident",
            "source_interaction_id": None,
            "source_message_preview": None,
            "classify_reasons": ["explicit member capture through OpenClaw"],
        }
        projection = "commitments"
    await _guard(runtime, session, event_type, payload)
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type=event_type,
        source_account_id="actions",
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await runtime.wait_for_projection(projection, event_id)
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.action",
        result_event_ids=[event_id],
    )
    return {
        "state": "captured",
        "item_id": item_id,
        "event_id": event_id,
        "duplicate": duplicate,
        "what_now": core._what_now(runtime, session),
    }


@router.post("/tools/review")
async def review(body: ReviewRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    if body.action == "inspect":
        result = await comms.pending(request, session)
        await _record_tool_call(
            request, session, body.provenance, operation="tools.review.inspect"
        )
        return result
    if not body.commitment_id or not body.expected_last_event_id:
        raise HTTPException(
            422, "commitment_id and expected_last_event_id are required"
        )
    if body.action == "approve":
        result = await comms.approve_commitment(
            body.commitment_id,
            comms.ApproveRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                send_acknowledgement=body.send_acknowledgement,
                openclaw_approved=body.openclaw_approved,
            ),
            request,
            session,
        )
    elif body.action == "dismiss":
        result = await comms.dismiss_commitment(
            body.commitment_id,
            comms.DismissRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                reason=body.reason,
            ),
            request,
            session,
        )
    else:
        result = await comms.edit_commitment(
            body.commitment_id,
            comms.EditRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                description=body.description,
                due_at=body.due_at,
            ),
            request,
            session,
        )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"tools.review.{body.action}",
        result_event_ids=_result_event_ids(result),
    )
    return result


async def _task_lifecycle(
    body: LifecycleRequest, request: Request, session: Session
) -> dict[str, Any]:
    runtime = _runtime(request)
    event_type = "task.completed" if body.action == "complete" else "task.updated"
    append_request_id = f"{body.item_id}:{body.request_id}"
    request_fingerprint = _fingerprint(
        body.model_dump(mode="json", exclude={"provenance"})
    )
    prior_event_id = await _prior_append(
        runtime,
        session,
        event_type=event_type,
        request_id=append_request_id,
        request_fingerprint=request_fingerprint,
    )
    row = get_task(
        runtime.projections.connection("tasks"), session, task_id=body.item_id
    )
    if row is None or (
        row["assignee_party"] not in {None, session.auth_member_id}
        or (row["assignee_party"] is None and session.auth_role != "principal")
    ):
        raise HTTPException(404, "task not found")
    if prior_event_id is not None:
        return {
            "event_id": prior_event_id,
            "task": row,
            "duplicate": True,
            "what_now": core._what_now(runtime, session),
        }
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(
            409, {"code": "stale_write", "current": row["last_event_id"]}
        )
    now = EventEnvelope.now_utc_iso()
    payload: dict[str, Any]
    if body.action == "complete":
        payload = {
            "task_id": body.item_id,
            "completed_by_member_id": session.auth_member_id,
            "completed_at": now,
            "note": body.note,
        }
    else:
        statuses = {
            "defer": "deferred",
            "snooze": "deferred",
            "block": "waiting_on",
            "unblock": "next",
        }
        if body.action in {"defer", "snooze"} and not body.until:
            raise HTTPException(409, "until is required")
        if body.action == "block" and not body.reason:
            raise HTTPException(409, "reason is required")
        updates: dict[str, Any] = {}
        if body.until:
            updates["due_date"] = body.until
        if body.action == "block":
            updates["waiting_on"] = body.reason
        if body.action == "unblock":
            updates["waiting_on"] = None
        payload = {
            "task_id": body.item_id,
            "updated_at": now,
            "updated_by_party_id": session.auth_member_id,
            "previous_status": row["status"],
            "new_status": statuses[body.action],
            "field_updates": updates,
        }
    await _guard(runtime, session, event_type, payload)
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type=event_type,
        source_account_id="tasks",
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=row["sensitivity"],
        payload=payload,
        request_id=append_request_id,
        causation_id=row["last_event_id"],
        request_fingerprint=request_fingerprint,
    )
    await runtime.wait_for_projection("tasks", event_id)
    updated = get_task(
        runtime.projections.connection("tasks"), session, task_id=body.item_id
    )
    return {
        "event_id": event_id,
        "task": updated,
        "duplicate": duplicate,
        "what_now": core._what_now(runtime, session),
    }


@router.post("/tools/lifecycle")
async def lifecycle(body: LifecycleRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    if body.item_kind == "task":
        result = await _task_lifecycle(body, request, session)
    else:
        result = await core.lifecycle(
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
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"tools.lifecycle.{body.item_kind}.{body.action}",
        result_event_ids=_result_event_ids(result),
    )
    return result


@router.post("/tools/correct")
async def correct(body: CorrectionRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    if body.item_kind == "commitment":
        unsupported = set(body.fields) - {"description", "due_at"}
        if unsupported:
            raise HTTPException(
                422, f"unsupported commitment fields: {sorted(unsupported)}"
            )
        result = await comms.edit_commitment(
            body.item_id,
            comms.EditRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                description=body.fields.get("description"),
                due_at=body.fields.get("due_at"),
            ),
            request,
            session,
        )
        await _record_tool_call(
            request,
            session,
            body.provenance,
            operation="tools.correct.commitment",
            result_event_ids=_result_event_ids(result),
        )
        return result
    allowed = {"title", "due_date", "energy", "effort", "notes"}
    if not body.fields or set(body.fields) - allowed:
        raise HTTPException(422, "task correction contains unsupported fields")
    runtime = _runtime(request)
    append_request_id = f"{body.item_id}:{body.request_id}"
    request_fingerprint = _fingerprint(
        body.model_dump(mode="json", exclude={"provenance"})
    )
    prior_event_id = await _prior_append(
        runtime,
        session,
        event_type="task.updated",
        request_id=append_request_id,
        request_fingerprint=request_fingerprint,
    )
    row = get_task(
        runtime.projections.connection("tasks"), session, task_id=body.item_id
    )
    if row is None or row["assignee_party"] != session.auth_member_id:
        raise HTTPException(404, "task not found")
    if prior_event_id is not None:
        result = {"event_id": prior_event_id, "duplicate": True, "task": row}
        await _record_tool_call(
            request,
            session,
            body.provenance,
            operation="tools.correct.task",
            result_event_ids=[prior_event_id],
        )
        return result
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(
            409, {"code": "stale_write", "current": row["last_event_id"]}
        )
    payload = {
        "task_id": body.item_id,
        "updated_at": EventEnvelope.now_utc_iso(),
        "updated_by_party_id": session.auth_member_id,
        "previous_status": row["status"],
        "new_status": None,
        "field_updates": body.fields,
    }
    await _guard(runtime, session, "task.updated", payload)
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="task.updated",
        source_account_id="tasks",
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=row["sensitivity"],
        payload=payload,
        request_id=append_request_id,
        causation_id=row["last_event_id"],
        request_fingerprint=request_fingerprint,
    )
    await runtime.wait_for_projection("tasks", event_id)
    result = {
        "event_id": event_id,
        "duplicate": duplicate,
        "task": get_task(
            runtime.projections.connection("tasks"), session, task_id=body.item_id
        ),
    }
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.correct.task",
        result_event_ids=[event_id],
    )
    return result


@router.post("/tools/skill")
async def invoke_skill(body: SkillRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    if body.skill_id not in PHASE3_SKILL_IDS:
        raise HTTPException(403, "skill is outside the Phase 3 allowlist")
    runtime = _runtime(request)
    result = await runtime.run_skill_fn(
        body.skill_id,
        body.inputs,
        SkillContext(
            session=session,
            triggering_event_id=body.triggering_event_id,
            observation_mode_active=await runtime.observation.is_active(),
        ),
        input_sensitivities=body.input_sensitivities,
    )
    response = {
        "output": result.output,
        "openclaw_invocation_id": result.openclaw_invocation_id,
        "provider": result.provider,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "duration_ms": result.duration_ms,
    }
    await _record_tool_call(
        request, session, body.provenance, operation=f"tools.skill.{body.skill_id}"
    )
    return response


@router.post("/bridge/events")
async def bridge_event(body: BridgeRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    if body.event_kind not in BRIDGE_EVENT_KINDS:
        raise HTTPException(422, "domain event is not allowlisted")
    payload = {
        "event_kind": body.event_kind,
        "content": body.content,
        "consent": True,
        "agent_id": body.provenance.agent_id,
        "session_key": body.provenance.session_key,
        "session_id": body.provenance.session_id,
        "channel_id": body.provenance.channel_id,
        "account_id": body.provenance.account_id,
        "message_id": body.provenance.message_id,
        "thread_id": body.provenance.thread_id,
        "correlation_id": body.provenance.correlation_id,
    }
    event_id, _ = await _append(
        _runtime(request),
        session,
        event_type="openclaw.domain_event.observed",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity=body.sensitivity,
        payload=payload,
        request_id=(
            f"{body.provenance.channel_id}:{body.provenance.account_id or '-'}:"
            f"{body.provenance.message_id or body.provenance.correlation_id}"
        ),
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"bridge.{body.event_kind}",
        result_event_ids=[event_id],
    )
    return {"accepted": True, "event_id": event_id}


@router.post("/notifications/result")
async def notification_result(
    body: NotificationResultRequest, request: Request
) -> dict[str, Any]:
    """Accept a delivery receipt only for this member's routed decision."""

    session = _session(request, body.provenance)
    runtime = _runtime(request)
    matched: dict[str, Any] | None = None
    async for event in runtime.event_log.read_since(
        types=["notification.decision.made"]
    ):
        payload = event["payload"]
        if (
            payload.get("decision_id") == body.decision_id
            and payload.get("signal_id") == body.signal_id
        ):
            matched = event
    if matched is None or matched["owner_scope"] != f"private:{session.auth_member_id}":
        raise HTTPException(404, "notification decision not found")
    if matched["payload"].get("decision") != "route":
        raise HTTPException(409, "notification decision was not routed")
    payload = {
        "attempt_id": body.attempt_id,
        "decision_id": body.decision_id,
        "signal_id": body.signal_id,
        "outcome": body.outcome,
        "route": "openclaw",
        "attempted_at": body.attempted_at,
        "provider_receipt_id": body.provider_receipt_id,
        "error_code": body.error_code,
        "next_retry_at": body.next_retry_at,
    }
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="notification.delivery.result",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=matched["owner_scope"],
        visibility_scope=matched["visibility_scope"],
        sensitivity=matched["sensitivity"],
        payload=payload,
        request_id=body.attempt_id,
        causation_id=matched["event_id"],
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="notifications.delivery-result",
        result_event_ids=[event_id],
    )
    return {"accepted": True, "event_id": event_id, "duplicate": duplicate}


@router.post("/model-calls")
async def model_call(body: ModelCallRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    if not (body.provenance.session_key or "").startswith("adminme:"):
        return {"accepted": False, "reason": "not_an_administrateme_session"}
    event_id, _ = await _append(
        _runtime(request),
        session,
        event_type="openclaw.model_call.recorded",
        source_account_id="model-provenance",
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload={
            "phase": body.phase,
            "run_id": body.run_id,
            "provider": body.provider,
            "model": body.model,
            "agent_id": body.provenance.agent_id,
            "session_key": body.provenance.session_key,
            "channel_id": body.provenance.channel_id,
        },
        request_id=f"{body.run_id}:{body.phase}",
    )
    return {"accepted": True, "event_id": event_id}


@router.post("/standing-orders/trigger")
async def standing_order(
    body: StandingOrderRequest, request: Request
) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = _runtime(request)
    if body.provenance.call_kind != "standing_order":
        raise HTTPException(422, "standing-order provenance required")
    if body.program_id not in runtime.standing_orders:
        raise HTTPException(404, "standing order is not registered")
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="openclaw.standing_order.triggered",
        source_account_id="automations",
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload={
            "program_id": body.program_id,
            "automation_id": body.automation_id,
            "trigger_id": body.trigger_id,
            "triggered_at": EventEnvelope.now_utc_iso(),
            "agent_id": body.provenance.agent_id,
            "session_key": body.provenance.session_key,
        },
        request_id=f"{body.program_id}:{body.automation_id}:{body.trigger_id}",
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"standing-order.{body.program_id}",
        result_event_ids=[event_id],
    )
    return {"accepted": True, "event_id": event_id, "duplicate": duplicate}


@router.post("/dashboard/today")
async def dashboard_today(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = _runtime(request)
    events: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since(limit=250):
        events.append(event)
    visible = filter_rows(session, events)[-20:]
    result = {
        "today": core._what_now(runtime, session),
        "activity": [
            {
                "event_id": event["event_id"],
                "type": event["type"],
                "occurred_at": event["occurred_at"],
            }
            for event in visible
        ],
    }
    await _record_tool_call(
        request, session, body.provenance, operation="dashboard.today"
    )
    return result


@router.post("/dashboard/inbox")
async def dashboard_inbox(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    result = await core.inbox(request, session)
    await _record_tool_call(
        request, session, body.provenance, operation="dashboard.inbox"
    )
    return result


@router.post("/dashboard/health")
async def dashboard_health(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = _runtime(request)
    result = {
        "openclaw_state": runtime.openclaw_state,
        "compatibility": {
            "version": OPENCLAW_VERSION,
            "commit": OPENCLAW_COMMIT,
        },
        "runtime": await runtime.diagnostics(),
    }
    await _record_tool_call(
        request, session, body.provenance, operation="dashboard.health"
    )
    return result


__all__ = ["router"]
