"""Post-processing for compose_morning_digest.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model returns a non-dict shape or omits the
required fields, coerce to the on_failure shape so the downstream
``morning_digest`` pipeline emits ``digest.composed`` on the sentinel
path rather than letting ``output_invalid`` halt the bus per [§7.7].
"""

from typing import Any


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "body_text": "",
            "claimed_event_ids": [],
            "validation_failed": True,
            "reasons": ["skill_post_process_non_dict_input"],
        }
    body_text = raw.get("body_text")
    claimed = raw.get("claimed_event_ids")
    if not isinstance(body_text, str) or not isinstance(claimed, list):
        return {
            "body_text": "",
            "claimed_event_ids": [],
            "validation_failed": True,
            "reasons": list(raw.get("reasons", []))
            + ["skill_post_process_shape_invalid"],
        }
    return {
        "body_text": body_text,
        "claimed_event_ids": [str(x) for x in claimed],
        "validation_failed": bool(raw.get("validation_failed", False)),
        "reasons": list(raw.get("reasons", [])),
    }
