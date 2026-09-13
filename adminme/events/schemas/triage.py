"""Human-gated proposal events for the canonical Triage surface.

Source observations remain facts.  These events represent derived candidates
that may become household truth or an external write only after a member has
reviewed (and, when necessary, enriched) them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.obligation_assessment import PersonalObligationAssessmentV1

from adminme.events.registry import registry


TriageTargetKind = Literal[
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
]
TriageProvider = Literal[
    "google_calendar",
    "apple_calendar",
    "apple_reminders",
    "google_tasks",
]


class TriageRouteV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1, max_length=240)
    provider: TriageProvider | None = None
    provider_account_id: str | None = Field(default=None, min_length=1, max_length=500)
    container_external_id: str | None = Field(
        default=None, min_length=1, max_length=1000
    )


class TriageItemProposedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    target_kind: TriageTargetKind
    owner_member_id: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=1000)
    candidate: dict[str, Any] = Field(default_factory=dict)
    route: TriageRouteV1 | None = None
    source_event_id: str = Field(min_length=1, max_length=240)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list, max_length=20)


class TriageItemProposedV2(TriageItemProposedV1):
    """A deterministically eligible child with complete source lineage."""

    model_config = {"extra": "forbid"}
    parent_intent_id: str | None = Field(default=None, min_length=1, max_length=240)
    candidate_key: str | None = Field(default=None, min_length=1, max_length=120)
    extraction_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    eligibility_rule_ids: list[str] = Field(min_length=1, max_length=20)


class TriageContextEvidenceV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1, max_length=240)
    quote: str = Field(min_length=1, max_length=1000)


class TriageContextSourceV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1, max_length=240)
    relation: Literal["thread", "related"]
    reason_codes: list[
        Literal[
            "provider_thread_id",
            "participant_overlap",
            "participant_reference_overlap",
            "subject_token_overlap",
            "attachment_token_overlap",
        ]
    ] = Field(min_length=1, max_length=4)


class TriageContextSourceV2(BaseModel):
    """One source admitted by the conservative message-context v2 contract.

    Weak participant/name/subject/filename observations remain explainable
    corroboration, but every cross-thread source must also carry one strong
    relationship reason.
    """

    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1, max_length=240)
    relation: Literal["thread", "related"]
    reason_codes: list[
        Literal[
            "provider_thread_id",
            "rfc_reference",
            "attachment_identity",
            "member_confirmed_context",
            "participant_overlap",
            "participant_reference_overlap",
            "subject_token_overlap",
            "attachment_filename_overlap",
        ]
    ] = Field(min_length=1, max_length=8)
    relationship_source_event_ids: list[str] = Field(max_length=100)

    @model_validator(mode="after")
    def exact_relationship_provenance(self) -> "TriageContextSourceV2":
        strong = {
            "rfc_reference",
            "attachment_identity",
            "member_confirmed_context",
        }
        if self.relation == "related" and (
            not strong.intersection(self.reason_codes)
            or not self.relationship_source_event_ids
        ):
            raise ValueError(
                "related context requires a strong reason and exact source edge"
            )
        if self.relation == "thread" and self.relationship_source_event_ids:
            raise ValueError("provider-thread context cannot carry cross-thread edges")
        return self


class TriageContextInterpretationV1(BaseModel):
    """Bounded language interpretation; never authority or source truth."""

    model_config = {"extra": "forbid"}
    contract: Literal["triage-context-interpretation-v1"]
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    thread_key: str = Field(min_length=1, max_length=64)
    anchor_event_id: str = Field(min_length=1, max_length=240)
    compact_summary: str = Field(min_length=1, max_length=160)
    case_summary: str | None = Field(default=None, min_length=1, max_length=600)
    why_member: str | None = Field(default=None, min_length=1, max_length=240)
    next_step: str | None = Field(default=None, min_length=1, max_length=240)
    thread_state: Literal[
        "new",
        "in_progress",
        "waiting_on_member",
        "waiting_on_other",
        "blocked",
        "unclear",
    ]
    action_owner: Literal["member", "group_including_member", "unknown"]
    lifecycle_state: Literal["open", "unknown"]
    unresolved_questions: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[TriageContextEvidenceV1] = Field(min_length=1, max_length=5)
    context_source_event_ids: list[str] = Field(min_length=1, max_length=20)
    context_sources: list[TriageContextSourceV1] = Field(min_length=1, max_length=20)
    related_source_event_ids: list[str] = Field(default_factory=list, max_length=10)


class TriageContextInterpretationV2(BaseModel):
    """Episode-bounded interpretation grounded in authored source text."""

    model_config = {"extra": "forbid"}
    contract: Literal["triage-context-interpretation-v2"]
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    thread_key: str = Field(min_length=1, max_length=64)
    anchor_event_id: str = Field(min_length=1, max_length=240)
    compact_summary: str = Field(min_length=1, max_length=160)
    case_summary: str | None = Field(default=None, min_length=1, max_length=600)
    why_member: str | None = Field(default=None, min_length=1, max_length=240)
    next_step: str | None = Field(default=None, min_length=1, max_length=240)
    thread_state: Literal[
        "new",
        "in_progress",
        "waiting_on_member",
        "waiting_on_other",
        "blocked",
        "unclear",
    ]
    action_owner: Literal["member", "group_including_member", "unknown"]
    lifecycle_state: Literal["open", "unknown"]
    unresolved_questions: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[TriageContextEvidenceV1] = Field(min_length=1, max_length=5)
    context_source_event_ids: list[str] = Field(min_length=1, max_length=20)
    context_sources: list[TriageContextSourceV2] = Field(min_length=1, max_length=20)
    related_source_event_ids: list[str] = Field(default_factory=list, max_length=10)


class TriageItemProposedV3(TriageItemProposedV2):
    """Conversation-aware proposal with bounded, review-only explanation."""

    model_config = {"extra": "forbid"}
    interpretation: TriageContextInterpretationV1 | None = None


class TriageItemProposedV4(TriageItemProposedV2):
    """Conservative contextual proposal with explicit provider source time."""

    model_config = {"extra": "forbid"}
    interpretation: TriageContextInterpretationV2 | None = None
    source_occurred_at: datetime | None = None


class TriageContextInterpretationV3(BaseModel):
    """Source-grounded action/condition assessment, still review-only."""

    model_config = {"extra": "forbid"}
    contract: Literal["triage-context-interpretation-v3"]
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    thread_key: str = Field(min_length=1, max_length=64)
    anchor_event_id: str = Field(min_length=1, max_length=240)
    compact_summary: str = Field(min_length=1, max_length=160)
    case_summary: str | None = Field(default=None, max_length=600)
    why_member: str | None = Field(default=None, max_length=240)
    next_step: str | None = Field(default=None, max_length=240)
    thread_state: Literal[
        "new",
        "in_progress",
        "waiting_on_member",
        "waiting_on_other",
        "blocked",
        "unclear",
    ]
    action_owner: Literal["member", "group_including_member", "unknown"]
    lifecycle_state: Literal["open", "unknown"]
    unresolved_questions: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[TriageContextEvidenceV1] = Field(min_length=1, max_length=5)
    context_source_event_ids: list[str] = Field(min_length=1, max_length=20)
    context_sources: list[TriageContextSourceV2] = Field(min_length=1, max_length=20)
    related_source_event_ids: list[str] = Field(default_factory=list, max_length=10)
    obligation_assessment: PersonalObligationAssessmentV1 | None = None
    assessment_state: Literal["verified", "invalid", "unavailable"]


class TriageItemProposedV5(TriageItemProposedV2):
    model_config = {"extra": "forbid"}
    interpretation: TriageContextInterpretationV3 | None = None
    source_occurred_at: datetime | None = None


class TriageSourceProposalDraftV1(BaseModel):
    """Exact already-assessed proposal, awaiting its generated extraction ID."""

    model_config = {"extra": "forbid"}
    idempotency_key: str = Field(min_length=1, max_length=240)
    proposal_schema_version: Literal[4, 5]
    payload: dict[str, Any]

    @model_validator(mode="after")
    def valid_proposal_without_extraction_id(self) -> "TriageSourceProposalDraftV1":
        model = (
            TriageItemProposedV5
            if self.proposal_schema_version == 5
            else TriageItemProposedV4
        )
        proposal = model.model_validate(self.payload)
        if proposal.extraction_event_id is not None:
            raise ValueError("publication draft cannot invent an extraction event ID")
        return self


class TriageSourcePublicationPlannedV1(BaseModel):
    """Private durable suffix, committed atomically with its source assessment."""

    model_config = {"extra": "forbid"}
    contract: Literal["triage-source-publication-v1"]
    source_event_id: str = Field(min_length=1, max_length=240)
    assessment_idempotency_key: str = Field(min_length=1, max_length=240)
    assessment_schema_version: Literal[3, 4, 5]
    proposals: list[TriageSourceProposalDraftV1] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_exact_source_drafts(self) -> "TriageSourcePublicationPlannedV1":
        keys = [proposal.idempotency_key for proposal in self.proposals]
        if len(keys) != len(set(keys)):
            raise ValueError("publication draft keys must be unique")
        if any(
            proposal.payload["source_event_id"] != self.source_event_id
            for proposal in self.proposals
        ):
            raise ValueError("publication draft must belong to the assessed source")
        return self


class TriageCurrentnessPublicationPlannedV1(BaseModel):
    """Private durable suffix, committed atomically with its source assessment."""

    model_config = {"extra": "forbid"}
    contract: Literal["triage-source-publication-v1"]
    source_event_id: str = Field(min_length=1, max_length=240)
    assessment_idempotency_key: str = Field(min_length=1, max_length=240)
    assessment_schema_version: Literal[1]
    proposals: list[TriageSourceProposalDraftV1] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_exact_source_drafts(self) -> "TriageCurrentnessPublicationPlannedV1":
        keys = [proposal.idempotency_key for proposal in self.proposals]
        if len(keys) != len(set(keys)):
            raise ValueError("publication draft keys must be unique")
        if any(
            proposal.payload["source_event_id"] != self.source_event_id
            for proposal in self.proposals
        ):
            raise ValueError("publication draft must belong to the assessed source")
        return self


class TriageSourcePublicationCompletedV1(BaseModel):
    """Content-free acknowledgement of every exact private publication draft."""

    model_config = {"extra": "forbid"}
    contract: Literal["triage-source-publication-completed-v1"]
    plan_event_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    assessment_event_id: str = Field(min_length=1, max_length=240)
    planned_count: int = Field(ge=1, le=20)
    published_count: int = Field(ge=0, le=20)
    already_present_count: int = Field(ge=0, le=20)
    member_decided_count: int = Field(ge=0, le=20)
    superseded_count: int = Field(ge=0, le=20)

    @model_validator(mode="after")
    def all_drafts_terminal(self) -> "TriageSourcePublicationCompletedV1":
        if self.planned_count != (
            self.published_count
            + self.already_present_count
            + self.member_decided_count
            + self.superseded_count
        ):
            raise ValueError(
                "every publication draft requires one terminal disposition"
            )
        return self


TriageSourceSurface = Literal[
    "today",
    "triage",
    "comms",
    "reminders",
    "calendar",
    "people",
    "notes",
    "finance",
    "scoreboard",
    "settings",
    "openclaw",
]


class TriageIntentSubmittedV1(BaseModel):
    """The member's exact proposal input; never canonical household truth."""

    model_config = {"extra": "forbid"}
    intent_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=2000)
    source_surface: TriageSourceSurface
    kind_hint: TriageTargetKind | None = None
    source_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    submitted_at: datetime
    client_request_id: str = Field(min_length=8, max_length=120)


