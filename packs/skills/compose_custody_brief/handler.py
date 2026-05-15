"""Post-processing for compose_custody_brief.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model returns a non-dict shape or omits the
required fields, coerce to the on_failure shape so the downstream
``custody_brief`` pipeline skips the row rather than letting
``output_invalid`` halt the bus per [§7.7].
"""

from typing import Any

_ALLOWED_CHANNELS = frozenset({"imessage", "sms", "email", "push"})


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
        }
    composed_text = raw.get("composed_text")
    suggested_channel = raw.get("suggested_channel")
    if not isinstance(composed_text, str) or not isinstance(suggested_channel, str):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
        }
    if suggested_channel not in _ALLOWED_CHANNELS:
        suggested_channel = "imessage"
    return {
        "composed_text": composed_text,
        "suggested_channel": suggested_channel,
    }
