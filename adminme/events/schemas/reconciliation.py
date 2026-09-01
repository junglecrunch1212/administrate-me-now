"""Typed cross-source reconciliation and Triage-admission receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry
from adminme.source_policy import ContextMode, SourceCoordinate


EvidenceRelationshipType = Literal[
    "exact_duplicate",
    "same_provider_object",
    "same_conversation",
    "exact_external_identifier",
    "exact_canonical_object_link",
    "exact_payment_reference",
    "exact_account_reference",
    "exact_appointment_reference",
    "strong_deterministic_correlation",
    "weak_review_suggestion",
    "contradiction",
    "satisfaction_or_completion",
    "cancellation_or_supersession",
]

AdmissionDecision = Literal[
    "admit",
    "update_existing_matter",
    "attach_evidence",
    "alert_only",
    "context_only",
    "already_satisfied",
    "superseded",
    "duplicate",
    "conflict_review",
    "source_policy_suppression",
    "unresolved",
]


class ReconciliationReferenceV1(BaseModel):
    """One content-free exact reference suitable for an explicit share."""

    model_config = {"extra": "forbid", "frozen": True}

    kind: Literal[
        "provider_object",
        "external_identifier",
        "canonical_object",
        "payment_reference",
        "account_reference",
        "appointment_reference",
        "strong_correlation",
    ]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReconciliationTypedFactSharedV1(BaseModel):
    """Explicitly shared typed state; raw source content is never copied."""

    model_config = {"extra": "forbid"}

    fact_id: str = Field(min_length=1, max_length=240)
    source_member_id: str = Field(min_length=1, max_length=240)
    shared_with_member_ids: list[str] = Field(min_length=1, max_length=20)
    shared_by_member_id: str = Field(min_length=1, max_length=240)
    share_grant_id: str = Field(min_length=1, max_length=240)
    source_reference_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_kind: Literal[
        "payment_state",
        "account_state",
        "appointment_state",
        "canonical_object_state",
        "obligation_state",
    ]
    references: list[ReconciliationReferenceV1] = Field(min_length=1, max_length=20)
    current_state: Literal[
        "current",
        "satisfied",
        "completed",
        "canceled",
        "superseded",
    ]
    occurred_at: datetime
    expires_at: datetime | None = None
    raw_body_included: Literal[False] = False
    owner_transfer: Literal[False] = False
    action_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_share(self) -> "ReconciliationTypedFactSharedV1":
        if self.shared_by_member_id != self.source_member_id:
            raise ValueError(
                "only the source member may explicitly share the typed fact"
            )
        if len(set(self.shared_with_member_ids)) != len(self.shared_with_member_ids):
            raise ValueError("shared-with member ids must be unique")
        if self.source_member_id in self.shared_with_member_ids:
            raise ValueError("a cross-member share cannot target its source member")
        return self


class ReconciliationEvidenceRelationshipV1(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    relationship_id: str = Field(pattern=r"^rel_[0-9a-f]{32}$")
    relationship_type: EvidenceRelationshipType
    related_source_event_id: str = Field(min_length=1, max_length=240)
    authority: Literal[
        "provider_exact",
        "member_confirmed",
        "explicitly_shared_typed_fact",
        "deterministic_derived",
        "review_suggestion",
    ]
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    automatic_effect: bool

    @model_validator(mode="after")
    def weak_relationship_has_no_effect(self) -> "ReconciliationEvidenceRelationshipV1":
        if self.relationship_type == "weak_review_suggestion" and (
            self.automatic_effect or self.authority != "review_suggestion"
        ):
            raise ValueError("weak similarity must remain a review-only suggestion")
        return self


class ReconciliationPolicySnapshotV1(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    policy_key: str = Field(pattern=r"^spv2_[0-9a-f]{64}$")
    policy_revision: int = Field(ge=0)
    policy_origin: Literal["exact_override", "recommended_default", "migration_needed"]
    triage_route: Literal["direct_work_order_proposal", "review", "none"]
    alert_route: Literal["security", "financial", "actionable", "none"]
    context_route: ContextMode
    reason_codes: list[str] = Field(min_length=1, max_length=40)


class TriageReconciliationDecidedV1(BaseModel):
    """One deterministic admission revision for one proposed candidate."""

    model_config = {"extra": "forbid"}

    reconciliation_id: str = Field(pattern=r"^recon_[0-9a-f]{32}$")
    proposal_id: str = Field(min_length=1, max_length=240)
    primary_proposal_id: str | None = Field(default=None, min_length=1, max_length=240)
    matter_id: str = Field(pattern=r"^ctx_reconciled_[0-9a-f]{32}$")
    owner_member_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    evidence_source_event_ids: list[str] = Field(min_length=1, max_length=100)
    member_authored_text_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    relationships: list[ReconciliationEvidenceRelationshipV1] = Field(
        default_factory=list, max_length=100
    )
    decision: AdmissionDecision
    decision_revision: int = Field(ge=1)
    supersedes_reconciliation_event_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )
    policy: ReconciliationPolicySnapshotV1
    source_coordinate: SourceCoordinate
    freshness: Literal["current", "stale", "unknown"]
    priority_rank: int = Field(ge=0, le=100)
    priority_basis: list[
        Literal[
            "explicit_member_instruction",
            "exact_member_commitment",
            "verified_deadline",
            "known_human_actionable_request",
            "current_state_and_consequence",
            "household_policy",
            "standard_actionable_candidate",
        ]
    ] = Field(min_length=1, max_length=10)
    reason_codes: list[str] = Field(min_length=1, max_length=60)
    decided_at: datetime
    admission_active: bool = False
    raw_cross_member_join: Literal[False] = False
    weak_similarity_changed_identity: Literal[False] = False
    weak_similarity_transferred_ownership: Literal[False] = False
    weak_similarity_authorized_action: Literal[False] = False
    provider_effects: Literal[False] = False
    llm_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision_authority(self) -> "TriageReconciliationDecidedV1":
        automatic = [item for item in self.relationships if item.automatic_effect]
        relationship_required = {
            "update_existing_matter",
            "attach_evidence",
            "duplicate",
            "conflict_review",
        }
        if self.decision in relationship_required and not automatic:
            raise ValueError(
                "relationship decision requires deterministic exact evidence"
            )
        if self.primary_proposal_id is None and self.decision in relationship_required:
            raise ValueError(
                "relationship decision requires one existing primary proposal"
            )
        if self.decision == "alert_only" and self.policy.alert_route == "none":
            raise ValueError("alert-only decision requires a typed alert route")
        if self.decision_revision == 1 and self.supersedes_reconciliation_event_id:
            raise ValueError("first decision cannot supersede another decision")
        if self.decision_revision > 1 and not self.supersedes_reconciliation_event_id:
            raise ValueError("decision correction must identify its predecessor")
        return self


registry.register(
    "context.reconciliation.typed_fact_shared", 1, ReconciliationTypedFactSharedV1
)
registry.register("triage.reconciliation.decided", 1, TriageReconciliationDecidedV1)


__all__ = [
    "AdmissionDecision",
    "EvidenceRelationshipType",
    "ReconciliationEvidenceRelationshipV1",
    "ReconciliationPolicySnapshotV1",
    "ReconciliationReferenceV1",
    "ReconciliationTypedFactSharedV1",
    "TriageReconciliationDecidedV1",
]