class TriageEvidenceSpanV1(BaseModel):
    model_config = {"extra": "forbid"}
    field: str = Field(min_length=1, max_length=120)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def require_ordered_span(self) -> "TriageEvidenceSpanV1":
        if self.end <= self.start:
            raise ValueError("evidence span end must follow start")
        return self


class TriageFeatureCandidateV1(BaseModel):
    model_config = {"extra": "forbid"}
    candidate_key: str = Field(min_length=1, max_length=120)
    target_kind: TriageTargetKind
    title: str = Field(min_length=1, max_length=1000)
    fields: dict[str, Any] = Field(default_factory=dict)
    evidence_spans: list[TriageEvidenceSpanV1] = Field(
        default_factory=list, max_length=20
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list, max_length=20)


class TriageIntentFeaturesExtractedV1(BaseModel):
    model_config = {"extra": "forbid"}
    intent_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    skill_call_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    extractor_id: str = Field(min_length=1, max_length=120)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    candidates: list[TriageFeatureCandidateV1] = Field(
        default_factory=list, max_length=10
    )
    extraction_state: Literal["complete", "defensive_default", "unavailable", "invalid"]
    extracted_at: datetime


class TriageIntentFeaturesExtractedV2(TriageIntentFeaturesExtractedV1):
    """A terminal, machine-auditable assessment of one candidate source.

    Empty candidate lists are intentional outcomes, never an absent event.
    ``reason_codes`` explain why a source yielded candidates, none, a
    quarantine, or an extraction failure without copying source content.
    """

    model_config = {"extra": "forbid"}
    assessment_outcome: Literal[
        "eligible_candidates", "no_candidate", "quarantined", "failed"
    ]
    reason_codes: list[str] = Field(min_length=1, max_length=20)


