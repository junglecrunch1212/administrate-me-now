"""Requested private reminders: encrypted intent -> OpenClaw timer -> guarded send.

No local timer, provider client, guessed recipient or model-authored cron program.
A saved request is not a scheduled claim. The plugin verifies the native timer.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field

from adminme.cos_responses import RESPONSE_CONTRACT, content_fingerprint
from adminme.cos_reminder_work import reminder_receipt, reminder_work_id
from adminme.openclaw_topology import member_agent_id
from adminme.products.openclaw import main as seam
from adminme.products.openclaw import cos_responses as responses
from adminme.products.openclaw.cos_self_replies import SelfReplyRequest, _context

router = APIRouter(tags=["cos-self-reminders"])
PREFIX = "/api/openclaw/v1/cos-reminders"
REQUESTED = "cos.reply.reminder_requested"
BOUND = "cos.reply.reminder_bound"
CANCELLED = "cos.reply.reminder_cancelled"


class ReminderRequest(SelfReplyRequest):
    due_at: AwareDatetime


class ReminderControl(SelfReplyRequest):
    reminder_event_id: str = Field(min_length=1, max_length=240)


class ReminderBinding(ReminderControl):
    job_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,120}$")


class ReminderFire(BaseModel):
    model_config = {"extra": "forbid"}
    reminder_event_id: str = Field(min_length=1, max_length=240)
    job_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,120}$")
    agent_id: str
    session_key: str


def _enabled(runtime: Any, binding: str) -> None:
    path = (
        runtime.settings.instance.config_dir.parent / "openclaw/runtime/openclaw.json"
    )
    try:
        if path.is_symlink():
            raise ValueError("symlink")
        entry = json.loads(path.read_text("utf-8"))["plugins"]["entries"][
            "administrateme"
        ]
        bindings = entry["config"].get("scheduledSelfReminderBindings", [])
        enabled = (
            entry.get("enabled") is True
            and isinstance(bindings, list)
            and binding in bindings
        )
    except (OSError, KeyError, TypeError, ValueError):
        enabled = False
    if not enabled:
        raise HTTPException(
            403, "requested self reminders are not enabled for this member"
        )


def _key(member: str, event_type: str, request_id: str) -> str:
    return f"openclaw:{member}:{event_type}:{request_id}"


async def _related(runtime: Any, session: Any, kind: str, reminder_id: str) -> Any:
    return await runtime.event_log.get_by_idempotency(
        session.tenant_id, _key(session.auth_member_id, kind, reminder_id)
    )


async def _owned(runtime: Any, session: Any, reminder_id: str) -> dict[str, Any]:
    event = await runtime.event_log.get(reminder_id)
    if (
        event is None
        or event["type"] != REQUESTED
        or event["tenant_id"] != session.tenant_id
        or event["owner_scope"] != f"private:{session.auth_member_id}"
        or event["visibility_scope"] != event["owner_scope"]
        or event["payload"]["member_id"] != session.auth_member_id
    ):
        raise HTTPException(404, "reminder not found")
    return event


@router.post(PREFIX + "/prepare")
async def prepare(body: ReminderRequest, request: Request) -> dict[str, Any]:
    runtime, session, activation, binding = await _context(body, request)
    _enabled(runtime, binding)
    if body.reply_index:
        raise HTTPException(422, "one reminder per request")
    due = body.due_at.astimezone(timezone.utc)
    async with runtime.settings_command_locks.setdefault(
        f"cos-reminder-source:{body.source_message_event_id}", asyncio.Lock()
    ):
        existing = await _related(
            runtime, session, REQUESTED, body.source_message_event_id
        )
        if existing is not None:
            payload = existing["payload"]
            if payload["body"] != body.body or payload["due_at"] != due.isoformat():
                raise HTTPException(
                    409, "this message already requested a different reminder"
                )
            if await _related(runtime, session, CANCELLED, existing["event_id"]):
                raise HTTPException(
                    409, "this reminder was cancelled; request a new reminder"
                )
            reminder_id = existing["event_id"]
        else:
            now = datetime.now(timezone.utc)
            if not 60 <= (due - now).total_seconds() <= 366 * 86400:
                raise HTTPException(
                    422, "reminder time must be from one minute to one year ahead"
                )
            options = [
                row
                for row in responses._imessage_options(runtime)
                if row["provider_account_id"] == body.provenance.account_id
            ]
            if len(options) != 1:
                raise HTTPException(
                    403, "exact reminder service channel is unavailable"
                )
            await responses._policy_check(
                runtime,
                session,
                {**options[0], "recipient": body.provenance.actor_id},
                require_current=False,
            )
            # Preparing future exact text uses the same response-approval
            # governance contract as an immediate self reply. The additional
            # timed host grant and durable request are checked independently.
            await seam._guard(
                runtime,
                session,
                "cos.response.approved",
                {"member_id": session.auth_member_id, "self_reply_binding": binding},
            )
            reminder_id = await responses._receipt(
                runtime,
                session,
                event_type=REQUESTED,
                request_id=body.source_message_event_id,
                causation_id=body.source_message_event_id,
                payload={
                    "member_id": session.auth_member_id,
                    "source_message_event_id": body.source_message_event_id,
                    "self_reply_binding": binding,
                    "activation_event_id": activation["event_id"],
                    "provenance": body.provenance.model_dump(mode="json"),
                    "body": body.body,
                    "due_at": due.isoformat(),
                    "requested_at": now.isoformat(),
                },
            )
        bound = await _related(runtime, session, BOUND, reminder_id)
        approval = await _related(
            runtime, session, "cos.response.approved", reminder_id
        )
        terminal = (
            await _related(
                runtime, session, "cos.response.result", approval["event_id"]
            )
            if approval
            else None
        )
        return {
            "task_id": reminder_work_id(reminder_id),
            "delivery_status": terminal["payload"]["status"]
            if terminal
            else ("delivery_pending_or_unknown" if approval else None),
            "status": "prepared",
            "reminder_event_id": reminder_id,
            "due_at": due.isoformat(),
            "job_id": bound["payload"]["job_id"] if bound else None,
        }


@router.post(PREFIX + "/bind")
async def bind(body: ReminderBinding, request: Request) -> dict[str, Any]:
    runtime, session, _, binding = await _context(body, request)
    _enabled(runtime, binding)
    async with runtime.settings_command_locks.setdefault(
        f"cos-reminder:{body.reminder_event_id}", asyncio.Lock()
    ):
        reminder = await _owned(runtime, session, body.reminder_event_id)
        if (
            reminder["payload"]["source_message_event_id"]
            != body.source_message_event_id
        ):
            raise HTTPException(403, "timer must be bound by the original request")
        if await _related(runtime, session, CANCELLED, body.reminder_event_id):
            raise HTTPException(409, "reminder is cancelled")
        existing = await _related(runtime, session, BOUND, body.reminder_event_id)
        if existing and existing["payload"]["job_id"] != body.job_id:
            raise HTTPException(409, "reminder is already bound to another timer")
        await responses._receipt(
            runtime,
            session,
            event_type=BOUND,
            request_id=body.reminder_event_id,
            causation_id=body.reminder_event_id,
            payload={
                "member_id": session.auth_member_id,
                "reminder_event_id": body.reminder_event_id,
                "job_id": body.job_id,
            },
        )
        return {"status": "registered", "reminder_event_id": body.reminder_event_id}


@router.post(PREFIX + "/cancel")
async def cancel(body: ReminderControl, request: Request) -> dict[str, Any]:
    # Revocation remains available after future-send permission is removed.
    runtime, session, _, _ = await _context(body, request)
    async with runtime.settings_command_locks.setdefault(
        f"cos-reminder:{body.reminder_event_id}", asyncio.Lock()
    ):
        await _owned(runtime, session, body.reminder_event_id)
        if not await _related(runtime, session, CANCELLED, body.reminder_event_id):
            await responses._receipt(
                runtime,
                session,
                event_type=CANCELLED,
                request_id=body.reminder_event_id,
                causation_id=body.reminder_event_id,
                payload={
                    "member_id": session.auth_member_id,
                    "reminder_event_id": body.reminder_event_id,
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        bound = await _related(runtime, session, BOUND, body.reminder_event_id)
        return {
            "status": "cancelled",
            "job_id": bound["payload"]["job_id"] if bound else None,
        }


@router.post(PREFIX + "/status")
async def status(body: ReminderControl, request: Request) -> dict[str, Any]:
    # Reading the member's own receipt does not need current sending authority.
    # Never return the service-private task, notes, or other source records.
    session = seam._session(request, body.provenance)
    runtime = seam._runtime(request)
    reminder = await _owned(runtime, session, body.reminder_event_id)
    return await reminder_receipt(runtime, reminder)


@router.post(PREFIX + "/fire")
async def fire(body: ReminderFire, request: Request) -> dict[str, Any]:
    seam._authenticate(request)
    runtime = seam._runtime(request)
    reminder = await runtime.event_log.get(body.reminder_event_id)
    if reminder is None or reminder["type"] != REQUESTED:
        raise HTTPException(404, "reminder not found")
    saved = reminder["payload"]
    original = SelfReplyRequest(
        provenance=saved["provenance"],
        source_message_event_id=saved["source_message_event_id"],
        host_binding=saved["self_reply_binding"],
        body=saved["body"],
    )
    runtime, session, activation, binding = await _context(
        original, request, scheduled=True
    )
    await _owned(runtime, session, body.reminder_event_id)
    expected_agent = member_agent_id(session.auth_member_id)
    if (
        body.agent_id != expected_agent
        or body.session_key != f"agent:{expected_agent}:cron:{body.job_id}:trigger"
    ):
        raise HTTPException(403, "reminder requires its exact native timer context")
    async with runtime.settings_command_locks.setdefault(
        f"cos-reminder:{body.reminder_event_id}", asyncio.Lock()
    ):
        bound = await _related(runtime, session, BOUND, body.reminder_event_id)
        if not bound or bound["payload"]["job_id"] != body.job_id:
            raise HTTPException(403, "reminder timer does not match")

        async def current() -> None:
            _enabled(runtime, binding)
            _, _, _, current_binding = await _context(original, request, scheduled=True)
            if current_binding != binding:
                raise HTTPException(
                    403, "reminder authority changed; request a new reminder"
                )
            if await _related(runtime, session, CANCELLED, body.reminder_event_id):
                raise HTTPException(409, "reminder was cancelled")
            now = datetime.now(timezone.utc)
            due = datetime.fromisoformat(saved["due_at"])
            if not 0 <= (now - due).total_seconds() <= 600:
                raise HTTPException(
                    409, "reminder is early or missed its ten-minute delivery window"
                )

        await current()
        async with runtime.settings_command_locks.setdefault(
            f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
        ):
            matches = [
                row
                for row in responses._imessage_options(runtime)
                if row["provider_account_id"] == original.provenance.account_id
            ]
            if len(matches) != 1:
                raise HTTPException(
                    403, "exact reminder service channel is unavailable"
                )
            values = {key: value for key, value in matches[0].items() if key != "label"}
            values.update(
                recipient=original.provenance.actor_id, subject="", body=saved["body"]
            )
            policy = await responses._policy_check(
                runtime, session, values, require_current=False
            )
            approved = await _related(
                runtime, session, "cos.response.approved", body.reminder_event_id
            )
            if approved is None:
                await seam._guard(
                    runtime,
                    session,
                    "cos.response.approved",
                    {
                        "member_id": session.auth_member_id,
                        "self_reply_binding": binding,
                    },
                )
                now = datetime.now(timezone.utc)
                payload = {
                    **values,
                    "contract": RESPONSE_CONTRACT,
                    "member_id": session.auth_member_id,
                    "content_fingerprint": content_fingerprint(
                        channel="imessage",
                        account_id=values["provider_account_id"],
                        recipient=values["recipient"],
                        subject="",
                        body=values["body"],
                    ),
                    "policy_event_id": policy["event_id"],
                    "policy_revision": policy["revision"],
                    "approved_at": now.isoformat(),
                    "expires_at": (now + timedelta(minutes=10)).isoformat(),
                    "authorization_method": "requested_self_reminder",
                    "standing_authorization_event_id": activation["event_id"],
                    "source_message_event_id": saved["source_message_event_id"],
                    "self_reply_binding": binding,
                    "reminder_event_id": body.reminder_event_id,
                    "reminder_binding_event_id": bound["event_id"],
                    "reply_index": 0,
                }
                approved_id = await responses._receipt(
                    runtime,
                    session,
                    event_type="cos.response.approved",
                    payload=payload,
                    request_id=body.reminder_event_id,
                    causation_id=body.reminder_event_id,
                    schema_version=3,
                )
                approved = await runtime.event_log.get(approved_id)
            assert approved is not None
            payload = approved["payload"]
            host_payload = {
                key: payload[key]
                for key in (
                    "member_id",
                    "standing_authorization_event_id",
                    "source_message_event_id",
                    "self_reply_binding",
                    "reminder_event_id",
                    "reminder_binding_event_id",
                    "content_fingerprint",
                    "authorization_method",
                )
            }
            host_payload.update(
                approval_event_id=approved["event_id"],
                authorized_at=payload["approved_at"],
                automatic_retry_allowed=False,
            )
            host_id = await responses._receipt(
                runtime,
                session,
                event_type="cos.reply.reminder_authorized",
                request_id=approved["event_id"],
                causation_id=approved["event_id"],
                payload=host_payload,
            )
            execution = responses.ResponseExecutionRequest(
                provenance=original.provenance,
                request_id=body.reminder_event_id,
                approval_event_id=approved["event_id"],
            )
            result = await responses._execute_locked(
                runtime,
                session,
                approved,
                execution,
                request,
                payload,
                standing_check=current,
                standing_host_event_id=host_id,
            )
            # Private plugin transport context; never included in the model result.
            return {**result, "result_provenance": saved["provenance"]}
