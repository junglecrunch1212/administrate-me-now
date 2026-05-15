"""Post-processing for compose_crm_surface.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model returns a non-dict shape or omits the
required fields, coerce to the on_failure shape so the downstream
``crm_surface`` pipeline skips the member row rather than letting
``output_invalid`` halt the bus per [§7.7].
"""

from typing import Any

_ALLOWED_CHANNELS = frozenset({"imessage", "sms", "email", "push"})


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
            "sort_order": [],
        }
    composed_text = raw.get("composed_text")
    suggested_channel = raw.get("suggested_channel")
    sort_order = raw.get("sort_order")
    if (
        not isinstance(composed_text, str)
        or not isinstance(suggested_channel, str)
        or not isinstance(sort_order, list)
    ):
        return {
            "composed_text": "",
            "suggested_channel": "imessage",
            "sort_order": [],
        }
    if suggested_channel not in _ALLOWED_CHANNELS:
        suggested_channel = "imessage"
    return {
        "composed_text": composed_text,
        "suggested_channel": suggested_channel,
        "sort_order": [str(x) for x in sort_order],
    }
