"""Post-processing for classify_thank_you_candidate.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Safety net: if the model returns is_candidate=true without urgency or
suggested_medium (which the schema requires when is_candidate=true), coerce
to is_candidate=false rather than letting output_invalid fire downstream
(the wrapper's `on_failure` defensive-default lookup is broader than this
case; the handler narrows it to the specific known-flaky path).

Per [REFERENCE_EXAMPLES.md §3 lines 1389-1395].
"""

from typing import Any


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return {
            "is_candidate": False,
            "confidence": 0.0,
            "reasons": ["skill_post_process_non_dict_input"],
        }
    if raw.get("is_candidate") is True and not raw.get("urgency"):
        return {
            "is_candidate": False,
            "confidence": float(raw.get("confidence", 0.0)),
            "reasons": list(raw.get("reasons", [])) + ["missing_urgency"],
        }
    return raw
