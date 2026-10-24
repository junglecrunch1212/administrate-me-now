"""Durable human input bindings for the existing OpenClaw conversation runtime.

Receipts retain the instruction and its real channel. A short signed ticket
binds it to one Gateway input without granting the model identity selection.
This module does not interpret requests or grant provider execution authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

from fastapi import HTTPException

from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.openclaw_topology import member_agent_id
from adminme.service_identities import (
    COS_SERVICE_PRINCIPAL_ID,
    resolve_service_channel_route,
)

CONTRACT = "cos-conversation-input-v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def gmail_binding(
    runtime: Any, member_id: str, account: str, thread_id: str
) -> tuple[str, str]:
    secret = runtime.settings.openclaw_shared_secret
    if not secret:
        raise HTTPException(503, "conversation authentication is unavailable")
    peer = hmac.new(
        secret.encode(),
        json.dumps(
            [
                "gmail-peer",
                runtime.settings.instance.tenant_id,
                member_id,
                account,
                thread_id,
            ],
            separators=(",", ":"),
        ).encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    signature = hmac.new(
        secret.encode(), f"gmail-session|{peer}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return (
        f"gmail:{peer}",
        f"agent:{member_agent_id(member_id)}:administrateme-gmail-{peer}-{signature}",
    )


def gmail_member(runtime: Any, provenance: Any) -> str | None:
    """Verify a service-created per-member, per-account, per-thread session."""
    if (
        provenance.channel_id != "gmail"
        or provenance.call_kind != "tool"
        or not provenance.account_id
        or not provenance.thread_id
    ):
        return None
    account_reference = (
        "provider-account:" + hashlib.sha256(provenance.account_id.encode()).hexdigest()
    )
    channels = [
        channel
        for channel in runtime.settings.service_channel_accounts
        if channel.provider == "gmail"
        and channel.service_principal_id == COS_SERVICE_PRINCIPAL_ID
        and channel.provider_account_reference == account_reference
        and channel.enrolled
        and channel.state in {"healthy", "observation", "degraded"}
    ]
    if len(channels) != 1:
        return None
    for member in sorted(
        {
            route.target_id
            for route in channels[0].routes
            if route.scope == "member_private" and route.target_id
        }
    ):
        actor, session = gmail_binding(
            runtime, member, provenance.account_id, provenance.thread_id
        )
        if (
            hmac.compare_digest(actor, provenance.actor_id)
            and hmac.compare_digest(session, provenance.session_key or "")
            and provenance.agent_id == member_agent_id(member)
        ):
            return member
    return None


def authored_email_text(body: str) -> str:
    """Only the direct author's new text is eligible instruction material.

    Quoted/forwarded bodies remain retained evidence in the source event. They
    are not copied into the privileged current-input path. Unsupported layouts
    with no clear author text require a new instruction from the member.
    """
    lines: list[str] = []
    for line in body.splitlines():
        if re.match(
            r"\s*(?:>|On .+wrote:|[-_]{2,}\s*(?:Original Message|Forwarded message)|Begin forwarded message:|From:\s)",
            line,
            re.I,
        ):
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text or len(text) > 32000:
        raise HTTPException(422, "a bounded direct human instruction is required")
    return text


def gmail_source_member(runtime: Any, source: dict[str, Any]) -> str | None:
    payload = source.get("payload") or {}
    account = payload.get("provider_account_id")
    if (
        source.get("type") != "messaging.received"
        or source.get("schema_version") != 8
        or payload.get("provider") != "gmail"
        or payload.get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID
        or payload.get("authenticated_member_route") is not True
        or payload.get("to_identifiers") != [account]
        or bool(payload.get("cc_identifiers"))
        or bool(payload.get("bcc_identifiers"))
        or payload.get("reply_to_identifier")
        not in {None, "", payload.get("from_identifier")}
        or payload.get("route_assurance")
        not in {"exact_dkim_identity", "receiver_aligned_sender"}
        or not isinstance(account, str)
        or not account
        or source.get("source_account_id") != account
        or source.get("tenant_id") != runtime.settings.instance.tenant_id
        or source.get("owner_scope") != f"service-private:{COS_SERVICE_PRINCIPAL_ID}"
        or source.get("visibility_scope") != source.get("owner_scope")
    ):
        return None
    reference = "provider-account:" + hashlib.sha256(account.encode()).hexdigest()
    channels = [
        channel
        for channel in runtime.settings.service_channel_accounts
        if channel.channel_id == payload.get("channel_id")
        and channel.provider == "gmail"
        and channel.service_principal_id == COS_SERVICE_PRINCIPAL_ID
        and channel.enrolled
        and channel.provider_account_reference == reference
        and channel.state in {"healthy", "observation", "degraded"}
    ]
    if len(channels) != 1 or not isinstance(payload.get("trusted_route_key"), str):
        return None
    route = resolve_service_channel_route(channels[0], payload["trusted_route_key"])
    if route.get("route_scope") != "member_private":
        return None
    member = route.get("routing_target_id")
    return (
        member
        if isinstance(member, str)
        and runtime.settings.member_roles.get(member) == "principal"
        else None
    )


async def capture_instruction(
    runtime: Any,
    *,
    member_id: str,
    provenance: dict[str, Any],
    text: str,
    request_id: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Called only by authenticated portal intake or verified provider intake."""
    channel = provenance["channel_id"]
    if channel == "gmail":
        if source is None or gmail_source_member(runtime, source) != member_id:
            raise HTTPException(403, "trusted direct member email is unavailable")
        text = authored_email_text(str(source["payload"].get("body_text") or ""))
    elif channel != "administrateme-portal" or source is not None:
        raise HTTPException(403, "unsupported authenticated conversation")
    identity = {
        name: provenance.get(name)
        for name in (
            "channel_id",
            "account_id",
            "actor_id",
            "agent_id",
            "session_key",
            "message_id",
            "thread_id",
        )
    }
    key = "cos-conversation-input:" + _digest(
        [runtime.settings.instance.tenant_id, member_id, identity]
    )
    fingerprint = _digest(
        [identity, text, request_id, source["event_id"] if source else None]
    )
    previous = await runtime.event_log.get_by_idempotency(
        runtime.settings.instance.tenant_id, key
    )
    if previous is not None:
        if previous.get("raw_ref") != f"request-sha256:{fingerprint}":
            raise HTTPException(
                409, "conversation request identity conflicts with earlier content"
            )
        return previous
    now = EventEnvelope.now_utc_iso()
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=runtime.settings.instance.tenant_id,
        type="cos.conversation.instruction",
        schema_version=1,
        occurred_at=now,
        source_adapter="cos-conversation",
        source_account_id=provenance["account_id"],
        owner_scope=f"private:{member_id}",
        visibility_scope=f"private:{member_id}",
        actor_identity=member_id,
        sensitivity=source["sensitivity"] if source else "sensitive",
        causation_id=source["event_id"] if source else None,
        correlation_id=provenance["correlation_id"],
        idempotency_key=key,
        raw_ref=f"request-sha256:{fingerprint}",
        payload={
            **identity,
            "member_id": member_id,
            "text": text,
            "submitted_at": now,
            "source_message_event_id": source["event_id"] if source else None,
            "request_id": request_id,
            "authentication_rule": "enrolled_receiver_authenticated_sender"
            if source
            else "authenticated_member_session",
        },
    )
    try:
        event_id = await runtime.events.write(envelope)
    except IdempotencyConflict:
        previous = await runtime.event_log.get_by_idempotency(
            runtime.settings.instance.tenant_id, key
        )
        if (
            previous is None
            or previous.get("raw_ref") != f"request-sha256:{fingerprint}"
        ):
            raise HTTPException(
                409, "conversation request identity conflicts with earlier content"
            ) from None
        return previous
    return await runtime.event_log.get(event_id)


