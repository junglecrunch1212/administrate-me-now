"""Closed-enum defensive post-processing for note semantic extraction."""

from __future__ import annotations

from typing import Any


_KINDS = {
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "list",
    "list_item",
    "recurrence",
    "party_identifier",
    "party_candidate",
    "party_relationship",
    "relationship_fact",
}


def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
    del inputs, ctx
    if not isinstance(raw, dict):
        return {"candidates": [], "reasons": ["skill_post_process_non_dict_input"]}
    candidates = raw.get("candidates")
    cleaned: list[dict[str, Any]] = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("target_kind") not in _KINDS:
            continue
        quote = candidate.get("evidence_quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        cleaned.append(
            {
                "target_kind": candidate["target_kind"],
                "title": str(candidate.get("title") or "")[:1000],
                "candidate": (
                    dict(candidate.get("candidate") or {})
                    if isinstance(candidate.get("candidate"), dict)
                    else {}
                ),
                "evidence_quote": quote[:2000],
                "confidence": max(
                    0.0, min(1.0, float(candidate.get("confidence") or 0.0))
                ),
                "reasons": [
                    str(value)[:500] for value in candidate.get("reasons") or []
                ][:20],
            }
        )
    return {
        "candidates": cleaned[:20],
        "reasons": [str(value)[:500] for value in raw.get("reasons") or []][:20],
    }
