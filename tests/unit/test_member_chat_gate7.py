"""The member relay exposes text, never provider or tool envelopes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adminme.member_chat import _assistant_text, _safe_visible_text
from adminme.member_chat_approvals import approval_ticket
from tools.cos_reply_visual_fixture import CoSReplyVisualTransport


def test_chat_text_sanitizes_runtime_directives_and_tool_envelopes() -> None:
    value = (
        "Hello [[reply_to_current]] "
        '<tool_call>{"name":"administrateme_state","secret":"nope"}</tool_call>'
        "<|system|>hidden"
    )
    visible = _safe_visible_text(value)
    assert "Hello" in visible
    assert "reply_to" not in visible
    assert "tool_call" not in visible
    assert "secret" not in visible
    assert "system" not in visible


def test_chat_extracts_only_rendered_assistant_text() -> None:
    message = {
        "role": "assistant",
        "provider": "must-not-escape",
        "usage": {"tokens": 99},
        "content": [
            {"type": "thinking", "text": "hidden reasoning"},
            {"type": "text", "text": "A safe visible answer."},
            {"type": "tool_call", "text": "hidden tool"},
        ],
    }
    assert _assistant_text(message) == "A safe visible answer."
    assert _assistant_text({"role": "system", "content": "hidden"}) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instruction_ticket", [None, {"nonce": "synthetic-instruction"}]
)
async def test_cos_browser_transport_accepts_current_protocol_without_granting_sending(
    instruction_ticket,
):
    member = "fixture-browser-member"
    session = "fixture-browser-session"
    approval_id = "fixture-domain-approval"
    secret = "synthetic-browser-approval-secret"
    runtime = SimpleNamespace(
        event_log=SimpleNamespace(
            get=AsyncMock(
                return_value={
                    "type": "cos.response.approved",
                    "owner_scope": f"private:{member}",
                }
            )
        ),
        member_chat_binding=lambda member_id: (member_id, session),
        settings=SimpleNamespace(openclaw_shared_secret=secret),
    )
    transport = CoSReplyVisualTransport(
        runtime, member, lambda: datetime.now(timezone.utc)
    )
    stream = transport.stream(
        session_key=session,
        message=f"Please use administrateme_cos_response to request sending approval for approved reply {approval_id}.",
        idempotency_key="fixture-browser-request",
        instruction_ticket=instruction_ticket,
    )
    assert (await anext(stream)).kind == "status"
    runtime.event_log.get.assert_awaited_once_with(approval_id)
    assert transport.phase == "pending"
    assert transport.pending is not None and transport.waiting is not None
    assert not transport.waiting.done()
    assert transport.decisions == transport.completed == []
    pending = transport.pending
    ticket = approval_ticket(
        secret,
        operation="resolve",
        session_key=session,
        agent_id=pending["agent_id"],
        member_id=member,
        approval_id=pending["approval_id"],
        revision=pending["revision"],
        decision="deny",
    )
    assert await transport.cos_approval_request(
        "administrateme.cosApprovals.resolve", ticket
    ) == {"resolved": True}
    chunks = [chunk async for chunk in stream]
    assert [chunk.kind for chunk in chunks] == ["delta", "final"]
    assert chunks[0].text == "Sending declined. No provider was contacted."
    assert transport.completed == ["deny"]
    assert transport.phase == "closed"
    assert transport.pending is None and transport.waiting is None
    # Ordinary fixture chat must accept the same keyword through its fallback.
    ordinary = [
        chunk
        async for chunk in transport.stream(
            session_key=session,
            message="A separate ordinary fixture question",
            idempotency_key="fixture-browser-ordinary",
            instruction_ticket=instruction_ticket,
        )
    ]
    assert ordinary[-1].kind == "final"
    assert transport.completed == ["deny"]