class TriageIntentFeaturesExtractedV3(TriageIntentFeaturesExtractedV2):
    """Expanded reviewed candidate batch without rewriting v1/v2 history."""

    candidates: list[TriageFeatureCandidateV1] = Field(
        default_factory=list, max_length=20
    )


class TriageIntentFeaturesExtractedV4(TriageIntentFeaturesExtractedV3):
    """Append-only replacement assessment produced by reviewed recovery."""

    model_config = {"extra": "forbid"}
    supersedes_assessment_event_id: str = Field(min_length=1, max_length=240)
    recovery_request_event_id: str = Field(min_length=1, max_length=240)
    recovery_reason: Literal[
        "language_seam_OpenClawTimeout",
        "language_seam_OpenClawUnreachable",
    ]
    recovery_policy: Literal["source-assessment-recovery-v1"]
    recovery_attempt_limit: Literal[3]


class TriageIntentFeaturesExtractedV5(TriageIntentFeaturesExtractedV3):
    """Explicit semantic revision; prior completed assessments remain history."""

    model_config = {"extra": "forbid"}
    supersedes_assessment_event_id: str = Field(min_length=1, max_length=240)
    revision_request_event_id: str = Field(min_length=1, max_length=240)
    assessment_revision: Literal["triage-semantics-2026-09-v1"]
    revision_attempt_limit: Literal[3]