def input_ticket(runtime: Any, source: dict[str, Any]) -> dict[str, Any]:
    """Transport-only, short-lived HMAC; never included in prompt or logs."""
    payload = source["payload"]
    fields = [
        "source_event_id",
        "session_key",
        "agent_id",
        "actor_id",
        "channel_id",
        "account_id",
        "message_id",
        "thread_id",
        "text_sha256",
        "nonce",
    ]
    values = {name: str(payload.get(name) or "") for name in fields}
    values.update(
        source_event_id=source["event_id"],
        text_sha256=hashlib.sha256(payload["text"].encode()).hexdigest(),
        nonce=secrets.token_hex(16),
    )
    expires = int(time.time() * 1000) + 10000
    if any("|" in value for value in values.values()):
        raise HTTPException(422, "invalid conversation coordinate")
    material = "|".join([CONTRACT, *[values[name] for name in fields], str(expires)])
    signature = hmac.new(
        runtime.settings.openclaw_shared_secret.encode(),
        material.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {**values, "expires_at_ms": expires, "signature": signature}


async def current_conversation_instruction(
    runtime: Any, member: Any, provenance: Any, source_id: str
) -> dict[str, Any]:
    source = await runtime.event_log.get(source_id)
    if (
        source is None
        or source.get("type") != "cos.conversation.instruction"
        or source.get("schema_version") != 1
        or source.get("source_adapter") != "cos-conversation"
        or source.get("tenant_id") != member.tenant_id
        or source.get("owner_scope") != f"private:{member.auth_member_id}"
        or source.get("visibility_scope") != source.get("owner_scope")
        or source.get("actor_identity") != member.auth_member_id
    ):
        raise HTTPException(404, "authenticated conversation instruction not found")
    payload = source["payload"]
    if (
        member.auth_role != "principal"
        or provenance.call_kind != "tool"
        or payload["member_id"] != member.auth_member_id
        or any(
            payload.get(name) != getattr(provenance, name, None)
            for name in (
                "channel_id",
                "account_id",
                "actor_id",
                "agent_id",
                "session_key",
                "message_id",
                "thread_id",
            )
        )
    ):
        raise HTTPException(403, "instruction does not match the current conversation")
    age = (
        datetime.now(timezone.utc) - datetime.fromisoformat(payload["submitted_at"])
    ).total_seconds()
    if not 0 <= age <= 3600:
        raise HTTPException(403, "current member instruction expired")
    if payload["channel_id"] == "gmail":
        original = await runtime.event_log.get(payload["source_message_event_id"])
        if (
            original is None
            or gmail_source_member(runtime, original) != member.auth_member_id
            or authored_email_text(str(original["payload"].get("body_text") or ""))
            != payload["text"]
        ):
            raise HTTPException(403, "email sender authority is no longer current")
    return source
