"""Deterministic presentation state for one Triage candidate.

These helpers classify only a derived proposal. They do not admit source facts,
authorize confirmation, suppress candidates, or perform provider work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def proposal_source_id(
    *,
    source_kind: str,
    candidate: Mapping[str, Any],
    eligibility_rule_ids: Sequence[str],
) -> str:
    """Return a stable reviewed source coordinate for inventory filtering."""

    prefix = "candidate_policy_v1:"
    for raw_rule in eligibility_rule_ids:
        rule = str(raw_rule)
        if not rule.startswith(prefix):
            continue
        coordinate = rule.removeprefix(prefix).rsplit(":", 1)[0]
        if coordinate.startswith(("adapter:", "surface:")):
            return coordinate
    if source_kind == "member_intent":
        surface = str(candidate.get("source_surface") or "triage").strip()
        return f"surface:{surface}"
    return f"origin:{source_kind}"


def triage_review_state(
    item: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any] | None = None,
    route: Mapping[str, Any] | None = None,
    owed_to_party_available: bool | None = None,
) -> dict[str, Any]:
    """Explain whether a proposal has the typed fields needed for review.

    A `ready` result is presentation readiness only. Every approval still
    traverses domain governance and the exact settlement contract.
    Member readers supply scoped Party availability; deterministic projection
    callers can assess field completeness without reading another projection.
    """

    candidate = candidate if candidate is not None else item.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    route = route if route is not None else item.get("route")
    route = route if isinstance(route, Mapping) else {}
    target = str(item.get("target_kind") or "")
    missing: list[str] = []
    if item.get("state") == "pending_interpretation" or not target:
        return {
            "contract": "triage-review-state-v2",
            "state": "needs_interpretation",
            "missing": ["kind"],
            "human_gate_required": True,
            "approval_authorized": False,
        }
    if not str(item.get("title") or "").strip():
        missing.append("title")
    if not route.get("member_id"):
        missing.append("member")
    if target == "commitment" and (
        not candidate.get("owed_to_party_id") or owed_to_party_available is False
    ):
        missing.append("owed_to_party")
    if target == "calendar_event":
        if not candidate.get("start"):
            missing.append("start")
        if not candidate.get("end"):
            missing.append("end")
    if target == "recurrence":
        if not candidate.get("rrule"):
            missing.append("rrule")
        if not candidate.get("next_occurrence"):
            missing.append("next_occurrence")
    if target == "party_identifier":
        for field in (
            "surviving_party_id",
            "candidate_kind",
            "candidate_value",
            "candidate_value_normalized",
        ):
            if not candidate.get(field):
                missing.append(field)
    if target == "party_candidate":
        if not candidate.get("display_name") and not item.get("title"):
            missing.append("display_name")
        if candidate.get("kind") not in {"person", "organization"}:
            missing.append("person_or_organization")
    if target == "party_relationship":
        for field in ("party_a", "party_b", "label", "direction"):
            if not candidate.get(field):
                missing.append(field)
    if target == "relationship_fact":
        for field in ("party_id", "kind", "value"):
            if not candidate.get(field):
                missing.append(field)
    if target in {"calendar_event", "reminder", "list", "list_item"}:
        if not route.get("provider"):
            missing.append("provider")
        if not route.get("provider_account_id"):
            missing.append("account")
        if target != "list" and not route.get("container_external_id"):
            missing.append("destination")
    return {
        "contract": "triage-review-state-v2",
        "state": "ready" if not missing else "needs_details",
        "missing": missing,
        "human_gate_required": True,
        "approval_authorized": False,
    }


__all__ = ["proposal_source_id", "triage_review_state"]
