"""Durable list-choice receipts backed by the existing CoS private task model.

Only fresh authenticated callers may offer or finish a choice. This module has
no provider executor and never turns retained history into a current input.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException

from adminme.events.envelope import Sensitivity
from adminme.events.schemas.reminder_choices import (
    ReminderChoiceFinishedV1,
    ReminderChoiceOfferedV1,
    ReminderListChoice,
    ReminderChoiceConversationV1,
    ReminderChoiceSelectionV1,
)
from adminme.lib.session import Session
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.service_context import source_authority
from adminme.products.openclaw.service_work import (
    _append as append_work,
    _instruction_source,
    _internal_session,
    _key as work_key,
)
from adminme.reminder_choices import (
    FINISHED,
    OFFERED,
    choice_task_id,
    digest,
    selected_task,
)
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
)


OFFER_TYPES = [OFFERED, "cos.reminder.mode_offered"]


def offered_payload(event: dict[str, Any]) -> Any:
    if event.get("type") == "cos.reminder.mode_offered":
        from adminme.events.schemas.reminder_modes import ReminderModeOfferedV1

        return ReminderModeOfferedV1.model_validate(event["payload"])
    if event.get("type") != OFFERED:
        raise HTTPException(403, "Unsupported reminder clarification")
    return ReminderChoiceOfferedV1.model_validate(event["payload"])


def draft_fields(*, title: str, due_at: datetime, notes: str | None) -> dict[str, Any]:
    return {"title": title, "due_at": due_at.isoformat(), "notes": notes}


def receipt_key(member_id: str, event_type: str, identity: str) -> str:
    return f"openclaw:{member_id}:{event_type}:{identity}"


def _owned(event: dict[str, Any], session: Session, event_type: str) -> bool:
    return bool(
        event.get("tenant_id") == session.tenant_id
        and event.get("type") == event_type
        and event.get("schema_version") == 1
        and event.get("owner_scope") == f"private:{session.auth_member_id}"
        and event.get("visibility_scope") == f"private:{session.auth_member_id}"
        and event.get("source_adapter") == "openclaw-plugin"
        and event.get("source_account_id") == session.auth_member_id
        and event.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
        and event.get("payload", {}).get("member_id") == session.auth_member_id
    )


async def source_and_work_authority(
    runtime: Any, member: Session, source_id: str
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    source = await _instruction_source(runtime, member, source_id)
    internal = _internal_session(runtime, member.correlation_id or "reminder-choice")
    context = member.service_context
    authority = (
        source_authority(runtime, internal, source, [context])
        if context is not None and "read_context" in context.permissions
        else None
    )
    if authority is None:
        raise HTTPException(
            403, "Current source policy does not permit this reminder task"
        )
    return source, internal, authority


async def offered_by_action(
    runtime: Any, member: Session, action_id: str
) -> dict[str, Any] | None:
    rows = []
    for kind in OFFER_TYPES:
        row = await runtime.event_log.get_by_idempotency(
            member.tenant_id, receipt_key(member.auth_member_id, kind, action_id)
        )
        if row is not None:
            if not _owned(row, member, kind):
                raise HTTPException(404, "Private reminder choice not found")
            rows.append(row)
    if len(rows) > 1:
        raise HTTPException(409, "Conflicting reminder offer identities")
    return rows[0] if rows else None


async def offer_choice(
    runtime: Any,
    member: Session,
    source: dict[str, Any],
    *,
    action_id: str,
    request_id: str,
    provider_account_id: str,
    title: str,
    due_at: datetime,
    notes: str | None,
    choices: list[ReminderListChoice],
) -> dict[str, Any]:
    """Persist one new offered draft; a replay cannot reset its identity or expiry."""
    from adminme.request_actions_control import request_actions_allowed

    if not choices:
        raise HTTPException(409, "No current authorized reminder list can be offered")
    if not request_actions_allowed(
        runtime, issued_at=source["payload"]["submitted_at"]
    ):
        raise HTTPException(
            409, "Authenticated-request actions are revoked for recovery"
        )
    draft = draft_fields(title=title, due_at=due_at, notes=notes)
    draft_fingerprint = digest(draft)
    lock = f"reminder-choice-offer:{member.auth_member_id}:{source['event_id']}"
    async with runtime.settings_command_locks.setdefault(lock, asyncio.Lock()):
        # Preserve the first request ID even if a model restarts with a new one.
        for previous in await runtime.event_log.get_by_causation(source["event_id"]):
            if (
                _owned(previous, member, OFFERED)
                and previous["payload"]["draft_fingerprint"] == draft_fingerprint
            ):
                await ensure_choice_work(runtime, member, previous)
                return previous
        prior = await offered_by_action(runtime, member, action_id)
        if prior is not None:
            if (
                prior["payload"]["draft_fingerprint"] != draft_fingerprint
                or prior["payload"]["original_source_event_id"] != source["event_id"]
            ):
                raise HTTPException(
                    409, "Request identity already belongs to a pending reminder"
                )
            await ensure_choice_work(runtime, member, prior)
            return prior
        now = datetime.now(timezone.utc)
        if due_at <= now:
            raise HTTPException(
                409, "A past-due reminder cannot enter a pending list choice"
            )
        source, _, _ = await source_and_work_authority(
            runtime, member, source["event_id"]
        )
        task_id = choice_task_id(member.tenant_id, member.auth_member_id, action_id)
        payload = ReminderChoiceOfferedV1(
            member_id=member.auth_member_id,
            task_id=task_id,
            action_id=action_id,
            request_id=request_id,
            original_source_event_id=source["event_id"],
            original_source_fingerprint=digest(source["payload"]["text"]),
            original_submitted_at=source["payload"]["submitted_at"],
            draft_fingerprint=draft_fingerprint,
            provider="apple_reminders",
            operation="create_reminder",
            resource_owner="requester",
            provider_account_id=provider_account_id,
            title=title,
            due_at=due_at,
            notes=notes,
            choices=choices,
            offered_at=now,
            pending_expires_at=due_at,
            expiry_reason="requested_reminder_due_time",
        )
        event_id, _ = await seam._append(
            runtime,
            member,
            event_type=OFFERED,
            source_account_id=member.auth_member_id,
            owner_scope=f"private:{member.auth_member_id}",
            visibility_scope=f"private:{member.auth_member_id}",
            sensitivity=cast(Sensitivity, source["sensitivity"]),
            payload=payload.model_dump(mode="json"),
            request_id=action_id,
            causation_id=source["event_id"],
        )
        event = await runtime.event_log.get(event_id)
        assert event is not None
        await ensure_choice_work(runtime, member, event)
        return event


async def ensure_choice_work(
    runtime: Any, member: Session, offered: dict[str, Any]
) -> None:
    """Repair only this explicitly accessed receipt after a partial intake append."""
    p = offered["payload"]
    source, internal, authority = await source_and_work_authority(
        runtime, member, p["original_source_event_id"]
    )
    task_id = p["task_id"]
    work_request = f"reminder-choice:{task_id}"
    task = await runtime.event_log.get_by_idempotency(
        member.tenant_id, work_key(internal, "task.created", work_request)
    )
    if task is not None:
        return
    await append_work(
        runtime,
        internal,
        event_type="task.created",
        version=4,
        request_id=work_request,
        fingerprint=digest([offered["event_id"], p["draft_fingerprint"]]),
        causation_id=source["event_id"],
        sensitivity=cast(Sensitivity, source["sensitivity"]),
        payload={
            "task_id": task_id,
            "title": "Complete a requested reminder after clarification",
            "due": p["due_at"],
            "record_kind": "work",
            "source_authorities": [authority],
            "service_work_type": "clarification",
            "work_kind": "service_operational",
            "owner_party_id": COS_PARTY_ID,
            "responsible_party_id": COS_PARTY_ID,
            "assignee_party_id": COS_PARTY_ID,
            "service_party_id": COS_PARTY_ID,
            "service_assignment_role": "internal",
            "authority_kind": "service_self",
            "authority_grant_id": None,
            "source_invocation_id": member.correlation_id,
            "created_by": COS_SERVICE_PRINCIPAL_ID,
            "source_system": (
                "cos-reminder-mode-choice"
                if offered["type"] != OFFERED
                else "member-reminder-list-choice"
            ),
            "provider_effect_count": 0,
        },
    )


async def finished_choice(
    runtime: Any, member: Session, offered: dict[str, Any]
) -> dict[str, Any] | None:
    event = await runtime.event_log.get_by_idempotency(
        member.tenant_id,
        receipt_key(member.auth_member_id, FINISHED, offered["payload"]["action_id"]),
    )
    if event is not None and (
        not _owned(event, member, FINISHED)
        or event["payload"]["offered_event_id"] != offered["event_id"]
    ):
        raise HTTPException(409, "Pending reminder result identity is inconsistent")
    return event


def instruction_identity(event: dict[str, Any], tenant: str, member: str) -> bool:
    return bool(
        event.get("tenant_id") == tenant
        and event.get("owner_scope")
        == event.get("visibility_scope")
        == f"private:{member}"
        and event.get("actor_identity") == member
        and event.get("payload", {}).get("member_id") == member
        and (
            (event.get("type"), event.get("schema_version"))
            == ("cos.conversation.instruction", 1)
            or (
                (event.get("type"), event.get("schema_version"))
                == ("triage.intent.submitted", 2)
                and event["payload"].get("submission_kind") == "member_forwarded"
                and event["payload"].get("intake_channel")
                in {"imessage", "apple_imessage"}
            )
        )
    )


def instruction_conversation(
    source: dict[str, Any],
) -> ReminderChoiceConversationV1 | None:
    p = source["payload"]
    if (
        (source["type"], source["schema_version"])
        == ("cos.conversation.instruction", 1)
        and p.get("channel_id") == "gmail"
        and p.get("authentication_rule") == "enrolled_receiver_authenticated_sender"
        and p.get("thread_id")
    ):
        return ReminderChoiceConversationV1.model_validate(
            {
                key: p[key]
                for key in ("channel_id", "account_id", "actor_id", "thread_id")
            }
        )
    return None


async def selection_snapshot(
    event_log: Any, tenant: str, member: str, answer: dict[str, Any]
) -> ReminderChoiceSelectionV1:
    """Rebuild the answer's immutable candidate set, independent of current grants.

    Offers, confirmations or cancellations recorded after this answer cannot
    change its meaning on replay. Current authority is separately rechecked for
    the selected original source and destination before any new confirmation.
    """
    if not instruction_identity(answer, tenant, member):
        raise HTTPException(403, "Reminder choice requires an authentic member answer")
    cutoff = datetime.fromisoformat(answer["payload"]["submitted_at"])
    candidates: list[dict[str, Any]] = []
    async for event in event_log.read_since(
        owner_scope=f"private:{member}", types=OFFER_TYPES
    ):
        if event["event_id"] >= answer["event_id"] or event["tenant_id"] != tenant:
            continue
        if (
            event["schema_version"] != 1
            or event["visibility_scope"] != f"private:{member}"
            or event["source_adapter"] != "openclaw-plugin"
            or event["source_account_id"] != member
            or event["actor_identity"] != COS_SERVICE_PRINCIPAL_ID
        ):
            raise HTTPException(
                409, "Retained reminder offer ownership is inconsistent"
            )
        p = offered_payload(event)
        if p.pending_expires_at <= cutoff:
            continue
        if event["type"] != OFFERED:
            from adminme.products.openclaw.reminder_mode_evidence import (
                presentation_matches,
            )

            shown = await event_log.get_by_idempotency(
                tenant,
                receipt_key(member, "cos.reminder.mode_presented", event["event_id"]),
            )
            if (
                shown is None
                or shown["event_id"] >= answer["event_id"]
                or not await presentation_matches(event_log, event, shown)
            ):
                continue
        original = await event_log.get(p.original_source_event_id)
        if (
            original is None
            or not instruction_identity(original, tenant, member)
            or p.member_id != member
            or original["event_id"] >= event["event_id"]
            or event["causation_id"] != original["event_id"]
            or digest(original["payload"]["text"]) != p.original_source_fingerprint
            or datetime.fromisoformat(original["payload"]["submitted_at"])
            != p.original_submitted_at
            or digest(draft_fields(title=p.title, due_at=p.due_at, notes=p.notes))
            != p.draft_fingerprint
            or p.task_id != choice_task_id(tenant, member, p.action_id)
        ):
            raise HTTPException(409, "Retained reminder task selection is inconsistent")
        finished = await event_log.get_by_idempotency(
            tenant, receipt_key(member, FINISHED, p.action_id)
        )
        domain = await event_log.get_by_idempotency(
            tenant, f"provider-action:{member}:{p.action_id}"
        )
        if any(
            row is not None and row["event_id"] < answer["event_id"]
            for row in (finished, domain)
        ):
            continue
        candidates.append(
            {
                "offered_event_id": event["event_id"],
                "task_id": p.task_id,
                "title_fingerprint": digest(p.title.strip().lower()),
                "pending_expires_at": p.pending_expires_at,
                "conversation": instruction_conversation(original),
            }
        )
    return ReminderChoiceSelectionV1.model_validate(
        {
            "answer_source_event_id": answer["event_id"],
            "answer_submitted_at": cutoff,
            "answer_conversation": instruction_conversation(answer),
            "eligible_tasks": sorted(
                candidates, key=lambda row: row["offered_event_id"]
            ),
        }
    )


async def selected_task_answer(
    runtime: Any, member: Session, offered: dict[str, Any], answer: dict[str, Any]
) -> tuple[ReminderChoiceSelectionV1, str]:
    snapshot = await selection_snapshot(
        runtime.event_log, member.tenant_id, member.auth_member_id, answer
    )
    selected = selected_task(snapshot, answer["payload"]["text"])
    if selected is None or selected[0].offered_event_id != offered["event_id"]:
        raise HTTPException(
            409, "The member's answer does not uniquely identify this pending reminder"
        )
    return snapshot, selected[1]


async def resolve_task(
    runtime: Any,
    member: Session,
    task_id: str | None,
    *,
    answer: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Effectful answers require authentic selection; task IDs only name reads."""
    if answer is not None:
        snapshot = await selection_snapshot(
            runtime.event_log, member.tenant_id, member.auth_member_id, answer
        )
        selected = selected_task(snapshot, answer["payload"]["text"])
        if selected is not None:
            if task_id is not None and task_id != selected[0].task_id:
                raise HTTPException(
                    409, "Tool task identity differs from the member's answer"
                )
            event = await event_log_get(runtime, selected[0].offered_event_id)
            await validate_original(runtime, member, event)
            return event
        if snapshot.eligible_tasks:
            return [
                await event_log_get(runtime, row.offered_event_id)
                for row in snapshot.eligible_tasks
            ]
        # Named finished/expired tasks can still report status, but cannot gain
        # a new authorization from a later unrelated message.
    choices = []
    now = datetime.now(timezone.utc)
    async for event in runtime.event_log.read_since(
        owner_scope=f"private:{member.auth_member_id}", types=OFFER_TYPES
    ):
        if not _owned(event, member, event["type"]):
            continue
        payload = offered_payload(event)
        if task_id is not None:
            if payload.task_id == task_id:
                await validate_original(runtime, member, event)
                return event
            continue
        if payload.pending_expires_at <= now or await finished_choice(
            runtime, member, event
        ):
            continue
        try:
            await validate_original(runtime, member, event)
        except HTTPException:
            continue
        choices.append(event)
    if task_id is not None:
        raise HTTPException(404, "Private reminder task not found")
    return choices[0] if len(choices) == 1 else choices


