"""Bounded finance records and preparation contracts, never action authority.

These records retain typed intent, evidence references and approval provenance.
They do not accept source documents, provider credentials or arbitrary prose.
A schema-valid approval is evidence to verify against existing exact grants;
it is not itself a grant to write, disclose or process another adult's data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Annotated, Any, Callable, Literal, Mapping, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
NonnegativeMinor = Annotated[int, Field(strict=True, ge=0, le=10**15)]


class FinanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecordScope(FinanceRecord):
    book_id: Id
    owner_member_id: Id
    applicable_adult_ids: list[Id] = Field(min_length=1, max_length=2)
    audience_member_ids: list[Id] = Field(min_length=1, max_length=2)
    policy_revision: Id
    baseline_publication_id: Id

    @model_validator(mode="after")
    def validate_scope(self) -> RecordScope:
        for members in (self.applicable_adult_ids, self.audience_member_ids):
            if len(members) != len(set(members)):
                raise ValueError("finance record members must be unique")
        if self.owner_member_id not in self.applicable_adult_ids:
            raise ValueError("finance record owner must be an applicable adult")
        if not set(self.audience_member_ids).issubset(self.applicable_adult_ids):
            raise ValueError("finance record audience must be an applicable subset")
        return self


class MetricReference(FinanceRecord):
    publication_id: Id
    metric_id: Id
    basis: Literal[
        "accounting_actual",
        "cash_movement",
        "observed_value",
        "cost_reference",
        "chosen_budget",
        "forecast",
        "intended_obligation",
    ]
    currency: Currency
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def validate_period(self) -> MetricReference:
        if self.period_end < self.period_start:
            raise ValueError("metric period ends before its start")
        return self


class FinanceGoalRecordedV1(FinanceRecord):
    goal_id: Id
    label: str | None = Field(default=None, min_length=1, max_length=120)
    revision: int = Field(ge=1)
    supersedes_event_id: Id | None = None
    scope: RecordScope
    selected_by_member_id: Id
    kind: Literal["reserve", "purchase", "education", "retirement", "support", "debt"]
    status: Literal["proposed", "selected", "paused", "completed"]
    target_minor: NonnegativeMinor | None = None
    currency: Currency
    target_date: date | None = None
    priority: Literal["unranked", "essential", "preferred", "optional"] = "unranked"
    evidence_refs: list[Id] = Field(default_factory=list, max_length=12)
    recorded_at: AwareDatetime
    external_action_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_selection(self) -> FinanceGoalRecordedV1:
        if self.selected_by_member_id not in self.scope.applicable_adult_ids:
            raise ValueError("goal selector must be an applicable adult")
        if self.target_minor is not None and self.target_minor < 0:
            raise ValueError("goal target must be nonnegative")
        if (self.revision == 1) != (self.supersedes_event_id is None):
            raise ValueError("goal revisions require an exact predecessor")
        return self


class FinancePreferenceRecordedV1(FinanceRecord):
    preference_id: Id
    scope: RecordScope
    chosen_by_member_id: Id
    dimension: Literal[
        "grocery_substitution",
        "service_time_tradeoff",
        "reserve_policy",
        "support_intent",
        "review_cadence",
        "decision_priority",
    ]
    selected_option_id: Id
    source: Literal["explicit_member_choice"] = "explicit_member_choice"
    evidence_refs: list[Id] = Field(default_factory=list, max_length=8)
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def validate_member(self) -> FinancePreferenceRecordedV1:
        if self.chosen_by_member_id != self.scope.owner_member_id:
            raise ValueError("preferences must be chosen by their owner")
        return self


class DecisionAlternative(FinanceRecord):
    alternative_id: Id
    kind: Literal["maintain", "reduce", "increase", "replace", "delay", "prepare"]
    expected_effects: list[MetricReference] = Field(default_factory=list, max_length=12)
    assumption_refs: list[Id] = Field(default_factory=list, max_length=12)
    evidence_refs: list[Id] = Field(default_factory=list, max_length=12)
    reversible: bool


class FinanceDecisionProposedV1(FinanceRecord):
    decision_id: Id
    revision: int = Field(ge=1)
    supersedes_event_id: Id | None = None
    scope: RecordScope
    proposer_member_id: Id
    capability_ids: list[
        Annotated[str, Field(pattern=r"^F(?:0[1-9]|[12][0-9]|3[01])$")]
    ] = Field(min_length=1, max_length=8)
    alternatives: list[DecisionAlternative] = Field(min_length=1, max_length=5)
    proposed_alternative_id: Id | None = None
    rationale_codes: list[
        Literal[
            "cash_timing",
            "reserve",
            "cost",
            "time",
            "risk",
            "coverage",
            "preference",
        ]
    ] = Field(min_length=1, max_length=7)
    proposed_at: AwareDatetime
    review_at: AwareDatetime
    external_action_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_proposal(self) -> FinanceDecisionProposedV1:
        ids = [alternative.alternative_id for alternative in self.alternatives]
        if len(set(ids)) != len(ids):
            raise ValueError("decision alternative IDs must be unique")
        if (
            self.proposed_alternative_id is not None
            and self.proposed_alternative_id not in ids
        ):
            raise ValueError("proposed alternative must be declared")
        if self.proposer_member_id not in self.scope.applicable_adult_ids:
            raise ValueError("decision proposer must be an applicable adult")
        if self.review_at <= self.proposed_at:
            raise ValueError("decision review must follow proposal")
        if (self.revision == 1) != (self.supersedes_event_id is None):
            raise ValueError("decision revisions require an exact predecessor")
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError("decision capability IDs must be unique")
        return self


def proposal_digest(proposal: FinanceDecisionProposedV1) -> str:
    encoded = json.dumps(
        proposal.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FinanceDecisionApprovedV1(FinanceRecord):
    """One adult's decision agreement; never financial execution permission."""

    decision_id: Id
    proposal_event_id: Id
    proposal_sha256: Digest
    chosen_alternative_id: Id
    approved_by_member_id: Id
    policy_revision: Id
    approval_receipt_id: Id
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    external_action_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_expiry(self) -> FinanceDecisionApprovedV1:
        if self.expires_at <= self.approved_at:
            raise ValueError("decision approval must expire after approval time")
        return self


