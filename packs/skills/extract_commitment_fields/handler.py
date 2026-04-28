"""Post-processing for extract_commitment_fields.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model returns a non-dict shape or an unknown
``kind``, coerce to ``kind="other"`` with confidence 0.0 so the
downstream pipeline emits ``commitment.suppressed`` with reason
``skill_failure_defensive_default`` rather than letting output_invalid
fire and fail the bus.
"""

from typing import Any

_VALID_KINDS = {
    "reply",
    "task",
    "appointment",
    "payment",
    "document_return",
    "visit",
    "other",
}


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "kind": "other",
            "confidence": 0.0,
            "reasons": ["skill_post_process_non_dict_input"],
        }
    kind = raw.get("kind")
    if kind not in _VALID_KINDS:
        return {
            "kind": "other",
            "confidence": 0.0,
            "reasons": list(raw.get("reasons", []))
            + [f"rejected_kind:{kind!r}"],
        }
    return raw
