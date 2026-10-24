"""Member-approved, privately scoped CoS response execution.

Approval grants exactly one text response to the approving member's own
allowlisted endpoint. It grants no attachment, group, third-party or household
disclosure authority. An attempted command is never automatically retried.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import time
from typing import Annotated, Any, Literal
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from adminme.cos_response_policy import (
    normalize_recipient,
    policy_from_event_log,
    recipient_decision,
)
from adminme.cos_channel_operation import require_cos_response_activation
from adminme.cos_responses import (
    CoSResponsePermit,
    RESPONSE_CONTRACT,
    content_fingerprint,
    reset_response_permit,
    set_response_permit,
    validate_imessage_response_text,
)
from adminme.lib.adapters import OutboundMessage
from adminme.lib.observation import (
    OutboundAuditContext,
    outbound,
    reset_outbound_audit_context,
    set_outbound_audit_context,
)
from adminme.lib.session import Session, build_internal_session
from adminme.products.auth import require_principal, session_from_request
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.conversation_reply import (
    router as conversation_reply_router,
)
from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    list_service_delegations,
    resolve_service_delegation,
)
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_SERVICE_PRINCIPAL_ID,
)

router = APIRouter(tags=["cos-responses"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class ResponseApprovalRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    channel: Literal["email", "imessage"]
    service_channel_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=320)
    recipient: str = Field(min_length=1, max_length=320)
    subject: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def validate_content(self) -> "ResponseApprovalRequest":
        self.recipient = normalize_recipient(self.channel, self.recipient)
        if (
            not self.body.strip()
            or (self.channel == "imessage" and self.subject)
            or (self.channel == "imessage" and not self.recipient.startswith("+"))
            or any(ord(char) < 32 for char in self.subject)
            or any(ord(char) < 32 and char not in "\n\t" for char in self.body)
        ):
            raise ValueError("CoS responses require bounded plain text")
        return self


class ResponseExecutionRequest(seam.ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    approval_event_id: str = Field(min_length=1, max_length=240)


def _service_channel(runtime: Any, payload: dict[str, Any]) -> Any:
    reference = (
        "provider-account:"
        + hashlib.sha256(payload["provider_account_id"].encode()).hexdigest()
    )
    matches = [
        channel
        for channel in runtime.settings.service_channel_accounts
        if channel.channel_id == payload["service_channel_id"]
        and (
            (payload["channel"] == "email" and channel.provider == "gmail")
            or (
                payload["channel"] == "imessage"
                and channel.provider == "apple_imessage"
                and channel.openclaw_channel_id == "imessage"
            )
        )
        and channel.service_principal_id == COS_SERVICE_PRINCIPAL_ID
        and channel.provider_account_reference == reference
        and channel.enrolled
        and channel.state in {"healthy", "observation"}
    ]
    if len(matches) != 1:
        raise HTTPException(403, "exact enrolled CoS response account is unavailable")
    return matches[0]


async def _policy_check(
    runtime: Any,
    session: Session,
    payload: dict[str, Any],
    *,
    require_current: bool,
) -> dict[str, Any]:
    _service_channel(runtime, payload)
    policy = await policy_from_event_log(runtime.event_log, session.tenant_id)
    decision = recipient_decision(
        policy,
        channel=payload["channel"],
        recipients=[payload["recipient"]],
        member_id=session.auth_member_id,
    )
    if not decision["allowed"]:
        raise HTTPException(403, "recipient is outside this member's CoS allowlist")
    if require_current and (
        policy["event_id"] != payload["policy_event_id"]
        or policy["revision"] != payload["policy_revision"]
    ):
        raise HTTPException(
            409, "response allowlist changed; confirm the response again"
        )
    return policy


async def _receipt(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    causation_id: str | None = None,
    schema_version: int = 1,
) -> str:
    event_id, _ = await seam._append(
        runtime,
        session,
        event_type=event_type,
        payload=payload,
        source_account_id="cos-response",
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity="sensitive",
        request_id=request_id,
        causation_id=causation_id,
        schema_version=schema_version,
    )
    return event_id


def _response_authority(runtime: Any, session: Session) -> dict[str, Any]:
    """Explain the caller's exact preparation prerequisite without granting it."""

    def status(
        state: str,
        reason: str,
        *,
        can_prepare: bool = False,
        can_read_context: bool = False,
    ) -> dict[str, Any]:
        ready_reason = (
            "Your private CoS permission is ready. Each reply still needs "
            "exact-text and separate sending approvals."
        )
        return {
            "state": state,
            "can_prepare": can_prepare,
            "can_read_context": can_read_context,
            "reason": reason,
            "channels": {
                "email": {
                    "ready": can_prepare,
                    "reason": ready_reason if can_prepare else reason,
                },
                # The native sender's result route independently requires
                # read_context. Never admit a reply whose result is inaccessible.
                "imessage": {
                    "ready": can_prepare and can_read_context,
                    "reason": (
                        ready_reason if can_prepare and can_read_context else reason
                    ),
                },
            },
        }

    chief_of_staff = [
        principal
        for principal in runtime.settings.service_principals
        if principal.service_principal_id == COS_SERVICE_PRINCIPAL_ID
        and principal.kind == "chief_of_staff"
    ]
    if runtime.projections is None or len(chief_of_staff) != 1:
        return status(
            "unavailable",
            "CoS permission status is unavailable. Try again when the service is healthy.",
        )
    connection = runtime.projections.connection("parties")
    try:
        context = resolve_service_delegation(
            connection,
            tenant_id=session.tenant_id,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
            context_scope="member_private",
            context_member_id=session.auth_member_id,
        )
    except ServiceDelegationResolutionError:
        active = [
            grant
            for grant in list_service_delegations(
                connection,
                session,
                service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            )
            if grant["active"]
            and grant["context_scope"] == "member_private"
            and grant["context_member_id"] == session.auth_member_id
            and grant["owner_party_id"] is None
        ]
        if len(active) > 1:
            return status(
                "ambiguous",
                "More than one private CoS permission is active. Review your CoS permissions before approving a reply.",
            )
        return status(
            "missing",
            "Approve your own private CoS permission to prepare replies before approving a reply. Expired or revoked permission cannot be used.",
        )
    can_prepare = "prepare_external_actions" in context.permissions
    can_read_context = "read_context" in context.permissions
    if not can_prepare:
        return status(
            "permission_missing",
            "Your private CoS permission does not allow reply preparation. Review your CoS permissions before approving a reply.",
            can_read_context=can_read_context,
        )
    if not can_read_context:
        return status(
            "permission_missing",
            "Email reply permission is ready. iMessage also needs your private context permission to confirm the sending result. Review your CoS permissions before approving an iMessage reply.",
            can_prepare=True,
        )
    return status(
        "ready",
        "Your private CoS permission allows reply preparation and sending-result checks. Each reply still needs exact-text and separate sending approvals.",
        can_prepare=True,
        can_read_context=True,
    )


