"""Authenticated member decisions for exact, native CoS tool approvals."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from adminme.cos_response_policy import policy_from_event_log, recipient_decision
from adminme.lib.session import Session
from adminme.openclaw_topology import member_agent_id

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


async def pending_cos_approvals(
    runtime: Any, session: Session, session_key: str
) -> list[dict[str, Any]]:
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
        event = await runtime.event_log.get(row["domain_approval_id"])
        if (
            event is None
            or event["type"] != "cos.response.approved"
            or event["tenant_id"] != session.tenant_id
            or event["actor_identity"] != session.auth_member_id
            or event["owner_scope"] != f"private:{session.auth_member_id}"
            or event["visibility_scope"] != f"private:{session.auth_member_id}"
            or event["payload"].get("member_id") != session.auth_member_id
        ):
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
    import asyncio

    async with runtime.settings_command_locks.setdefault(
        f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
    ):
        rows = await pending_cos_approvals(runtime, session, session_key)
        match = next(
            (
                row
                for row in rows
                if row["approval_id"] == approval_id and row["revision"] == revision
            ),
            None,
        )
        if match is None or (decision == "allow-once" and not match["can_allow"]):
            raise HTTPException(409, "This reply approval changed or expired")
        if decision not in {"allow-once", "deny"}:
            raise HTTPException(422, "Unsupported reply decision")
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
