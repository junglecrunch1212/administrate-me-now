"""Authenticated household instruction -> CoS-owned native reminder command."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from adminme.adapters.cos_apple_reminders import digest
from adminme.adapters.cos_reminders_helper import NativeWriteResult
from adminme.adapters.cos_reminders_write import exact_due_observed
from adminme.adapters.cos_reminders_write_policy import (
    RULE_ID,
    policy_from_config,
    write_selection,
)
from adminme.cos_reminders_commands import (
    CoSReminderIntent,
    DISPATCHER,
    SOURCE_ADAPTER,
    command_owner_matches,
)
from adminme.events.envelope import EventEnvelope
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.apple_reminders import (
    AppleReminderStatusRequest,
    ConfirmAppleReminderRequest,
)
from adminme.products.openclaw.request_authorization import (
    ground_action,
    grounding_for_domain,
    ticket,
)
from adminme.products.openclaw.service_work import current_private_instruction
from adminme.request_actions_control import request_actions_allowed
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["cos-owned-reminders"])


def _same_requester(event: dict[str, Any], session: Any) -> bool:
    return bool(
        event.get("tenant_id") == session.tenant_id
        and event.get("owner_scope")
        == event.get("visibility_scope")
        == f"private:{session.auth_member_id}"
        and event.get("payload", {}).get("member_id") == session.auth_member_id
    )


async def reminder_status(runtime: Any, session: Any, domain_id: str) -> dict[str, Any]:
    domain = await runtime.event_log.get(domain_id)
    if (
        domain is None
        or not _same_requester(domain, session)
        or domain.get("type") != "member.provider_action.confirmed"
        or domain.get("schema_version") != 1
        or domain.get("source_adapter") != SOURCE_ADAPTER
        or domain.get("actor_identity") != session.auth_member_id
        or domain.get("source_account_id") != session.auth_member_id
        or domain["payload"].get("provider") != "apple_reminders"
        or domain["payload"].get("operation") != "create_reminder"
    ):
        raise HTTPException(404, "CoS reminder confirmation not found")
    intent = CoSReminderIntent.model_validate(domain["payload"]["command_payload"])
    receipt: dict[str, Any] = {
        "state": "awaiting_request_policy",
        "domain_approval_event_id": domain_id,
        "command_id": domain["payload"]["action_id"],
        "resource_owner": COS_SERVICE_PRINCIPAL_ID,
        "title": intent.title,
        "due_at": intent.due_at.isoformat(),
        "list_id": intent.selection.list_id,
        "authorization_expires_at": intent.authorization_expires_at.isoformat(),
        "provider_effect_count": 0,
        "automatic_retry": False,
    }
    command = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"external-sync-approved:{domain_id}"
    )
    if command is None:
        try:
            intent.require_current(runtime.settings.adapter_config)
            grounded = await grounding_for_domain(runtime, session, domain)
            if grounded is None or not request_actions_allowed(
                runtime, issued_at=grounded["payload"]["issued_at"]
            ):
                raise PermissionError("Request execution revoked")
        except (PermissionError, ValueError):
            receipt["state"] = "expired_or_revoked"
        return receipt
    if not await command_owner_matches(
        runtime.event_log, command, through=command["event_id"]
    ):
        return {**receipt, "state": "unknown", "provider_effect_count": None}
    receipt.update(
        state="pending",
        command_event_id=command["event_id"],
        provider_effect_count=None,
    )
    terminal = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"external-sync-result:{receipt['command_id']}"
    )
    if terminal is None:
        return receipt
    payload = terminal.get("payload") or {}
    if (
        not _same_requester(terminal, session)
        or terminal.get("source_adapter") != DISPATCHER
        or terminal.get("source_account_id") != intent.selection.provider_account_id
        or terminal.get("actor_identity") != COS_SERVICE_PRINCIPAL_ID
        or terminal.get("schema_version") != 2
        or terminal.get("causation_id") != command["event_id"]
        or payload.get("command_event_id") != command["event_id"]
        or any(
            payload.get(key) != command["payload"].get(key)
            for key in (
                "command_id",
                "domain_approval_event_id",
                "openclaw_approval_event_id",
                "provider",
                "provider_account_id",
                "operation",
                "request_fingerprint",
            )
        )
    ):
        return {**receipt, "state": "unknown"}
    receipt["result_event_id"] = terminal["event_id"]
    if terminal["type"] == "external_sync.write_completed" and not payload.get(
        "suppressed"
    ):
        try:
            native = NativeWriteResult.model_validate_json(
                json.dumps(payload["result"]["native"])
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return {**receipt, "state": "unknown"}
        if (
            payload.get("provider_called") is True
            and native.external_id == payload.get("external_id")
            and native.provider_account_id == intent.selection.provider_account_id
            and native.source_id == intent.selection.source_id
            and native.list_id == intent.selection.list_id
            and native.requested_due_at == intent.due_at
            and exact_due_observed(native.observed_due, intent.due_at)
        ):
            return {
                **receipt,
                "state": "succeeded",
                "provider_effect_count": 1,
                "external_id": native.external_id,
                "verification": "native_terminal_and_read_back",
                "observed_due": native.observed_due,
            }
    if terminal["type"] == "external_sync.write_failed":
        return {
            **receipt,
            "state": "unknown" if payload.get("provider_called") else "failed",
            "provider_effect_count": None if payload.get("provider_called") else 0,
            "error_kind": payload.get("error_kind"),
        }
    if payload.get("suppressed") is True and payload.get("provider_called") is False:
        return {**receipt, "state": "suppressed", "provider_effect_count": 0}
    return {**receipt, "state": "unknown"}


@router.post("/cos-apple-reminders/confirm")
async def confirm(
    body: ConfirmAppleReminderRequest, request: Request
) -> dict[str, Any]:
    session, source = await current_private_instruction(
        request,
        body.provenance,
        body.source_message_event_id,
        required_permission="prepare_external_actions",
    )
    runtime = seam._runtime(request)
    if not body.request_authorization:
        raise HTTPException(
            403, "CoS-owned reminders require the current authenticated request"
        )
    fingerprint = digest(body.model_dump(mode="json", exclude={"provenance"}))
    action_id = "cos-native-reminder:" + digest(
        {
            "tenant_id": session.tenant_id,
            "member_id": session.auth_member_id,
            "request_id": body.request_id,
        }
    )
    key = f"provider-action:{session.auth_member_id}:{action_id}"
    async with runtime.settings_command_locks.setdefault(
        f"cos-native-reminder:{action_id}", asyncio.Lock()
    ):
        existing = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
        if existing is not None:
            if existing.get("raw_ref") != f"request-sha256:{fingerprint}":
                raise HTTPException(
                    409, "Request id belongs to a different CoS reminder"
                )
            status = await reminder_status(runtime, session, existing["event_id"])
            grounded = await grounding_for_domain(runtime, session, existing)
            if grounded is not None and status["state"] == "awaiting_request_policy":
                status["request_authorization"] = ticket(
                    runtime, session, existing, grounded
                )
            return {**status, "duplicate": True, "event_id": existing["event_id"]}
        try:
            selection = write_selection(runtime.settings.adapter_config)
            policy = policy_from_config(runtime.settings.adapter_config)
            if body.list_id is not None and body.list_id != selection.list_id:
                raise PermissionError(
                    "Requested list is not the authorized CoS work list"
                )
            assert policy is not None
            intent = CoSReminderIntent(
                resource_owner=COS_SERVICE_PRINCIPAL_ID,
                operating_principal=COS_SERVICE_PRINCIPAL_ID,
                selection=selection,
                write_policy=policy,
                source_event_id=source["event_id"],
                title=body.title,
                notes=body.notes,
                due_at=body.due_at,
                authorization_expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=10),
                selected_by_policy=body.list_id is None,
            )
            intent.require_current(runtime.settings.adapter_config)
        except (PermissionError, ValueError) as exc:
            raise HTTPException(
                409, "The exact CoS reminder writer or selected list is unavailable"
            ) from exc
        grounded = await ground_action(
            runtime,
            session,
            source,
            action_id=action_id,
            request_fingerprint=fingerprint,
            action=intent.action(),
            rule_id=RULE_ID,
        )
        intent = intent.model_copy(
            update={
                "authorization_expires_at": datetime.fromisoformat(
                    grounded["payload"]["expires_at"]
                )
            }
        )
        now = EventEnvelope.now_utc_iso()
        payload = {
            "action_id": action_id,
            "member_id": session.auth_member_id,
            "provider": "apple_reminders",
            "provider_account_id": selection.provider_account_id,
            "operation": "create_reminder",
            "target_external_id": None,
            "expected_provider_revision": None,
            "command_payload": intent.model_dump(mode="json"),
            "request_fingerprint": fingerprint,
            "confirmed_at": now,
        }
        # Member bridge permission binding is intentionally not reused here:
        # the exact CoS-owned source is authorized by this separate typed rule.
        await seam._guard(
            runtime,
            session,
            "external_sync.write_requested",
            {
                "command_id": action_id,
                "member_id": session.auth_member_id,
                "provider": "apple_reminders",
                "provider_account_id": selection.provider_account_id,
                "operation": "create_reminder",
                "target_external_id": None,
                "expected_provider_revision": None,
                "payload": intent.model_dump(mode="json"),
                "requested_at": now,
            },
        )
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=session.tenant_id,
                type="member.provider_action.confirmed",
                schema_version=1,
                occurred_at=now,
                source_adapter=SOURCE_ADAPTER,
                source_account_id=session.auth_member_id,
                owner_scope=f"private:{session.auth_member_id}",
                visibility_scope=f"private:{session.auth_member_id}",
                sensitivity=source["sensitivity"],
                actor_identity=session.auth_member_id,
                correlation_id=session.correlation_id,
                causation_id=source["event_id"],
                idempotency_key=key,
                raw_ref=f"request-sha256:{fingerprint}",
                payload=payload,
            )
        )
        domain = await runtime.event_log.get(event_id)
        assert domain is not None
        await seam._record_tool_call(
            request,
            session,
            body.provenance,
            operation="tools.cos-apple-reminders.confirm",
            result_event_ids=[event_id],
        )
        return {
            **await reminder_status(runtime, session, event_id),
            "duplicate": False,
            "event_id": event_id,
            "source_message_event_id": source["event_id"],
            "request_authorization": ticket(runtime, session, domain, grounded),
        }


@router.post("/cos-apple-reminders/status")
async def status(body: AppleReminderStatusRequest, request: Request) -> dict[str, Any]:
    session, _ = await current_private_instruction(
        request, body.provenance, body.source_message_event_id
    )
    result = await reminder_status(
        seam._runtime(request), session, body.domain_approval_event_id
    )
    await seam._record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.cos-apple-reminders.status",
        result_event_ids=[body.domain_approval_event_id],
    )
    return result
