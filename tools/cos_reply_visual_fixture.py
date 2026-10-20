"""Synthetic Gateway approval boundary for the disposable browser harness.

The member HTTP routes, domain approval log and UI are production code.
Only Gateway conversation/approval state is simulated. No provider is contacted,
and a resolved sending decision is never represented as a delivered message.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime
import hashlib
import hmac
import json
from typing import Any
import uuid

from adminme.member_chat import (
    DeterministicOpenClawChatTransport,
    MemberChatChunk,
    MemberChatError,
)
from adminme.openclaw_topology import member_agent_id
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID

ACCOUNT = "visual-cos-imessage-fixture"
PHONE = "+12025550101"  # fixture:tenant_data:ok
CHANNEL = "channel:cos:visual-replies"


def service_config() -> dict[str, Any]:
    return {
        "principals": [
            {
                "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "kind": "chief_of_staff",
                "party_id": COS_PARTY_ID,
                "display_name": "Fixture CoS",
            }
        ],
        "channels": [
            {
                "channel_id": CHANNEL,
                "display_name": "Fixture CoS iMessage",
                "provider": "apple_imessage",
                "openclaw_channel_id": "imessage",
                "intake_mode": "member_submission",
                "response_transport_contract": "cos-imessage-guard-v1",
                "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "state": "observation",
                "enrolled": True,
                "observation_active": True,
                "sending_enabled": False,
                "provider_account_reference": "provider-account:"
                + hashlib.sha256(ACCOUNT.encode()).hexdigest(),
                "routes": [],
            }
        ],
    }


class CoSReplyVisualTransport(DeterministicOpenClawChatTransport):
    def __init__(
        self, runtime: Any, member_id: str, now: Callable[[], datetime]
    ) -> None:
        super().__init__()
        self.runtime, self.member_id, self.now = runtime, member_id, now
        self.pending: dict[str, Any] | None = None
        self.waiting: asyncio.Future[str] | None = None
        self.decisions: list[dict[str, str]] = []
        self.completed: list[str] = []
        self.phase = "idle"

    async def stream(
        self, *, session_key: str, message: str, idempotency_key: str
    ) -> AsyncIterator[MemberChatChunk]:
        prefix = (
            "Please use administrateme_cos_response to request sending approval "
            "for approved reply "
        )
        if not message.startswith(prefix) or not message.endswith("."):
            async for chunk in super().stream(
                session_key=session_key,
                message=message,
                idempotency_key=idempotency_key,
            ):
                yield chunk
            return
        approval_id = message[len(prefix) : -1]
        event = await self.runtime.event_log.get(approval_id)
        if (
            event is None
            or event["type"] != "cos.response.approved"
            or event["owner_scope"] != f"private:{self.member_id}"
            or session_key != self.runtime.member_chat_binding(self.member_id)[1]
        ):
            raise MemberChatError("fixture_scope", "Fixture reply is unavailable")
        params = {
            "request_id": f"visual-response-{len(self.completed) + 1}",
            "approval_event_id": approval_id,
        }
        self.pending = {
            "approval_id": f"plugin:{uuid.uuid4()}",
            "revision": hashlib.sha256(approval_id.encode()).hexdigest(),
            "expires_at_ms": int(self.now().timestamp() * 1000) + 90_000,
            "domain_approval_id": approval_id,
            "request_id": params["request_id"],
            "request_fingerprint": hashlib.sha256(
                json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "session_key": session_key,
            "agent_id": member_agent_id(self.member_id),
        }
        self.waiting = asyncio.get_running_loop().create_future()
        self.phase = "pending"
        yield MemberChatChunk(kind="status", phase="starting_model")
        try:
            decision = await asyncio.wait_for(self.waiting, timeout=90)
            self.phase = "decision_received"
            response = (
                "Sending allowed once. This isolated browser fixture contacts "
                "no provider."
                if decision == "allow-once"
                else "Sending declined. No provider was contacted."
            )
            self.completed.append(decision)
            self._transcripts[session_key].append(
                {"role": "assistant", "text": response}
            )
            yield MemberChatChunk(kind="delta", text=response)
            self.phase = "final_yielded"
            yield MemberChatChunk(kind="final")
        finally:
            self.phase = "closed"
            self.pending = None
            self.waiting = None

    async def cos_approval_request(
        self, method: str, ticket: dict[str, Any]
    ) -> dict[str, Any]:
        operation = method.rsplit(".", 1)[-1]
        fields = (
            "session_key",
            "agent_id",
            "member_id",
            "approval_id",
            "revision",
            "decision",
            "nonce",
        )
        material = "|".join(
            [
                "cos-member-approval-v1",
                operation,
                *(ticket[field] for field in fields),
                str(ticket["expires_at_ms"]),
            ]
        )
        assert hmac.compare_digest(
            ticket["signature"],
            hmac.new(
                self.runtime.settings.openclaw_shared_secret.encode(),
                material.encode(),
                hashlib.sha256,
            ).hexdigest(),
        )
        assert int(self.now().timestamp() * 1000) < ticket["expires_at_ms"]
        if operation == "list":
            if self.pending is None:
                return {"items": []}
            # Return unrelated native state deliberately: the production member
            # API must remove both mismatched scope and generic tool approvals.
            return {
                "items": [
                    self.pending,
                    {**self.pending, "session_key": "foreign-fixture-session"},
                    {
                        **self.pending,
                        "approval_id": "plugin:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "domain_approval_id": "generic-tool-with-no-domain-approval",
                    },
                ]
            }
        if operation != "resolve" or self.pending is None or self.waiting is None:
            raise MemberChatError("fixture_pending", "No fixture reply is pending")
        for field in ("session_key", "agent_id", "approval_id", "revision"):
            assert ticket[field] == self.pending[field]
        assert ticket["member_id"] == self.member_id
        assert ticket["decision"] in {"allow-once", "deny"}
        self.decisions.append(
            {key: ticket[key] for key in ("approval_id", "revision", "decision")}
        )
        self.waiting.set_result(ticket["decision"])
        self.pending = None
        return {"resolved": True}
