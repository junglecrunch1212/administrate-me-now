"""Current private instructions prepare exact CoS emails to the requesting member.

The existing response executor remains the only sender. This router prepares a
source-bound domain receipt and reads results; it never dispatches a provider.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from adminme.cos_response_assurance import (
    _approval_valid,
    _attempt_valid,
    _outbound_valid,
    _terminal_valid,
    _time,
)
from adminme.cos_response_policy import (
    POLICY_EVENT_TYPE,
    normalize_recipient,
    policy_from_event_log,
)
from adminme.cos_responses import RESPONSE_CONTRACT, content_fingerprint
from adminme.events.envelope import Sensitivity
from adminme.lib.session import Session
from adminme.openclaw_approvals import ApprovalReceiptError
from adminme.products.openclaw import cos_responses as responses
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.request_authorization import (
    GROUNDING_TYPE,
    evidence_matches,
    fingerprint,
    ground_action,
    ticket,
)
from adminme.products.openclaw.service_work import current_private_instruction
from adminme.request_actions_control import request_actions_allowed
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

RULE = "current-human-request.cos-email-to-requester.v1"
router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["requested-email"])


class RequestedEmailRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provenance: seam.Provenance
    source_message_event_id: str = Field(min_length=1, max_length=240)
    request_id: str = Field(min_length=8, max_length=120)
    subject: str = Field(max_length=300)
    body: str = Field(min_length=1, max_length=8000)
    provider_account_id: str | None = Field(default=None, min_length=1, max_length=320)
    recipient: str | None = Field(default=None, min_length=1, max_length=320)

    @field_validator("subject", "body")
    @classmethod
    def plain_text(cls, value: str, info: Any) -> str:
        if (info.field_name == "body" and not value.strip()) or any(
            ord(char) < 32 and (info.field_name == "subject" or char not in "\n\t")
            for char in value
        ):
            raise ValueError("Requested email requires bounded plain text")
        return value


class RequestedEmailStatusRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provenance: seam.Provenance
    source_message_event_id: str = Field(min_length=1, max_length=240)
    approval_event_id: str = Field(min_length=1, max_length=240)


def _accounts(runtime: Any) -> list[dict[str, str]]:
    """Current enabled adapters plus the existing service-channel authority."""
    accounts: list[dict[str, str]] = []
    for row in getattr(runtime.settings, "adapter_config", {}).get("adapters", []):
        config = row.get("config", {})
        if (
            str(row.get("kind") or "").removeprefix("adapter:") != "messaging-gmail-api"
            or row.get("enabled") is not True
            or config.get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID
            or config.get("member_id")
            or not config.get("provider_account_id")
            or config.get("user_email") != config.get("provider_account_id")
            or config.get("enable_writes") is not True
            or config.get("read_only") is not False
        ):
            continue
        account = {
            "channel": "email",
            "service_channel_id": config.get("channel_id"),
            "provider_account_id": config["provider_account_id"],
        }
        try:
            channel = responses._service_channel(runtime, account)
        except HTTPException:
            continue
        if channel.sending_enabled and not channel.observation_active:
            accounts.append({**account, "label": channel.display_name})
    return accounts


async def selected_email(
    runtime: Any, session: Session, body: RequestedEmailRequest
) -> dict[str, Any]:
    accounts = _accounts(runtime)
    if body.provider_account_id is not None:
        accounts = [
            row
            for row in accounts
            if row["provider_account_id"] == body.provider_account_id
        ]
    if len(accounts) != 1:
        raise HTTPException(
            409,
            {
                "code": "email_sender_choice_required"
                if accounts
                else "email_sender_unavailable",
                "accounts": accounts,
                "reason": "Choose one current writable CoS email account.",
            },
        )
    policy = await policy_from_event_log(runtime.event_log, session.tenant_id)
    recipients = [
        entry["address"]
        for entry in policy["entries"]
        if entry["member_id"] == session.auth_member_id and entry["channel"] == "email"
    ]
    if body.recipient is not None:
        try:
            chosen = normalize_recipient("email", body.recipient)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if chosen not in recipients:
            raise HTTPException(403, "Email recipient is not this requesting member")
        recipients = [chosen]
    if len(recipients) != 1:
        raise HTTPException(
            409,
            {
                "code": "email_recipient_choice_required"
                if recipients
                else "email_recipient_unavailable",
                "recipients": recipients,
                "reason": "Choose one of your currently authorized email addresses.",
            },
        )
    return responses.ResponseApprovalRequest(
        request_id=body.request_id,
        channel="email",
        service_channel_id=accounts[0]["service_channel_id"],
        provider_account_id=accounts[0]["provider_account_id"],
        recipient=recipients[0],
        subject=body.subject,
        body=body.body,
    ).model_dump(exclude={"request_id"})


def email_action(
    member_id: str, values: dict[str, Any], body: RequestedEmailRequest
) -> dict[str, Any]:
    return {
        "provider": "gmail",
        "operation": "send_email",
        "resource_owner": COS_SERVICE_PRINCIPAL_ID,
        "operating_principal": COS_SERVICE_PRINCIPAL_ID,
        "recipient_member_id": member_id,
        "service_channel_id": values["service_channel_id"],
        "provider_account_id": values["provider_account_id"],
        "recipient": values["recipient"],
        "subject": values["subject"],
        "body": values["body"],
        "sender_selected_by_policy": body.provider_account_id is None,
        "recipient_selected_by_policy": body.recipient is None,
        "content_fingerprint": content_fingerprint(
            account_id=values["provider_account_id"],
            recipient=values["recipient"],
            subject=values["subject"],
            body=values["body"],
            channel="email",
        ),
    }


def _owned_approval(
    session: Session, approved: dict[str, Any] | None
) -> dict[str, Any]:
    if (
        approved is None
        or approved.get("tenant_id") != session.tenant_id
        or approved.get("type") != "cos.response.approved"
        or approved.get("owner_scope") != f"private:{session.auth_member_id}"
        or approved.get("visibility_scope") != f"private:{session.auth_member_id}"
        or approved.get("actor_identity") != COS_SERVICE_PRINCIPAL_ID
        or approved.get("payload", {}).get("authorization_method") != "requested_email"
        or approved.get("payload", {}).get("member_id") != session.auth_member_id
    ):
        raise HTTPException(404, "This requested email receipt is unavailable")
    return approved


async def retained_result(
    runtime: Any, session: Session, approved: dict[str, Any]
) -> dict[str, Any]:
    """Read exact retained facts without creating a terminal or contacting Gmail."""
    p = approved["payload"]
    base = {
        "approval_event_id": approved["event_id"],
        "source_message_event_id": p["request_source_event_id"],
        "provider_account_id": p["provider_account_id"],
        "recipient": p["recipient"],
        "subject": p["subject"],
        "expires_at": p["expires_at"],
        "automatic_retry_allowed": False,
    }
    prefix = f"openclaw:{session.auth_member_id}"
    terminal = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"{prefix}:cos.response.result:{approved['event_id']}"
    )
    attempted = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"{prefix}:cos.response.attempted:{approved['event_id']}"
    )
    if terminal is not None:
        valid = bool(
            terminal.get("type") == "cos.response.result"
            and terminal.get("payload", {}).get("approval_event_id")
            == approved["event_id"]
            and _terminal_valid(terminal, approved)
        )
        if terminal["payload"].get("status") == "sent":
            by_id = {approved["event_id"]: approved}
            for event_id in (
                p.get("request_source_event_id"),
                p.get("request_authorization_event_id"),
                p.get("policy_event_id"),
                (attempted or {}).get("payload", {}).get("openclaw_approval_event_id"),
            ):
                retained = (
                    await runtime.event_log.get(str(event_id)) if event_id else None
                )
                if retained is not None:
                    by_id[retained["event_id"]] = retained
            policies = [
                event
                async for event in runtime.event_log.read_since(
                    through=(attempted or approved)["event_id"],
                    types=[POLICY_EVENT_TYPE],
                )
                if event.get("tenant_id") == session.tenant_id
            ]
            outbound = (
                await runtime.event_log.get_by_causation(attempted["event_id"])
                if attempted
                else []
            )
            sent = [row for row in outbound if row["type"] == "external.sent"]
            valid = bool(
                valid
                and attempted
                and attempted.get("type") == "cos.response.attempted"
                and attempted.get("payload", {}).get("approval_event_id")
                == approved["event_id"]
                and _approval_valid(approved, by_id)
                and _attempt_valid(attempted, approved, by_id, policies)
                and len(sent) == 1
                and _outbound_valid(sent[0], attempted, approved)
            )
        if not valid:
            return {
                **base,
                "status": "unknown",
                "reason": "terminal_chain_mismatch",
                "provider_effect_count": None,
            }
        return {**base, "event_id": terminal["event_id"], **terminal["payload"]}
    if attempted is not None:
        return {
            **base,
            "status": "unknown",
            "reason": "prior_attempt_unresolved",
            "provider_effect_count": None,
        }
    expires_at = _time(p.get("expires_at"))
    if expires_at is None or _time(p.get("approved_at")) is None:
        return {
            **base,
            "status": "unknown",
            "reason": "invalid_retained_authority_time",
            "provider_effect_count": None,
        }
    expired = expires_at <= datetime.now(timezone.utc)
    revoked = not request_actions_allowed(runtime, issued_at=p["approved_at"])
    return {
        **base,
        "status": "expired"
        if expired
        else "revoked"
        if revoked
        else "awaiting_request_policy",
        "provider_effect_count": 0,
    }


async def validate_email_authority(
    request: Request,
    session: Session,
    provenance: seam.Provenance,
    approved: dict[str, Any],
    grounding_id: str,
) -> dict[str, str]:
    """Closed email branch for the shared independent request-policy executor."""
    approved = _owned_approval(session, approved)
    p = approved["payload"]
    runtime = seam._runtime(request)
    if p.get("request_authorization_event_id") != grounding_id:
        raise HTTPException(
            403, "Email request authority differs from its original receipt"
        )
    grounding = await runtime.event_log.get(grounding_id)
    _, source = await current_private_instruction(
        request,
        provenance,
        p["request_source_event_id"],
        required_permission="prepare_external_actions",
    )
    g = (grounding or {}).get("payload", {})
    action = p["request_action"]
    now = datetime.now(timezone.utc)
    issued_at, expires_at = _time(p.get("approved_at")), _time(p.get("expires_at"))
    if (
        grounding is None
        or grounding.get("type") != GROUNDING_TYPE
        or grounding.get("schema_version") != 1
        or grounding.get("tenant_id") != session.tenant_id
        or grounding.get("owner_scope") != f"private:{session.auth_member_id}"
        or grounding.get("visibility_scope") != f"private:{session.auth_member_id}"
        or grounding.get("actor_identity") != COS_SERVICE_PRINCIPAL_ID
        or g.get("member_id") != session.auth_member_id
        or g.get("rule_id") != RULE
        or g.get("action") != action
        or g.get("action_id") != p["action_id"]
        or g.get("request_fingerprint") != p["request_fingerprint"]
        or g.get("action_fingerprint") != fingerprint(action)
        or g.get("source_event_id") != source["event_id"]
        or g.get("source_fingerprint") != fingerprint(source["payload"]["text"])
        or not evidence_matches(source["payload"]["text"], g.get("evidence"))
        or approved.get("causation_id") != source["event_id"]
        or g.get("issued_at") != p["approved_at"]
        or g.get("expires_at") != p["expires_at"]
        or issued_at is None
        or expires_at is None
        or not issued_at <= now < expires_at
    ):
        raise HTTPException(403, "Email request authority is stale or mismatched")
    if not request_actions_allowed(runtime, issued_at=p["approved_at"]):
        raise HTTPException(409, "Requested email authority is revoked for recovery")
    if runtime.observation is None or await runtime.observation.is_active():
        raise HTTPException(409, "Observation mode suppresses requested email")
    original = RequestedEmailRequest(
        provenance=provenance,
        source_message_event_id=source["event_id"],
        request_id="retained-email-check",
        subject=p["subject"],
        body=p["body"],
        provider_account_id=None
        if action.get("sender_selected_by_policy")
        else p["provider_account_id"],
        recipient=None
        if action.get("recipient_selected_by_policy")
        else p["recipient"],
    )
    current = await selected_email(runtime, session, original)
    if email_action(session.auth_member_id, current, original) != action:
        raise HTTPException(409, "Requested email destination or content changed")
    await responses._policy_check(runtime, session, p, require_current=True)
    return {
        "source_event_id": source["event_id"],
        "request_authorization_event_id": grounding_id,
        "domain_approval_event_id": approved["event_id"],
        "action_fingerprint": g["action_fingerprint"],
        "rule_id": RULE,
    }


async def execute_requested_email(
    request: Request,
    session: Session,
    approved: dict[str, Any],
    body: responses.ResponseExecutionRequest,
    grounding_id: str,
) -> dict[str, Any]:
    """Caller holds the existing response and tenant-policy locks."""
    runtime = seam._runtime(request)
    approved = _owned_approval(session, approved)
    result = await retained_result(runtime, session, approved)
    if result["status"] != "awaiting_request_policy":
        return result
    authorization = await validate_email_authority(
        request, session, body.provenance, approved, grounding_id
    )

    async def current_authority() -> None:
        await validate_email_authority(
            request, session, body.provenance, approved, grounding_id
        )
        try:
            if (
                not body.openclaw_approval_receipt_id
                or runtime.openclaw_approval_store is None
            ):
                raise ApprovalReceiptError(
                    "independent email request decision is unavailable"
                )
            receipt = runtime.openclaw_approval_store.get(
                body.openclaw_approval_receipt_id
            )
            if receipt.expires_at_ms <= datetime.now(timezone.utc).timestamp() * 1000:
                raise ApprovalReceiptError("independent email request decision expired")
        except ApprovalReceiptError as exc:
            raise HTTPException(403, str(exc)) from exc

    # The existing executor keeps final guard -> independent receipt consumption
    # -> durable attempt -> exact permit -> provider -> terminal ordering.
    return await responses._execute_locked(
        runtime,
        session,
        approved,
        body,
        request,
        approved["payload"],
        standing_check=current_authority,
        request_authorization=authorization,
    )


@router.post("/confirm-requested-email")
async def confirm_requested_email(
    body: RequestedEmailRequest, request: Request
) -> dict[str, Any]:
    session, source = await current_private_instruction(
        request,
        body.provenance,
        body.source_message_event_id,
        required_permission="prepare_external_actions",
    )
    runtime = seam._runtime(request)
    action_id = "requested-email:" + fingerprint(
        [session.tenant_id, session.auth_member_id, body.request_id]
    )
    requested = body.model_dump(
        exclude={"provenance", "source_message_event_id", "request_id"}
    )
    request_hash = fingerprint(requested)
    key = f"openclaw:{session.auth_member_id}:cos.response.approved:{action_id}"
    async with runtime.settings_command_locks.setdefault(key, asyncio.Lock()):
        prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
        if prior is not None:
            prior = _owned_approval(session, prior)
            if (
                prior["payload"].get("request_fingerprint") != request_hash
                or prior["payload"].get("request_source_event_id") != source["event_id"]
            ):
                raise HTTPException(
                    409, "This request ID belongs to different original email intent"
                )
            result = await retained_result(runtime, session, prior)
            if result["status"] != "awaiting_request_policy":
                return result
            grounding = await runtime.event_log.get(
                prior["payload"]["request_authorization_event_id"]
            )
            if grounding is None:
                raise HTTPException(
                    409, "Retained email request evidence is unavailable"
                )
            return {
                **result,
                "request_authorization": ticket(runtime, session, prior, grounding),
            }
        values = await selected_email(runtime, session, body)
        policy = await responses._policy_check(
            runtime, session, values, require_current=False
        )
        if not responses._response_authority(runtime, session)["channels"]["email"][
            "ready"
        ]:
            raise HTTPException(
                403, "Current private email preparation authority is unavailable"
            )
        await seam._guard(runtime, session, "cos.response.approved", values)
        action = email_action(session.auth_member_id, values, body)
        grounding = await ground_action(
            runtime,
            session,
            source,
            action_id=action_id,
            request_fingerprint=request_hash,
            action=action,
            rule_id=RULE,
        )
        payload = {
            **values,
            "contract": RESPONSE_CONTRACT,
            "member_id": session.auth_member_id,
            "authorization_method": "requested_email",
            "action_id": action_id,
            "request_source_event_id": source["event_id"],
            "request_authorization_event_id": grounding["event_id"],
            "request_fingerprint": request_hash,
            "request_action": action,
            "content_fingerprint": action["content_fingerprint"],
            "policy_event_id": policy["event_id"],
            "policy_revision": policy["revision"],
            "approved_at": grounding["payload"]["issued_at"],
            "expires_at": grounding["payload"]["expires_at"],
        }
        approved_id, _ = await seam._append(
            runtime,
            session,
            event_type="cos.response.approved",
            payload=payload,
            source_account_id="cos-response",
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity=cast(
                Sensitivity,
                "privileged" if source["sensitivity"] == "privileged" else "sensitive",
            ),
            request_id=action_id,
            causation_id=source["event_id"],
            schema_version=5,
        )
        approved = await runtime.event_log.get(approved_id)
        assert approved is not None
        return {
            **await retained_result(runtime, session, approved),
            "request_authorization": ticket(runtime, session, approved, grounding),
        }


@router.post("/requested-email-status")
async def requested_email_status(
    body: RequestedEmailStatusRequest, request: Request
) -> dict[str, Any]:
    session, _ = await current_private_instruction(
        request, body.provenance, body.source_message_event_id
    )
    runtime = seam._runtime(request)
    approved = _owned_approval(
        session, await runtime.event_log.get(body.approval_event_id)
    )
    return await retained_result(runtime, session, approved)