class TriageCurrentnessFeaturesExtractedV1(TriageIntentFeaturesExtractedV3):
    """Explicit semantic revision; prior completed assessments remain history."""

    model_config = {"extra": "forbid"}
    supersedes_assessment_event_id: str = Field(min_length=1, max_length=240)
    revision_request_event_id: str = Field(min_length=1, max_length=240)
    assessment_revision: Literal["currentness-v1"]
    revision_attempt_limit: Literal[3]


class TriageItemEnrichedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    enriched_at: str = Field(min_length=1)
    enriched_by_member_id: str = Field(min_length=1, max_length=240)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    target_kind: TriageTargetKind | None = None
    candidate_updates: dict[str, Any] = Field(default_factory=dict)
    route: TriageRouteV1 | None = None

    @model_validator(mode="after")
    def require_change(self) -> "TriageItemEnrichedV1":
        if not any(
            (
                self.title is not None,
                self.target_kind is not None,
                bool(self.candidate_updates),
                self.route is not None,
            )
        ):
            raise ValueError("an enrichment must change at least one field")
        return self


class TriageItemConfirmedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    confirmed_at: str = Field(min_length=1)
    confirmed_by_member_id: str = Field(min_length=1, max_length=240)
    materialized_event_ids: list[str] = Field(min_length=1, max_length=10)


class TriageItemConfirmedV2(BaseModel):
    """The member's immutable decision, recorded before any materialization."""

    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    confirmed_at: str = Field(min_length=1)
    confirmed_by_member_id: str = Field(min_length=1, max_length=240)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class TriageItemMaterializedV1(BaseModel):
    """Receipt linking a prior confirmation to its allowlisted event batch."""

    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    confirmation_event_id: str = Field(min_length=1, max_length=240)
    materialized_event_ids: list[str] = Field(min_length=1, max_length=10)
    materialized_at: str = Field(min_length=1)


class TriageItemDismissedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    dismissed_at: str = Field(min_length=1)
    dismissed_by_member_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=500)


class TriageItemSupersededV1(BaseModel):
    """System invalidation when the exact source version has changed."""

    model_config = {"extra": "forbid"}
    proposal_id: str = Field(min_length=1, max_length=240)
    superseded_at: str = Field(min_length=1)
    replacement_source_event_id: str = Field(min_length=1, max_length=240)
    reason: Literal["source_changed", "source_deleted", "source_privileged"]


class TriageItemSupersededV2(BaseModel):
    """Reviewed invalidation may retire a superseded eligibility rule."""

    model_config = {"extra": "forbid"}
    proposal_id: str = Field(min_length=1, max_length=240)
    superseded_at: str = Field(min_length=1)
    replacement_source_event_id: str = Field(min_length=1, max_length=240)
    reason: Literal[
        "source_changed",
        "source_deleted",
        "source_privileged",
        "eligibility_rule_replaced",
    ]


