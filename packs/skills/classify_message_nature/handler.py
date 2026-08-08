"""Post-processing for classify_message_nature.

The wrapper validates input, calls OpenClaw, parses the response envelope,
then passes raw model output to this function. The function returns a dict
the wrapper validates against output.schema.json.

Defensive default: if the model omits or returns an unknown classification
(out of the closed enum), coerce to ``personal`` with confidence 0.0
rather than letting output_invalid fire downstream — mis-classifying a
real message as noise (and dropping it from interaction history) is a worse failure
than mis-classifying noise as personal.
"""

from typing import Any

_VALID = {"noise", "transactional", "personal", "professional", "promotional"}
_CANDIDATE_KINDS = {
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "recurrence",
    "list",
    "list_item",
    "party_identifier",
}


def _defensive(reasons: list[str]) -> dict:
    return {
        "classification": "personal",
        "confidence": 0.0,
        "reasons": reasons,
        "reply_signal": "unknown",
        "automated": False,
        "transactional_due": False,
        "security_code": False,
        "person_to_person": False,
        "deadline": None,
        "candidates": [],
    }


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    if not isinstance(raw, dict):
        return _defensive(["skill_post_process_non_dict_input"])
    classification = raw.get("classification")
    if classification not in _VALID:
        return _defensive(
            list(raw.get("reasons", []))
            + [f"rejected_classification:{classification!r}"]
        )
    result = dict(raw)
    result["reply_signal"] = (
        raw.get("reply_signal")
        if raw.get("reply_signal") in {"required", "not_required", "unknown"}
        else "unknown"
    )
    for key in (
        "automated",
        "transactional_due",
        "security_code",
        "person_to_person",
    ):
        result[key] = bool(raw.get(key, False))
    result["deadline"] = (
        raw.get("deadline") if isinstance(raw.get("deadline"), str) else None
    )
    candidates = raw.get("candidates")
    result["candidates"] = [
        candidate
        for candidate in (candidates if isinstance(candidates, list) else [])
        if isinstance(candidate, dict)
        and candidate.get("target_kind") in _CANDIDATE_KINDS
        and isinstance(candidate.get("evidence_quote"), str)
        and bool(candidate["evidence_quote"].strip())
    ]
    return result
