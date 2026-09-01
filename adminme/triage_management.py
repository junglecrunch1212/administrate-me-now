"""Deterministic human-centered Triage management and preview helpers.

The helpers in this module are deliberately pure.  They classify already
authorized Triage candidates and reconciliation receipts; they do not append
events, mutate projections, call a model, or contact a provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal


HumanTriageLane = Literal[
    "direct_instruction",
    "member_promise",
    "known_human_request",
    "scheduled_deadline",
    "waiting_follow_up",
    "clarification_conflict",
    "alerts_safety",
    "low_confidence_review",
]

HUMAN_TRIAGE_LANE_ORDER: dict[str, int] = {
    "direct_instruction": 0,
    "member_promise": 1,
    "known_human_request": 2,
    "scheduled_deadline": 3,
    "waiting_follow_up": 4,
    "clarification_conflict": 5,
    "alerts_safety": 6,
    "low_confidence_review": 7,
}

ORDINARY_ADMISSION_DECISIONS = frozenset({"admit", "conflict_review", "unresolved"})
CONTEXT_ADMISSION_DECISIONS = frozenset(
    {"update_existing_matter", "attach_evidence", "context_only"}
)
HANDLED_ADMISSION_DECISIONS = frozenset(
    {"already_satisfied", "superseded", "duplicate", "source_policy_suppression"}
)
NON_OBLIGATION_PROVIDER_CATEGORIES = frozenset(
    {"promotions", "social", "updates", "spam", "forums"}
)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reconciliation_value(
    item: Mapping[str, Any],
    reconciliation: Mapping[str, Any] | None,
    key: str,
    default: Any,
) -> Any:
    if reconciliation is not None and key in reconciliation:
        return reconciliation[key]
    public = _mapping(item.get("reconciliation"))
    if key in public:
        return public[key]
    public_aliases = {
        "reason_codes": "reasons",
        "evidence_source_event_ids": "evidence",
        "source_coordinate": "scope",
    }
    alias = public_aliases.get(key)
    if alias is not None and alias in public:
        return public[alias]
    columns = {
        "decision": "admission_decision",
        "priority_basis": "priority_basis_json",
        "reason_codes": "reconciliation_reasons_json",
        "evidence_source_event_ids": "reconciliation_evidence_json",
        "source_coordinate": "reconciliation_scope_json",
    }
    value = item.get(columns.get(key, key), default)
    if key in {"priority_basis", "reason_codes", "evidence_source_event_ids"}:
        return _json_list(value)
    if key == "source_coordinate" and isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, json.JSONDecodeError):
            return default
        return parsed if isinstance(parsed, Mapping) else default
    return value


def human_triage_lane(
    item: Mapping[str, Any],
    reconciliation: Mapping[str, Any] | None = None,
) -> HumanTriageLane:
    """Return one stable human lane from typed admission and candidate state."""

    decision = str(_reconciliation_value(item, reconciliation, "decision", "admit"))
    basis = {
        str(value)
        for value in _reconciliation_value(
            item, reconciliation, "priority_basis", []
        )
    }
    if decision == "alert_only":
        return "alerts_safety"
    if "explicit_member_instruction" in basis:
        return "direct_instruction"
    if "exact_member_commitment" in basis or item.get("target_kind") == "commitment":
        return "member_promise"
    if "known_human_actionable_request" in basis:
        return "known_human_request"
    if "verified_deadline" in basis:
        return "scheduled_deadline"
    interpretation = _mapping(item.get("interpretation"))
    candidate = _mapping(item.get("candidate"))
    if interpretation.get("thread_state") in {"waiting_on_member", "waiting_on_other"}:
        return "waiting_follow_up"
    if any(
        candidate.get(field) not in (None, "", [])
        for field in (
            "waiting_for",
            "follow_up_at",
            "follow_up_on",
            "next_contact_at",
        )
    ):
        return "waiting_follow_up"
    if decision in {"conflict_review", "unresolved"}:
        return "clarification_conflict"
    if interpretation.get("thread_state") in {"blocked", "unclear"}:
        return "clarification_conflict"
    if any(
        candidate.get(field) not in (None, "", [])
        for field in (
            "due",
            "due_at",
            "due_date",
            "deadline",
            "deadline_at",
            "start",
            "start_at",
        )
    ):
        return "scheduled_deadline"
    return "low_confidence_review"


def proposed_disposition(
    item: Mapping[str, Any],
    reconciliation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain the proposed member-facing disposition without mutating state."""

    decision = str(_reconciliation_value(item, reconciliation, "decision", "admit"))
    if decision in ORDINARY_ADMISSION_DECISIONS:
        disposition = "ordinary_queue"
    elif decision == "alert_only":
        disposition = "alerts"
    elif decision in CONTEXT_ADMISSION_DECISIONS:
        disposition = "context"
    elif decision in HANDLED_ADMISSION_DECISIONS:
        disposition = "handled"
    else:
        disposition = "clarification"
    return {
        "disposition": disposition,
        "ordinary_queue": disposition == "ordinary_queue",
        "lane": human_triage_lane(item, reconciliation),
        "admission_decision": decision,
        "reason_codes": [
            str(value)
            for value in _reconciliation_value(
                item, reconciliation, "reason_codes", []
            )
        ],
        "evidence_event_ids": [
            str(value)
            for value in _reconciliation_value(
                item, reconciliation, "evidence_source_event_ids", []
            )
        ],
        "source_coordinate": dict(
            _mapping(
                _reconciliation_value(
                    item, reconciliation, "source_coordinate", {}
                )
            )
        ),
    }


