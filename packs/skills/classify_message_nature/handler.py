"""Post-processing for classify_message_nature.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model omits or returns an unknown classification
(out of the closed enum), coerce to ``personal`` with confidence 0.0
rather than letting output_invalid fire downstream — mis-classifying a
real message as noise (and dropping it from the inbox) is a worse failure
than mis-classifying noise as personal.
"""

from typing import Any

_VALID = {"noise", "transactional", "personal", "professional", "promotional"}


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "classification": "personal",
            "confidence": 0.0,
            "reasons": ["skill_post_process_non_dict_input"],
        }
    classification = raw.get("classification")
    if classification not in _VALID:
        return {
            "classification": "personal",
            "confidence": 0.0,
            "reasons": list(raw.get("reasons", []))
            + [f"rejected_classification:{classification!r}"],
        }
    return raw
