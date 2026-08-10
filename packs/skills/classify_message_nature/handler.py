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
    "party_candidate",
    "party_relationship",
    "relationship_fact",
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
    context = inputs.get("context_pack") if isinstance(inputs, dict) else None
    context = context if isinstance(context, dict) else {}
    messages = {
        str(message.get("source_id")): message
        for message in context.get("messages") or []
        if isinstance(message, dict)
    }
    current_source_id = str(context.get("current_source_id") or "m01")
    candidates = raw.get("candidates")
    normalized_candidates: list[dict[str, Any]] = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if (
            not isinstance(candidate, dict)
            or candidate.get("target_kind") not in _CANDIDATE_KINDS
            or not isinstance(candidate.get("evidence_quote"), str)
            or not candidate["evidence_quote"].strip()
        ):
            continue
        value = dict(candidate)
        source_id = str(value.get("evidence_source_id") or current_source_id)
        if messages and source_id not in messages:
            continue
        value["evidence_source_id"] = source_id
        value["action_owner"] = (
            value.get("action_owner")
            if value.get("action_owner")
            in {"member", "group_including_member", "other_party", "unknown"}
            else "unknown"
        )
        value["lifecycle_state"] = (
            value.get("lifecycle_state")
            if value.get("lifecycle_state")
            in {"open", "answered", "completed", "superseded", "unknown"}
            else "unknown"
        )
        value["compact_summary"] = str(
            value.get("compact_summary")
            or value.get("title")
            or value["evidence_quote"]
        )[:160]
        for key, limit in (
            ("case_summary", 600),
            ("why_member", 240),
            ("next_step", 240),
        ):
            item = value.get(key)
            value[key] = str(item)[:limit] if item else None
        value["thread_state"] = (
            value.get("thread_state")
            if value.get("thread_state")
            in {
                "new",
                "in_progress",
                "waiting_on_member",
                "waiting_on_other",
                "blocked",
                "unclear",
            }
            else "unclear"
        )
        value["unresolved_questions"] = [
            str(item)[:240]
            for item in value.get("unresolved_questions") or []
            if isinstance(item, str) and item.strip()
        ][:8]
        value["related_source_ids"] = [
            str(item)
            for item in value.get("related_source_ids") or []
            if isinstance(item, str) and (not messages or item in messages)
        ][:10]
        normalized_candidates.append(value)
    result["candidates"] = normalized_candidates[:20]
    return result