def current_disposition(item: Mapping[str, Any]) -> dict[str, Any]:
    """Describe current projected behavior before a reassessment is applied."""

    state = str(item.get("state") or "")
    management_state = str(item.get("management_state") or "open")
    if management_state == "context":
        disposition = "context"
    elif management_state == "alert":
        disposition = "alerts"
    elif management_state in {
        "linked_existing",
        "handled",
        "duplicate",
        "dismissed_not_useful",
        "snoozed",
        "ownership_corrected",
    }:
        disposition = "handled"
    elif state in {"pending_interpretation", "pending_review"}:
        disposition = (
            "ordinary_queue"
            if item.get("decision_disposition", "decision") == "decision"
            else "context"
        )
    else:
        disposition = "handled"
    return {
        "disposition": disposition,
        "ordinary_queue": disposition == "ordinary_queue",
        "lane": str(item.get("human_lane") or item.get("queue_lane") or "low_confidence_review"),
        "admission_decision": str(item.get("admission_decision") or "admit"),
    }


def ordinary_queue_eligible(item: Mapping[str, Any]) -> bool:
    """Return whether active G04 semantics admit one ordinary obligation."""

    if item.get("state") not in {"pending_interpretation", "pending_review"}:
        return False
    if item.get("management_state", "open") not in {
        "open",
        "clarification",
        "suppression_proposed",
    }:
        return False
    return proposed_disposition(item)["ordinary_queue"]


def _cursor_encode(index: int, fingerprint: str) -> str:
    raw = json.dumps([index, fingerprint], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str | None, fingerprint: str) -> int:
    if cursor is None:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        index, bound = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid backlog preview cursor") from exc
    if not isinstance(index, int) or index < 0 or bound != fingerprint:
        raise ValueError("stale backlog preview cursor")
    return index


