"""The member relay exposes text, never provider or tool envelopes."""

from adminme.member_chat import _assistant_text, _safe_visible_text


def test_chat_text_sanitizes_runtime_directives_and_tool_envelopes() -> None:
    value = (
        "Hello [[reply_to_current]] "
        "<tool_call>{\"name\":\"administrateme_state\",\"secret\":\"nope\"}</tool_call>"
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
