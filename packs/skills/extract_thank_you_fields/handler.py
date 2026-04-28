"""Post-processing for extract_thank_you_fields.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model returns a non-dict shape or an unknown
``urgency``, coerce to ``urgency="no_rush"`` with confidence 0.0 so the
downstream pipeline emits ``commitment.suppressed`` with reason
``skill_failure_defensive_default`` rather than letting output_invalid
fire and fail the bus.
"""

from typing import Any

_VALID_URGENCIES = {"today", "this_week", "this_month", "no_rush"}


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "recipient_party_id": "",
            "suggested_text": "",
            "urgency": "no_rush",
            "confidence": 0.0,
            "reasons": ["skill_post_process_non_dict_input"],
        }
    urgency = raw.get("urgency")
    if urgency not in _VALID_URGENCIES:
        return {
            **raw,
            "urgency": "no_rush",
            "confidence": 0.0,
            "reasons": list(raw.get("reasons", []))
            + [f"rejected_urgency:{urgency!r}"],
        }
    return raw
