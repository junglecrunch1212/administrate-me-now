"""Current private member instruction -> existing two-approval Apple command."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from adminme.apple_service_permissions import permission_options, service_permission
from adminme.events.envelope import Sensitivity
from adminme.lib.session import Session
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.service_work import current_private_instruction

router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["native-apple-reminders"])


class CurrentReminderInstruction(BaseModel):
    model_config = {"extra": "forbid"}
    provenance: seam.Provenance
    source_message_event_id: str = Field(min_length=1, max_length=240)


class ConfirmAppleReminderRequest(CurrentReminderInstruction):
    request_authorization: bool = False
    title: str = Field(min_length=1, max_length=1000)
    due_at: AwareDatetime
    notes: str | None = Field(default=None, max_length=10000)
    list_id: str | None = Field(default=None, min_length=1, max_length=1000)
    request_id: str = Field(min_length=8, max_length=120)

    @field_validator("title")
    @classmethod
    def title_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a reminder requires a title")
        return value.strip()

    @field_validator("due_at", mode="before")
    @classmethod
    def explicit_time(cls, value: Any) -> str:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("due_at must be an offset-aware date and time")
        return value

    @field_validator("due_at")
    @classmethod
    def exact_seconds(cls, value: datetime) -> datetime:
        if value.microsecond:
            raise ValueError("Apple reminder due time supports whole seconds")
        try:
            return value.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("due_at cannot be represented as UTC components") from exc


def reminder_command_id(tenant_id: str, member_id: str, request_id: str) -> str:
    """Provider terminal idempotency is tenant-wide, so bind the requesting member."""
    return "native-reminder:" + seam._fingerprint(
        {
            "tenant_id": tenant_id,
            "member_id": member_id,
            "request_id": request_id,
        }
    )


def due_components(value: datetime) -> dict[str, Any]:
    """Use a native recognized zone and preserve the exact offset-aware instant."""
    value = value.astimezone(timezone.utc)
    return {
        **{
            key: getattr(value, key)
            for key in ("year", "month", "day", "hour", "minute", "second")
        },
        "time_zone": "UTC",
        "all_day": False,
        "floating": False,
    }


class AppleReminderStatusRequest(CurrentReminderInstruction):
    domain_approval_event_id: str = Field(min_length=1, max_length=240)


def _same_owner(event: dict[str, Any], session: Session) -> bool:
    return (
        event.get("tenant_id") == session.tenant_id
        and event.get("owner_scope") == f"private:{session.auth_member_id}"
        and event.get("visibility_scope") == f"private:{session.auth_member_id}"
        and event.get("payload", {}).get("member_id") == session.auth_member_id
    )


async def apple_reminder_status(
    runtime: Any, session: Session, domain_event_id: str
) -> dict[str, Any]:
    """Read one exact member command chain. An accepted command is not an effect."""
    domain = await runtime.event_log.get(domain_event_id)
    if (
        domain is None
        or not _same_owner(domain, session)
        or domain.get("type") != "member.provider_action.confirmed"
        or domain.get("schema_version") != 1
        or domain.get("actor_identity") != session.auth_member_id
        or domain.get("source_account_id") != session.auth_member_id
        or domain.get("source_adapter") != "openclaw-apple-reminder"
        or domain["payload"].get("provider") != "apple_reminders"
        or domain["payload"].get("operation") != "create_reminder"
    ):
        raise HTTPException(404, "private Apple reminder confirmation not found")
    approval = domain["payload"]
    requested = approval["command_payload"]
    due = requested["due"]
    due_at = datetime(
        **{
            key: due[key]
            for key in ("year", "month", "day", "hour", "minute", "second")
        },
        tzinfo=timezone.utc,
    ).isoformat()
    receipt: dict[str, Any] = {
        "state": "awaiting_openclaw_approval",
        "domain_approval_event_id": domain_event_id,
        "command_id": approval["action_id"],
        "due_at": due_at,
        "list_id": requested["list_external_id"],
        "title": requested["title"],
        "provider_effect_count": 0,
        "automatic_retry": False,
        "due_status": "past_due"
        if datetime.fromisoformat(due_at) < datetime.now(timezone.utc)
        else "future",
        "authorization_age_seconds": max(
            0,
            int(
                (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(approval["confirmed_at"])
                ).total_seconds()
            ),
        ),
        "authorization_expires_at": None,  # Legacy confirmations never recorded an expiry.
    }
    command = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"external-sync-approved:{domain_event_id}"
    )
    if command is None:
        from adminme.products.openclaw.request_authorization import grounding_for_domain

        grounding = await grounding_for_domain(runtime, session, domain)
        if grounding is not None:
            expires = grounding["payload"]["expires_at"]
            receipt["authorization_expires_at"] = expires
            from adminme.request_actions_control import request_actions_allowed

            receipt["request_authorization_state"] = (
                "revoked"
                if not request_actions_allowed(
                    runtime, issued_at=grounding["payload"]["issued_at"]
                )
                else "expired"
                if datetime.fromisoformat(expires) <= datetime.now(timezone.utc)
                else "current"
            )
            if receipt["request_authorization_state"] == "current":
                receipt["state"] = "awaiting_request_policy"
        elif receipt["due_status"] == "past_due":
            receipt["request_authorization_state"] = (
                "legacy_overdue_requires_explicit_resume"
            )
        return receipt
    from adminme.apple_service_history import apple_command_owner_matches

    expected = {
        "command_id": approval["action_id"],
        "provider": "apple_reminders",
        "operation": "create_reminder",
        "provider_account_id": approval["provider_account_id"],
        "domain_approval_event_id": domain_event_id,
        "request_fingerprint": approval["request_fingerprint"],
    }
    if (
        not _same_owner(command, session)
        or command.get("type") != "external_sync.write_requested"
        or command.get("schema_version") != 2
        or any(command["payload"].get(k) != v for k, v in expected.items())
        or not await apple_command_owner_matches(
            runtime.event_log, command, through=command["event_id"]
        )
    ):
        return {**receipt, "state": "unknown", "provider_effect_count": None}
    receipt.update(
        state="pending",
        command_event_id=command["event_id"],
        provider_effect_count=None,
    )
    terminal = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"external-sync-result:{approval['action_id']}"
    )
    if terminal is None:
        return receipt
    payload = terminal.get("payload") or {}
    if (
        not _same_owner(terminal, session)
        or terminal.get("source_adapter")
        not in {
            "adminme-bridge-command-dispatcher",
            "bridge-command-observation-gate",
            "bridge-command-acquisition-gate",
            "bridge-command-current-authority-gate",
        }
        or terminal.get("source_account_id") != approval["provider_account_id"]
        or terminal.get("causation_id") != command["event_id"]
        or terminal.get("schema_version") != 2
        or any(payload.get(k) != v for k, v in expected.items())
        or payload.get("command_event_id") != command["event_id"]
        or payload.get("openclaw_approval_event_id")
        != command["payload"]["openclaw_approval_event_id"]
    ):
        return {**receipt, "state": "unknown"}
    result = {**receipt, "state": "unknown", "result_event_id": terminal["event_id"]}
    if terminal["type"] == "external_sync.write_completed":
        if (
            terminal.get("source_adapter") == "adminme-bridge-command-dispatcher"
            and payload.get("result", {}).get("applied") is True
            and payload.get("suppressed") is False
            and payload.get("provider_called") is True
            and isinstance(payload.get("external_id"), str)
            and payload["external_id"]
        ):
            result.update(
                state="succeeded",
                provider_effect_count=1,
                external_id=payload["external_id"],
                verification="provider_terminal",
                provider_observation="pending",
            )
            # An independent provider mirror is additional evidence, not a
            # fabricated Triage proposal or a prerequisite for a terminal receipt.
            from adminme.projections.lists.queries import get_item

            observed = get_item(
                runtime.projections.connection("lists"),
                session,
                list_external_id=requested["list_external_id"],
                item_external_id=payload["external_id"],
            )
            if (
                observed is not None
                and observed.get("provider") == "apple_reminders"
                and observed.get("provider_account_id")
                == approval["provider_account_id"]
                and observed.get("member_id") == session.auth_member_id
            ):
                result["provider_observation"] = "observed"
                result["observation_event_id"] = observed["last_event_id"]
        elif (
            payload.get("suppressed") is True or payload.get("provider_called") is False
        ):
            result.update(state="failed", provider_effect_count=0)
    elif terminal["type"] == "external_sync.write_failed":
        if payload.get("provider_called") is False:
            result.update(state="failed", provider_effect_count=0)
        # A provider-called failure can be ambiguous. Never invent a retry/new ID.
    return result


@router.post("/confirm-apple-reminder")
async def confirm_apple_reminder(
    body: ConfirmAppleReminderRequest, request: Request
) -> dict[str, Any]:
    session, source = await current_private_instruction(
        request,
        body.provenance,
        body.source_message_event_id,
        required_permission="prepare_external_actions",
    )
    runtime = seam._runtime(request)
    fingerprint = seam._fingerprint(
        body.model_dump(
            mode="json",
            exclude={"provenance", "source_message_event_id", "request_authorization"},
        )
    )
    command_id = reminder_command_id(
        session.tenant_id, session.auth_member_id, body.request_id
    )
    async with runtime.settings_command_locks.setdefault(
        f"apple-reminder:{command_id}", asyncio.Lock()
    ):
        prior = await runtime.event_log.get_by_idempotency(
            session.tenant_id, f"provider-action:{session.auth_member_id}:{command_id}"
        )
        if prior is not None:
            if prior.get("raw_ref") != f"request-sha256:{fingerprint}":
                raise HTTPException(
                    409, "request id conflicts with a prior Apple reminder"
                )
            receipt = await apple_reminder_status(runtime, session, prior["event_id"])
            if body.request_authorization:
                from adminme.products.openclaw.request_authorization import (
                    grounding_for_domain,
                    ticket,
                )

                grounding = await grounding_for_domain(runtime, session, prior)
                if (
                    grounding is not None
                    and receipt.get("request_authorization_state") == "current"
                ):
                    receipt["request_authorization"] = ticket(
                        runtime, session, prior, grounding
                    )
            return {**receipt, "duplicate": True, "event_id": prior["event_id"]}
        if await runtime.observation.is_active():
            raise HTTPException(
                409, "Observation mode suppresses Apple reminder confirmation"
            )
        permission = service_permission(
            runtime, session.auth_member_id, "apple_reminders"
        )
        if permission is None or "create_reminder" not in permission["operations"]:
            raise HTTPException(
                409, "Current Apple Reminders writing permission is unavailable"
            )
        lists = permission["allowed_container_ids"]
        selected = body.list_id
        if selected is None:
            if len(lists) != 1:
                choices = permission_options(
                    runtime, session.auth_member_id, "apple_reminders"
                )
                return {
                    "state": "list_choice_required",
                    "provider_effect_count": 0,
                    "lists": [
                        {"list_id": row["container_id"], "label": row["label"]}
                        for row in choices["scopes"]
                        if row["container_id"] in lists
                    ],
                }
            selected = lists[0]
        if selected not in lists:
            raise HTTPException(
                403, "Apple Reminders list is outside current member permission"
            )
        grounding = None
        if body.request_authorization:
            from adminme.products.openclaw.request_authorization import ground_action

            grounding = await ground_action(
                runtime,
                session,
                source,
                action_id=command_id,
                request_fingerprint=fingerprint,
                action={
                    "provider": "apple_reminders",
                    "operation": "create_reminder",
                    "resource_owner": "requester",
                    "title": body.title,
                    "due_at": body.due_at.isoformat(),
                    "notes": body.notes,
                    "list_id": selected,
                    "selected_by_policy": body.list_id is None,
                },
            )
        from adminme.products.member.main import (
            ReminderCommandRequest,
            confirm_reminder_command,
        )

        confirmed = await confirm_reminder_command(
            ReminderCommandRequest(
                provider="apple_reminders",
                provider_account_id=permission["provider_account_id"],
                operation="create_reminder",
                list_external_id=selected,
                request_id=command_id,
                payload={
                    "title": body.title,
                    "due": due_components(body.due_at),
                    **({"notes": body.notes} if body.notes is not None else {}),
                },
            ),
            runtime,
            session,
            source_adapter="openclaw-apple-reminder",
            causation_id=source["event_id"],
            source_sensitivity=cast(Sensitivity, source["sensitivity"]),
            request_fingerprint=fingerprint,
        )
        receipt = await apple_reminder_status(runtime, session, confirmed["event_id"])
        if grounding is not None:
            from adminme.products.openclaw.request_authorization import ticket

            domain = await runtime.event_log.get(confirmed["event_id"])
            assert domain is not None
            receipt["request_authorization"] = ticket(
                runtime, session, domain, grounding
            )
            receipt["state"] = "awaiting_request_policy"

    await seam._record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.confirm-apple-reminder",
        result_event_ids=[confirmed["event_id"]],
    )
    return {**receipt, "duplicate": False, "event_id": confirmed["event_id"]}


@router.post("/apple-reminder-status")
async def reminder_status(
    body: AppleReminderStatusRequest, request: Request
) -> dict[str, Any]:
    session, _ = await current_private_instruction(
        request, body.provenance, body.source_message_event_id
    )
    result = await apple_reminder_status(
        seam._runtime(request), session, body.domain_approval_event_id
    )
    await seam._record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.apple-reminder-status",
        result_event_ids=[body.domain_approval_event_id],
    )
    return result