async def event_log_get(runtime: Any, event_id: str) -> dict[str, Any]:
    event = await runtime.event_log.get(event_id)
    if event is None:
        raise HTTPException(409, "Retained reminder choice is unavailable")
    return cast(dict[str, Any], event)


async def validate_original(
    runtime: Any, member: Session, offered: dict[str, Any]
) -> dict[str, Any]:
    if offered.get("type") not in OFFER_TYPES or not _owned(
        offered, member, offered["type"]
    ):
        raise HTTPException(404, "Private reminder choice not found")
    p = offered_payload(offered)
    source, _, _ = await source_and_work_authority(
        runtime, member, p.original_source_event_id
    )
    if (
        digest(source["payload"]["text"]) != p.original_source_fingerprint
        or datetime.fromisoformat(source["payload"]["submitted_at"])
        != p.original_submitted_at
        or offered["causation_id"] != source["event_id"]
        or p.draft_fingerprint
        != digest(draft_fields(title=p.title, due_at=p.due_at, notes=p.notes))
        or p.task_id
        != choice_task_id(member.tenant_id, member.auth_member_id, p.action_id)
    ):
        raise HTTPException(409, "Original reminder choice evidence is inconsistent")
    return source


async def finish_choice(
    runtime: Any,
    member: Session,
    offered: dict[str, Any],
    answer: dict[str, Any],
    *,
    state: str,
    selected_list_id: str | None = None,
    domain_approval_event_id: str | None = None,
) -> dict[str, Any]:
    """Called under the action lock; confirmed is not a provider success."""
    prior = await finished_choice(runtime, member, offered)
    if prior is not None:
        await update_choice_work(
            runtime, member, offered, prior["payload"]["state"], prior["event_id"]
        )
        return prior
    p = offered["payload"]
    payload = ReminderChoiceFinishedV1.model_validate(
        {
            "member_id": member.auth_member_id,
            "task_id": p["task_id"],
            "action_id": p["action_id"],
            "offered_event_id": offered["event_id"],
            "answer_source_event_id": answer["event_id"],
            "state": state,
            "decided_at": datetime.now(timezone.utc),
            "selected_list_id": selected_list_id,
            "domain_approval_event_id": domain_approval_event_id,
        }
    )
    event_id, _ = await seam._append(
        runtime,
        member,
        event_type=FINISHED,
        source_account_id=member.auth_member_id,
        owner_scope=f"private:{member.auth_member_id}",
        visibility_scope=f"private:{member.auth_member_id}",
        sensitivity=cast(Sensitivity, offered["sensitivity"]),
        payload=payload.model_dump(mode="json"),
        request_id=p["action_id"],
        causation_id=answer["event_id"],
    )
    event = await runtime.event_log.get(event_id)
    assert event is not None
    await update_choice_work(runtime, member, offered, state, event_id)
    return event


