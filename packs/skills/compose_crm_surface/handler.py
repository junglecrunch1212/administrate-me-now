"""Post-processing for compose_crm_surface.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model returns a non-dict shape or omits the
required fields, coerce to the on_failure shape so the downstream
``crm_surface`` pipeline emits ``crm.surfaced`` on the sentinel path
rather than letting ``output_invalid`` halt the bus per [§7.7].
"""

from typing import Any

_ALLOWED_CHANNELS = {"imessage", "sms", "email", "push"}
_MAX_LEN = 1000


def _failure(reasons: list[str]) -> dict:
    return {
        "composed_text": "",
        "suggested_channel": "imessage",
        "validation_failed": True,
        "reasons": reasons,
    }


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return _failure(["skill_post_process_non_dict_input"])
    composed = raw.get("composed_text")
    channel = raw.get("suggested_channel")
    if not isinstance(composed, str) or not isinstance(channel, str):
        return _failure(
            list(raw.get("reasons", [])) + ["skill_post_process_shape_invalid"]
        )
    if channel not in _ALLOWED_CHANNELS:
        return _failure(
            list(raw.get("reasons", [])) + ["skill_post_process_unknown_channel"]
        )
    if len(composed) > _MAX_LEN:
        composed = composed[:_MAX_LEN]
    return {
        "composed_text": composed,
        "suggested_channel": channel,
        "validation_failed": bool(raw.get("validation_failed", False)),
        "reasons": list(raw.get("reasons", [])),
    }
