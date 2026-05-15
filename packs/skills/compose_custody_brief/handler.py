"""Post-processing for compose_custody_brief.

Defensive default: if the model returns a non-dict shape, omits
``composed_text``, or names an unknown channel, coerce per [§7.7] so
the downstream ``custody_brief`` pipeline still emits
``custody_brief.composed`` (with composed_text="" on the hardest fallback)
rather than letting ``output_invalid`` halt the bus.
"""

from typing import Any

_ALLOWED_CHANNELS = {"imessage", "sms", "email", "push"}


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
        }
    composed_text = raw.get("composed_text")
    suggested_channel = raw.get("suggested_channel")
    if not isinstance(composed_text, str):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
        }
    if not isinstance(suggested_channel, str) or suggested_channel not in _ALLOWED_CHANNELS:
        suggested_channel = "imessage"
    return {
        "composed_text": composed_text,
        "suggested_channel": suggested_channel,
    }
