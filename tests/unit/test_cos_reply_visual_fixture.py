"""The browser fixture accepts conversation provenance without granting a send."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adminme.member_chat import DeterministicOpenClawChatTransport
from tools.cos_reply_visual_fixture import CoSReplyVisualTransport

TICKET = {"source_event_id": "fixture-conversation", "nonce": "fixture-private-nonce"}
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("ticket", [None, TICKET])
async def test_visual_transport_forwards_general_conversation_ticket(
    monkeypatch, ticket
):
    original = DeterministicOpenClawChatTransport.stream
    received = []

    async def observed(self, **kwargs):
        received.append(kwargs["instruction_ticket"])
        async for chunk in original(self, **kwargs):
            yield chunk

    monkeypatch.setattr(DeterministicOpenClawChatTransport, "stream", observed)
    transport = CoSReplyVisualTransport(
        SimpleNamespace(), "fixture-member", lambda: NOW
    )
    chunks = [
        chunk
        async for chunk in transport.stream(
            session_key="fixture-session",
            message="Help me choose a small step.",
            idempotency_key="fixture-chat",
            instruction_ticket=ticket,
        )
    ]
    assert received == [ticket] and received[0] is ticket
    assert [chunk.kind for chunk in chunks] == ["status", "delta", "final"]
    history = await transport.history(session_key="fixture-session", limit=10)
    assert [row["role"] for row in history] == ["user", "assistant"]
    assert "fixture-private-nonce" not in str(history)
    assert transport.pending is None and transport.decisions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ticket", [None, TICKET])
async def test_visual_reply_ticket_does_not_replace_independent_approval(ticket):
    event_log = SimpleNamespace(
        get=AsyncMock(
            return_value={
                "type": "cos.response.approved",
                "owner_scope": "private:fixture-member",
            }
        )
    )
    runtime = SimpleNamespace(
        event_log=event_log,
        member_chat_binding=lambda member: ("fixture-agent", "fixture-session"),
    )
    transport = CoSReplyVisualTransport(runtime, "fixture-member", lambda: NOW)
    stream = transport.stream(
        session_key="fixture-session",
        message="Please use administrateme_cos_response to request sending approval "
        "for approved reply fixture-domain-approval.",
        idempotency_key="fixture-send-request",
        instruction_ticket=ticket,
    )
    next_chunk = None
    try:
        chunk = await anext(stream)
        assert chunk.kind == "status" and chunk.phase == "starting_model"
        next_chunk = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        event_log.get.assert_awaited_once_with("fixture-domain-approval")
        assert transport.pending["domain_approval_id"] == "fixture-domain-approval"
        assert transport.pending["session_key"] == "fixture-session"
        assert transport.waiting is not None and not transport.waiting.done()
        assert not next_chunk.done()
        assert transport.decisions == [] and transport.completed == []
        assert await transport.history(session_key="fixture-session", limit=10) == []
    finally:
        if next_chunk is not None:
            next_chunk.cancel()
            await asyncio.gather(next_chunk, return_exceptions=True)
        await stream.aclose()
    assert transport.phase == "closed" and transport.pending is None