def backlog_reassessment_preview(
    items: Sequence[Mapping[str, Any]],
    reconciliations: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Build one bounded, replay-stable, non-mutating reassessment page."""

    limit = max(1, min(limit, 100))
    ordered = sorted(
        items,
        key=lambda value: (
            str(value.get("source_id") or ""),
            str(value.get("proposed_at") or ""),
            str(value.get("proposal_id") or ""),
        ),
    )
    inventory_fingerprint = hashlib.sha256(
        json.dumps(
            [
                [
                    value.get("proposal_id"),
                    value.get("last_event_id"),
                    reconciliations.get(str(value.get("proposal_id")), {}).get(
                        "last_event_id"
                    ),
                ]
                for value in ordered
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    start = _cursor_decode(cursor, inventory_fingerprint)
    page = ordered[start : start + limit]
    changes: list[dict[str, Any]] = []
    for item in page:
        proposal_id = str(item.get("proposal_id") or "")
        receipt = reconciliations.get(proposal_id)
        old = current_disposition(item)
        proposed = proposed_disposition(item, receipt)
        coordinate = proposed["source_coordinate"]
        grouping_rule = str(proposed["admission_decision"])
        coordinate_key = hashlib.sha256(
            json.dumps(coordinate, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        changes.append(
            {
                "proposal_id": proposal_id,
                "source_event_id": item.get("source_event_id"),
                "old": old,
                "proposed": proposed,
                "changed": old["disposition"] != proposed["disposition"]
                or old["lane"] != proposed["lane"],
                "group": {
                    "deterministic_rule": grouping_rule,
                    "source_coordinate_digest": coordinate_key,
                    "source_coordinate": coordinate,
                },
                "reconciliation_event_id": (
                    receipt.get("last_event_id") if receipt else None
                ),
            }
        )
    end = start + len(page)
    preview_digest = hashlib.sha256(
        json.dumps(changes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "contract": "triage-backlog-reassessment-preview-v1",
        "preview_digest": preview_digest,
        "inventory_fingerprint": inventory_fingerprint,
        "changes": changes,
        "batch": {
            "offset": start,
            "count": len(changes),
            "limit": limit,
            "total": len(ordered),
        },
        "has_more": end < len(ordered),
        "next_cursor": (
            _cursor_encode(end, inventory_fingerprint) if end < len(ordered) else None
        ),
        "mutates_backlog": False,
        "source_events_deleted": 0,
        "provider_effects": 0,
        "application_available_in_phase_a": False,
    }


_INSTRUCTION_DIFFS: dict[str, tuple[str, str]] = {
    "always": ("retain_current", "Retain only the already-authorized route."),
    "usually": (
        "low_confidence_review",
        "Keep matching authorized observations reviewable but below ordinary obligations.",
    ),
    "ask": (
        "clarification_required",
        "Require member clarification before ordinary-queue admission.",
    ),
    "never": (
        "context_only",
        "Keep permitted context while denying ordinary-queue admission.",
    ),
}


def source_policy_rule_preview(
    *,
    scope_kind: str,
    scope_value_digest: str,
    instruction: str,
    outcome_kind: str,
    feedback_event_ids: Sequence[str],
    proposal_ids: Sequence[str],
) -> dict[str, Any]:
    """Return an exact narrow-only policy diff from verified member outcomes."""

    if instruction not in _INSTRUCTION_DIFFS:
        raise ValueError("unsupported source policy instruction")
    admission_to, explanation = _INSTRUCTION_DIFFS[instruction]
    bounded_ids = sorted({str(value) for value in proposal_ids if value})[:100]
    evidence_ids = sorted({str(value) for value in feedback_event_ids if value})[:100]
    diff = {
        "admission": {"from": "current", "to": admission_to},
        "context": {"from": "current", "to": "no_wider_than_current"},
        "access": {"from": "current", "to": "unchanged"},
        "provider_writes": {"from": False, "to": False},
    }
    canonical = {
        "scope_kind": scope_kind,
        "scope_value_digest": scope_value_digest,
        "instruction": instruction,
        "outcome_kind": outcome_kind,
        "feedback_event_ids": evidence_ids,
        "proposal_ids": bounded_ids,
        "policy_diff": diff,
    }
    return {
        **canonical,
        "contract": "source-policy-rule-preview-v1",
        "preview_digest": hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "recent_impact": {
            "matching_outcomes": len(evidence_ids),
            "bounded_proposals": len(bounded_ids),
            "proposal_ids": bounded_ids,
        },
        "explanation": explanation,
        "access_widening": False,
        "cross_member_effect": False,
        "provider_effects": 0,
        "self_activates": False,
    }


__all__ = [
    "CONTEXT_ADMISSION_DECISIONS",
    "HANDLED_ADMISSION_DECISIONS",
    "HUMAN_TRIAGE_LANE_ORDER",
    "HumanTriageLane",
    "NON_OBLIGATION_PROVIDER_CATEGORIES",
    "ORDINARY_ADMISSION_DECISIONS",
    "backlog_reassessment_preview",
    "current_disposition",
    "human_triage_lane",
    "ordinary_queue_eligible",
    "proposed_disposition",
    "source_policy_rule_preview",
]