def decision_status(
    proposal: FinanceDecisionProposedV1,
    approvals: Sequence[FinanceDecisionApprovedV1],
    *,
    proposal_event_id: str,
    as_of: datetime,
    verify_receipt: Callable[[FinanceDecisionApprovedV1], bool],
) -> dict[str, Any]:
    """Resolve quorum only after the caller verifies each current exact receipt.

    The verifier must authenticate owner, policy, audience, exact proposal and
    revocation state through existing domain authority. Never pass a constant
    verifier in production. Absence, expiry or disagreement preserves proposal.
    """
    if as_of.tzinfo is None:
        raise ValueError("decision time must include a timezone")
    if len(approvals) > 20:
        raise ValueError("decision approval evaluation exceeds its bound")
    digest = proposal_digest(proposal)
    alternatives = {item.alternative_id for item in proposal.alternatives}
    relevant = [
        approval
        for approval in approvals
        if approval.decision_id == proposal.decision_id
        and approval.proposal_event_id == proposal_event_id
        and approval.proposal_sha256 == digest
        and approval.policy_revision == proposal.scope.policy_revision
        and approval.chosen_alternative_id in alternatives
        and approval.approved_by_member_id in proposal.scope.applicable_adult_ids
        and approval.approved_at <= as_of < approval.expires_at
        and verify_receipt(approval)
    ]
    by_member: dict[str, set[str]] = {}
    for approval in relevant:
        by_member.setdefault(approval.approved_by_member_id, set()).add(
            approval.chosen_alternative_id
        )
    choices = set().union(*by_member.values()) if by_member else set()
    approved = (
        set(by_member) == set(proposal.scope.applicable_adult_ids) and len(choices) == 1
    )
    return {
        "status": "approved" if approved else "proposed",
        "chosen_alternative_id": next(iter(choices)) if approved else None,
        "reason": "exact_current_quorum" if approved else "current_agreement_required",
        "external_action_authorized": False,
    }


class FinanceDecisionOutcomeRecordedV1(FinanceRecord):
    outcome_id: Id
    decision_id: Id
    decision_approval_event_ids: list[Id] = Field(min_length=1, max_length=2)
    scope: RecordScope
    implementation: Literal[
        "not_started", "partial", "completed", "reversed", "unknown"
    ]
    baseline: MetricReference
    observed: MetricReference
    attribution: Literal["unresolved", "association_only", "reviewed_decomposition"]
    decomposition_evidence_refs: list[Id] = Field(default_factory=list, max_length=12)
    observed_at: AwareDatetime
    causal_savings_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_comparison(self) -> FinanceDecisionOutcomeRecordedV1:
        if self.baseline.publication_id != self.scope.baseline_publication_id:
            raise ValueError("outcome must retain its accepted baseline")
        if (
            self.baseline.basis != self.observed.basis
            or self.baseline.currency != self.observed.currency
        ):
            raise ValueError("outcome comparison requires the same basis and currency")
        if (self.baseline.period_end - self.baseline.period_start) != (
            self.observed.period_end - self.observed.period_start
        ):
            raise ValueError("outcome periods must have comparable duration")
        if (
            self.attribution == "reviewed_decomposition"
            and not self.decomposition_evidence_refs
        ):
            raise ValueError("reviewed attribution requires decomposition evidence")
        return self


