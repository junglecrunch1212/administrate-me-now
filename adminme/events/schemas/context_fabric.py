"""Explicit member decisions consumed by the rebuildable Context Fabric."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


ContextObjectKind = Literal[
    "source_event",
    "triage_candidate",
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "recurrence",
    "note",
    "party",
    "asset",
    "account",
    "context",
]


class ContextMembershipConfirmedV1(BaseModel):
    model_config = {"extra": "forbid"}
    context_id: str = Field(min_length=1, max_length=240)
    object_kind: ContextObjectKind
    object_id: str = Field(min_length=1, max_length=240)
    confirmed_by_member_id: str = Field(min_length=1, max_length=240)
    confirmed_at: datetime
    expected_last_event_id: str | None = Field(default=None, max_length=240)


class ContextMembershipRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}
    context_id: str = Field(min_length=1, max_length=240)
    object_kind: ContextObjectKind
    object_id: str = Field(min_length=1, max_length=240)
    revoked_by_member_id: str = Field(min_length=1, max_length=240)
    revoked_at: datetime
    reason: str = Field(min_length=1, max_length=240)


class ContextMergedV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_context_ids: list[str] = Field(min_length=2, max_length=20)
    target_context_id: str = Field(min_length=1, max_length=240)
    merged_by_member_id: str = Field(min_length=1, max_length=240)
    merged_at: datetime

    @model_validator(mode="after")
    def target_is_not_source(self) -> "ContextMergedV1":
        if self.target_context_id in self.source_context_ids:
            raise ValueError("target context must be distinct from source contexts")
        if len(set(self.source_context_ids)) != len(self.source_context_ids):
            raise ValueError("source context ids must be unique")
        return self


class ContextSplitV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_context_id: str = Field(min_length=1, max_length=240)
    target_context_id: str = Field(min_length=1, max_length=240)
    object_kind: ContextObjectKind
    object_ids: list[str] = Field(min_length=1, max_length=100)
    split_by_member_id: str = Field(min_length=1, max_length=240)
    split_at: datetime

    @model_validator(mode="after")
    def target_and_objects_are_distinct(self) -> "ContextSplitV1":
        if self.target_context_id == self.source_context_id:
            raise ValueError("target context must be distinct from source context")
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("split object ids must be unique")
        return self


class ContextRelationshipRejectedV1(BaseModel):
    """An exact member correction of one proposed source-to-source edge."""

    model_config = {"extra": "forbid"}
    relationship_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    related_source_event_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: str = Field(min_length=1, max_length=120)
    provider_account_id: str = Field(min_length=1, max_length=500)
    workspace: Literal["personal", "work"]
    algorithm: Literal["message-context-pack-v2"]
    rejected_by_member_id: str = Field(min_length=1, max_length=240)
    rejected_at: datetime
    reason: Literal["unrelated_sources"]

    @model_validator(mode="after")
    def distinct_sources(self) -> "ContextRelationshipRejectedV1":
        if self.source_event_id == self.related_source_event_id:
            raise ValueError("relationship correction sources must be distinct")
        return self


class ContextRelationshipConfirmedV1(BaseModel):
    """Explicit member reinstatement/link for one exact source pair."""

    model_config = {"extra": "forbid"}
    relationship_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    related_source_event_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: str = Field(min_length=1, max_length=120)
    provider_account_id: str = Field(min_length=1, max_length=500)
    workspace: Literal["personal", "work"]
    algorithm: Literal["message-context-pack-v2"]
    confirmed_by_member_id: str = Field(min_length=1, max_length=240)
    confirmed_at: datetime
    reason: Literal["member_reinstated"]

    @model_validator(mode="after")
    def distinct_sources(self) -> "ContextRelationshipConfirmedV1":
        if self.source_event_id == self.related_source_event_id:
            raise ValueError("relationship correction sources must be distinct")
        return self


class ContextSourceTextCorrectedV1(BaseModel):
    """Member evidence that retained provider text is quoted/forwarded history."""

    model_config = {"extra": "forbid"}
    correction_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    corrected_by_member_id: str = Field(min_length=1, max_length=240)
    corrected_at: datetime
    reason: Literal["quoted_or_forwarded_history"]
    evidence_quote_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class ContextSourceTextReinstatedV1(BaseModel):
    """Explicit reversal of an exact source-text correction."""

    model_config = {"extra": "forbid"}
    correction_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    reinstated_by_member_id: str = Field(min_length=1, max_length=240)
    reinstated_at: datetime
    reason: Literal["member_reinstated"]


FeedbackReason = Literal[
    "not_mine",
    "not_actionable",
    "already_done",
    "duplicate",
    "wrong_kind",
    "wrong_context",
    "wrong_owner",
    "recurring_normal",
    "low_signal",
    "spam",
    "provider_promotion",
    "other",
]


class TriageFeedbackRecordedV1(BaseModel):
    model_config = {"extra": "forbid"}
    feedback_id: str = Field(min_length=1, max_length=240)
    proposal_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    reason: FeedbackReason
    context_id: str | None = Field(default=None, max_length=240)
    target_kind: str | None = Field(default=None, max_length=120)
    source_adapter: str | None = Field(default=None, max_length=240)
    provider_account_id: str | None = Field(default=None, max_length=500)
    workspace: Literal["personal", "work"] | None = None
    sender_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime


PolicyScope = Literal["candidate_kind", "sender", "context", "account", "workspace"]
PolicyDisposition = Literal["quiet", "always_surface", "route_to_low_signal"]


class TriagePolicyRuleApprovedV1(BaseModel):
    model_config = {"extra": "forbid"}
    rule_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    scope_kind: PolicyScope
    scope_value_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: PolicyDisposition
    enforcement_mode: Literal["shadow"] = "shadow"
    preview_affected_count: int = Field(ge=0)
    approved_by_member_id: str = Field(min_length=1, max_length=240)
    approved_at: datetime
    expires_at: datetime | None = None


class TriagePolicyRuleRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}
    rule_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    revoked_by_member_id: str = Field(min_length=1, max_length=240)
    revoked_at: datetime
    reason: str = Field(min_length=1, max_length=240)


registry.register("context.membership.confirmed", 1, ContextMembershipConfirmedV1)
registry.register("context.membership.revoked", 1, ContextMembershipRevokedV1)
registry.register("context.merged", 1, ContextMergedV1)
registry.register("context.split", 1, ContextSplitV1)
registry.register("context.relationship.rejected", 1, ContextRelationshipRejectedV1)
registry.register("context.relationship.confirmed", 1, ContextRelationshipConfirmedV1)
registry.register("context.source_text.corrected", 1, ContextSourceTextCorrectedV1)
registry.register("context.source_text.reinstated", 1, ContextSourceTextReinstatedV1)
registry.register("triage.feedback.recorded", 1, TriageFeedbackRecordedV1)
registry.register("triage.policy_rule.approved", 1, TriagePolicyRuleApprovedV1)
registry.register("triage.policy_rule.revoked", 1, TriagePolicyRuleRevokedV1)


__all__ = [
    "ContextMembershipConfirmedV1",
    "ContextMembershipRevokedV1",
    "ContextMergedV1",
    "ContextSplitV1",
    "ContextRelationshipRejectedV1",
    "ContextRelationshipConfirmedV1",
    "ContextSourceTextCorrectedV1",
    "ContextSourceTextReinstatedV1",
    "TriageFeedbackRecordedV1",
    "TriagePolicyRuleApprovedV1",
    "TriagePolicyRuleRevokedV1",
]
