"""Least-privileged contract for the exact pinned OpenClaw chat seam."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from adminme.member_chat import MemberChatError, OpenClawGatewayChatTransport


class _PinnedGatewaySocket:
    """Protocol-v4 fixture enforcing the pinned session subscription boundary."""

    def __init__(self, session_key: str) -> None:
        self.session_key = session_key
        self.sent: list[dict[str, Any]] = []
        self._responses = 0
        self.subscribed = False
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        request = self.sent[-1]
        if request["method"] == "sessions.messages.subscribe":
            assert request["params"] == {"key": self.session_key}
            self.subscribed = True
            return json.dumps(
                {
                    "type": "res",
                    "id": request["id"],
                    "ok": True,
                    "payload": {"subscribed": True, "key": self.session_key},
                }
            )
        if request["method"] == "administrateme.conversation.prepare":
            return json.dumps(
                {
                    "type": "res",
                    "id": request["id"],
                    "ok": True,
                    "payload": {"accepted": True},
                }
            )
        assert self.subscribed, "Gateway withholds scoped chat without subscription"
        if self._responses == 0:
            self._responses += 1
            return json.dumps(
                {
                    "type": "res",
                    "id": request["id"],
                    "ok": True,
                    "payload": {"runId": "run-member-chat"},
                }
            )
        return json.dumps(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "sessionKey": self.session_key,
                    "runId": "run-member-chat",
                    "state": "final",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Ready."}],
                    },
                },
            }
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_chat_send_omits_every_admin_only_system_provenance_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal member chat must pass with only operator read/write scopes.

    In the exact pinned gateway source, ``chat-send-request.ts`` rejects all
    three fields below unless the connection has ``operator.admin``.  The
    portal deliberately has no such scope.
    """

    session_key = "agent:main:administrateme-portal-member"
    socket = _PinnedGatewaySocket(session_key)
    transport = OpenClawGatewayChatTransport(
        url="ws://127.0.0.1:18789",
        token="fixture-token",
    )

    async def _connect() -> _PinnedGatewaySocket:
        return socket

    monkeypatch.setattr(transport, "_connect", _connect)
    chunks = [
        chunk
        async for chunk in transport.stream(
            session_key=session_key,
            message="Add pick up medication for review",
            idempotency_key="member-chat-request-1",
        )
    ]

    assert [chunk.kind for chunk in chunks] == ["status", "delta", "final"]
    assert socket.sent[0]["method"] == "sessions.messages.subscribe"
    assert socket.closed
    request = socket.sent[1]
    assert request["method"] == "chat.send"
    params = request["params"]
    assert params == {
        "sessionKey": session_key,
        "message": "Add pick up medication for review",
        "deliver": False,
        "idempotencyKey": "member-chat-request-1",
    }
    assert {
        "systemInputProvenance",
        "systemProvenanceReceipt",
        "suppressCommandInterpretation",
    }.isdisjoint(params)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_session", ["member-session", "another-session"])
async def test_native_approval_extends_only_its_member_stream_deadline(
    monkeypatch: pytest.MonkeyPatch, pending_session: str
) -> None:
    """A bounded native decision window survives the normal 30-second silence."""

    class Socket(_PinnedGatewaySocket):
        timed_out = False

        async def recv(self) -> str:
            request = self.sent[-1]
            if request["method"].endswith(".list"):
                return json.dumps(
                    {
                        "type": "res",
                        "id": request["id"],
                        "ok": True,
                        "payload": {
                            "items": [
                                {
                                    "session_key": pending_session,
                                    "expires_at_ms": int(time.time() * 1000) + 60_000,
                                }
                            ]
                        },
                    }
                )
            if request["method"] == "chat.abort":
                return json.dumps(
                    {"type": "res", "id": request["id"], "ok": True, "payload": {}}
                )
            if (
                request["method"] == "chat.send"
                and self._responses
                and not self.timed_out
            ):
                self.timed_out = True
                raise TimeoutError
            return await super().recv()

    socket = Socket("member-session")
    transport = OpenClawGatewayChatTransport(
        url="ws://127.0.0.1:18789", token="fixture-token"
    )

    async def connect() -> Socket:
        return socket

    monkeypatch.setattr(transport, "_connect", connect)
    await transport.cos_approval_request(
        "administrateme.cosApprovals.list", {"session_key": "member-session"}
    )

    async def collect() -> list[str]:
        return [
            chunk.kind
            async for chunk in transport.stream(
                session_key="member-session",
                message="Please send the approved reply.",
                idempotency_key="reply-after-domain-approval",
            )
        ]

    if pending_session == "member-session":
        assert await collect() == ["status", "delta", "final"]
        assert not any(row["method"] == "chat.abort" for row in socket.sent)
    else:
        with pytest.raises(MemberChatError, match="timed out"):
            await collect()
        assert socket.sent[-1]["method"] == "chat.abort"
    assert transport._approval_deadlines == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{"subscribed": False}, {"subscribed": True, "key": "other"}]
)
async def test_unconfirmed_subscription_never_starts_or_aborts_a_member_run(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    class Socket(_PinnedGatewaySocket):
        async def recv(self) -> str:
            return json.dumps(
                {
                    "type": "res",
                    "id": self.sent[-1]["id"],
                    "ok": True,
                    "payload": payload,
                }
            )

    socket = Socket("member-session")
    transport = OpenClawGatewayChatTransport(
        url="ws://127.0.0.1:18789", token="fixture-token"
    )

    async def connect() -> Socket:
        return socket

    monkeypatch.setattr(transport, "_connect", connect)
    with pytest.raises(MemberChatError, match="subscription was not confirmed"):
        _ = [
            chunk
            async for chunk in transport.stream(
                session_key="member-session",
                message="A normal message",
                idempotency_key="subscription-refused",
            )
        ]
    assert [row["method"] for row in socket.sent] == ["sessions.messages.subscribe"]
    assert socket.closed
