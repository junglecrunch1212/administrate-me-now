"""Post-processing for compose_reminder.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model returns a non-dict shape or omits the
required fields, coerce to the on_failure shape so the downstream
``reminder_dispatch`` pipeline emits ``reminder.dispatched`` on the
sentinel path rather than letting ``output_invalid`` halt the bus per
[§7.7].
"""

from typing import Any

_ALLOWED_CHANNELS = frozenset({"imessage", "sms", "email", "push"})


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
            "tone_note": "skill_post_process_non_dict_input",
        }
    composed_text = raw.get("composed_text")
    suggested_channel = raw.get("suggested_channel")
    if not isinstance(composed_text, str) or not isinstance(suggested_channel, str):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
            "tone_note": "skill_post_process_shape_invalid",
        }
    tone_note = raw.get("tone_note")
    if suggested_channel not in _ALLOWED_CHANNELS:
        coerced_note = f"channel_coerced_from_{suggested_channel}"
        return {
            "composed_text": composed_text,
            "suggested_channel": "imessage",
            "tone_note": coerced_note if not isinstance(tone_note, str) else f"{tone_note}; {coerced_note}",
        }
    out: dict[str, Any] = {
        "composed_text": composed_text,
        "suggested_channel": suggested_channel,
    }
    if isinstance(tone_note, str):
        out["tone_note"] = tone_note
    return out