class FinancePacketPreparedV1(FinanceRecord):
    packet_id: Id
    scope: RecordScope
    professional_kind: Literal["tax", "legal", "investment", "lending", "insurance"]
    audience_principal_ids: list[Id] = Field(min_length=1, max_length=4)
    evidence_refs: list[Id] = Field(default_factory=list, max_length=30)
    unresolved_question_ids: list[Id] = Field(min_length=1, max_length=20)
    content_sha256: Digest
    prepared_by_member_id: Id
    prepared_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["prepared"] = "prepared"
    disclosure_authorized: Literal[False] = False
    source_sharing_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_packet(self) -> FinancePacketPreparedV1:
        if self.prepared_by_member_id not in self.scope.applicable_adult_ids:
            raise ValueError("packet preparer must be an applicable adult")
        if self.expires_at <= self.prepared_at:
            raise ValueError("packet must expire after preparation")
        if len(set(self.audience_principal_ids)) != len(self.audience_principal_ids):
            raise ValueError("packet audience must be exact and unique")
        return self


class CalendarFinanceBrief(FinanceRecord):
    audience_member_ids: list[Id] = Field(min_length=1, max_length=2)
    action_label: Literal[
        "Review bill",
        "Review renewal",
        "Review plan",
        "Prepare documents",
    ]
    due_at: AwareDatetime
    protected_reference: Id
    provider_write_authorized: Literal[False] = False


class TravelFinanceBrief(FinanceRecord):
    audience_member_ids: list[Id] = Field(min_length=1, max_length=2)
    decision_id: Id
    currency: Currency
    chosen_budget_minor: NonnegativeMinor | None
    remaining_commitment_minor: NonnegativeMinor | None
    reserve_state: Literal["within_chosen_policy", "below_chosen_policy", "unknown"]
    readiness: Literal["ready", "qualified", "missing_evidence"]
    protected_reference: Id
    booking_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_ready(self) -> TravelFinanceBrief:
        if self.readiness == "ready" and (
            self.chosen_budget_minor is None
            or self.remaining_commitment_minor is None
            or self.reserve_state == "unknown"
        ):
            raise ValueError("ready travel constraint requires complete evidence")
        return self


class ShoppingFinanceBrief(FinanceRecord):
    audience_member_ids: list[Id] = Field(min_length=1, max_length=2)
    currency: Currency
    chosen_target_minor: NonnegativeMinor | None
    selected_item_refs: list[Id] = Field(default_factory=list, max_length=20)
    explicit_substitution_preference_refs: list[Id] = Field(
        default_factory=list, max_length=12
    )
    protected_reference: Id
    purchase_authorized: Literal[False] = False


class FamilySupportBrief(FinanceRecord):
    audience_principal_ids: list[Id] = Field(min_length=1, max_length=8)
    arrangement_id: Id
    intent: Literal[
        "gift", "documented_loan", "reimbursement", "optional_reserve", "undecided"
    ]
    currency: Currency
    proposed_support_minor: NonnegativeMinor | None
    agreement_evidence_ref: Id | None = None
    repayment_state: Literal[
        "not_applicable", "expected", "received", "disputed", "unknown"
    ]
    legal_debt_asserted: Literal[False] = False
    transfer_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_intent(self) -> FamilySupportBrief:
        if self.intent == "documented_loan" and self.agreement_evidence_ref is None:
            raise ValueError("documented loan requires agreement evidence")
        if (
            self.intent in {"gift", "optional_reserve"}
            and self.repayment_state != "not_applicable"
        ):
            raise ValueError("gift or optional reserve cannot imply repayment")
        return self


