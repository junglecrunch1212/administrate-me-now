"""Authenticated member decisions for exact, native CoS tool approvals."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from adminme.apple_service_permissions import (
    PROVIDERS as APPLE_PROVIDERS,
    command_container,
    command_permission,
    permission_options,
)
from adminme.cos_response_policy import policy_from_event_log, recipient_decision
from adminme.lib.session import Session
from adminme.openclaw_topology import member_agent_id
from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    resolve_service_delegation,
)
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_SERVICE_PRINCIPAL_ID,
)

CONTRACT = "cos-member-approval-v1"


def approval_ticket(
    secret: str,
    *,
    operation: str,
    session_key: str,
    agent_id: str,
    member_id: str,
    approval_id: str = "",
    revision: str = "",
    decision: str = "",
) -> dict[str, Any]:
    values = {
        "session_key": session_key,
        "agent_id": agent_id,
        "member_id": member_id,
        "approval_id": approval_id,
        "revision": revision,
        "decision": decision,
        "nonce": uuid.uuid4().hex,
    }
    if not secret or len(secret) < 24 or any("|" in value for value in values.values()):
        raise HTTPException(503, "Reply approval is unavailable")
    expires = int(time.time() * 1000) + 8_000
    material = "|".join([CONTRACT, operation, *values.values(), str(expires)])
    return {
        **values,
        "expires_at_ms": expires,
        "signature": hmac.new(
            secret.encode(), material.encode(), hashlib.sha256
        ).hexdigest(),
    }


def _review_value(value: Any) -> str:
    if value is None:
        return "Clear"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return "; ".join(_review_value(item) for item in value) or "None"
    if isinstance(value, dict):
        return "; ".join(
            f"{key.replace('_', ' ').replace('dateTime', 'Date').replace('timeZone', 'Time zone').capitalize()}: {_review_value(item)}"
            for key, item in value.items()
            if key not in {"id", "external_id", "member_id"} and not key.endswith("_id")
        )
    return str(value)


def _apple_review(payload: dict[str, Any]) -> list[dict[str, str]]:
    command = payload.get("command_payload") or {}
    if payload["provider"] == "apple_notes":
        selected = command.get("note_request") or {}
        fields = {
            key: selected[key]
            for key in ("title", "body")
            if selected.get(key) is not None
        }
    else:
        labels = {
            "title": "Title",
            "summary": "Title",
            "body": "Text",
            "notes": "Notes",
            "description": "Description",
            "start": "Starts",
            "end": "Ends",
            "start_at": "Starts",
            "end_at": "Ends",
            "due": "Due",
            "due_at": "Due",
            "all_day": "All day",
            "timezone": "Time zone",
            "location": "Location",
            "priority": "Priority",
            "attendees": "Attendees",
            "url": "Link",
            "source_url": "Link",
            "alarms": "Alerts",
            "recurrence_rules": "Repeats",
            "recurrence": "Repeats",
            "availability": "Availability",
            "span": "Applies to",
        }
        fields = {labels[key]: value for key, value in command.items() if key in labels}
    return [
        {"label": key.capitalize(), "value": _review_value(value)}
        for key, value in fields.items()
    ]


def _apple_target_title(
    runtime: Any, session: Session, payload: dict[str, Any]
) -> str | None:
    from adminme.projections.calendars.queries import get_calendar_event
    from adminme.projections.lists.queries import get_item
    from adminme.projections.member_knowledge.queries import get_visible

    if runtime.projections is None:
        return None
    command = payload.get("command_payload") or {}
    provider = payload["provider"]
    container = command_container(provider, command)
    target = payload.get("target_external_id")
    item = None
    if provider == "apple_notes":
        knowledge_id = (command.get("note_request") or {}).get("knowledge_id")
        if knowledge_id:
            item = get_visible(
                runtime.projections.connection("member_knowledge"),
                session,
                knowledge_id=knowledge_id,
            )
        if (
            item is None
            or item.get("access_mode") != "owner"
            or item.get("source_external_id") != target
            or item.get("account_external_id") != command.get("account_external_id")
            or item.get("folder_external_id") != container
        ):
            return None
    elif provider == "apple_reminders":
        item = get_item(
            runtime.projections.connection("lists"),
            session,
            list_external_id=str(container),
            item_external_id=str(target),
        )
        if (
            item is None
            or item.get("provider") != provider
            or item.get("provider_account_id") != payload["provider_account_id"]
        ):
            return None
    else:
        conn = runtime.projections.connection("calendars")
        rows = conn.execute(
            "SELECT calendar_event_id FROM calendar_events WHERE tenant_id=? AND member_id=? AND calendar_source=? AND provider_account_id=? AND calendar_external_id=? AND external_uid=?",
            (
                session.tenant_id,
                session.auth_member_id,
                provider,
                payload["provider_account_id"],
                container,
                target,
            ),
        ).fetchall()
        if len(rows) == 1:
            item = get_calendar_event(
                conn, session, calendar_event_id=rows[0]["calendar_event_id"]
            )
    if (
        item is None
        or item.get("member_id") != session.auth_member_id
        or item.get("owner_scope") != f"private:{session.auth_member_id}"
        or item.get("visibility_scope") != f"private:{session.auth_member_id}"
    ):
        return None
    expected = payload.get("expected_provider_revision")
    if expected is not None and expected != item.get("provider_revision"):
        return None
    return str(item.get("title") or item.get("summary") or "Untitled item")


async def _apple_approval_row(
    runtime: Any, session: Session, event: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any] | None:
    payload = event["payload"]
    if payload.get("provider") not in APPLE_PROVIDERS:
        return None
    expected = hashlib.sha256(
        json.dumps(
            {"domain_approval_event_id": event["event_id"]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if row.get("request_fingerprint") != expected:
        return None
    command = {
        "member_id": session.auth_member_id,
        "provider": payload["provider"],
        "provider_account_id": payload["provider_account_id"],
        "operation": payload["operation"],
        "payload": dict(payload.get("command_payload") or {}),
    }
    can_allow = True
    review = _apple_review(payload)
    if not str(payload["operation"]).startswith("create_"):
        target_title = _apple_target_title(runtime, session, payload)
        if target_title is None:
            can_allow = False
        else:
            review.insert(0, {"label": "Existing item", "value": target_title})
    destination = "Previously selected destination"
    try:
        command_permission(runtime, session.auth_member_id, command)
        options = permission_options(
            runtime, session.auth_member_id, payload["provider"]
        )
        container = command_container(payload["provider"], command["payload"])
        destination_container = (
            command["payload"].get("destination_calendar_id")
            or command["payload"].get("destination_list_id")
            or container
        )
        destination = next(
            scope["label"]
            for scope in options["scopes"]
            if scope["provider_account_id"] == payload["provider_account_id"]
            and scope["container_id"] == destination_container
        )
    except (ValueError, StopIteration):
        can_allow = False
    if runtime.projections is None:
        can_allow = False
    else:
        try:
            delegation = resolve_service_delegation(
                runtime.projections.connection("parties"),
                tenant_id=session.tenant_id,
                service_principal_id=COS_SERVICE_PRINCIPAL_ID,
                operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
                context_scope="member_private",
                context_member_id=session.auth_member_id,
            )
            can_allow = (
                can_allow
                and {"read_context", "prepare_external_actions"}
                <= delegation.permissions
            )
        except ServiceDelegationResolutionError:
            can_allow = False
    observation = getattr(runtime, "observation", None)
    if observation is None or await observation.is_active():
        can_allow = False
    for key in (
        f"external-sync-approved:{event['event_id']}",
        f"external-sync-result:{payload['action_id']}",
    ):
        if (
            await runtime.event_log.get_by_idempotency(session.tenant_id, key)
            is not None
        ):
            can_allow = False
    return {
        "kind": "provider_action",
        "provider": payload["provider"],
        "operation": payload["operation"],
        "destination": destination,
        "review": review,
        "approval_id": row["approval_id"],
        "revision": row["revision"],
        "expires_at_ms": row["expires_at_ms"],
        "can_allow": bool(can_allow),
    }


async def pending_cos_approvals(
    runtime: Any, session: Session, session_key: str, *, _locked: bool = False
) -> list[dict[str, Any]]:
    if not _locked:
        async with runtime.settings_command_locks.setdefault(
            "settings-control", asyncio.Lock()
        ):
            return await pending_cos_approvals(
                runtime, session, session_key, _locked=True
            )
    agent_id = member_agent_id(session.auth_member_id)
    ticket = approval_ticket(
        runtime.settings.openclaw_shared_secret,
        operation="list",
        session_key=session_key,
        agent_id=agent_id,
        member_id=session.auth_member_id,
    )
    response = await runtime.member_chat.cos_approval_request(
        "administrateme.cosApprovals.list", ticket
    )
    rows = response.get("items")
    if not isinstance(rows, list) or len(rows) > 20:
        raise HTTPException(503, "Reply approval is unavailable")
    policy = await policy_from_event_log(runtime.event_log, session.tenant_id)
    visible = []
    if any(
        isinstance(row, dict) and row.get("kind") == "provider_action" for row in rows
    ):
        await runtime.settings_control.replay_apple_service_permission_events(
            runtime.event_log, tenant_id=session.tenant_id
        )
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            row.get("session_key") != session_key
            or row.get("agent_id") != agent_id
            or not isinstance(row.get("approval_id"), str)
            or not row["approval_id"].startswith("plugin:")
            or not isinstance(row.get("revision"), str)
            or len(row["revision"]) != 64
            or type(row.get("expires_at_ms")) is not int
            or not int(time.time() * 1000)
            < row["expires_at_ms"]
            <= int(time.time() * 1000) + 120_000
            or not isinstance(row.get("domain_approval_id"), str)
            or not isinstance(row.get("request_id"), str)
        ):
            continue
        kind = row.get("kind", "cos_response")
        tool = row.get("tool_name", "administrateme_cos_response")
        if not isinstance(kind, str) or not isinstance(tool, str):
            continue
        event_type = {
            ("cos_response", "administrateme_cos_response"): "cos.response.approved",
            (
                "provider_action",
                "administrateme_execute_pending_provider_action",
            ): "member.provider_action.confirmed",
        }.get((kind, tool))
        if event_type is None:
            continue
        event = await runtime.event_log.get(row["domain_approval_id"])
        if (
            event is None
            or event["type"] != event_type
            or event["tenant_id"] != session.tenant_id
            or event["actor_identity"] != session.auth_member_id
            or event["owner_scope"] != f"private:{session.auth_member_id}"
            or event["visibility_scope"] != f"private:{session.auth_member_id}"
            or event["payload"].get("member_id") != session.auth_member_id
        ):
            continue
        if kind == "provider_action":
            provider_row = await _apple_approval_row(runtime, session, event, row)
            if provider_row is not None:
                visible.append(provider_row)
            continue
        payload = event["payload"]
        expected = hashlib.sha256(
            json.dumps(
                {
                    "request_id": row["request_id"],
                    "approval_event_id": event["event_id"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if row.get("request_fingerprint") != expected:
            continue
        try:
            unexpired = datetime.fromisoformat(payload["expires_at"]) > datetime.now(
                timezone.utc
            )
        except (TypeError, ValueError, KeyError):
            continue
        allowed = recipient_decision(
            policy,
            channel=payload["channel"],
            recipients=[payload["recipient"]],
            member_id=session.auth_member_id,
        )["allowed"]
        consumed = any(
            [
                await runtime.event_log.get_by_idempotency(
                    session.tenant_id,
                    f"openclaw:{session.auth_member_id}:{kind}:{event['event_id']}",
                )
                is not None
                for kind in ("cos.response.attempted", "cos.response.result")
            ]
        )
        can_allow = bool(
            unexpired
            and allowed
            and not consumed
            and policy["event_id"] == payload["policy_event_id"]
        )
        visible.append(
            {
                "kind": "cos_response",
                "approval_id": row["approval_id"],
                "revision": row["revision"],
                "expires_at_ms": row["expires_at_ms"],
                "channel": payload["channel"],
                "recipient": payload["recipient"],
                "subject": payload["subject"],
                "body": payload["body"],
                "can_allow": can_allow,
            }
        )
    return visible


async def resolve_cos_approval(
    runtime: Any,
    session: Session,
    session_key: str,
    *,
    approval_id: str,
    revision: str,
    decision: str,
) -> dict[str, bool]:
    # Re-read both native pending state and the immutable member approval.
    # Settings revocation and final provider dispatch also serialize on this lock.
    async with (
        runtime.settings_command_locks.setdefault(
            f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
        ),
        runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock()),
    ):
        rows = await pending_cos_approvals(runtime, session, session_key, _locked=True)
        match = next(
            (
                row
                for row in rows
                if row["approval_id"] == approval_id and row["revision"] == revision
            ),
            None,
        )
        if match is None or (decision == "allow-once" and not match["can_allow"]):
            raise HTTPException(409, "This approval changed or expired")
        if decision not in {"allow-once", "deny"}:
            raise HTTPException(422, "Unsupported approval decision")
        ticket = approval_ticket(
            runtime.settings.openclaw_shared_secret,
            operation="resolve",
            session_key=session_key,
            agent_id=member_agent_id(session.auth_member_id),
            member_id=session.auth_member_id,
            approval_id=approval_id,
            revision=revision,
            decision=decision,
        )
        result = await runtime.member_chat.cos_approval_request(
            "administrateme.cosApprovals.resolve", ticket
        )
        if result.get("resolved") is not True:
            raise HTTPException(503, "No sending decision is confirmed")
        return {"resolved": True, "sent": False}
