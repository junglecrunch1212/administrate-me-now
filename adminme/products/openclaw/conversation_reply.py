"""Current native final reply -> existing governed CoS Gmail transport."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import re
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from adminme.cos_response_policy import normalize_recipient
from adminme.cos_responses import RESPONSE_CONTRACT, content_fingerprint
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.request_authorization import canonical

router = APIRouter(
    prefix="/api/openclaw/v1/conversations", tags=["conversation-replies"]
)
CONTRACT = "authenticated-conversation-reply-v1"


class ReplyHostDecision(BaseModel):
    model_config = {"extra": "forbid"}
    contract: Literal["authenticated-conversation-reply-v1"]
    source_event_id: str
    channel: Literal["gmail"]
    account_id: str
    actor_id: str
    session_key: str
    agent_id: str
    message_id: str
    thread_id: str
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_id: Literal["same_conversation_reply_v1"]
    issued_at_ms: int
    expires_at_ms: int
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConversationReplyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provenance: seam.Provenance
    source_message_event_id: str
    body: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(min_length=8, max_length=120)
    host_decision: ReplyHostDecision


def validate_host(
    runtime: Any, body: ConversationReplyRequest, source: dict[str, Any]
) -> None:
    host = body.host_decision
    values = host.model_dump(exclude={"signature"})
    secret = runtime.settings.openclaw_shared_secret
    now = int(time.time() * 1000)
    expected = hmac.new(
        secret.encode(), (CONTRACT + "|" + canonical(values)).encode(), hashlib.sha256
    ).hexdigest()
    if (
        not hmac.compare_digest(host.signature, expected)
        or host.source_event_id != source["event_id"]
        or host.channel != source["payload"].get("channel_id")
        or host.body_sha256 != hashlib.sha256(body.body.encode()).hexdigest()
        or host.issued_at_ms > now + 30_000
        or host.expires_at_ms <= now
        or not 0 < host.expires_at_ms - host.issued_at_ms <= 120_000
        or any(
            getattr(host, key) != source["payload"].get(key)
            for key in (
                "account_id",
                "actor_id",
                "session_key",
                "agent_id",
                "message_id",
                "thread_id",
            )
        )
    ):
        raise HTTPException(
            403, "native conversation reply policy is stale or mismatched"
        )


@router.post("/reply")
async def conversation_reply(
    body: ConversationReplyRequest, request: Request
) -> dict[str, Any]:
    from adminme.conversation_instructions import gmail_source_member
    from adminme.products.openclaw.service_work import current_private_instruction
    from adminme.products.openclaw import cos_responses as responses

    session, source = await current_private_instruction(
        request,
        body.provenance,
        body.source_message_event_id,
        required_permission="prepare_external_actions",
    )
    runtime = seam._runtime(request)
    validate_host(runtime, body, source)
    original = await runtime.event_log.get(
        source["payload"].get("source_message_event_id", "")
    )
    if (
        original is None
        or gmail_source_member(runtime, original) != session.auth_member_id
    ):
        raise HTTPException(403, "the original member email route is unavailable")
    original_payload = original["payload"]
    try:
        recipient = normalize_recipient("email", original_payload["from_identifier"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            403, "one exact authenticated email sender is required"
        ) from exc
    if (
        original_payload.get("cc_identifiers")
        or original_payload.get("bcc_identifiers")
        or original_payload.get("reply_to_identifier") not in {None, "", recipient}
        or not original_payload.get("thread_id")
        or not re.fullmatch(
            r"<[^<>\s\r\n]+>", str(original_payload.get("rfc_message_id") or "")
        )
    ):
        raise HTTPException(
            403, "conversation reply requires one private unambiguous email origin"
        )
    subject = str(original_payload.get("subject") or "")
    subject = subject if subject.lower().startswith("re:") else "Re: " + subject
    values = responses.ResponseApprovalRequest(
        request_id=body.request_id,
        channel="email",
        service_channel_id=original_payload["channel_id"],
        provider_account_id=original_payload["provider_account_id"],
        recipient=recipient,
        subject=subject,
        body=body.body,
    ).model_dump(exclude={"request_id"})
    origin = {
        "authorization_method": "authenticated_conversation",
        "conversation_source_event_id": source["event_id"],
        "thread_id": original_payload["thread_id"],
        "in_reply_to": original_payload["rfc_message_id"],
    }
    request_key = f"conversation-reply:{source['event_id']}"
    key = f"openclaw:{session.auth_member_id}:cos.response.approved:{request_key}"
    async with runtime.settings_command_locks.setdefault(request_key, asyncio.Lock()):
        approved = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
        if approved is not None:
            if any(
                approved["payload"].get(k) != v for k, v in {**values, **origin}.items()
            ):
                raise HTTPException(
                    409, "this conversation already has a different final reply"
                )
        else:
            if await runtime.observation.is_active():
                raise HTTPException(
                    409, "Observation mode suppresses conversation replies"
                )
            policy = await responses._policy_check(
                runtime, session, values, require_current=False
            )
            if not responses._response_authority(runtime, session)["channels"]["email"][
                "ready"
            ]:
                raise HTTPException(
                    403, "current member reply authority is unavailable"
                )
            await seam._guard(runtime, session, "cos.response.approved", values)
            now = datetime.now(timezone.utc)
            payload = {
                **values,
                **origin,
                "contract": RESPONSE_CONTRACT,
                "member_id": session.auth_member_id,
                "content_fingerprint": content_fingerprint(
                    account_id=values["provider_account_id"],
                    recipient=recipient,
                    subject=subject,
                    body=body.body,
                    channel="email",
                ),
                "policy_event_id": policy["event_id"],
                "policy_revision": policy["revision"],
                "approved_at": now.isoformat(),
                "expires_at": datetime.fromtimestamp(
                    body.host_decision.expires_at_ms / 1000, timezone.utc
                ).isoformat(),
            }
            approved_id = await responses._receipt(
                runtime,
                session,
                event_type="cos.response.approved",
                payload=payload,
                request_id=request_key,
                causation_id=source["event_id"],
                schema_version=4,
            )
            approved = await runtime.event_log.get(approved_id)
        assert approved is not None
        approved_id, payload = approved["event_id"], approved["payload"]
        prior_host = await runtime.event_log.get_by_idempotency(
            session.tenant_id,
            f"openclaw:{session.auth_member_id}:cos.conversation.reply_authorized:{approved_id}",
        )
        if prior_host is not None:
            host_id = prior_host["event_id"]
        else:
            host_id = await responses._receipt(
                runtime,
                session,
                event_type="cos.conversation.reply_authorized",
                payload={
                    "member_id": session.auth_member_id,
                    "approval_event_id": approved_id,
                    "source_event_id": source["event_id"],
                    "content_fingerprint": payload["content_fingerprint"],
                    "authorized_at": payload["approved_at"],
                    "host_decision": body.host_decision.model_dump(),
                    "automatic_retry_allowed": False,
                },
                request_id=approved_id,
                causation_id=approved_id,
            )

        async def current_authority() -> None:
            _, current = await current_private_instruction(
                request,
                body.provenance,
                body.source_message_event_id,
                required_permission="prepare_external_actions",
            )
            validate_host(runtime, body, current)
            live_original = await runtime.event_log.get(original["event_id"])
            if (
                live_original is None
                or gmail_source_member(runtime, live_original) != session.auth_member_id
            ):
                raise HTTPException(403, "original email authority changed")

        async with runtime.settings_command_locks.setdefault(
            f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
        ):
            result = await responses._execute_locked(
                runtime,
                session,
                approved,
                responses.ResponseExecutionRequest(
                    provenance=body.provenance,
                    request_id=body.request_id,
                    approval_event_id=approved_id,
                ),
                request,
                payload,
                standing_check=current_authority,
                standing_host_event_id=host_id,
            )
        return {
            **result,
            "source_message_event_id": source["event_id"],
            "reply_thread_id": payload["thread_id"],
        }
