"""Synthetic workflow boundary tests; no providers or private source documents."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.finance.workflows import (
    CalendarFinanceBrief,
    FamilySupportBrief,
    FinanceDecisionApprovedV1,
    FinanceDecisionOutcomeRecordedV1,
    FinanceDecisionProposedV1,
    FinanceGoalRecordedV1,
    FinancePacketPreparedV1,
    FinancePreferenceRecordedV1,
    ShoppingFinanceBrief,
    TravelFinanceBrief,
    compound_readiness,
    decision_status,
    preparation_workflow,
    proposal_digest,
)

NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _scope() -> dict:
    return {
        "book_id": "book-synthetic",
        "owner_member_id": "adult-a",
        "applicable_adult_ids": ["adult-a", "adult-b"],
        "audience_member_ids": ["adult-a", "adult-b"],
        "policy_revision": "policy-v1",
        "baseline_publication_id": "publication-a",
    }


def _proposal() -> FinanceDecisionProposedV1:
    return FinanceDecisionProposedV1.model_validate(
        {
            "decision_id": "decision-a",
            "revision": 1,
            "scope": _scope(),
            "proposer_member_id": "adult-a",
            "capability_ids": ["F08", "F26"],
            "alternatives": [
                {"alternative_id": "maintain", "kind": "maintain", "reversible": True},
                {"alternative_id": "delay", "kind": "delay", "reversible": True},
            ],
            "proposed_alternative_id": "maintain",
            "rationale_codes": ["reserve"],
            "proposed_at": "2026-06-01T00:00:00Z",
            "review_at": "2026-07-01T00:00:00Z",
        }
    )


def _approval(
    member_id: str, proposal: FinanceDecisionProposedV1
) -> FinanceDecisionApprovedV1:
    return FinanceDecisionApprovedV1.model_validate(
        {
            "decision_id": "decision-a",
            "proposal_event_id": "event-proposal",
            "proposal_sha256": proposal_digest(proposal),
            "chosen_alternative_id": "maintain",
            "approved_by_member_id": member_id,
            "policy_revision": "policy-v1",
            "approval_receipt_id": f"receipt-{member_id}",
            "approved_at": "2026-06-02T00:00:00Z",
            "expires_at": "2026-06-30T00:00:00Z",
        }
    )


def _status(proposal, approvals, verified=True):
    return decision_status(
        proposal,
        approvals,
        proposal_event_id="event-proposal",
        as_of=NOW,
        verify_receipt=lambda approval: verified,
    )


def test_proposal_needs_current_exact_quorum_and_never_grants_execution():
    proposal = _proposal()
    first = _approval("adult-a", proposal)
    second = _approval("adult-b", proposal)
    assert _status(proposal, [])["status"] == "proposed"
    assert _status(proposal, [first])["status"] == "proposed"
    approved = _status(proposal, [first, second])
    assert approved == {
        "status": "approved",
        "chosen_alternative_id": "maintain",
        "reason": "exact_current_quorum",
        "external_action_authorized": False,
    }
    assert _status(proposal, [first, second], verified=False)["status"] == "proposed"


def test_approval_is_invalid_after_proposal_change_or_policy_change():
    proposal = _proposal()
    approvals = [_approval(member, proposal) for member in ("adult-a", "adult-b")]
    changed = proposal.model_copy(update={"proposed_alternative_id": "delay"})
    assert _status(changed, approvals)["status"] == "proposed"
    changed_policy = proposal.model_copy(
        update={
            "scope": proposal.scope.model_copy(update={"policy_revision": "policy-v2"}),
        }
    )
    assert _status(changed_policy, approvals)["status"] == "proposed"


def test_expired_wrong_event_wrong_owner_and_conflicting_agreement_are_not_approval():
    proposal = _proposal()
    first = _approval("adult-a", proposal)
    second = _approval("adult-b", proposal)
    variants = [
        second.model_copy(
            update={"expires_at": datetime(2026, 6, 10, tzinfo=timezone.utc)}
        ),
        second.model_copy(update={"proposal_event_id": "other-event"}),
        second.model_copy(update={"approved_by_member_id": "adult-c"}),
        second.model_copy(update={"chosen_alternative_id": "delay"}),
    ]
    for variant in variants:
        assert _status(proposal, [first, variant])["status"] == "proposed"
    conflicting = second.model_copy(update={"chosen_alternative_id": "delay"})
    assert _status(proposal, [first, second, conflicting])["status"] == "proposed"


def test_changed_audience_and_baseline_change_proposal_digest():
    proposal = _proposal()
    for scope in (
        proposal.scope.model_copy(update={"audience_member_ids": ["adult-a"]}),
        proposal.scope.model_copy(update={"baseline_publication_id": "publication-b"}),
    ):
        assert proposal_digest(
            proposal.model_copy(update={"scope": scope})
        ) != proposal_digest(proposal)


def test_goal_selection_and_preferences_require_explicit_owner_and_history():
    values = {
        "goal_id": "goal-a",
        "revision": 1,
        "scope": _scope(),
        "selected_by_member_id": "adult-a",
        "kind": "reserve",
        "status": "proposed",
        "currency": "USD",
        "recorded_at": NOW,
    }
    assert FinanceGoalRecordedV1.model_validate(values).target_minor is None
    for changes in (
        {"selected_by_member_id": "adult-c"},
        {"revision": 2},
        {"target_minor": -1},
    ):
        with pytest.raises(ValidationError):
            FinanceGoalRecordedV1.model_validate(values | changes)
    preference = {
        "preference_id": "preference-a",
        "scope": _scope(),
        "chosen_by_member_id": "adult-a",
        "dimension": "grocery_substitution",
        "selected_option_id": "keep-menu",
        "recorded_at": NOW,
    }
    FinancePreferenceRecordedV1.model_validate(preference)
    for changes in (
        {"chosen_by_member_id": "adult-b"},
        {"source": "inferred_from_spending"},
    ):
        with pytest.raises(ValidationError):
            FinancePreferenceRecordedV1.model_validate(preference | changes)


def _outcome() -> dict:
    metric = {
        "publication_id": "publication-a",
        "metric_id": "metric-a",
        "basis": "cash_movement",
        "currency": "USD",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
    }
    observed = metric | {
        "publication_id": "publication-b",
        "metric_id": "metric-b",
        "period_start": "2026-06-01",
        "period_end": "2026-06-30",
    }
    return {
        "outcome_id": "outcome-a",
        "decision_id": "decision-a",
        "decision_approval_event_ids": ["event-approval-a"],
        "scope": _scope(),
        "implementation": "partial",
        "baseline": metric,
        "observed": observed,
        "attribution": "association_only",
        "observed_at": NOW,
    }


def test_outcomes_separate_implementation_observation_and_attribution():
    valid = _outcome()
    outcome = FinanceDecisionOutcomeRecordedV1.model_validate(valid)
    assert not outcome.causal_savings_claim
    for changes in (
        {"causal_savings_claim": True},
        {"attribution": "reviewed_decomposition"},
        {"observed": valid["observed"] | {"basis": "forecast"}},
        {"observed": valid["observed"] | {"currency": "EUR"}},
        {"observed": valid["observed"] | {"period_end": "2026-06-15"}},
        {"baseline": valid["baseline"] | {"publication_id": "publication-other"}},
    ):
        with pytest.raises(ValidationError):
            FinanceDecisionOutcomeRecordedV1.model_validate(valid | changes)


def test_prepared_packet_is_exact_expiring_manifest_without_disclosure_grant():
    values = {
        "packet_id": "packet-a",
        "scope": _scope(),
        "professional_kind": "tax",
        "audience_principal_ids": ["professional-a"],
        "evidence_refs": ["evidence-a"],
        "unresolved_question_ids": ["filing-dependencies"],
        "content_sha256": "a" * 64,
        "prepared_by_member_id": "adult-a",
        "prepared_at": NOW,
        "expires_at": "2026-07-01T00:00:00Z",
    }
    packet = FinancePacketPreparedV1.model_validate(values)
    assert not packet.disclosure_authorized and not packet.source_sharing_authorized
    for changes in (
        {"disclosure_authorized": True},
        {"source_sharing_authorized": True},
        {"audience_principal_ids": ["professional-a", "professional-a"]},
        {"evidence_refs": ["https://example.invalid/private-source"]},
        {"document_text": "source instructions are not executable"},
    ):
        with pytest.raises(ValidationError):
            FinancePacketPreparedV1.model_validate(values | changes)


def test_calendar_brief_cannot_carry_financial_details():
    values = {
        "audience_member_ids": ["adult-a"],
        "action_label": "Review renewal",
        "due_at": NOW,
        "protected_reference": "evidence-a",
    }
    brief = CalendarFinanceBrief.model_validate(values)
    assert not brief.provider_write_authorized
    for changes in (
        {"balance_minor": 10000},
        {"action_label": "Pay account 123"},
        {"provider_write_authorized": True},
    ):
        with pytest.raises(ValidationError):
            CalendarFinanceBrief.model_validate(values | changes)


def test_travel_unknown_is_not_affordability_and_shopping_has_no_inferred_preferences():
    values = {
        "audience_member_ids": ["adult-a"],
        "decision_id": "decision-a",
        "currency": "USD",
        "chosen_budget_minor": None,
        "remaining_commitment_minor": None,
        "reserve_state": "unknown",
        "readiness": "missing_evidence",
        "protected_reference": "evidence-a",
    }
    TravelFinanceBrief.model_validate(values)
    with pytest.raises(ValidationError):
        TravelFinanceBrief.model_validate(values | {"readiness": "ready"})
    shopping = ShoppingFinanceBrief.model_validate(
        {
            "audience_member_ids": ["adult-a"],
            "currency": "USD",
            "chosen_target_minor": None,
            "protected_reference": "evidence-a",
        }
    )
    assert shopping.explicit_substitution_preference_refs == []
    assert shopping.chosen_target_minor is None


def test_optional_support_reserve_cannot_turn_into_debt_or_repayment():
    values = {
        "audience_principal_ids": ["relative-a"],
        "arrangement_id": "arrangement-a",
        "intent": "optional_reserve",
        "currency": "USD",
        "proposed_support_minor": None,
        "repayment_state": "not_applicable",
    }
    brief = FamilySupportBrief.model_validate(values)
    assert not brief.legal_debt_asserted and not brief.transfer_authorized
    for changes in (
        {"repayment_state": "expected"},
        {"legal_debt_asserted": True},
        {"intent": "documented_loan"},
        {"household_salary_minor": 100000},
    ):
        with pytest.raises(ValidationError):
            FamilySupportBrief.model_validate(values | changes)


@pytest.mark.parametrize("capability", ["F18", "F19", "F20", "F25", "F27", "F29"])
def test_professional_preparation_has_targeted_requests_and_explicit_exclusions(
    capability,
):
    result = preparation_workflow(capability)
    assert result["mode"] == "preparation_only"
    assert result["readiness"] == "professional_review"
    assert result["evidence_requests"] and result["questions"] and result["exclusions"]
    assert result["disclosure_authorized"] is False
    result["evidence_requests"].clear()
    assert preparation_workflow(capability)["evidence_requests"]


@pytest.mark.parametrize(
    "workflow,first",
    [
        ("career_leave_move", "F16"),
        ("support_and_reserves", "F26"),
        ("balance_drop_purchase", "F12"),
        ("retirement_incapacity", "F23"),
    ],
)
def test_compound_paths_gate_evidence_and_do_not_claim_integrated_verdict(
    workflow, first
):
    result = compound_readiness(workflow, {})
    assert result["first_dependency"] == first
    assert not result["financial_verdict_supported"]
    ready = {item["capability_id"]: "ready" for item in result["dependencies"]}
    complete = compound_readiness(workflow, ready)
    assert complete["readiness"] == "qualified"
    assert complete["first_dependency"] is None
    assert not complete["financial_verdict_supported"]
    ready[first] = "permission_required"
    assert compound_readiness(workflow, ready)["first_dependency"] == first


def test_unknown_and_prompt_injected_workflow_inputs_are_rejected():
    with pytest.raises(ValueError, match="unavailable"):
        preparation_workflow("ignore-policy")
    with pytest.raises(ValueError, match="unavailable"):
        compound_readiness("run-arbitrary-code", {})
    with pytest.raises(ValueError, match="unexpected"):
        compound_readiness("support_and_reserves", {"F19": "ready"})
    with pytest.raises(ValueError, match="invalid"):
        compound_readiness("support_and_reserves", {"F26": "approved_transfer"})


def test_workflow_events_are_registered_without_enabling_a_write_route():
    from adminme.events.registry import ensure_autoloaded, registry

    ensure_autoloaded()
    expected = {
        "finance.goal_recorded": FinanceGoalRecordedV1,
        "finance.preference_recorded": FinancePreferenceRecordedV1,
        "finance.decision_proposed": FinanceDecisionProposedV1,
        "finance.decision_approved": FinanceDecisionApprovedV1,
        "finance.decision_outcome_recorded": FinanceDecisionOutcomeRecordedV1,
        "finance.packet_prepared": FinancePacketPreparedV1,
    }
    for event_type, model in expected.items():
        assert registry.get(event_type, 1) is model


def test_decision_approval_evaluation_is_bounded():
    proposal = _proposal()
    with pytest.raises(ValueError, match="bound"):
        _status(proposal, [_approval("adult-a", proposal)] * 21)