@router.get("/api/member/v1/cos/responses/options")
async def response_options(request: Request, session: SessionDep) -> dict[str, Any]:
    require_principal(session)
    if session.view_member_id != session.auth_member_id:
        raise HTTPException(403, "CoS response options require your own private view")
    runtime = seam._runtime(request)
    policy = await policy_from_event_log(runtime.event_log, session.tenant_id)
    accounts = []
    for row in runtime.settings.adapter_config.get("adapters", []):
        config = row.get("config", {})
        if (
            row.get("kind") != "messaging-gmail-api"
            or row.get("enabled") is not True
            or config.get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID
            or config.get("member_id")
            or not config.get("provider_account_id")
            or config.get("user_email") != config.get("provider_account_id")
        ):
            continue
        account = {
            "channel": "email",
            "service_channel_id": config.get("channel_id"),
            "provider_account_id": config["provider_account_id"],
        }
        try:
            channel = _service_channel(runtime, account)
        except HTTPException:
            continue
        accounts.append({**account, "label": channel.display_name})
    return {
        "authority": _response_authority(runtime, session),
        "accounts": accounts + _imessage_options(runtime),
        "recipients": [
            {"channel": entry["channel"], "address": entry["address"]}
            for entry in policy["entries"]
            if entry["member_id"] == session.auth_member_id
        ],
        "provider_effect_count": 0,
    }


