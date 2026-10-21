"""Production member route settles only after its scoped Gateway subscription."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from adminme.member_chat import OpenClawGatewayChatTransport
from adminme.runtime.application import create_application
from tests.acceptance.test_gate7_household_experience import (
    ADULT,
    HEADERS,
    PRINCIPAL,
    _events,
    _settings,
)
from tests.unit.test_member_chat_protocol import _PinnedGatewaySocket


def test_authenticated_member_stream_subscribes_and_finishes_without_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        _, own_key = runtime.member_chat_binding(ADULT)
        _, other_key = runtime.member_chat_binding(PRINCIPAL)

        class Socket(_PinnedGatewaySocket):
            emitted_other = False

            async def recv(self) -> str:
                if self._responses and not self.emitted_other:
                    self.emitted_other = True
                    return json.dumps(
                        {
                            "type": "event",
                            "event": "chat",
                            "payload": {
                                "sessionKey": other_key,
                                "runId": "run-member-chat",
                                "state": "final",
                                "message": {
                                    "role": "assistant",
                                    "content": "Other private reply",
                                },
                            },
                        }
                    )
                return await super().recv()

        socket = Socket(own_key)
        transport = OpenClawGatewayChatTransport(
            url="ws://127.0.0.1:18789", token="fixture-token"
        )

        async def connect() -> Socket:
            return socket

        monkeypatch.setattr(transport, "_connect", connect)
        runtime.member_chat = transport
        before = client.portal.call(_events, runtime)
        response = client.post(
            "/api/member/v1/chat/stream",
            headers=HEADERS[ADULT],
            json={
                "message": "Show the current reply status.",
                "request_id": "subscribed-member-stream",
            },
        )
        assert response.status_code == 200
        frames = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [frame["kind"] for frame in frames] == ["status", "delta", "final"]
        assert frames[1]["text"] == "Ready."
        assert all(
            value not in response.text
            for value in (own_key, other_key, "Other private reply", "fixture-token")
        )
        assert socket.sent[0]["method"] == "sessions.messages.subscribe"
        assert socket.sent[0]["params"] == {"key": own_key}
        assert [row["method"] for row in socket.sent] == [
            "sessions.messages.subscribe",
            "chat.send",
        ]
        assert socket.closed
        assert client.portal.call(_events, runtime) == before
