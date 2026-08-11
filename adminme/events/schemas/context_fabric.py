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
registry.register("triage.feedback.recorded", 1, TriageFeedbackRecordedV1)
registry.register("triage.policy_rule.approved", 1, TriagePolicyRuleApprovedV1)
registry.register("triage.policy_rule.revoked", 1, TriagePolicyRuleRevokedV1)


__all__ = [
    "ContextMembershipConfirmedV1",
    "ContextMembershipRevokedV1",
    "ContextMergedV1",
    "ContextSplitV1",
    "TriageFeedbackRecordedV1",
    "TriagePolicyRuleApprovedV1",
    "TriagePolicyRuleRevokedV1",
]
