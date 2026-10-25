"""Trusted presentation and current answer checks for the closed reminder mode."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from fastapi import HTTPException

from adminme.events.schemas.reminder_modes import (
    ReminderModeOfferedV1,
    ReminderModePresentedV1,
)
from adminme.products.openclaw.reminder_choice_work import (
    _owned,
    receipt_key,
    selected_task_answer,
    source_and_work_authority,
    validate_original,
)
from adminme.reminder_modes import (
    MODE_PRESENTED,
    mode_answer,
    mode_prompt,
)
from adminme.request_actions_control import request_actions_allowed


def exact_mode_action(p: ReminderModeOfferedV1) -> dict[str, Any]:
    a = p.action
    keys = {
        "provider",
        "operation",
        "resource_owner",
        "operating_principal",
        "provider_account_id",
        "source_id",
        "list_id",
        "device_identity_sha256",
        "os_uid",
        "selection_sha256",
        "write_policy_sha256",
        "title",
        "notes",
        "due_at",
        "selected_by_policy",
    }
    if (
        set(a) != keys
        or a["provider"] != "apple_reminders"
        or a["operation"] != "create_reminder"
        or a["resource_owner"] != "service-principal:chief-of-staff"
        or a["operating_principal"] != a["resource_owner"]
        or a["provider_account_id"] != p.provider_account_id
        or a["title"] != p.title
        or a["notes"] != p.notes
        or a["due_at"] != p.due_at.astimezone(timezone.utc).isoformat()
        or a["selected_by_policy"] is not True
        or not all(isinstance(a[k], str) and a[k] for k in ("source_id", "list_id"))
        or p.prompt != mode_prompt(p.title, p.due_at.isoformat(), p.notes)
    ):
        raise HTTPException(403, "Recorded reminder mode differs from its exact draft")
    # The complete shape is shared with the existing own-writer intent. Current
    # account/device/policy equality is checked below, not inferred from text.
    return dict(a)


def reply_source_matches(p: ReminderModeOfferedV1, approval: dict[str, Any]) -> bool:
    """Use each existing reply contract's actual original-source field."""
    a = approval.get("payload", {})
    if p.origin.channel_id in {"imessage", "apple_imessage"}:
        from adminme.cos_self_replies import self_reply_binding

        return bool(
            approval.get("schema_version") == 2
            and a.get("authorization_method") == "standing_self_reply"
            and a.get("source_message_event_id") == p.original_source_event_id
            and a.get("self_reply_binding")
            == self_reply_binding(p.member_id, p.origin.account_id, p.origin.actor_id)
            and a.get("channel") == "imessage"
            and a.get("recipient") == p.origin.actor_id
            and a.get("reply_index") == 0
        )
    return bool(
        p.origin.channel_id == "gmail"
        and approval.get("schema_version") == 4
        and a.get("authorization_method") == "authenticated_conversation"
        and a.get("conversation_source_event_id") == p.original_source_event_id
        and approval.get("causation_id") == p.original_source_event_id
        and a.get("thread_id") == p.origin.thread_id
        and a.get("channel") == "email"
    )


async def presentation_for(
    runtime: Any, member: Any, offered: dict[str, Any]
) -> dict[str, Any]:
    event = await runtime.event_log.get_by_idempotency(
        member.tenant_id,
        receipt_key(member.auth_member_id, MODE_PRESENTED, offered["event_id"]),
    )
    if event is None or not _owned(event, member, MODE_PRESENTED):
        raise HTTPException(
            409, "The exact numbered clarification has not been presented"
        )
    return event


async def presentation_matches(
    event_log: Any, offered: dict[str, Any], event: dict[str, Any]
) -> bool:
    try:
        p = ReminderModeOfferedV1.model_validate(offered["payload"])
        shown = ReminderModePresentedV1.model_validate(event["payload"])
        exact_mode_action(p)
        if not (
            event["type"] == MODE_PRESENTED
            and event["schema_version"] == 1
            and event["tenant_id"] == offered["tenant_id"]
            and event["owner_scope"]
            == event["visibility_scope"]
            == f"private:{p.member_id}"
            and event["source_account_id"] == p.member_id
            and event["actor_identity"] == "service-principal:chief-of-staff"
            and event["source_adapter"] == "openclaw-plugin"
            and event["causation_id"] == offered["event_id"]
            and shown.member_id == p.member_id
            and shown.task_id == p.task_id
            and shown.offered_event_id == offered["event_id"]
            and shown.source_event_id == p.original_source_event_id
            and shown.origin == p.origin
            and shown.prompt_sha256 == sha256(p.prompt.encode()).hexdigest()
            and p.offered_at <= shown.presented_at < p.pending_expires_at
            and shown.issued_at_ms
            <= int(shown.presented_at.timestamp() * 1000)
            < shown.expires_at_ms
            and 0 < shown.expires_at_ms - shown.issued_at_ms <= 30_000
            and offered["event_id"] < event["event_id"]
        ):
            return False
        if shown.presentation_kind == "native_portal_final":
            return True  # Signed native final-turn evidence, never a model argument.
        return await sent_reply_matches(
            event_log,
            offered,
            shown.response_result_event_id,
            presentation_event_id=event["event_id"],
        )
    except (KeyError, TypeError, ValueError, HTTPException):
        return False


