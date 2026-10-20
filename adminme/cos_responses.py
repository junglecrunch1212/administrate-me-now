"""Exact, plain-text CoS responses and the final provider boundary.

The permit is process-local authority, never a payload field a model can forge.
Every CoS Gmail send (including legacy callers) must carry it. The callback
rechecks durable policy immediately before the provider call.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Awaitable, Callable

from adminme.lib.adapters import OutboundMessage
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

RESPONSE_CONTRACT = "cos-response-plain-text-v1"


def content_fingerprint(
    *,
    account_id: str,
    recipient: str,
    subject: str,
    body: str,
    channel: str = "email",
) -> str:
    return hashlib.sha256(
        json.dumps(
            [RESPONSE_CONTRACT, channel, account_id, recipient, subject, body],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class CoSResponsePermit:
    account_id: str
    recipient: str
    subject: str
    body: str
    member_id: str
    approval_event_id: str
    final_check: Callable[[bool], Awaitable[None]]


_PERMIT: ContextVar[CoSResponsePermit | None] = ContextVar(
    "administrateme_cos_response_permit", default=None
)


def set_response_permit(permit: CoSResponsePermit) -> Token:
    return _PERMIT.set(permit)


def reset_response_permit(token: Token) -> None:
    _PERMIT.reset(token)


async def guard_gmail_response(
    config: dict[str, Any], message: OutboundMessage, *, provider_effect: bool = False
) -> None:
    """Reject service sends outside the exact governed response composition."""
    if not config.get("service_principal_id"):
        return
    permit = _PERMIT.get()
    account = config.get("provider_account_id") or config.get("user_email")
    if (
        config.get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID
        or permit is None
        or config.get("member_id")
        or account != permit.account_id
        or config.get("user_email") != permit.account_id
        or message.channel != "gmail"
        or message.recipient != permit.recipient
        or message.body != permit.body
        or message.metadata != {"subject": permit.subject}
    ):
        raise PermissionError(
            "CoS Gmail send requires an exact governed response permit"
        )
    await permit.final_check(provider_effect)