def _imessage_options(runtime: Any) -> list[dict[str, str]]:
    result = []
    account_ids = {
        key.split("|")[1]
        for key in runtime.settings.openclaw_actor_identities
        if key.startswith("imessage|") and len(key.split("|")) == 3
    }
    for account_id in sorted(account_ids):
        for channel in runtime.settings.service_channel_accounts:
            account = {
                "channel": "imessage",
                "service_channel_id": channel.channel_id,
                "provider_account_id": account_id,
            }
            try:
                matched = _service_channel(runtime, account)
            except HTTPException:
                continue
            result.append({**account, "label": matched.display_name})
    return result


@router.post("/api/member/v1/cos/responses/approvals", status_code=201)
async def approve_response(
    body: ResponseApprovalRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_principal(session)
    if session.view_member_id != session.auth_member_id:
        raise HTTPException(403, "confirm a CoS response in your own private view")
    runtime = seam._runtime(request)
    if body.channel == "imessage":
        try:
            validate_imessage_response_text(body.body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    values = body.model_dump(exclude={"request_id"})
    policy = await _policy_check(runtime, session, values, require_current=False)
    fingerprint = content_fingerprint(
        account_id=body.provider_account_id,
        recipient=body.recipient,
        subject=body.subject,
        body=body.body,
        channel=body.channel,
    )
    now = datetime.now(timezone.utc)
    # Retrying a successful exact approval returns its original expiry; it
    # cannot refresh authority or mutate the previously confirmed content.
    key = f"openclaw:{session.auth_member_id}:cos.response.approved:{body.request_id}"
    async with runtime.settings_command_locks.setdefault(key, asyncio.Lock()):
        previous = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
        if previous is not None:
            prior = previous["payload"]
            if any(prior.get(field) != value for field, value in values.items()):
                raise HTTPException(
                    409, "approval request conflicts with prior content"
                )
            return {
                "approval_event_id": previous["event_id"],
                "expires_at": prior["expires_at"],
                "provider_effect_count": 0,
                "state": "awaiting_openclaw_approval",
            }
        authority = _response_authority(runtime, session)
        channel_authority = authority["channels"][body.channel]
        if not channel_authority["ready"]:
            raise HTTPException(
                503 if authority["state"] == "unavailable" else 403,
                {
                    "code": "cos_response_authority_required",
                    "reason": channel_authority["reason"],
                    "authority": authority,
                },
            )
        await seam._guard(runtime, session, "cos.response.approved", values)
        payload = {
            **values,
            "contract": RESPONSE_CONTRACT,
            "member_id": session.auth_member_id,
            "content_fingerprint": fingerprint,
            "policy_event_id": policy["event_id"],
            "policy_revision": policy["revision"],
            "approved_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
        event_id = await _receipt(
            runtime,
            session,
            event_type="cos.response.approved",
            payload=payload,
            request_id=body.request_id,
        )
    return {
        "approval_event_id": event_id,
        "expires_at": payload["expires_at"],
        "provider_effect_count": 0,
        "state": "awaiting_openclaw_approval",
    }


@router.post("/api/openclaw/v1/tools/cos-response")
async def execute_response(
    body: ResponseExecutionRequest, request: Request
) -> dict[str, Any]:
    session = seam._session(
        request, body.provenance, required_permission="prepare_external_actions"
    )
    seam._require_adult(session)
    runtime = seam._runtime(request)
    approved = await runtime.event_log.get(body.approval_event_id)
    if (
        approved is None
        or approved["tenant_id"] != session.tenant_id
        or approved["type"] != "cos.response.approved"
        or approved["owner_scope"] != f"private:{session.auth_member_id}"
        or approved["visibility_scope"] != f"private:{session.auth_member_id}"
        or approved["actor_identity"] != session.auth_member_id
        or approved["payload"].get("member_id") != session.auth_member_id
    ):
        raise HTTPException(404, "exact member response approval is unavailable")
    payload = approved["payload"]
    async with runtime.settings_command_locks.setdefault(
        f"cos-response:{body.approval_event_id}", asyncio.Lock()
    ):
        # Settings revocation uses the same tenant lock. A completed removal
        # means no later dispatch can still use the old allowlist.
        async with runtime.settings_command_locks.setdefault(
            f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
        ):
            return await _execute_locked(
                runtime, session, approved, body, request, payload
            )


async def _execute_locked(
    runtime: Any,
    session: Session,
    approved: dict[str, Any],
    body: ResponseExecutionRequest,
    request: Request,
    payload: dict[str, Any],
    *,
    standing_check: Callable[[], Awaitable[None]] | None = None,
    standing_host_event_id: str | None = None,
) -> dict[str, Any]:
    approval_id = approved["event_id"]
    terminal_key = (
        f"openclaw:{session.auth_member_id}:cos.response.result:{approval_id}"
    )
    terminal = await runtime.event_log.get_by_idempotency(
        session.tenant_id, terminal_key
    )
    if terminal is not None:
        return {"event_id": terminal["event_id"], **terminal["payload"]}
    attempt_key = (
        f"openclaw:{session.auth_member_id}:cos.response.attempted:{approval_id}"
    )
    attempted = await runtime.event_log.get_by_idempotency(
        session.tenant_id, attempt_key
    )
    if attempted is not None:
        # A crash/timeout after durable contact intent cannot prove non-delivery.
        return await _terminal(
            runtime,
            session,
            payload,
            approval_id,
            "unknown",
            "prior_attempt_unresolved",
        )

    async def final_check(provider_effect: bool = False) -> None:
        if standing_check is not None:
            await standing_check()
        if payload["channel"] == "imessage" and standing_check is None:
            try:
                validate_imessage_response_text(payload["body"])
            except ValueError as exc:
                raise HTTPException(403, str(exc)) from exc
        await _policy_check(runtime, session, payload, require_current=True)
        if datetime.fromisoformat(payload["expires_at"]) <= datetime.now(timezone.utc):
            raise HTTPException(409, "exact response approval expired")
        if (
            content_fingerprint(
                account_id=payload["provider_account_id"],
                recipient=payload["recipient"],
                subject=payload["subject"],
                body=payload["body"],
                channel=payload["channel"],
            )
            != payload["content_fingerprint"]
        ):
            raise HTTPException(403, "confirmed response content changed")
        # Activation authority is independent of observation and the recipient
        # ceiling. Until the operational gate is wired, live sends fail closed.
        if runtime.observation is None:
            raise HTTPException(503, "observation authority is unavailable")
        observation_active = await runtime.observation.is_active()
        if provider_effect and observation_active:
            raise HTTPException(403, "observation now suppresses this response")
        if not observation_active:
            channel = _service_channel(runtime, payload)
            if not channel.sending_enabled or channel.observation_active:
                raise HTTPException(403, "CoS response channel is not write-enabled")
            activation = await require_cos_response_activation(
                runtime,
                channel=payload["channel"],
                provider_account_id=payload["provider_account_id"],
                requested_at=payload["approved_at"],
            )
            if not activation["authorized"]:
                raise HTTPException(
                    403, "CoS response activation authority unavailable"
                )

    try:
        await final_check()
        await seam._guard(
            runtime,
            session,
            "cos.response.send",
            {
                "content_fingerprint": payload["content_fingerprint"],
                "member_id": session.auth_member_id,
            },
        )
        openclaw_id = standing_host_event_id or await seam._consume_openclaw_approval(
            request,
            session,
            body.provenance,
            body,
            tool_name="administrateme_cos_response",
            action="cos.response.send",
            causation_id=approval_id,
        )
    except HTTPException as exc:
        await _terminal(
            runtime,
            session,
            payload,
            approval_id,
            "denied",
            f"authorization_denied_{exc.status_code}",
        )
        raise
    attempt_id = await _receipt(
        runtime,
        session,
        event_type="cos.response.attempted",
        payload={
            "contract": RESPONSE_CONTRACT,
            "member_id": session.auth_member_id,
            "approval_event_id": approval_id,
            "openclaw_approval_event_id": openclaw_id,
            "content_fingerprint": payload["content_fingerprint"],
            "policy_event_id": payload["policy_event_id"],
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "automatic_retry_allowed": False,
        },
        request_id=approval_id,
        causation_id=openclaw_id,
    )
    if payload["channel"] == "imessage":
        if await runtime.observation.is_active():

            async def never_send() -> None:
                raise PermissionError("observation must suppress native dispatch")

            audit_token = set_outbound_audit_context(
                OutboundAuditContext(
                    initiating_command_id=approval_id,
                    initiating_event_id=attempt_id,
                    domain_approval_event_id=approval_id,
                    openclaw_approval_event_id=openclaw_id,
                    provider="imessage",
                    provider_account_id=payload["provider_account_id"],
                    operation="cos.response.send",
                    content_fingerprint=payload["content_fingerprint"],
                    owner_scope=f"private:{session.auth_member_id}",
                    visibility_scope=f"private:{session.auth_member_id}",
                    sensitivity="sensitive",
                )
            )
            try:
                await outbound(
                    session,
                    "cos.response.send",
                    {"content_fingerprint": payload["content_fingerprint"]},
                    never_send,
                    manager=runtime.observation,
                    event_sink=runtime.events,
                    target_channel="imessage",
                    target_identifier=payload["recipient"],
                )
            finally:
                reset_outbound_audit_context(audit_token)
            return await _terminal(
                runtime,
                session,
                payload,
                approval_id,
                "suppressed",
                "observation_active",
            )
        admissions = _admissions(runtime)
        if any(
            admission["payload"]["content_fingerprint"]
            == payload["content_fingerprint"]
            and admission["expires_monotonic"] > time.monotonic()
            and not admission["claimed"]
            for admission in admissions.values()
        ):
            return await _terminal(
                runtime,
                session,
                payload,
                approval_id,
                "denied",
                "duplicate_pending_content",
            )
        dispatch_id = secrets.token_urlsafe(32)
        admissions[dispatch_id] = {
            "session": session,
            "payload": payload,
            "approval_event_id": approval_id,
            "attempt_event_id": attempt_id,
            "openclaw_approval_event_id": openclaw_id,
            "final_check": final_check,
            "expires_monotonic": time.monotonic() + 30,
            "claimed": False,
            "claim_id": None,
            "provider_future": None,
            "outbound_task": None,
        }
        admissions[dispatch_id]["expiry_task"] = asyncio.create_task(
            _expire_native_admission(runtime, dispatch_id)
        )
        # This record is consumed privately by the plugin. Its execute()
        # implementation never returns this raw record as model tool content.
        return {
            "transport_dispatch": {
                "channel": "imessage",
                "account_id": payload["provider_account_id"],
                "recipient": payload["recipient"],
                "body": payload["body"],
                "approval_event_id": approval_id,
            },
            "automatic_retry_allowed": False,
        }
    permit_token = set_response_permit(
        CoSResponsePermit(
            account_id=payload["provider_account_id"],
            recipient=payload["recipient"],
            subject=payload["subject"],
            body=payload["body"],
            member_id=session.auth_member_id,
            approval_event_id=approval_id,
            final_check=final_check,
            thread_id=payload.get("thread_id"),
            in_reply_to=payload.get("in_reply_to"),
        )
    )
    audit_token = set_outbound_audit_context(
        OutboundAuditContext(
            initiating_command_id=approval_id,
            initiating_event_id=attempt_id,
            domain_approval_event_id=approval_id,
            openclaw_approval_event_id=openclaw_id,
            provider="gmail",
            provider_account_id=payload["provider_account_id"],
            operation="cos.response.send",
            content_fingerprint=payload["content_fingerprint"],
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="sensitive",
        )
    )
    try:
        result = await runtime.adapters.dispatch_cos_email(
            provider_account_id=payload["provider_account_id"],
            message=OutboundMessage(
                channel="gmail",
                recipient=payload["recipient"],
                body=payload["body"],
                metadata={
                    "subject": payload["subject"],
                    **(
                        {
                            "thread_id": payload["thread_id"],
                            "in_reply_to": payload["in_reply_to"],
                        }
                        if payload.get("thread_id")
                        else {}
                    ),
                },
            ),
        )
        status = "suppressed" if result.suppressed else "sent"
        if not result.suppressed and not result.delivered:
            status = "unknown"
        return await _terminal(runtime, session, payload, approval_id, status, status)
    except BaseException:
        # Provider errors include transport timeouts with ambiguous acceptance.
        # Never retry or report a send as failed with certainty after contact.
        await asyncio.shield(
            _terminal(
                runtime,
                session,
                payload,
                approval_id,
                "unknown",
                "provider_outcome_unknown",
            )
        )
        raise
    finally:
        reset_outbound_audit_context(audit_token)
        reset_response_permit(permit_token)


async def _terminal(
    runtime: Any,
    session: Session,
    payload: dict[str, Any],
    approval_id: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    result = {
        "contract": RESPONSE_CONTRACT,
        "member_id": session.auth_member_id,
        "approval_event_id": approval_id,
        "content_fingerprint": payload["content_fingerprint"],
        "status": status,
        "reason": reason,
        "provider_effect_count": None if status == "unknown" else int(status == "sent"),
        "automatic_retry_allowed": False,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    event_id = await _receipt(
        runtime,
        session,
        event_type="cos.response.result",
        payload=result,
        request_id=approval_id,
        causation_id=approval_id,
    )
    return {"event_id": event_id, **result}


def _admissions(runtime: Any) -> dict[str, Any]:
    """Ephemeral send permits do not survive a process restart."""
    if not hasattr(runtime, "_cos_response_admissions"):
        runtime._cos_response_admissions = {}
    return runtime._cos_response_admissions


async def _expire_native_admission(runtime: Any, dispatch_id: str) -> None:
    await asyncio.sleep(30)
    admission = _admissions(runtime).get(dispatch_id)
    if admission is None:
        return
    async with runtime.settings_command_locks.setdefault(
        f"cos-response:{admission['approval_event_id']}", asyncio.Lock()
    ):
        # Match result retirement's lock order. A claim may be suspended in
        # its final checks; expiry cannot retire its permit underneath it.
        async with runtime.settings_command_locks.setdefault(
            f"cos-response-native-claim:{runtime.settings.instance.tenant_id}",
            asyncio.Lock(),
        ):
            if not admission["claimed"]:
                admission["claimed"] = True
                await _existing_or_terminal(
                    runtime,
                    admission["session"],
                    admission["payload"],
                    admission["approval_event_id"],
                    "denied",
                    "native_dispatch_not_claimed",
                )
    task = admission["outbound_task"]
    if task is not None and not task.done():
        await asyncio.shield(task)
    _admissions(runtime).pop(dispatch_id, None)


async def recover_response_attempts(runtime: Any) -> None:
    """Close orphaned durable attempts after restart without provider contact."""
    tenant_id = runtime.settings.instance.tenant_id
    attempted: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    async for event in runtime.event_log.read_since(
        types=["cos.response.attempted", "cos.response.result"]
    ):
        if event["tenant_id"] != tenant_id:
            continue
        approval_id = event["payload"]["approval_event_id"]
        if event["type"] == "cos.response.result":
            completed.add(approval_id)
        else:
            attempted[approval_id] = event
    for approval_id, event in attempted.items():
        if approval_id in completed:
            continue
        approved = await runtime.event_log.get(approval_id)
        member_id = event["payload"]["member_id"]
        if (
            approved is None
            or approved["type"] != "cos.response.approved"
            or approved["tenant_id"] != tenant_id
            or approved["owner_scope"] != f"private:{member_id}"
            or approved["payload"]["member_id"] != member_id
        ):
            raise RuntimeError("CoS response attempt has invalid approval provenance")
        await _terminal(
            runtime,
            build_internal_session(member_id, "principal", tenant_id),
            approved["payload"],
            approval_id,
            "unknown",
            "restart_after_contact_intent",
        )


class ResponseClaimRequest(BaseModel):
    model_config = {"extra": "forbid"}
    account_id: str = Field(min_length=1, max_length=320)
    recipient: str = Field(min_length=1, max_length=320)
    body: str = Field(min_length=1, max_length=8000)


class ResponseTerminalRequest(BaseModel):
    model_config = {"extra": "forbid"}
    claim_id: str = Field(min_length=20, max_length=200)
    status: Literal["sent", "unknown"]
    receipt_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@router.post("/api/openclaw/v1/cos-responses/claim")
async def claim_imessage(
    body: ResponseClaimRequest, request: Request
) -> dict[str, Any]:
    seam._authenticate(request)
    runtime = seam._runtime(request)
    try:
        recipient = normalize_recipient("imessage", body.recipient)
    except ValueError as exc:
        raise HTTPException(403, "exact single recipient required") from exc
    if recipient != body.recipient:
        raise HTTPException(403, "recipient must match the confirmed spelling")
    fingerprint = content_fingerprint(
        channel="imessage",
        account_id=body.account_id,
        recipient=recipient,
        subject="",
        body=body.body,
    )
    async with runtime.settings_command_locks.setdefault(
        f"cos-response-native-claim:{runtime.settings.instance.tenant_id}",
        asyncio.Lock(),
    ):
        matches = [
            admission
            for admission in _admissions(runtime).values()
            if not admission["claimed"]
            and admission["expires_monotonic"] > time.monotonic()
            and admission["payload"]["content_fingerprint"] == fingerprint
        ]
        if len(matches) != 1:
            raise HTTPException(403, "no exact one-use CoS response admission")
        admission = matches[0]
        await admission["final_check"](True)
        if await runtime.observation.is_active():
            raise HTTPException(403, "observation suppresses CoS response")
        if admission["expires_monotonic"] <= time.monotonic():
            raise HTTPException(403, "CoS response admission expired")
        admission["claimed"] = True
        admission["claim_id"] = secrets.token_urlsafe(32)
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[bool] = loop.create_future()
        admission["provider_future"] = loop.create_future()
        admission["outbound_task"] = asyncio.create_task(
            _native_outbound(runtime, admission, ready)
        )
        try:
            admitted = await asyncio.wait_for(asyncio.shield(ready), 4)
        except TimeoutError as exc:
            admission["outbound_task"].cancel()
            await asyncio.shield(admission["outbound_task"])
            raise HTTPException(503, "CoS native dispatch admission timed out") from exc
        if not admitted:
            raise HTTPException(403, "CoS native dispatch was suppressed")
        return {
            "allowed": True,
            "claim_id": admission["claim_id"],
            "approval_event_id": admission["approval_event_id"],
        }


@router.post("/api/openclaw/v1/cos-responses/terminal")
async def complete_imessage(
    body: ResponseTerminalRequest, request: Request
) -> dict[str, Any]:
    seam._authenticate(request)
    runtime = seam._runtime(request)
    matches = [
        admission
        for admission in _admissions(runtime).values()
        if admission["claimed"] and admission["claim_id"] == body.claim_id
    ]
    if len(matches) != 1:
        raise HTTPException(403, "CoS response claim is unknown")
    admission = matches[0]
    future = admission["provider_future"]
    if future is not None and not future.done():
        future.set_result(body.status)
    task = admission["outbound_task"]
    if task is not None:
        return await asyncio.shield(task)
    raise HTTPException(409, "CoS native dispatch is not running")


class ResponseResultRequest(seam.ContextRequest):
    approval_event_id: str = Field(min_length=1, max_length=240)


@router.post("/api/openclaw/v1/cos-responses/result")
async def response_result(
    body: ResponseResultRequest, request: Request
) -> dict[str, Any]:
    session = seam._session(request, body.provenance)
    runtime = seam._runtime(request)
    event = await runtime.event_log.get(body.approval_event_id)
    if (
        event is None
        or event["tenant_id"] != session.tenant_id
        or event["type"] != "cos.response.approved"
        or event["owner_scope"] != f"private:{session.auth_member_id}"
        or event["payload"]["member_id"] != session.auth_member_id
    ):
        raise HTTPException(404, "response unavailable")
    async with runtime.settings_command_locks.setdefault(
        f"cos-response:{body.approval_event_id}", asyncio.Lock()
    ):
        # Called after the plugin's sole provider invocation. Missing durable
        # terminal evidence is unknown, never permission for a retry.
        for dispatch_id, admission in list(_admissions(runtime).items()):
            if admission["approval_event_id"] == body.approval_event_id:
                async with runtime.settings_command_locks.setdefault(
                    f"cos-response-native-claim:{session.tenant_id}", asyncio.Lock()
                ):
                    if not admission["claimed"]:
                        # The same lock protects claim admission. Retire it
                        # before publishing zero effects, so no late wrapper
                        # request can send after this proof of non-contact.
                        admission["claimed"] = True
                        _admissions(runtime).pop(dispatch_id, None)
                        admission["expiry_task"].cancel()
                        return await _existing_or_terminal(
                            runtime,
                            session,
                            event["payload"],
                            body.approval_event_id,
                            "denied",
                            "native_dispatch_not_claimed",
                        )
                admission["claimed"] = True
                future = admission["provider_future"]
                if future is not None and not future.done():
                    future.set_result("unknown")
                if admission["outbound_task"] is not None:
                    return await asyncio.shield(admission["outbound_task"])
        return await _existing_or_terminal(
            runtime,
            session,
            event["payload"],
            body.approval_event_id,
            "unknown",
            "transport_terminal_missing",
        )


async def _existing_or_terminal(
    runtime: Any,
    session: Session,
    payload: dict[str, Any],
    approval_id: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    existing = await runtime.event_log.get_by_idempotency(
        session.tenant_id,
        f"openclaw:{session.auth_member_id}:cos.response.result:{approval_id}",
    )
    if existing is not None:
        return {"event_id": existing["event_id"], **existing["payload"]}
    return await _terminal(runtime, session, payload, approval_id, status, reason)


async def _native_outbound(
    runtime: Any, admission: dict[str, Any], ready: asyncio.Future[bool]
) -> dict[str, Any]:
    """Keep the final observation seam open until the native result arrives."""
    session = admission["session"]
    payload = admission["payload"]
    approval_id = admission["approval_event_id"]
    policy_lock = runtime.settings_command_locks.setdefault(
        f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
    )
    acquired = False
    token = set_outbound_audit_context(
        OutboundAuditContext(
            initiating_command_id=approval_id,
            initiating_event_id=admission["attempt_event_id"],
            domain_approval_event_id=approval_id,
            openclaw_approval_event_id=admission["openclaw_approval_event_id"],
            provider="imessage",
            provider_account_id=payload["provider_account_id"],
            operation="cos.response.send",
            content_fingerprint=payload["content_fingerprint"],
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="sensitive",
        )
    )

    async def native_effect() -> dict[str, Any]:
        await admission["final_check"](True)
        ready.set_result(True)
        status = await asyncio.wait_for(admission["provider_future"], 45)
        if status != "sent":
            raise RuntimeError("native provider outcome is unknown")
        return {"accepted": True}

    try:
        # Hold the same lock as Settings revocation until native settlement.
        # The callback never needs this lock, so a successful revocation cannot
        # race a still-admitted transport call.
        await policy_lock.acquire()
        acquired = True
        result = await outbound(
            session,
            "cos.response.send",
            {"content_fingerprint": payload["content_fingerprint"]},
            native_effect,
            manager=runtime.observation,
            event_sink=runtime.events,
            target_channel="imessage",
            target_identifier=payload["recipient"],
        )
        status = "suppressed" if result.suppressed else "sent"
        return await _existing_or_terminal(
            runtime, session, payload, approval_id, status, "transport_" + status
        )
    except BaseException:
        return await asyncio.shield(
            _existing_or_terminal(
                runtime,
                session,
                payload,
                approval_id,
                "unknown",
                "transport_outcome_unknown",
            )
        )
    finally:
        if acquired:
            policy_lock.release()
        if not ready.done():
            ready.set_result(False)
        reset_outbound_audit_context(token)


router.include_router(conversation_reply_router)
