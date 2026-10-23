"""Standing self replies: trusted inbound receipt -> exact one-use native permit.

Neither a model tool nor a recipient list creates standing authority. The
operator's explicit activation and OpenClaw's independently checked binding
must both authorize this member. The existing native guard remains mandatory.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator

from adminme.cos_channel_operation import runtime_live_authorization
from adminme.cos_responses import RESPONSE_CONTRACT, content_fingerprint
from adminme.cos_self_replies import self_reply_binding, self_reply_session
from adminme.openclaw_topology import member_agent_id
from adminme.products.openclaw import main as seam
from adminme.products.openclaw import cos_responses as responses

router = APIRouter(tags=["cos-self-replies"])


class SelfReplyRequest(seam.ContextRequest):
    source_message_event_id: str = Field(min_length=1, max_length=240)
    host_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    body: str = Field(min_length=1, max_length=3500)
    reply_index: int = Field(default=0, ge=0, le=15)
    finance_run_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def bounded_text(self) -> SelfReplyRequest:
        # The plugin escapes Markdown for the native renderer. Authority binds
        # the resulting exact text, including punctuation, never the markup.
        if self.body != self.body.strip() or any(
            ord(char) < 32 or ord(char) == 127 for char in self.body
        ):
            raise ValueError("self replies require a bounded plain-text line")
        if len(self.body.encode("utf-16-le")) // 2 > 3500:
            raise ValueError("self reply exceeds the native chunk limit")
        return self


def _host_bindings(runtime: Any) -> list[str]:
    path = (
        runtime.settings.instance.config_dir.parent / "openclaw/runtime/openclaw.json"
    )
    try:
        if path.is_symlink():
            return []
        value = json.loads(path.read_text("utf-8"))["plugins"]["entries"][
            "administrateme"
        ]
        bindings = value["config"].get("standingSelfReplyBindings", [])
        return (
            bindings
            if value.get("enabled") is True and isinstance(bindings, list)
            else []
        )
    except (OSError, KeyError, TypeError, ValueError):
        return []


async def _context(
    body: SelfReplyRequest, request: Request, *, scheduled: bool = False
) -> tuple[Any, Any, dict[str, Any], str]:
    session = seam._session(request, body.provenance)
    if session.auth_role != "principal":
        raise HTTPException(403, "standing replies require the exact adult member")
    runtime = seam._runtime(request)
    p = body.provenance
    if (
        p.call_kind != "bridge"
        or p.channel_id != "imessage"
        or not p.account_id
        or not p.message_id
        or p.thread_id is not None
        or not p.actor_id.startswith("+")
        or p.agent_id != member_agent_id(session.auth_member_id)
        or p.session_key
        != self_reply_session(session.auth_member_id, p.account_id, p.actor_id)
    ):
        raise HTTPException(
            403, "standing replies require an exact private inbound conversation"
        )
    binding = self_reply_binding(session.auth_member_id, p.account_id, p.actor_id)
    if body.host_binding != binding or binding not in _host_bindings(runtime):
        raise HTTPException(
            403, "independent OpenClaw standing reply permission is absent"
        )
    identity = [p.channel_id, p.account_id, p.actor_id, p.message_id]
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode()
    ).hexdigest()
    source = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"openclaw-forward:{digest}"
    )
    if (
        source is None
        or source["event_id"] != body.source_message_event_id
        or source["type"] != "triage.intent.submitted"
        or source["owner_scope"] != f"private:{session.auth_member_id}"
        or source["visibility_scope"] != f"private:{session.auth_member_id}"
        or source["actor_identity"] != session.auth_member_id
        or source["payload"].get("member_id") != session.auth_member_id
        or source["payload"].get("submission_kind") != "member_forwarded"
    ):
        raise HTTPException(
            403, "standing replies require this member's durable inbound receipt"
        )
    try:
        submitted = datetime.fromisoformat(source["payload"]["submitted_at"])
        age = (datetime.now(timezone.utc) - submitted).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = -1
    if age < 0 or (not scheduled and age > 3600):
        raise HTTPException(403, "inbound reply authority expired")
    activation = await runtime_live_authorization(
        runtime,
        channel="imessage",
        provider_account_id=p.account_id,
        requested_at=None if scheduled else source["payload"]["submitted_at"],
    )
    if not activation.get("authorized") or binding not in activation.get(
        "self_reply_bindings", []
    ):
        raise HTTPException(
            403, "domain standing reply permission is absent or revoked"
        )
    from adminme.finance.disclosure import FinanceDisclosureError, check_disclosure

    try:
        await check_disclosure(
            runtime,
            session,
            p.session_key or "",
            run_id=body.finance_run_id,
            require_run=not scheduled,
        )
    except FinanceDisclosureError as exc:
        raise HTTPException(403, str(exc)) from None
    return runtime, session, activation, binding


@router.post("/api/openclaw/v1/cos-responses/self-reply")
async def self_reply(body: SelfReplyRequest, request: Request) -> dict[str, Any]:
    runtime, session, activation, binding = await _context(body, request)
    source_id = body.source_message_event_id
    request_id = hashlib.sha256(
        f"self-reply|{source_id}|{body.reply_index}".encode()
    ).hexdigest()
    p = body.provenance
    matches = [
        option
        for option in responses._imessage_options(runtime)
        if option["provider_account_id"] == p.account_id
    ]
    if len(matches) != 1:
        raise HTTPException(403, "exact self reply service channel is unavailable")
    values = {key: value for key, value in matches[0].items() if key != "label"}
    values.update(recipient=p.actor_id, subject="", body=body.body)
    async with runtime.settings_command_locks.setdefault(
        f"self-reply:{source_id}", asyncio.Lock()
    ):
        async with runtime.settings_command_locks.setdefault(
            f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
        ):
            policy = await responses._policy_check(
                runtime, session, values, require_current=False
            )
            key = (
                f"openclaw:{session.auth_member_id}:cos.response.approved:{request_id}"
            )
            approved = await runtime.event_log.get_by_idempotency(
                session.tenant_id, key
            )
            if approved is None:
                if body.reply_index:
                    prior_id = hashlib.sha256(
                        f"self-reply|{source_id}|{body.reply_index - 1}".encode()
                    ).hexdigest()
                    prior = await runtime.event_log.get_by_idempotency(
                        session.tenant_id,
                        f"openclaw:{session.auth_member_id}:cos.response.approved:{prior_id}",
                    )
                    terminal = (
                        None
                        if prior is None
                        else await runtime.event_log.get_by_idempotency(
                            session.tenant_id,
                            f"openclaw:{session.auth_member_id}:cos.response.result:{prior['event_id']}",
                        )
                    )
                    if terminal is None or terminal["payload"]["status"] != "sent":
                        raise HTTPException(
                            409, "previous reply part is not proven sent"
                        )
                await seam._guard(
                    runtime,
                    session,
                    "cos.response.approved",
                    {
                        "member_id": session.auth_member_id,
                        "self_reply_binding": binding,
                    },
                )
                now = datetime.now(timezone.utc)
                payload = {
                    **values,
                    "contract": RESPONSE_CONTRACT,
                    "member_id": session.auth_member_id,
                    "content_fingerprint": content_fingerprint(
                        channel="imessage",
                        account_id=str(p.account_id),
                        recipient=p.actor_id,
                        subject="",
                        body=body.body,
                    ),
                    "policy_event_id": policy["event_id"],
                    "policy_revision": policy["revision"],
                    "approved_at": now.isoformat(),
                    "expires_at": (now + timedelta(minutes=10)).isoformat(),
                    "authorization_method": "standing_self_reply",
                    "standing_authorization_event_id": activation["event_id"],
                    "source_message_event_id": source_id,
                    "self_reply_binding": binding,
                    "reply_index": body.reply_index,
                }
                approval_id = await responses._receipt(
                    runtime,
                    session,
                    event_type="cos.response.approved",
                    payload=payload,
                    request_id=request_id,
                    causation_id=activation["event_id"],
                    schema_version=2,
                )
                approved = await runtime.event_log.get(approval_id)
            assert approved is not None
            payload = approved["payload"]
            if (
                any(payload.get(key) != value for key, value in values.items())
                or payload.get("standing_authorization_event_id")
                != activation["event_id"]
            ):
                raise HTTPException(
                    409, "this inbound reply already has a different bound response"
                )
            host_id = await responses._receipt(
                runtime,
                session,
                event_type="cos.reply.openclaw_authorized",
                request_id=approved["event_id"],
                causation_id=approved["event_id"],
                payload={
                    "member_id": session.auth_member_id,
                    "approval_event_id": approved["event_id"],
                    "standing_authorization_event_id": activation["event_id"],
                    "source_message_event_id": source_id,
                    "self_reply_binding": binding,
                    "content_fingerprint": payload["content_fingerprint"],
                    "authorized_at": payload["approved_at"],
                    "authorization_method": "standing_self_reply",
                    "automatic_retry_allowed": False,
                },
            )

            async def standing_check() -> None:
                _, _, current, current_binding = await _context(body, request)
                if (
                    current["event_id"] != activation["event_id"]
                    or current_binding != binding
                ):
                    raise HTTPException(403, "standing reply permission changed")

            execution = responses.ResponseExecutionRequest(
                provenance=p,
                request_id=request_id,
                approval_event_id=approved["event_id"],
            )
            return await responses._execute_locked(
                runtime,
                session,
                approved,
                execution,
                request,
                payload,
                standing_check=standing_check,
                standing_host_event_id=host_id,
            )
