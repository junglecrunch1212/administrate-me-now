"""G04 deterministic lane, preview, management, and learning proofs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlcipher3
from pydantic import ValidationError

from adminme.comms import message_attention_decision
from adminme.events.schemas.triage_management import (
    TriageSourcePolicyRuleProposedV1,
    TriageSourcePolicyRuleReviewedV1,
)
from adminme.context_fabric import policy_scope_digest
from adminme.lib.session import build_internal_session
from adminme.projections.context_fabric import ContextFabricProjection
from adminme.projections.context_fabric.queries import (
    source_policy_proposals_for_member,
    source_policy_rules_for_member,
)
from adminme.projections.triage import TriageProjection
from adminme.projections.triage.queries import (
    get_item,
    recent_for_member,
    triage_page_for_member,
)
from adminme.triage_management import (
    backlog_reassessment_preview,
    human_triage_lane,
    source_policy_rule_preview,
)


MEMBER = "fixture-g04-member"  # fixture:tenant_data:ok
OTHER = "fixture-g04-other"  # fixture:tenant_data:ok
TENANT = "fixture-g04-tenant"  # fixture:tenant_data:ok
PRIVATE = f"private:{MEMBER}"


def _event(
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    member: str = MEMBER,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_at_ms": 1,
        "tenant_id": TENANT,
        "type": event_type,
        "schema_version": 1,
        "occurred_at": "2030-01-01T00:00:00Z",
        "recorded_at": "2030-01-01T00:00:00Z",
        "source_adapter": "fixture",
        "source_account_id": "fixture-account",
        "owner_scope": f"private:{member}",
        "visibility_scope": f"private:{member}",
        "sensitivity": "normal",
        "correlation_id": None,
        "causation_id": None,
        "payload": payload,
    }


def _triage() -> tuple[TriageProjection, sqlcipher3.Connection]:
    projection = TriageProjection()
    conn = sqlcipher3.connect(":memory:")
    conn.row_factory = sqlcipher3.Row
    conn.executescript(Path(projection.schema_path).read_text("utf-8"))
    projection.migrate_schema(conn)
    return projection, conn


def _context() -> tuple[ContextFabricProjection, sqlcipher3.Connection]:
    projection = ContextFabricProjection()
    conn = sqlcipher3.connect(":memory:")
    conn.row_factory = sqlcipher3.Row
    conn.executescript(Path(projection.schema_path).read_text("utf-8"))
    return projection, conn


def _proposal(event_id: str = "event-0001", proposal_id: str = "proposal-1") -> dict[str, Any]:
    return _event(
        event_id,
        "triage.item.proposed",
        {
            "proposal_id": proposal_id,
            "target_kind": "task",
            "owner_member_id": MEMBER,
            "title": "Review fixture obligation",
            "candidate": {},
            "route": {"member_id": MEMBER},
            "source_event_id": f"source-{proposal_id}",
            "confidence": 0.6,
            "reasons": ["fixture evidence"],
        },
    )


def _reconciliation(
    event_id: str,
    proposal_id: str,
    decision: str,
    *,
    basis: list[str] | None = None,
) -> dict[str, Any]:
    return _event(
        event_id,
        "triage.reconciliation.decided",
        {
            "reconciliation_id": f"reconciliation-{proposal_id}",
            "proposal_id": proposal_id,
            "primary_proposal_id": None,
            "matter_id": f"matter-{proposal_id}",
            "owner_member_id": MEMBER,
            "source_event_id": f"source-{proposal_id}",
            "evidence_source_event_ids": [f"source-{proposal_id}"],
            "member_authored_text_digest": None,
            "decision": decision,
            "decision_revision": 1,
            "supersedes_reconciliation_event_id": None,
            "policy": {"id": "fixture-policy"},
            "source_coordinate": {"provider": "fixture", "account": "digest"},
            "freshness": "current",
            "priority_rank": 10,
            "priority_basis": basis or ["known_human_actionable_request"],
            "reason_codes": ["fixture-reason"],
            "relationships": [],
            "admission_active": True,
            "decided_at": "2030-01-01T00:01:00Z",
        },
    )


def test_required_human_lanes_are_deterministic() -> None:
    base = {"target_kind": "task", "candidate": {}, "interpretation": {}}
    assert human_triage_lane(base, {"priority_basis": ["explicit_member_instruction"]}) == "direct_instruction"
    assert human_triage_lane(base, {"priority_basis": ["exact_member_commitment"]}) == "member_promise"
    assert human_triage_lane(base, {"priority_basis": ["known_human_actionable_request"]}) == "known_human_request"
    assert human_triage_lane(base, {"priority_basis": ["verified_deadline"]}) == "scheduled_deadline"
    assert human_triage_lane({**base, "candidate": {"waiting_for": "reply"}}) == "waiting_follow_up"
    assert human_triage_lane(base, {"decision": "conflict_review"}) == "clarification_conflict"
    assert human_triage_lane(base, {"decision": "alert_only"}) == "alerts_safety"
    assert human_triage_lane(base) == "low_confidence_review"


def test_backlog_preview_is_bounded_stable_and_non_mutating() -> None:
    items = [
        {
            "proposal_id": f"proposal-{index:03d}",
            "source_event_id": f"source-{index:03d}",
            "source_id": "origin:fixture",
            "proposed_at": f"2030-01-01T00:{index:02d}:00Z",
            "last_event_id": f"event-{index:03d}",
            "state": "pending_review",
            "queue_lane": "standard",
        }
        for index in range(12)
    ]
    receipts = {
        item["proposal_id"]: {
            "last_event_id": f"receipt-{index:03d}",
            "decision": "context_only" if index % 2 else "admit",
            "priority_basis": ["known_human_actionable_request"],
            "reason_codes": ["fixture"],
            "evidence_source_event_ids": [item["source_event_id"]],
            "source_coordinate": {"source": "fixture"},
        }
        for index, item in enumerate(items)
    }
    first = backlog_reassessment_preview(items, receipts, limit=5)
    again = backlog_reassessment_preview(items, receipts, limit=5)
    second = backlog_reassessment_preview(
        items, receipts, limit=5, cursor=first["next_cursor"]
    )
    assert first == again
    assert first["batch"] == {"offset": 0, "count": 5, "limit": 5, "total": 12}
    assert second["batch"]["offset"] == 5
    assert first["mutates_backlog"] is False
    assert first["source_events_deleted"] == first["provider_effects"] == 0
    assert first["changes"][1]["old"]["disposition"] == "ordinary_queue"
    assert first["changes"][1]["proposed"]["disposition"] == "context"
    assert len(items) == 12


def test_management_actions_are_event_sourced_reversible_and_member_scoped() -> None:
    projection, conn = _triage()
    projection.apply(_proposal(), conn)
    projection.apply(
        _event(
            "event-0002",
            "triage.item.managed",
            {
                "action_id": "action-1",
                "proposal_id": "proposal-1",
                "member_id": MEMBER,
                "action": "keep_context",
                "action_revision": 1,
                "supersedes_action_id": None,
                "expected_last_event_id": "event-0001",
                "reason_codes": ["member-said-context"],
                "evidence_event_ids": ["source-proposal-1"],
                "reversible": True,
                "provider_effects": False,
                "managed_at": "2030-01-01T00:02:00Z",
            },
        ),
        conn,
    )
    session = build_internal_session(MEMBER, "principal", TENANT)
    item = get_item(conn, session, proposal_id="proposal-1")
    assert item is not None
    assert item["management"]["state"] == "context"
    assert item["management"]["provider_effects"] == 0
    assert [
        value["proposal_id"]
        for value in recent_for_member(conn, session, member_id=MEMBER)
    ] == ["proposal-1"]
    projection.apply(
        _event(
            "event-0003",
            "triage.item.managed",
            {
                "action_id": "action-2",
                "proposal_id": "proposal-1",
                "member_id": MEMBER,
                "action": "reopen",
                "action_revision": 2,
                "supersedes_action_id": "action-1",
                "expected_last_event_id": "event-0002",
                "reason_codes": ["member-reopened"],
                "evidence_event_ids": [],
                "reversible": True,
                "provider_effects": False,
                "managed_at": "2030-01-01T00:03:00Z",
            },
        ),
        conn,
    )
    reopened = get_item(conn, session, proposal_id="proposal-1")
    assert reopened is not None and reopened["management"]["state"] == "open"
    assert conn.execute("SELECT COUNT(*) FROM triage_management_actions").fetchone()[0] == 2

    other_scope = _event(
        "event-0004",
        "triage.item.managed",
        {
            "action_id": "action-other",
            "proposal_id": "proposal-1",
            "member_id": MEMBER,
            "action": "mark_handled",
            "action_revision": 3,
            "supersedes_action_id": "action-2",
            "expected_last_event_id": "event-0003",
            "reason_codes": [],
            "evidence_event_ids": [],
            "reversible": True,
            "provider_effects": False,
            "managed_at": "2030-01-01T00:04:00Z",
        },
        member=OTHER,
    )
    projection.apply(other_scope, conn)
    unchanged = get_item(conn, session, proposal_id="proposal-1")
    assert unchanged is not None and unchanged["last_event_id"] == "event-0003"


def test_active_queue_contains_only_admitted_obligations_and_separate_alerts() -> None:
    projection, conn = _triage()
    decisions = ["admit", "context_only", "source_policy_suppression", "alert_only"]
    for index, decision in enumerate(decisions):
        proposal_id = f"proposal-{index}"
        projection.apply(_proposal(f"event-{index * 2 + 1:04d}", proposal_id), conn)
        projection.apply(
            _reconciliation(f"event-{index * 2 + 2:04d}", proposal_id, decision),
            conn,
        )
    session = build_internal_session(MEMBER, "principal", TENANT)
    page = triage_page_for_member(
        conn,
        session,
        member_id=MEMBER,
        lens="all_candidates",
        human_centered=True,
    )
    assert {matter["root_proposal_id"] for matter in page["matters"]} == {
        "proposal-0",
        "proposal-3",
    }
    alerts = triage_page_for_member(
        conn,
        session,
        member_id=MEMBER,
        lens="alerts",
        human_centered=True,
    )
    assert [matter["root_proposal_id"] for matter in alerts["matters"]] == [
        "proposal-3"
    ]


def test_learning_never_self_activates_and_negative_feedback_deactivates() -> None:
    projection, conn = _context()
    preview = source_policy_rule_preview(
        scope_kind="sender",
        scope_value_digest="a" * 64,
        instruction="never",
        outcome_kind="dismiss_not_useful",
        feedback_event_ids=["feedback-event-1"],
        proposal_ids=["proposal-1"],
    )
    proposed_payload = {
        "policy_proposal_id": "policy-proposal-1",
        "member_id": MEMBER,
        "scope_kind": "sender",
        "scope_value_digest": "a" * 64,
        "instruction": "never",
        "outcome_kind": "dismiss_not_useful",
        "verified_feedback_event_ids": ["feedback-event-1"],
        "affected_proposal_ids": ["proposal-1"],
        "preview_digest": preview["preview_digest"],
        "policy_diff": preview["policy_diff"],
        "explanation": preview["explanation"],
        "proposed_by_member_id": MEMBER,
        "proposed_at": "2030-01-01T00:01:00Z",
        "self_activates": False,
        "access_widening": False,
        "cross_member_effect": False,
        "provider_effects": False,
    }
    projection.apply(
        _event("event-0001", "triage.source_policy_rule.proposed", proposed_payload),
        conn,
    )
    session = build_internal_session(MEMBER, "principal", TENANT)
    proposals = source_policy_proposals_for_member(conn, session, member_id=MEMBER)
    assert proposals[0]["status"] == "proposed"
    assert source_policy_rules_for_member(conn, session, member_id=MEMBER) == []

    projection.apply(
        _event(
            "event-0002",
            "triage.source_policy_rule.decided",
            {
                "decision_id": "decision-1",
                "policy_proposal_id": "policy-proposal-1",
                "rule_id": "rule-1",
                "member_id": MEMBER,
                "scope_kind": "sender",
                "scope_value_digest": "a" * 64,
                "outcome_kind": "dismiss_not_useful",
                "decision": "trial",
                "instruction": "never",
                "preview_digest": preview["preview_digest"],
                "trial_proposal_ids": ["proposal-1"],
                "decided_by_member_id": MEMBER,
                "decided_at": "2030-01-01T00:02:00Z",
                "access_widening": False,
                "cross_member_effect": False,
                "provider_effects": False,
            },
        ),
        conn,
    )
    rules = source_policy_rules_for_member(conn, session, member_id=MEMBER)
    assert rules[0]["status"] == "trial"
    assert rules[0]["trial_proposal_ids"] == ["proposal-1"]
    projection.apply(
        _event(
            "event-0003",
            "triage.source_policy_rule.reviewed",
            {
                "review_id": "review-1",
                "rule_id": "rule-1",
                "member_id": MEMBER,
                "decision": "suspend",
                "negative_outcome": "missed_obligation",
                "previous_instruction": "never",
                "replacement_instruction": None,
                "reason": "Fixture obligation was hidden",
                "reviewed_by_member_id": MEMBER,
                "reviewed_at": "2030-01-01T00:03:00Z",
                "access_widening": False,
                "cross_member_effect": False,
                "provider_effects": False,
            },
        ),
        conn,
    )
    assert source_policy_rules_for_member(conn, session, member_id=MEMBER)[0]["status"] == "suspended"


def test_policy_event_schemas_reject_cross_member_or_widening_and_retain_negative() -> None:
    with pytest.raises(ValidationError):
        TriageSourcePolicyRuleProposedV1(
            policy_proposal_id="proposal",
            member_id=MEMBER,
            scope_kind="sender",
            scope_value_digest="a" * 64,
            instruction="never",
            outcome_kind="dismiss_not_useful",
            verified_feedback_event_ids=["event"],
            preview_digest="b" * 64,
            policy_diff={
                "access": {"from": "current", "to": "household"},
                "provider_writes": {"from": False, "to": False},
            },
            explanation="Fixture",
            proposed_by_member_id=OTHER,
            proposed_at="2030-01-01T00:00:00Z",
        )
    with pytest.raises(ValidationError):
        TriageSourcePolicyRuleReviewedV1(
            review_id="review",
            rule_id="rule",
            member_id=MEMBER,
            decision="retain",
            negative_outcome="missed_obligation",
            previous_instruction="never",
            reason="Fixture",
            reviewed_by_member_id=MEMBER,
            reviewed_at="2030-01-01T00:00:00Z",
        )
    with pytest.raises(ValidationError):
        TriageSourcePolicyRuleReviewedV1(
            review_id="review-widening",
            rule_id="rule",
            member_id=MEMBER,
            decision="narrow",
            negative_outcome="scope_mismatch",
            previous_instruction="never",
            replacement_instruction="always",
            reason="Fixture widening attempt",
            reviewed_by_member_id=MEMBER,
            reviewed_at="2030-01-01T00:00:00Z",
        )


def test_comms_focus_requires_an_admitted_matter_and_alerts_stay_separate() -> None:
    from datetime import datetime, timezone

    latest = {
        "direction": "inbound",
        "semantic_features_json": '{"reply_signal":"required"}',
        "response_urgency": "urgent",
        "occurred_at": "2030-01-01T00:00:00Z",
    }
    now = datetime(2030, 1, 1, 0, 5, tzinfo=timezone.utc)
    later, later_why = message_attention_decision(
        latest, now, admitted_matters_only=True
    )
    focus, focus_why = message_attention_decision(
        latest, now, admitted_matters_only=True, triage_count=1
    )
    alerts, alert_why = message_attention_decision(
        latest, now, admitted_matters_only=True, alert_count=1
    )
    assert later == "later"
    assert later_why["rule_id"] == "comms.context-without-admitted-matter-v1"
    assert focus == "focus" and focus_why["signals"]["triage_count"] == 1
    assert alerts == "alerts" and alert_why["signals"]["alert_count"] == 1


def test_approved_rule_narrows_matching_admission_and_suspension_restores_it() -> None:
    projection, conn = _triage()
    projection.apply(_proposal("event-0001", "proposal-learned"), conn)
    projection.apply(
        _reconciliation("event-0002", "proposal-learned", "admit"), conn
    )
    session = build_internal_session(MEMBER, "principal", TENANT)
    assert triage_page_for_member(
        conn, session, member_id=MEMBER, human_centered=True
    )["inventory"]["total_candidates"] == 1
    projection.apply(
        _event(
            "event-0003",
            "triage.source_policy_rule.decided",
            {
                "decision_id": "decision-learned",
                "policy_proposal_id": "policy-proposal-learned",
                "rule_id": "rule-learned",
                "member_id": MEMBER,
                "scope_kind": "candidate_kind",
                "scope_value_digest": policy_scope_digest("candidate_kind", "task"),
                "outcome_kind": "dismiss_not_useful",
                "decision": "approve",
                "instruction": "never",
                "preview_digest": "d" * 64,
                "trial_proposal_ids": [],
                "decided_by_member_id": MEMBER,
                "decided_at": "2030-01-01T00:03:00Z",
                "access_widening": False,
                "cross_member_effect": False,
                "provider_effects": False,
            },
        ),
        conn,
    )
    narrowed = get_item(conn, session, proposal_id="proposal-learned")
    assert narrowed is not None
    assert narrowed["learned_policy"] == {
        "state": "context_only",
        "rule_id": "rule-learned",
        "narrow_only": True,
        "provider_effects": 0,
    }
    assert triage_page_for_member(
        conn, session, member_id=MEMBER, human_centered=True
    )["inventory"]["total_candidates"] == 0
    projection.apply(
        _event(
            "event-0004",
            "triage.source_policy_rule.reviewed",
            {
                "review_id": "review-learned",
                "rule_id": "rule-learned",
                "member_id": MEMBER,
                "decision": "suspend",
                "negative_outcome": "missed_obligation",
                "previous_instruction": "never",
                "replacement_instruction": None,
                "reason": "Fixture obligation was hidden",
                "reviewed_by_member_id": MEMBER,
                "reviewed_at": "2030-01-01T00:04:00Z",
                "access_widening": False,
                "cross_member_effect": False,
                "provider_effects": False,
            },
        ),
        conn,
    )
    restored = get_item(conn, session, proposal_id="proposal-learned")
    assert restored is not None and restored["learned_policy"]["state"] == "none"
    assert triage_page_for_member(
        conn, session, member_id=MEMBER, human_centered=True
    )["inventory"]["total_candidates"] == 1