_PREPARATION: dict[str, dict[str, Any]] = {
    "F18": {
        "professional_kind": "tax",
        "evidence_requests": [
            "entity_owner_identity",
            "household_facing_exposures",
            "jurisdictions",
        ],
        "questions": [
            "entity_household_boundary",
            "cross_border_reporting",
            "qualified_advisor_scope",
        ],
        "exclusions": [
            "company_books_import",
            "tax_characterization",
            "legal_determination",
        ],
    },
    "F19": {
        "professional_kind": "tax",
        "evidence_requests": [
            "tax_year_jurisdictions",
            "person_attributed_income",
            "withholding_payments",
            "rule_version",
        ],
        "questions": [
            "estimated_payment_dependencies",
            "withholding_alignment",
            "rule_model_exclusions",
        ],
        "exclusions": ["tax_filing", "payment_execution", "unvalidated_tax_estimate"],
    },
    "F20": {
        "professional_kind": "tax",
        "evidence_requests": [
            "filing_assumptions",
            "ownership_allocation",
            "reconciled_tax_evidence",
        ],
        "questions": [
            "filing_status_dependencies",
            "deduction_evidence",
            "entity_foreign_interests",
        ],
        "exclusions": [
            "equal_split_assumption",
            "merchant_tax_characterization",
            "filing_decision",
        ],
    },
    "F25": {
        "professional_kind": "insurance",
        "evidence_requests": [
            "policy_terms_dates",
            "limits_exclusions",
            "deductibles",
            "claim_evidence",
        ],
        "questions": [
            "coverage_exclusions",
            "renewal_comparison",
            "claim_requirements",
        ],
        "exclusions": ["binding_coverage", "policy_cancellation", "claim_assertion"],
    },
    "F27": {
        "professional_kind": "legal",
        "evidence_requests": [
            "document_exists_date",
            "authorized_fiduciary_metadata",
            "protected_document_reference",
        ],
        "questions": [
            "beneficiary_consistency",
            "missing_documents",
            "incapacity_authority",
        ],
        "exclusions": [
            "credential_storage",
            "legal_instrument",
            "automatic_access_grant",
        ],
    },
    "F29": {
        "professional_kind": "legal",
        "evidence_requests": [
            "affected_cash_commitments",
            "benefit_coverage_terms",
            "authorized_legal_facts",
        ],
        "questions": [
            "near_term_stabilization",
            "reversible_steps",
            "professional_dependencies",
        ],
        "exclusions": [
            "eligibility_inference",
            "relationship_as_authority",
            "irreversible_action",
        ],
    },
}


def preparation_workflow(capability_id: str) -> dict[str, Any]:
    """A fixed checklist; no retrieval, source disclosure or advisor contact."""
    if capability_id not in _PREPARATION:
        raise ValueError("preparation capability unavailable")
    return {
        "capability_id": capability_id,
        "readiness": "professional_review",
        "mode": "preparation_only",
        **{
            key: list(value) if isinstance(value, list) else value
            for key, value in _PREPARATION[capability_id].items()
        },
        "next_step": "select_authorized_evidence_for_review",
        "external_action_authorized": False,
        "disclosure_authorized": False,
    }


_COMPOUND = {
    "career_leave_move": ("F16", "F17", "F07", "F14", "F19", "F23"),
    "support_and_reserves": ("F26", "F08", "F28"),
    "balance_drop_purchase": ("F12", "F11", "F07", "F15"),
    "retirement_incapacity": ("F23", "F25", "F27"),
}
_READINESS = frozenset(
    {
        "ready",
        "qualified",
        "missing_evidence",
        "model_unavailable",
        "permission_required",
        "professional_review",
        "temporarily_unavailable",
    }
)


def compound_readiness(
    workflow_id: str,
    readiness_by_capability: Mapping[str, str],
) -> dict[str, Any]:
    """Compose dependency limits; missing evidence never becomes a verdict.

    The caller supplies already authorized readiness only. This function never
    queries another adult's records or reveals hidden dependency identifiers.
    """
    dependencies = _COMPOUND.get(workflow_id)
    if dependencies is None:
        raise ValueError("compound workflow unavailable")
    if set(readiness_by_capability) - set(dependencies):
        raise ValueError("unexpected compound capability")
    if any(value not in _READINESS for value in readiness_by_capability.values()):
        raise ValueError("invalid capability readiness")
    states = {
        key: readiness_by_capability.get(key, "missing_evidence")
        for key in dependencies
    }
    unresolved = [key for key in dependencies if states[key] != "ready"]
    # Compound planning is qualified even if component arithmetic is ready:
    # an integrated model and owner decision remain separate requirements.
    return {
        "workflow_id": workflow_id,
        "readiness": "qualified" if not unresolved else "missing_evidence",
        "dependencies": [
            {"capability_id": key, "readiness": states[key]} for key in dependencies
        ],
        "available_output": "dependency_checklist_and_reversible_options",
        "next_step": "resolve_first_dependency"
        if unresolved
        else "review_integrated_assumptions",
        "first_dependency": unresolved[0] if unresolved else None,
        "financial_verdict_supported": False,
        "external_action_authorized": False,
    }
