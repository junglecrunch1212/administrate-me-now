"""Post-processing for compose_reminder.

Defensive default: if the model returns a non-dict shape, omits
``composed_text``, or names an unknown channel, coerce per [§7.7] so
the downstream ``reminder_dispatch`` pipeline still emits
``reminder.dispatched`` (with composed_text="" on the hardest fallback)
rather than letting ``output_invalid`` halt the bus.
"""

from typing import Any

_ALLOWED_CHANNELS = {"imessage", "sms", "email", "push"}


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
            "tone_note": "skill_post_process_non_dict_input",
        }
    composed_text = raw.get("composed_text")
    suggested_channel = raw.get("suggested_channel")
    if not isinstance(composed_text, str):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
            "tone_note": "skill_post_process_shape_invalid",
        }
    tone_note = raw.get("tone_note")
    if not isinstance(suggested_channel, str) or suggested_channel not in _ALLOWED_CHANNELS:
        coercion_note = (
            f"channel_coerced_from={suggested_channel!r}"
            if suggested_channel is not None
            else "channel_coerced_from_missing"
        )
        suggested_channel = "imessage"
        tone_note = (
            f"{tone_note}; {coercion_note}" if isinstance(tone_note, str) else coercion_note
        )
    out: dict[str, Any] = {
        "composed_text": composed_text,
        "suggested_channel": suggested_channel,
    }
    if isinstance(tone_note, str):
        out["tone_note"] = tone_note
    return out
