"""One retained reminder-only choice through genuine private conversations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator

from adminme.adapters.cos_reminders_write_policy import (
    policy_from_config,
    write_selection,
)
from adminme.cos_reminders_commands import CoSReminderIntent
from adminme.events.envelope import Sensitivity
from adminme.events.schemas.reminder_modes import (
    ReminderModeOfferedV1,
    ReminderModePresentedV1,
)
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.apple_reminders import CurrentReminderInstruction
from adminme.products.openclaw.cos_apple_reminders import (
    confirm_intent,
    reminder_status,
)
from adminme.products.openclaw.reminder_choice_work import (
    _owned,
    choice_task_id,
    draft_fields,
    ensure_choice_work,
    finish_choice,
    finished_choice,
    receipt_key,
    resolve_task,
    selected_task_answer,
    source_and_work_authority,
    update_choice_work,
    validate_original,
)
from adminme.products.openclaw.reminder_mode_evidence import (
    current_mode,
    presentation_matches,
    sent_reply_matches,
)
from adminme.products.openclaw.service_work import current_private_instruction
from adminme.reminder_choices import digest
from adminme.reminder_modes import (
    MODE_OFFERED,
    MODE_PRESENTED,
    mode_answer,
    mode_origin,
    mode_prompt,
)
from adminme.request_actions_control import request_actions_allowed

router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["reminder-mode"])


class ReminderModeRequest(CurrentReminderInstruction):
    action: Literal["offer", "answer", "status", "cancel"]
    task_id: str | None = Field(default=None, max_length=240)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    due_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=1000)
    request_id: str | None = Field(default=None, min_length=8, max_length=120)

    @model_validator(mode="after")
    def stage_or_continue(self) -> ReminderModeRequest:
        if self.action == "offer":
            if (
                not self.title
                or self.due_at is None
                or self.due_at.tzinfo is None
                or not self.request_id
                or self.task_id
            ):
                raise ValueError(
                    "offer requires title, exact due time and request identity"
                )
        elif any(
            value is not None
            for value in (self.title, self.due_at, self.notes, self.request_id)
        ):
            raise ValueError(
                "a fresh answer cannot replace the retained reminder draft"
            )
        return self


def mode_summary(offered: dict[str, Any]) -> dict[str, Any]:
    p = ReminderModeOfferedV1.model_validate(offered["payload"])
    return {
        "state": "expired"
        if p.pending_expires_at <= datetime.now(timezone.utc)
        else "mode_choice_required",
        "task_id": p.task_id,
        "choice_event_id": offered["event_id"],
        "request_id": p.request_id,
        "title": p.title,
        "due_at": p.due_at.isoformat(),
        "pending_expires_at": p.pending_expires_at.isoformat(),
        "presentation_prompt": p.prompt,
        "continuation_tool": "administrateme_cos_reminder_choice",
        "provider_effect_count": 0,
        "automatic_retry": False,
        "timer_registered": False,
    }


def require_answer_origin(
    p: ReminderModeOfferedV1, answer: dict[str, Any], provenance: Any
) -> None:
    if mode_origin(provenance) != p.origin and not answer["payload"][
        "text"
    ].strip().startswith(p.task_id + ":"):
        raise HTTPException(
            409, "A cross-conversation answer must explicitly name this retained task"
        )


async def offer_mode(
    runtime: Any, member: Any, source: dict[str, Any], body: ReminderModeRequest
) -> dict[str, Any]:
    assert (
        body.due_at is not None
        and body.title is not None
        and body.request_id is not None
    )
    source, _, _ = await source_and_work_authority(runtime, member, source["event_id"])
    if not request_actions_allowed(
        runtime, issued_at=source["payload"]["submitted_at"]
    ):
        raise HTTPException(409, "Request authority is revoked")
    if runtime.observation is None or await runtime.observation.is_active():
        raise HTTPException(409, "Observation mode suppresses new reminder offers")
    now = datetime.now(timezone.utc)
    try:
        selection = write_selection(runtime.settings.adapter_config)
        policy = policy_from_config(runtime.settings.adapter_config)
        assert policy is not None
        intent = CoSReminderIntent(
            resource_owner="service-principal:chief-of-staff",
            operating_principal="service-principal:chief-of-staff",
            selection=selection,
            write_policy=policy,
            source_event_id=source["event_id"],
            title=body.title,
            due_at=body.due_at,
            notes=body.notes,
            authorization_expires_at=now + timedelta(minutes=10),
            selected_by_policy=True,
        )
        intent.require_current(runtime.settings.adapter_config)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(
            409, "The exact CoS reminder writer is unavailable"
        ) from exc
    action_id = "cos-reminder-mode:" + digest(
        [member.tenant_id, member.auth_member_id, source["event_id"]]
    )
    key = receipt_key(member.auth_member_id, MODE_OFFERED, action_id)
    async with runtime.settings_command_locks.setdefault(key, asyncio.Lock()):
        prior = await runtime.event_log.get_by_idempotency(member.tenant_id, key)
        if prior is not None:
            if (
                not _owned(prior, member, MODE_OFFERED)
                or prior["payload"]["action"] != intent.action()
                or prior["payload"]["origin"]
                != mode_origin(body.provenance).model_dump(mode="json")
            ):
                raise HTTPException(
                    409, "The original reminder offer cannot be replaced"
                )
            await ensure_choice_work(runtime, member, prior)
            return prior
        # No historical one-message execution can become a new numbered offer.
        if any(
            row["type"] == "member.provider_action.confirmed"
            for row in await runtime.event_log.get_by_causation(source["event_id"])
        ):
            raise HTTPException(409, "This instruction already has an action receipt")
        payload = ReminderModeOfferedV1(
            member_id=member.auth_member_id,
            task_id=choice_task_id(member.tenant_id, member.auth_member_id, action_id),
            action_id=action_id,
            request_id=body.request_id,
            original_source_event_id=source["event_id"],
            original_source_fingerprint=digest(source["payload"]["text"]),
            original_submitted_at=source["payload"]["submitted_at"],
            draft_fingerprint=digest(
                draft_fields(
                    title=intent.title, due_at=intent.due_at, notes=intent.notes
                )
            ),
            provider="apple_reminders",
            operation="create_reminder",
            resource_owner=intent.resource_owner,
            provider_account_id=selection.provider_account_id,
            title=intent.title,
            due_at=intent.due_at,
            notes=intent.notes,
            offered_at=now,
            pending_expires_at=intent.due_at,
            expiry_reason="requested_reminder_due_time",
            action=intent.action(),
            origin=mode_origin(body.provenance),
            prompt=mode_prompt(intent.title, intent.due_at.isoformat(), intent.notes),
        )
        if len(payload.prompt.encode("utf-16-le")) // 2 > 3500:
            raise HTTPException(
                422,
                "Use a concise reminder title and notes so the exact clarification fits its reply channel",
            )
        event_id, _ = await seam._append(
            runtime,
            member,
            event_type=MODE_OFFERED,
            source_account_id=member.auth_member_id,
            owner_scope=f"private:{member.auth_member_id}",
            visibility_scope=f"private:{member.auth_member_id}",
            sensitivity=cast(Sensitivity, source["sensitivity"]),
            payload=payload.model_dump(mode="json"),
            request_id=action_id,
            causation_id=source["event_id"],
        )
        offered = await runtime.event_log.get(event_id)
        assert offered is not None
        await ensure_choice_work(runtime, member, offered)
        return offered


@router.post("/cos-reminder-choice")
async def reminder_mode(body: ReminderModeRequest, request: Request) -> dict[str, Any]:
    member, answer = await current_private_instruction(
        request,
        body.provenance,
        body.source_message_event_id,
        required_permission="prepare_external_actions"
        if body.action in {"offer", "answer"}
        else "read_context",
    )
    runtime = seam._runtime(request)
    if body.action == "offer":
        result = mode_summary(await offer_mode(runtime, member, answer, body))
    else:
        offered = await resolve_task(
            runtime,
            member,
            body.task_id,
            answer=answer if body.action != "status" else None,
        )
        if isinstance(offered, list):
            return {
                "state": "task_choice_required"
                if offered
                else "no_pending_reminder_choice",
                "tasks": [
                    mode_summary(row) for row in offered if row["type"] == MODE_OFFERED
                ],
                "provider_effect_count": 0,
            }
        if offered["type"] != MODE_OFFERED:
            return {
                "state": "different_choice_kind",
                "continuation_tool": "administrateme_apple_reminder_choice",
                "task_id": offered["payload"]["task_id"],
                "provider_effect_count": 0,
            }
        p = ReminderModeOfferedV1.model_validate(offered["payload"])
        async with runtime.settings_command_locks.setdefault(
            f"cos-native-reminder:{p.action_id}", asyncio.Lock()
        ):
            original = await validate_original(runtime, member, offered)
            finished = await finished_choice(runtime, member, offered)
            domain = await runtime.event_log.get_by_idempotency(
                member.tenant_id,
                f"provider-action:{member.auth_member_id}:{p.action_id}",
            )
            result = mode_summary(offered)
            if domain is not None:
                if finished is None:
                    await finish_choice(
                        runtime,
                        member,
                        offered,
                        await runtime.event_log.get(domain["causation_id"]),
                        state="confirmed",
                        selected_list_id=str(p.action["list_id"]),
                        domain_approval_event_id=domain["event_id"],
                    )
                receipt = await reminder_status(runtime, member, domain["event_id"])
                result = {**result, **receipt, "continuation_closed": True}
                if receipt["state"] == "succeeded":
                    await update_choice_work(
                        runtime, member, offered, "succeeded", domain["event_id"]
                    )
                if (
                    body.action == "answer"
                    and domain["causation_id"] == answer["event_id"]
                    and receipt["state"] == "awaiting_request_policy"
                ):
                    from adminme.products.openclaw.request_authorization import (
                        grounding_for_domain,
                        ticket,
                    )

                    grounding = await grounding_for_domain(runtime, member, domain)
                    assert grounding is not None
                    await current_mode(runtime, member, offered, answer, p.action)
                    require_answer_origin(p, answer, body.provenance)
                    if datetime.fromisoformat(
                        grounding["payload"]["expires_at"]
                    ) > datetime.now(timezone.utc):
                        result["request_authorization"] = ticket(
                            runtime, member, domain, grounding
                        )
            elif finished is not None:
                result.update(
                    state=finished["payload"]["state"], continuation_closed=True
                )
            elif body.action != "status":
                require_answer_origin(p, answer, body.provenance)
                _, authored = await selected_task_answer(
                    runtime, member, offered, answer
                )
                if body.action == "cancel":
                    if (
                        authored.strip().lower()
                        not in {
                            "cancel",
                            "cancel it",
                            "cancel the reminder",
                            "cancel reminder",
                        }
                        or answer["event_id"] == p.original_source_event_id
                    ):
                        raise HTTPException(
                            409, "A fresh explicit cancellation is required"
                        )
                    await finish_choice(
                        runtime, member, offered, answer, state="cancelled"
                    )
                    result.update(state="cancelled", continuation_closed=True)
                elif mode_answer(authored) == "email_unresolved":
                    from adminme.products.openclaw.reminder_mode_evidence import (
                        presentation_for,
                    )

                    shown = await presentation_for(runtime, member, offered)
                    if (
                        not await presentation_matches(
                            runtime.event_log, offered, shown
                        )
                        or not shown["event_id"] < answer["event_id"]
                        or p.pending_expires_at <= datetime.now(timezone.utc)
                    ):
                        raise HTTPException(409, "The numbered offer is not current")
                    result.update(
                        state="email_workflow_unresolved",
                        reason="No email or timer was scheduled and no reminder was created.",
                    )
                else:
                    from adminme.products.openclaw.reminder_choice_authorization import (
                        ground_reminder_choice,
                    )
                    from adminme.products.openclaw.request_authorization import ticket

                    await current_mode(runtime, member, offered, answer, p.action)
                    fingerprint = digest(
                        {"offered_event_id": offered["event_id"], "action": p.action}
                    )
                    grounded = await ground_reminder_choice(
                        runtime,
                        member,
                        original,
                        answer,
                        offered=offered,
                        action=p.action,
                        request_fingerprint=fingerprint,
                    )
                    selection, policy = (
                        write_selection(runtime.settings.adapter_config),
                        policy_from_config(runtime.settings.adapter_config),
                    )
                    assert policy is not None
                    intent = CoSReminderIntent(
                        resource_owner=p.resource_owner,
                        operating_principal=p.resource_owner,
                        selection=selection,
                        write_policy=policy,
                        source_event_id=answer["event_id"],
                        title=p.title,
                        notes=p.notes,
                        due_at=p.due_at,
                        selected_by_policy=True,
                        authorization_expires_at=datetime.fromisoformat(
                            grounded["payload"]["expires_at"]
                        ),
                    )
                    if intent.action() != p.action:
                        raise HTTPException(
                            409, "Current CoS reminder destination changed"
                        )
                    domain = await confirm_intent(
                        runtime,
                        member,
                        {**answer, "sensitivity": grounded["sensitivity"]},
                        intent=intent,
                        action_id=p.action_id,
                        fingerprint=fingerprint,
                    )
                    await finish_choice(
                        runtime,
                        member,
                        offered,
                        answer,
                        state="confirmed",
                        selected_list_id=selection.list_id,
                        domain_approval_event_id=domain["event_id"],
                    )
                    result = {
                        **result,
                        **await reminder_status(runtime, member, domain["event_id"]),
                        "request_authorization": ticket(
                            runtime, member, domain, grounded
                        ),
                        "continuation_closed": True,
                    }
    await seam._record_tool_call(
        request,
        member,
        body.provenance,
        operation=f"tools.cos-reminder-choice.{body.action}",
        result_event_ids=[
            result[key]
            for key in ("choice_event_id", "domain_approval_event_id")
            if isinstance(result.get(key), str)
        ],
    )
    return result


class ModePresentationRequest(CurrentReminderInstruction):
    offered_event_id: str
    body: str = Field(min_length=1, max_length=3500)
    run_id: str = Field(min_length=1, max_length=240)
    response_result_event_id: str | None = None
    issued_at_ms: int
    expires_at_ms: int
    signature: str


@router.post("/cos-reminder-choice/presented")
async def presented(body: ModePresentationRequest, request: Request) -> dict[str, Any]:
    from adminme.products.openclaw.request_authorization import canonical

    member, source = await current_private_instruction(
        request, body.provenance, body.source_message_event_id
    )
    runtime = seam._runtime(request)
    secret = runtime.settings.openclaw_shared_secret
    material = body.model_dump(mode="json", exclude={"provenance", "signature"})
    material["origin"] = mode_origin(body.provenance).model_dump(mode="json")
    expected = hmac.new(
        (secret or "").encode(),
        ("cos-reminder-presentation-v1|" + canonical(material)).encode(),
        hashlib.sha256,
    ).hexdigest()
    now = datetime.now(timezone.utc)
    if (
        not secret
        or not hmac.compare_digest(expected, body.signature)
        or not body.issued_at_ms <= int(now.timestamp() * 1000) < body.expires_at_ms
        or body.expires_at_ms - body.issued_at_ms > 30_000
    ):
        raise HTTPException(403, "Actual native presentation evidence is unavailable")
    offered = await runtime.event_log.get(body.offered_event_id)
    if offered is None or not _owned(offered, member, MODE_OFFERED):
        raise HTTPException(404, "Reminder mode offer not found")
    p = ReminderModeOfferedV1.model_validate(offered["payload"])
    await validate_original(runtime, member, offered)
    if (
        p.original_source_event_id != source["event_id"]
        or p.origin != mode_origin(body.provenance)
        or body.body != p.prompt
        or (
            (p.origin.channel_id == "administrateme-portal")
            != (body.response_result_event_id is None)
        )
        or now >= p.pending_expires_at
    ):
        raise HTTPException(403, "Presented options differ from the original offer")
    prior = await runtime.event_log.get_by_idempotency(
        member.tenant_id,
        receipt_key(member.auth_member_id, MODE_PRESENTED, offered["event_id"]),
    )
    if prior is not None:
        if not _owned(prior, member, MODE_PRESENTED) or not await presentation_matches(
            runtime.event_log, offered, prior
        ):
            raise HTTPException(403, "Original presentation evidence is inconsistent")
        return {
            "state": "presented",
            "presentation_event_id": prior["event_id"],
            "provider_effect_count": 0,
        }
    payload = ReminderModePresentedV1(
        member_id=member.auth_member_id,
        task_id=p.task_id,
        offered_event_id=offered["event_id"],
        source_event_id=source["event_id"],
        origin=p.origin,
        prompt_sha256=hashlib.sha256(body.body.encode()).hexdigest(),
        run_id=body.run_id,
        presented_at=now,
        issued_at_ms=body.issued_at_ms,
        expires_at_ms=body.expires_at_ms,
        host_signature=body.signature,
        response_result_event_id=body.response_result_event_id,
        presentation_kind="native_portal_final"
        if p.origin.channel_id == "administrateme-portal"
        else "sent_reply",
    )
    if payload.presentation_kind == "sent_reply" and not await sent_reply_matches(
        runtime.event_log, offered, body.response_result_event_id
    ):
        raise HTTPException(403, "The numbered prompt has no matching sent receipt")
    event_id, _ = await seam._append(
        runtime,
        member,
        event_type=MODE_PRESENTED,
        source_account_id=member.auth_member_id,
        owner_scope=f"private:{member.auth_member_id}",
        visibility_scope=f"private:{member.auth_member_id}",
        sensitivity=cast(Sensitivity, offered["sensitivity"]),
        payload=payload.model_dump(mode="json"),
        request_id=offered["event_id"],
        causation_id=offered["event_id"],
    )
    shown = await runtime.event_log.get(event_id)
    assert shown is not None
    if not await presentation_matches(runtime.event_log, offered, shown):
        raise HTTPException(403, "Actual presentation receipt is inconsistent")
    return {
        "state": "presented",
        "presentation_event_id": event_id,
        "provider_effect_count": 0,
    }
