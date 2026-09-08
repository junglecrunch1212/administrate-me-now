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
from datetime import datetime, timezone
from typing import Any, Literal

from adminme.obligation_assessment import assessment_from_interpretation


HumanTriageLane = Literal[
    "direct_instruction",
    "member_promise",
    "known_human_request",
    "scheduled_deadline",
    "ordinary_obligation",
    "waiting_follow_up",
    "clarification_conflict",
    "alerts_safety",
    "low_confidence_review",
    "possible_opportunity",
]

HUMAN_TRIAGE_LANE_ORDER: dict[str, int] = {
    "direct_instruction": 0,
    "member_promise": 1,
    "known_human_request": 2,
    "scheduled_deadline": 3,
    "ordinary_obligation": 4,
    "waiting_follow_up": 5,
    "clarification_conflict": 6,
    "alerts_safety": 7,
    "low_confidence_review": 8,
    "possible_opportunity": 9,
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
        for value in _reconciliation_value(item, reconciliation, "priority_basis", [])
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
    if not interpretation and item.get("interpretation_json"):
        interpretation = _mapping(json.loads(str(item["interpretation_json"])))
    if not candidate and item.get("candidate_json"):
        candidate = _mapping(json.loads(str(item["candidate_json"])))
    assessment = (
        assessment_from_interpretation(interpretation)
        if interpretation.get("assessment_state") == "verified"
        else None
    )
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
    if decision == "conflict_review":
        return "clarification_conflict"
    if (
        assessment is not None
        and assessment.purpose == "optional_opportunity"
        and not assessment.independent_obligation
    ):
        return "possible_opportunity"
    if decision == "unresolved":
        return "clarification_conflict"
    if interpretation.get("thread_state") in {"blocked", "unclear"}:
        return "clarification_conflict"
    if assessment is not None and assessment.independent_obligation:
        return "ordinary_obligation"
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


NEEDS_YOU_PRIORITY_BASES = frozenset(
    {
        "explicit_member_instruction",
        "exact_member_commitment",
        "known_human_actionable_request",
        "verified_deadline",
        "household_policy",
    }
)


def human_attention_view(item: Mapping[str, Any]) -> str:
    """Classify attention by admitted responsibility, never date availability.

    This is the public counterpart of the modern SQL lens predicate. Legacy
    flag-off views continue using their existing queue-lane filters.
    """

    decision = str(_reconciliation_value(item, None, "decision", "admit"))
    management = str(
        item.get("management_state")
        or _mapping(item.get("management")).get("state")
        or "open"
    )
    live_state = item.get("state") in {"pending_interpretation", "pending_review"} or (
        item.get("state") == "dismissed"
        and item.get("resolved_by") == "system:cross-source-reconciliation"
        and item.get("resolution_reason")
        in {
            "reconciliation:context_only",
            "reconciliation:source_policy_suppression",
            "reconciliation:alert_only",
        }
    )
    if (
        live_state
        and management in {"open", "clarification", "suppression_proposed", "alert"}
        and (decision == "alert_only" or management == "alert")
    ):
        return "alerts"
    learned = str(
        item.get("learned_policy_state")
        or _mapping(item.get("learned_policy")).get("state")
        or "none"
    )
    if (
        item.get("state") not in {"pending_interpretation", "pending_review"}
        or management not in {"open", "clarification", "suppression_proposed"}
        or decision not in ORDINARY_ADMISSION_DECISIONS
        or item.get("decision_disposition", "decision") != "decision"
        or learned == "context_only"
    ):
        return "history"
    basis = set(_reconciliation_value(item, None, "priority_basis", []))
    if (
        basis & NEEDS_YOU_PRIORITY_BASES
        or item.get("source_kind") == "commitment_proposal"
    ):
        return "needs_you"
    if learned == "clarification_required" or decision == "conflict_review":
        return "needs_you"
    interpretation = _mapping(item.get("interpretation"))
    if not interpretation and item.get("interpretation_json"):
        interpretation = _mapping(json.loads(str(item["interpretation_json"])))
    assessment = (
        assessment_from_interpretation(interpretation)
        if interpretation.get("assessment_state") == "verified"
        else None
    )
    if assessment is not None and assessment.independent_obligation:
        return "needs_you"
    opportunity = (
        assessment is not None and assessment.purpose == "optional_opportunity"
    )
    if decision == "unresolved" and not opportunity:
        return "needs_you"
    return "possibilities"


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
            for value in _reconciliation_value(item, reconciliation, "reason_codes", [])
        ],
        "evidence_event_ids": [
            str(value)
            for value in _reconciliation_value(
                item, reconciliation, "evidence_source_event_ids", []
            )
        ],
        "source_coordinate": dict(
            _mapping(
                _reconciliation_value(item, reconciliation, "source_coordinate", {})
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
        "lane": str(
            item.get("human_lane") or item.get("queue_lane") or "low_confidence_review"
        ),
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


def attention_values(item: Mapping[str, Any]) -> dict[str, str]:
    """Read only typed candidate interpretation and immutable source coordinates."""

    candidate = _mapping(item.get("candidate"))
    if not candidate and item.get("candidate_json"):
        candidate = _mapping(json.loads(str(item["candidate_json"])))
    interpretation = _mapping(item.get("interpretation"))
    if not interpretation and item.get("interpretation_json"):
        interpretation = _mapping(json.loads(str(item["interpretation_json"])))
    assessment = (
        assessment_from_interpretation(interpretation)
        if interpretation.get("assessment_state") == "verified"
        else None
    )
    coordinate = _mapping(_reconciliation_value(item, None, "source_coordinate", {}))
    values = {
        field: coordinate.get(field)
        for field in (
            "provider",
            "provider_account_id",
            "adapter_instance_id",
            "native_container_id",
        )
    }
    values.update(
        candidate_kind=item.get("target_kind"),
        sender_digest=candidate.get("sender_digest"),
        message_purpose=(assessment.purpose if assessment is not None else None),
    )
    return {key: str(value) for key, value in values.items() if value not in (None, "")}


def attention_protection(item: Mapping[str, Any]) -> str | None:
    basis = set(_reconciliation_value(item, None, "priority_basis", []))
    if "explicit_member_instruction" in basis or item.get("parent_intent_id"):
        return "authenticated_member_instruction"
    if "exact_member_commitment" in basis:
        return "exact_member_promise"
    if _reconciliation_value(item, None, "decision", "") == "alert_only":
        return "separate_safety_alert"
    interpretation = _mapping(item.get("interpretation"))
    if not interpretation and item.get("interpretation_json"):
        interpretation = _mapping(json.loads(str(item["interpretation_json"])))
    assessment = (
        assessment_from_interpretation(interpretation)
        if interpretation.get("assessment_state") == "verified"
        else None
    )
    if assessment is not None and assessment.independent_obligation:
        return "independent_obligation"
    return None


def attention_conditions_match(
    conditions: Mapping[str, Any], values: Mapping[str, Any]
) -> bool:
    terms = {key: value for key, value in conditions.items() if value is not None}
    return bool(terms) and all(values.get(key) == value for key, value in terms.items())


def attention_match(rule: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    values = attention_values(item)
    if not attention_conditions_match(_mapping(rule.get("conditions")), values):
        return "nonmatch"
    if any(
        attention_conditions_match(_mapping(exception), values)
        for exception in rule.get("exceptions", [])
    ):
        return "exception"
    if rule.get("action") != "review" and attention_protection(item):
        return "protected"
    return "match"


def independent_observation_key(item: Mapping[str, Any]) -> str:
    """Count known exact objects once; a provider thread is not an obligation."""

    if item.get("existing_object_kind") and item.get("existing_object_id"):
        value = [item["existing_object_kind"], item["existing_object_id"]]
    elif item.get("duplicate_of_proposal_id"):
        value = ["proposal", item["duplicate_of_proposal_id"]]
    else:
        value = ["source", item.get("source_event_id") or item.get("proposal_id")]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def resolve_attention_rules(rules: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Explicit rules outrank suggestions; strict conjunction inclusion wins.

    Incomparable conditions do not acquire authority from count, timestamp,
    suppression strength, or rule id. Conflicting maximal rules require review.
    """

    if not rules:
        return {"action": "review", "rule_ids": [], "conflict": False}
    explicit = [rule for rule in rules if rule.get("origin") == "explicit"]
    candidates = explicit or list(rules)
    maximal = []
    for rule in candidates:
        terms = {
            (key, value)
            for key, value in _mapping(rule.get("conditions")).items()
            if value is not None
        }
        if any(
            terms
            < {
                (key, value)
                for key, value in _mapping(other.get("conditions")).items()
                if value is not None
            }
            for other in candidates
        ):
            continue
        maximal.append(rule)
    actions = {str(rule["action"]) for rule in maximal}
    return {
        "action": next(iter(actions)) if len(actions) == 1 else "ask",
        "rule_ids": sorted(str(rule["rule_id"]) for rule in maximal),
        "conflict": len(actions) > 1,
    }


def attention_rule_expired(rule: Mapping[str, Any], as_of: str) -> bool:
    expires = rule.get("expires_at")
    if not expires:
        return False

    def parsed(value: str) -> datetime:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)
        )

    return parsed(as_of) >= parsed(str(expires))


def attention_rule_preview(
    rule: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    *,
    source_only: Sequence[Mapping[str, Any]] = (),
    as_of: str | None = None,
    existing_rules: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Counterfactuals include all authorized sampled items, not just feedback."""

    examples: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "matched": 0,
        "exceptions": 0,
        "nonmatches": 0,
        "protected": 0,
    }
    canonical_inventory: list[Any] = []
    for item in [*items, *source_only]:
        match = (
            "nonmatch"
            if item.get("assessment_unavailable")
            else attention_match(rule, item)
        )
        current = current_disposition(item)
        if current["ordinary_queue"] and (
            item.get("reconciliation_active")
            or _mapping(item.get("reconciliation")).get("active")
        ):
            current = proposed_disposition(item)
        before = str(current["disposition"])
        policy_state = item.get("learned_policy_state") or _mapping(
            item.get("learned_policy")
        ).get("state")
        if as_of is not None:
            schedule = _json_list(item.get("attention_expiry_schedule_json"))
            elapsed = [
                value
                for value in schedule
                if attention_rule_expired({"expires_at": value["at"]}, as_of)
            ]
            if elapsed:
                policy_state = elapsed[-1]["state"]
        if current["ordinary_queue"] and policy_state == "context_only":
            before = "context"
        elif current["ordinary_queue"] and policy_state == "clarification_required":
            before = "clarification"
        after = before
        reasons = [f"rule_{match}"]
        if match == "match":
            applicable = [
                value
                for value in existing_rules
                if value.get("status") in {"active", "trial"}
                and (
                    value.get("status") != "trial"
                    or str(item.get("proposal_event_id") or "")
                    > str(value.get("created_event_id") or "")
                )
                and (as_of is None or not attention_rule_expired(value, as_of))
                and attention_match(value, item) == "match"
            ]
            selection = resolve_attention_rules(
                [*applicable, {**rule, "rule_id": "proposed_rule"}]
            )
            action = selection["action"]
            if selection["conflict"]:
                reasons.append("conflicting_attention_rules")
            elif "proposed_rule" not in selection["rule_ids"]:
                reasons.append("more_specific_existing_rule")
            # Review is retention of eligibility, never access/admission widening.
            if action == "reference" and current["ordinary_queue"]:
                after = "context"
            elif action == "ask" and current["ordinary_queue"]:
                after = "clarification"
            elif action == "review" and current["ordinary_queue"]:
                after = "ordinary_queue"
        elif match == "protected":
            reasons.append(str(attention_protection(item)))
        if item.get("assessment_unavailable"):
            reasons.append("source_has_no_assessed_candidate")
        counts["total"] += 1
        counts[
            {
                "match": "matched",
                "exception": "exceptions",
                "nonmatch": "nonmatches",
                "protected": "protected",
            }[match]
        ] += 1
        examples.append(
            {
                "proposal_id": item.get("proposal_id"),
                "source_event_id": item.get("source_event_id"),
                "title": item.get("title") or "Source observation",
                "match": match,
                "before": before,
                "after": after,
                "reasons": reasons,
            }
        )
        canonical_inventory.append(
            [
                item.get("proposal_id"),
                item.get("source_event_id"),
                item.get("last_event_id"),
                match,
                before,
                after,
                attention_values(item),
            ]
        )
    canonical = {
        "rule": dict(rule),
        "inventory": canonical_inventory,
        "existing_rules": [
            [value.get("rule_id"), value.get("last_event_id"), value.get("status")]
            for value in existing_rules
        ],
    }
    # Show each result class before filling the remaining bounded examples.
    selected: list[dict[str, Any]] = []
    for category in ("match", "protected", "exception", "nonmatch"):
        selected.extend([value for value in examples if value["match"] == category][:5])
    return {
        "contract": "attention-rule-preview-v2",
        "rule": dict(rule),
        "preview_digest": hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "recent_impact": counts,
        "examples": selected,
        "sample_limit": 500,
        "source_sample_limit": 250,
        "mutation_performed": False,
        "access_widening": False,
        "cross_member_effect": False,
        "provider_effects": 0,
        "limitations": [
            "This bounded counterfactual uses authorized retained observations.",
            "Unassessed sources remain visible in coverage; they are not negative labels or proof of recall.",
        ],
    }
