"""Least-privileged contract for the exact pinned OpenClaw chat seam."""

from __future__ import annotations

import json
from typing import Any

import pytest

from adminme.member_chat import OpenClawGatewayChatTransport


class _PinnedGatewaySocket:
    """Protocol-v4 fixture enforcing OpenClaw commit 944cf08's P0 rule."""

    def __init__(self, session_key: str) -> None:
        self.session_key = session_key
        self.sent: list[dict[str, Any]] = []
        self._responses = 0

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        request = self.sent[-1]
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
        return None


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
    request = socket.sent[0]
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