async def update_choice_work(
    runtime: Any, member: Session, offered: dict[str, Any], state: str, evidence_id: str
) -> None:
    """Mirror a confirmed/cancelled/terminal fact, never infer a provider result."""
    from adminme.projections.tasks.queries import get_task

    await ensure_choice_work(runtime, member, offered)
    internal = _internal_session(runtime, member.correlation_id or "reminder-choice")
    task_id = offered["payload"]["task_id"]
    work_request = f"reminder-choice:{task_id}"
    created = await runtime.event_log.get_by_idempotency(
        member.tenant_id, work_key(internal, "task.created", work_request)
    )
    assert created is not None
    await runtime.wait_for_projection("tasks", created["event_id"])
    transition = {
        "confirmed": ("start", "in_progress", "member_input"),
        "cancelled": ("cancel", "cancelled", "cancelled"),
        "succeeded": ("complete", "done", "completed"),
    }
    if state not in transition:
        return
    action, status, reason = transition[state]
    request_id = f"reminder-choice:{task_id}:{state}"
    async with runtime.settings_command_locks.setdefault(
        f"cos-private-work:{member.tenant_id}:{task_id}", asyncio.Lock()
    ):
        previous = await runtime.event_log.get_by_idempotency(
            member.tenant_id, work_key(internal, "cos.work.state_changed", request_id)
        )
        if previous is not None:
            return
        row = get_task(
            runtime.projections.connection("tasks"), internal, task_id=task_id
        )
        if row is None or row["status"] in {"done", "cancelled", "dismissed"}:
            return
        event_id = await append_work(
            runtime,
            internal,
            event_type="cos.work.state_changed",
            version=2,
            request_id=request_id,
            fingerprint=digest([evidence_id, state]),
            causation_id=evidence_id,
            sensitivity=cast(Sensitivity, offered["sensitivity"]),
            payload={
                "task_id": task_id,
                "action": action,
                "resulting_status": status,
                "changed_at": datetime.now(timezone.utc).isoformat(),
                "actor_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "actor_service_party_id": COS_PARTY_ID,
                "operator_principal_id": COS_AUTOMATION_OPERATOR_ID,
                "authority_kind": "service_self",
                "authority_grant_id": None,
                "reason_code": reason,
                "expected_last_event_id": row["last_event_id"],
                "provider_effect_count": 0,
            },
        )
        await runtime.wait_for_projection("tasks", event_id)


def choice_summary(offered: dict[str, Any]) -> dict[str, Any]:
    p = offered["payload"]
    if offered["type"] != OFFERED:
        from adminme.products.openclaw.reminder_mode_tools import mode_summary

        return mode_summary(offered)
    return {
        "state": "expired"
        if datetime.fromisoformat(p["pending_expires_at"]) <= datetime.now(timezone.utc)
        else "list_choice_required",
        "task_id": p["task_id"],
        "choice_event_id": offered["event_id"],
        "request_id": p["request_id"],
        "title": p["title"],
        "due_at": p["due_at"],
        "pending_expires_at": p["pending_expires_at"],
        "lists": p["choices"],
        "provider_effect_count": 0,
        "automatic_retry": False,
        "timer_registered": False,
    }