class TriageItemSupersededV3(TriageItemSupersededV2):
    """Compare-and-apply supersession preserving a newer member revision."""

    model_config = {"extra": "forbid"}
    expected_last_event_id: str = Field(min_length=1, max_length=240)


class TriageItemSupersededV4(TriageItemSupersededV2):
    """Retire unchanged interpretation despite newer routing receipts.

    A member content/management revision still defeats this compare-and-apply;
    reconciliation and attention evaluation never acquire content authority.
    """

    model_config = {"extra": "forbid"}
    expected_content_revision_event_id: str = Field(min_length=1, max_length=240)
    # Retained predecessors read this key for every version >= 3. Writers
    # include a conservative legacy guard; current readers use only the content
    # revision above. Optional solely for V4 events already in immutable logs.
    expected_last_event_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )


registry.register(
    "triage.source_publication.planned", 1, TriageSourcePublicationPlannedV1
)
registry.register(
    "triage.source_publication.completed", 1, TriageSourcePublicationCompletedV1
)
registry.register("triage.item.proposed", 1, TriageItemProposedV1)
registry.register("triage.item.proposed", 2, TriageItemProposedV2)
registry.register("triage.item.proposed", 3, TriageItemProposedV3)
registry.register("triage.item.proposed", 4, TriageItemProposedV4)
registry.register("triage.item.proposed", 5, TriageItemProposedV5)
registry.register("triage.intent.submitted", 1, TriageIntentSubmittedV1)
registry.register(
    "triage.intent.features_extracted", 1, TriageIntentFeaturesExtractedV1
)
registry.register(
    "triage.intent.features_extracted", 2, TriageIntentFeaturesExtractedV2
)
registry.register(
    "triage.intent.features_extracted", 3, TriageIntentFeaturesExtractedV3
)
registry.register(
    "triage.intent.features_extracted", 4, TriageIntentFeaturesExtractedV4
)
registry.register(
    "triage.intent.features_extracted", 5, TriageIntentFeaturesExtractedV5
)
registry.register(
    "triage.source_revision_publication.planned", 1, TriageSourcePublicationPlannedV1
)
registry.register(
    "triage.source_revision_publication.completed",
    1,
    TriageSourcePublicationCompletedV1,
)
registry.register("triage.item.enriched", 1, TriageItemEnrichedV1)
registry.register("triage.item.confirmed", 1, TriageItemConfirmedV1)
registry.register("triage.item.confirmed", 2, TriageItemConfirmedV2)
registry.register("triage.item.materialized", 1, TriageItemMaterializedV1)
registry.register("triage.item.dismissed", 1, TriageItemDismissedV1)
registry.register("triage.item.superseded", 1, TriageItemSupersededV1)
registry.register("triage.item.superseded", 2, TriageItemSupersededV2)
registry.register("triage.item.superseded", 3, TriageItemSupersededV3)
registry.register("triage.item.superseded", 4, TriageItemSupersededV4)


__all__ = [
    "TriageItemConfirmedV1",
    "TriageItemConfirmedV2",
    "TriageItemDismissedV1",
    "TriageItemEnrichedV1",
    "TriageItemMaterializedV1",
    "TriageItemProposedV1",
    "TriageItemProposedV2",
    "TriageItemProposedV3",
    "TriageItemProposedV4",
    "TriageItemProposedV5",
    "TriageItemSupersededV1",
    "TriageItemSupersededV2",
    "TriageItemSupersededV3",
    "TriageItemSupersededV4",
    "TriageRouteV1",
    "TriageTargetKind",
    "TriageSourceSurface",
    "TriageIntentSubmittedV1",
    "TriageIntentFeaturesExtractedV1",
    "TriageIntentFeaturesExtractedV2",
    "TriageIntentFeaturesExtractedV3",
    "TriageIntentFeaturesExtractedV4",
    "TriageIntentFeaturesExtractedV5",
]

registry.register(
    "triage.currentness.features_extracted", 1, TriageCurrentnessFeaturesExtractedV1
)
registry.register(
    "triage.currentness_publication.planned", 1, TriageCurrentnessPublicationPlannedV1
)
registry.register(
    "triage.currentness_publication.completed", 1, TriageSourcePublicationCompletedV1
)