async def sent_reply_matches(
    event_log: Any,
    offered: dict[str, Any],
    result_event_id: str | None,
    *,
    presentation_event_id: str | None = None,
) -> bool:
    """Check the existing sent receipt both before and after retaining proof."""
    try:
        p = ReminderModeOfferedV1.model_validate(offered["payload"])
        result = await event_log.get(result_event_id) if result_event_id else None
        if result is None:
            return False
        approval = await event_log.get(result["payload"]["approval_event_id"])
        if approval is None:
            return False
        a, r = approval["payload"], result["payload"]
        return bool(
            all(
                row["tenant_id"] == offered["tenant_id"]
                and row["owner_scope"]
                == row["visibility_scope"]
                == f"private:{p.member_id}"
                and row["source_adapter"] == "openclaw-plugin"
                and row["source_account_id"] == "cos-response"
                and row["actor_identity"] == "service-principal:chief-of-staff"
                for row in (approval, result)
            )
            and result["type"] == "cos.response.result"
            and result["schema_version"] == 1
            and result["causation_id"] == approval["event_id"]
            and r["member_id"] == p.member_id
            and r["status"] == "sent"
            and approval["type"] == "cos.response.approved"
            and a["member_id"] == p.member_id
            and a["body"] == p.prompt
            and a["provider_account_id"] == p.origin.account_id
            and a["content_fingerprint"] == r["content_fingerprint"]
            and reply_source_matches(p, approval)
            and offered["event_id"] < approval["event_id"] < result["event_id"]
            and (
                presentation_event_id is None
                or result["event_id"] < presentation_event_id
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


async def current_mode(
    runtime: Any,
    member: Any,
    offered: dict[str, Any],
    answer: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    from adminme.adapters.cos_reminders_write_policy import (
        write_selection,
        policy_from_config,
    )
    from adminme.adapters.cos_apple_reminders import digest

    p = ReminderModeOfferedV1.model_validate(offered["payload"])
    await validate_original(runtime, member, offered)
    retained, _, _ = await source_and_work_authority(
        runtime, member, answer["event_id"]
    )
    if retained != answer or answer["event_id"] == p.original_source_event_id:
        raise HTTPException(403, "A fresh authentic member answer is required")
    shown = await presentation_for(runtime, member, offered)
    if not await presentation_matches(runtime.event_log, offered, shown):
        raise HTTPException(403, "The actual numbered presentation is mismatched")
    now = datetime.now(timezone.utc)
    if not (
        shown["event_id"] < answer["event_id"]
        # Authenticated intake stores submitted_at at whole-second precision.
        # Strict retained event order above still rejects an earlier answer,
        # including one recorded in the same second as the presentation.
        and datetime.fromisoformat(shown["payload"]["presented_at"]).replace(
            microsecond=0
        )
        <= datetime.fromisoformat(answer["payload"]["submitted_at"])
        < p.pending_expires_at
        and now < p.pending_expires_at
    ):
        raise HTTPException(
            409, "Reminder clarification is expired or precedes its offer"
        )
    _, authored = await selected_task_answer(runtime, member, offered, answer)
    if mode_answer(authored) != "reminder_only" or action != exact_mode_action(p):
        raise HTTPException(
            409, "This answer does not select only the unchanged reminder"
        )
    if runtime.observation is None or await runtime.observation.is_active():
        raise HTTPException(409, "Observation mode suppresses reminder mode execution")
    if not request_actions_allowed(
        runtime, issued_at=p.original_submitted_at.isoformat()
    ) or not request_actions_allowed(
        runtime, issued_at=answer["payload"]["submitted_at"]
    ):
        raise HTTPException(409, "Reminder authority was revoked")
    try:
        selection = write_selection(runtime.settings.adapter_config)
        policy = policy_from_config(runtime.settings.adapter_config)
        if policy is None or any(
            action[k] != v
            for k, v in {
                "provider_account_id": selection.provider_account_id,
                "source_id": selection.source_id,
                "list_id": selection.list_id,
                "device_identity_sha256": selection.device_identity_sha256,
                "os_uid": selection.os_uid,
                "selection_sha256": policy.selection_sha256,
                "write_policy_sha256": digest(policy.model_dump(mode="json")),
            }.items()
        ):
            raise ValueError("selected writer changed")
    except (ValueError, PermissionError) as exc:
        raise HTTPException(
            409, "The exact CoS reminder writer is no longer authorized"
        ) from exc
    return shown
