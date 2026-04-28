"""Post-processing for classify_commitment_candidate.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model omits ``is_candidate`` or returns a
non-bool / non-dict shape, coerce to ``is_candidate: False`` so the
downstream pipeline emits ``commitment.suppressed`` with reason
``skill_failure_defensive_default`` rather than letting output_invalid
fire later in the chain.
"""

from typing import Any


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "is_candidate": False,
            "confidence": 0.0,
            "reasons": ["skill_post_process_non_dict_input"],
        }
    is_candidate = raw.get("is_candidate")
    if not isinstance(is_candidate, bool):
        return {
            "is_candidate": False,
            "confidence": float(raw.get("confidence", 0.0)),
            "reasons": list(raw.get("reasons", []))
            + [f"rejected_is_candidate:{is_candidate!r}"],
        }
    return raw
