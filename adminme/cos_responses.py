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
import re
from typing import Any, Awaitable, Callable
import unicodedata

from adminme.lib.adapters import OutboundMessage
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

RESPONSE_CONTRACT = "cos-response-plain-text-v1"
IMESSAGE_TEXT_GUIDANCE = (
    "For iMessage, use one plain-text line without formatting marks, tabs, "
    "or spaces at the beginning or end. Use email for multiple lines or text "
    "containing formatting marks."
)


def validate_imessage_response_text(body: str) -> None:
    """Conservative literal subset of the pinned OpenClaw Markdown sender.

    That sender has no raw-text option. Reject syntax that can introduce
    formatting, directives, entities, escapes or block structure before approval.
    The final native guard still compares the entire text; this is not a
    replacement for that guard or a claim to recognize all safe Markdown.
    """
    if (
        not body
        or body != body.strip()
        or any(char in body for char in "*_~`[]<>\\&#|")
        or any(
            (char.isspace() and char != " ")
            or unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"}
            for char in body
        )
        or re.match(r"^(?:[-+=]|\d+[.)] )", body)
        or len(body.encode("utf-16-le")) // 2 > 8000
    ):
        raise ValueError(IMESSAGE_TEXT_GUIDANCE)


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
