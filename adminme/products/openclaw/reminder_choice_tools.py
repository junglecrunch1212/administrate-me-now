"""Fresh authenticated answers and cross-channel status for offered reminder tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from adminme.apple_service_permissions import permission_options, service_permission
from adminme.events.envelope import Sensitivity
from adminme.events.schemas.reminder_choices import (
    ReminderChoiceOfferedV1,
    ReminderListChoice,
)
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.apple_reminders import (
    CurrentReminderInstruction,
    apple_reminder_status,
    due_components,
)
from adminme.products.openclaw.reminder_choice_work import (
    choice_summary,
    finish_choice,
    finished_choice,
    resolve_task,
    selected_task_answer,
    validate_original,
    update_choice_work,
)
from adminme.products.openclaw.service_work import current_private_instruction
from adminme.reminder_choices import digest, selected_choice
from adminme.request_actions_control import request_actions_allowed

router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["reminder-list-choice"])


class ReminderChoiceRequest(CurrentReminderInstruction):
    action: Literal["answer", "status", "cancel"]
    task_id: str | None = Field(default=None, min_length=1, max_length=240)


async def choice_status(
    runtime: Any, member: Any, offered: dict[str, Any]
) -> dict[str, Any]:
    summary = choice_summary(offered)
    finished = await finished_choice(runtime, member, offered)
    domain_id = (
        finished["payload"].get("domain_approval_event_id") if finished else None
    )
    if finished is not None and finished["payload"]["state"] == "cancelled":
        return {**summary, "state": "cancelled", "cancelled_before_confirmation": True}
    if domain_id is None:
        # A crash between confirmation and the finished receipt is not a new action.
        domain = await runtime.event_log.get_by_idempotency(
            member.tenant_id,
            f"provider-action:{member.auth_member_id}:{offered['payload']['action_id']}",
        )
        domain_id = domain["event_id"] if domain else None
    if domain_id is not None:
        status = await apple_reminder_status(runtime, member, domain_id)
        # Reconcile only terminal proof during an explicitly requested read.
        if (
            status.get("state") == "succeeded"
            and not await runtime.observation.is_active()
        ):
            await update_choice_work(
                runtime, member, offered, "succeeded", status["result_event_id"]
            )
        return {**summary, **status}
    return summary


@router.post("/apple-reminder-choice")
async def reminder_choice(
    body: ReminderChoiceRequest, request: Request
) -> dict[str, Any]:
    member, answer = await current_private_instruction(
        request,
        body.provenance,
        body.source_message_event_id,
        required_permission="prepare_external_actions"
        if body.action == "answer"
        else "read_context",
    )
    runtime = seam._runtime(request)
    offered = await resolve_task(
        runtime,
        member,
        body.task_id,
        answer=answer if body.action != "status" else None,
    )
    if isinstance(offered, list):
        visible = []
        for event in offered:
            try:
                await validate_original(runtime, member, event)
            except HTTPException:
                continue
            visible.append(choice_summary(event))
        return {
            "state": "task_choice_required"
            if offered
            else "no_pending_reminder_choice",
            "tasks": visible,
            "provider_effect_count": 0,
            "automatic_retry": False,
        }
    p = ReminderChoiceOfferedV1.model_validate(offered["payload"])
    if offered["type"] == "cos.reminder.mode_offered":
        return {
            "state": "different_choice_kind",
            "continuation_tool": "administrateme_cos_reminder_choice",
            "task_id": offered["payload"]["task_id"],
            "provider_effect_count": 0,
        }
    async with runtime.settings_command_locks.setdefault(
        f"apple-reminder:{p.action_id}", asyncio.Lock()
    ):
        original = await validate_original(runtime, member, offered)
        status = await choice_status(runtime, member, offered)
        if body.action == "status":
            result = status
        elif status["state"] == "cancelled" or status.get("domain_approval_event_id"):
            result = {**status, "continuation_closed": True}
            if (
                body.action == "answer"
                and status.get("request_authorization_state") == "current"
            ):
                from adminme.products.openclaw.request_authorization import (
                    grounding_for_domain,
                    ticket,
                )

                domain = await runtime.event_log.get(status["domain_approval_event_id"])
                assert domain is not None
                grounding = await grounding_for_domain(runtime, member, domain)
                if (
                    grounding is not None
                    and grounding["payload"]["source_event_id"] == answer["event_id"]
                ):
                    # Recover a lost response only for the same native answer;
                    # ticket() returns original issued/expiry, never renews it.
                    result["request_authorization"] = ticket(
                        runtime, member, domain, grounding
                    )
            if body.action == "cancel" and status.get("domain_approval_event_id"):
                result["cancellation"] = (
                    "already_confirmed_no_provider_reversal_attempted"
                )
        else:
            if answer["event_id"] == p.original_source_event_id:
                raise HTTPException(409, "A fresh authenticated answer is required")
            if await runtime.observation.is_active():
                raise HTTPException(
                    409, "Observation mode suppresses reminder task changes"
                )
            _, authored_answer = await selected_task_answer(
                runtime, member, offered, answer
            )
            if body.action == "cancel":
                if authored_answer.strip().casefold() not in {
                    "cancel",
                    "cancel it",
                    "cancel this reminder",
                    "cancel that reminder",
                    "never mind",
                }:
                    raise HTTPException(
                        409, "An explicit current cancellation instruction is required"
                    )
                await finish_choice(runtime, member, offered, answer, state="cancelled")
                result = {
                    **status,
                    "state": "cancelled",
                    "cancelled_before_confirmation": True,
                }
            else:
                if not request_actions_allowed(
                    runtime, issued_at=answer["payload"]["submitted_at"]
                ):
                    raise HTTPException(
                        409, "Authenticated-request actions are revoked for recovery"
                    )
                permission = service_permission(
                    runtime, member.auth_member_id, "apple_reminders"
                )
                if (
                    permission is None
                    or "create_reminder" not in permission["operations"]
                ):
                    raise HTTPException(
                        409, "Current Apple Reminders writing permission is unavailable"
                    )
                options = permission_options(
                    runtime, member.auth_member_id, "apple_reminders"
                )
                selected = selected_choice(
                    p,
                    authored_answer,
                    provider_account_id=permission["provider_account_id"],
                    current_choices=[
                        ReminderListChoice(
                            list_id=row["container_id"], label=row["label"]
                        )
                        for row in options["scopes"]
                        if row["container_id"] in permission["allowed_container_ids"]
                    ],
                    now=datetime.now(timezone.utc),
                )
                from adminme.products.openclaw.reminder_choice_authorization import (
                    ground_reminder_choice,
                )
                from adminme.products.openclaw.request_authorization import ticket
                from adminme.products.member.main import (
                    ReminderCommandRequest,
                    confirm_reminder_command,
                )

                action = {
                    "provider": "apple_reminders",
                    "operation": "create_reminder",
                    "resource_owner": "requester",
                    "title": p.title,
                    "due_at": p.due_at.isoformat(),
                    "notes": p.notes,
                    "list_id": selected.list_id,
                    "selected_by_policy": False,
                }
                fingerprint = digest(
                    {"offered_event_id": offered["event_id"], "action": action}
                )
                grounding = await ground_reminder_choice(
                    runtime,
                    member,
                    original,
                    answer,
                    offered=offered,
                    action=action,
                    request_fingerprint=fingerprint,
                )
                confirmed = await confirm_reminder_command(
                    ReminderCommandRequest(
                        provider="apple_reminders",
                        provider_account_id=p.provider_account_id,
                        operation="create_reminder",
                        list_external_id=selected.list_id,
                        request_id=p.action_id,
                        payload={
                            "title": p.title,
                            "due": due_components(p.due_at),
                            **({"notes": p.notes} if p.notes is not None else {}),
                        },
                    ),
                    runtime,
                    member,
                    source_adapter="openclaw-apple-reminder",
                    causation_id=answer["event_id"],
                    source_sensitivity=cast(Sensitivity, offered["sensitivity"]),
                    request_fingerprint=fingerprint,
                )
                await finish_choice(
                    runtime,
                    member,
                    offered,
                    answer,
                    state="confirmed",
                    selected_list_id=selected.list_id,
                    domain_approval_event_id=confirmed["event_id"],
                )
                domain = await runtime.event_log.get(confirmed["event_id"])
                assert domain is not None
                result = {
                    **status,
                    **await apple_reminder_status(runtime, member, domain["event_id"]),
                    "request_authorization": ticket(runtime, member, domain, grounding),
                    "state": "awaiting_request_policy",
                    "continuation_closed": True,
                }
    await seam._record_tool_call(
        request,
        member,
        body.provenance,
        operation=f"tools.apple-reminder-choice.{body.action}",
        result_event_ids=[offered["event_id"]],
    )
    return result
