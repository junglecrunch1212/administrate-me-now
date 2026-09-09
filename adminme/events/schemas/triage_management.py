"""Typed, member-private events for human-centered Triage management."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


TriageManagementAction = Literal[
    "link_existing",
    "mark_handled",
    "mark_duplicate",
    "keep_context",
    "route_alert",
    "dismiss_not_useful",
    "propose_suppression",
    "ask_clarification",
    "snooze",
    "reopen",
    "correct_extraction",
    "correct_ownership",
]


class TriageItemManagedV1(BaseModel):
    """One reversible local decision about an already-scoped candidate."""

    model_config = {"extra": "forbid"}
    action_id: str = Field(min_length=1, max_length=240)
    proposal_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    action: TriageManagementAction
    action_revision: int = Field(ge=1)
    supersedes_action_id: str | None = Field(default=None, max_length=240)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=100)
    linked_object_kind: str | None = Field(default=None, max_length=120)
    linked_object_id: str | None = Field(default=None, max_length=240)
    duplicate_of_proposal_id: str | None = Field(default=None, max_length=240)
    snoozed_until: datetime | None = None
    corrected_title: str | None = Field(default=None, max_length=500)
    corrected_target_kind: str | None = Field(default=None, max_length=120)
    corrected_owner_member_id: str | None = Field(default=None, max_length=240)
    policy_proposal_id: str | None = Field(default=None, max_length=240)
    reversible: Literal[True] = True
    provider_effects: Literal[False] = False
    managed_at: datetime

    @model_validator(mode="after")
    def action_has_exact_arguments(self) -> "TriageItemManagedV1":
        if (self.linked_object_kind is None) != (self.linked_object_id is None):
            raise ValueError("linked object kind and id must be supplied together")
        if self.action == "link_existing" and self.linked_object_id is None:
            raise ValueError("link_existing requires an exact object")
        if self.action == "mark_duplicate" and self.duplicate_of_proposal_id is None:
            raise ValueError("mark_duplicate requires an exact proposal")
        if self.action == "snooze" and self.snoozed_until is None:
            raise ValueError("snooze requires a review time")
        if self.action == "correct_extraction" and not (
            self.corrected_title or self.corrected_target_kind
        ):
            raise ValueError("correct_extraction requires an exact correction")
        if (
            self.action == "correct_ownership"
            and self.corrected_owner_member_id is None
        ):
            raise ValueError("correct_ownership requires an exact owner")
        if self.action == "propose_suppression" and self.policy_proposal_id is None:
            raise ValueError("propose_suppression requires a policy proposal")
        return self


PolicyScope = Literal["candidate_kind", "sender", "context", "account", "workspace"]
PolicyInstruction = Literal["always", "usually", "ask", "never"]
PolicyOutcome = Literal[
    "dismiss_not_useful",
    "keep_context",
    "mark_duplicate",
    "correct_extraction",
    "correct_ownership",
    "reopen",
]


class TriageSourcePolicyRuleProposedV1(BaseModel):
    """An inert proposal grounded in verified outcomes and an exact preview."""

    model_config = {"extra": "forbid"}
    policy_proposal_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    scope_kind: PolicyScope
    scope_value_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction: PolicyInstruction
    outcome_kind: PolicyOutcome
    verified_feedback_event_ids: list[str] = Field(min_length=1, max_length=100)
    affected_proposal_ids: list[str] = Field(default_factory=list, max_length=100)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_diff: dict[str, object]
    explanation: str = Field(min_length=1, max_length=500)
    proposed_by_member_id: str = Field(min_length=1, max_length=240)
    proposed_at: datetime
    self_activates: Literal[False] = False
    access_widening: Literal[False] = False
    cross_member_effect: Literal[False] = False
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def remains_member_private_and_narrow(self) -> "TriageSourcePolicyRuleProposedV1":
        if self.proposed_by_member_id != self.member_id:
            raise ValueError("a member may propose only their own rule")
        if len(set(self.verified_feedback_event_ids)) != len(
            self.verified_feedback_event_ids
        ):
            raise ValueError("verified feedback ids must be unique")
        if len(set(self.affected_proposal_ids)) != len(self.affected_proposal_ids):
            raise ValueError("affected proposal ids must be unique")
        access = self.policy_diff.get("access")
        writes = self.policy_diff.get("provider_writes")
        if access != {"from": "current", "to": "unchanged"}:
            raise ValueError("a learned rule may not change access")
        if writes != {"from": False, "to": False}:
            raise ValueError("a learned rule may not enable provider writes")
        return self


PolicyDecision = Literal["approve", "edit", "trial", "reject", "suppress"]


class TriageSourcePolicyRuleDecidedV1(BaseModel):
    """Explicit member disposition; only approve/trial can activate a rule."""

    model_config = {"extra": "forbid"}
    decision_id: str = Field(min_length=1, max_length=240)
    policy_proposal_id: str = Field(min_length=1, max_length=240)
    rule_id: str | None = Field(default=None, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    scope_kind: PolicyScope
    scope_value_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_kind: PolicyOutcome
    decision: PolicyDecision
    instruction: PolicyInstruction
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_proposal_ids: list[str] = Field(default_factory=list, max_length=100)
    decided_by_member_id: str = Field(min_length=1, max_length=240)
    decided_at: datetime
    access_widening: Literal[False] = False
    cross_member_effect: Literal[False] = False
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def activation_is_explicit_and_bounded(self) -> "TriageSourcePolicyRuleDecidedV1":
        if self.decided_by_member_id != self.member_id:
            raise ValueError("a member may decide only their own rule")
        if self.decision in {"approve", "trial"} and not self.rule_id:
            raise ValueError("activation requires an exact rule id")
        if self.decision == "trial" and not self.trial_proposal_ids:
            raise ValueError("trial activation must name a bounded cohort")
        if self.decision != "trial" and self.trial_proposal_ids:
            raise ValueError("only trial activation may name a cohort")
        if len(set(self.trial_proposal_ids)) != len(self.trial_proposal_ids):
            raise ValueError("trial proposal ids must be unique")
        return self


PolicyReviewDecision = Literal["retain", "narrow", "suspend", "revoke"]
NegativePolicyOutcome = Literal[
    "false_suppression",
    "missed_obligation",
    "member_correction",
    "scope_mismatch",
]


class TriageSourcePolicyRuleReviewedV1(BaseModel):
    """A reversible review; negative evidence can only narrow or deactivate."""

    model_config = {"extra": "forbid"}
    review_id: str = Field(min_length=1, max_length=240)
    rule_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    decision: PolicyReviewDecision
    negative_outcome: NegativePolicyOutcome | None = None
    previous_instruction: PolicyInstruction
    replacement_instruction: PolicyInstruction | None = None
    reason: str = Field(min_length=1, max_length=500)
    reviewed_by_member_id: str = Field(min_length=1, max_length=240)
    reviewed_at: datetime
    access_widening: Literal[False] = False
    cross_member_effect: Literal[False] = False
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def negative_feedback_never_widens(self) -> "TriageSourcePolicyRuleReviewedV1":
        if self.reviewed_by_member_id != self.member_id:
            raise ValueError("a member may review only their own rule")
        if self.negative_outcome is not None and self.decision not in {
            "narrow",
            "suspend",
            "revoke",
        }:
            raise ValueError("negative outcomes must narrow, suspend, or revoke")
        if self.decision == "narrow" and self.replacement_instruction is None:
            raise ValueError("narrow requires a replacement instruction")
        if self.decision != "narrow" and self.replacement_instruction is not None:
            raise ValueError("only narrow accepts a replacement instruction")
        narrowing_order = {"always": 0, "usually": 1, "ask": 2, "never": 3}
        if (
            self.replacement_instruction is not None
            and narrowing_order[self.replacement_instruction]
            <= narrowing_order[self.previous_instruction]
        ):
            raise ValueError("replacement instruction must strictly narrow admission")
        return self


registry.register("triage.item.managed", 1, TriageItemManagedV1)
registry.register(
    "triage.source_policy_rule.proposed", 1, TriageSourcePolicyRuleProposedV1
)
registry.register(
    "triage.source_policy_rule.decided", 1, TriageSourcePolicyRuleDecidedV1
)
registry.register(
    "triage.source_policy_rule.reviewed", 1, TriageSourcePolicyRuleReviewedV1
)


__all__ = [
    "TriageItemManagedV1",
    "TriageSourcePolicyRuleDecidedV1",
    "TriageSourcePolicyRuleProposedV1",
    "TriageSourcePolicyRuleReviewedV1",
]


class AttentionConditionsV1(BaseModel):
    """All supplied exact terms must match; omitted terms are unconstrained.

    No pattern language, model prompt, member selector, or provider action is
    accepted. These conditions describe attention within existing read scope.
    """

    model_config = {"extra": "forbid"}
    provider: str | None = Field(default=None, min_length=1, max_length=120)
    provider_account_id: str | None = Field(default=None, min_length=1, max_length=500)
    adapter_instance_id: str | None = Field(default=None, min_length=1, max_length=500)
    native_container_id: str | None = Field(default=None, min_length=1, max_length=1000)
    sender_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_kind: str | None = Field(default=None, min_length=1, max_length=120)
    message_purpose: (
        Literal[
            "personal_request",
            "member_promise",
            "appointment",
            "preparation",
            "payment",
            "account_alert",
            "optional_opportunity",
            "status_update",
            "receipt",
            "survey",
            "reference",
            "recurrence",
            "unknown",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def has_a_condition(self) -> "AttentionConditionsV1":
        if not self.model_dump(exclude_none=True):
            raise ValueError("at least one exact condition is required")
        return self


class AttentionRuleV1(BaseModel):
    model_config = {"extra": "forbid"}
    conditions: AttentionConditionsV1
    exceptions: list[AttentionConditionsV1] = Field(default_factory=list, max_length=20)
    action: Literal["review", "reference", "ask"]
    origin: Literal["explicit", "suggested"] = "explicit"
    feedback_event_ids: list[str] = Field(default_factory=list, max_length=100)
    preserve_independent_obligations: Literal[True] = True

    @model_validator(mode="after")
    def is_an_exact_source_conjunction(self) -> "AttentionRuleV1":
        value = self.conditions
        if not value.provider_account_id or not value.adapter_instance_id:
            raise ValueError("rules require an exact account and adapter instance")
        if not (
            value.message_purpose or value.sender_digest or value.native_container_id
        ):
            raise ValueError("select a message purpose, sender, or conversation")
        if len(set(self.feedback_event_ids)) != len(self.feedback_event_ids):
            raise ValueError("feedback ids must be unique")
        return self


class TriageAttentionRuleSavedV1(AttentionRuleV1):
    """An explicit, previewed private rule; trial limits are future observations."""

    rule_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["active", "trial", "shadow"]
    starts_at: datetime
    expires_at: datetime | None = None
    max_observations: int | None = Field(default=None, ge=1, le=100)
    saved_by_member_id: str = Field(min_length=1, max_length=240)
    access_widening: Literal[False] = False
    cross_member_effect: Literal[False] = False
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def is_private_bounded_and_explicit(self) -> "TriageAttentionRuleSavedV1":
        if self.saved_by_member_id != self.member_id:
            raise ValueError("members can save only their own attention rules")
        if self.starts_at.tzinfo is None or (
            self.expires_at is not None and self.expires_at.tzinfo is None
        ):
            raise ValueError("rule times require an explicit timezone")
        if self.mode in {"trial", "shadow"}:
            if self.expires_at is None or self.max_observations is None:
                raise ValueError("future trials require time and observation limits")
            if (
                not self.starts_at
                < self.expires_at
                <= self.starts_at + timedelta(days=30)
            ):
                raise ValueError("trial duration must be positive and at most 30 days")
        elif self.expires_at is not None or self.max_observations is not None:
            raise ValueError("permanent approval cannot carry trial limits")
        return self


class TriageAttentionRuleReviewedV1(BaseModel):
    model_config = {"extra": "forbid"}
    rule_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    decision: Literal["retain", "suspend", "revoke"]
    negative_outcome: NegativePolicyOutcome | None = None
    reason: str = Field(min_length=1, max_length=500)
    reviewed_by_member_id: str = Field(min_length=1, max_length=240)
    reviewed_at: datetime
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def adverse_feedback_restores_visibility(self) -> "TriageAttentionRuleReviewedV1":
        if self.reviewed_by_member_id != self.member_id:
            raise ValueError("members can review only their own attention rules")
        if self.negative_outcome and self.decision == "retain":
            raise ValueError("negative evidence must suspend or revoke the rule")
        return self


class TriageAttentionFeedbackRecordedV1(BaseModel):
    """A correction category is explicit; completion is never a future preference."""

    model_config = {"extra": "forbid"}
    feedback_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    proposal_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    independent_observation_key: str = Field(min_length=1, max_length=240)
    kind: Literal[
        "future_attention_preference",
        "interpretation_correction",
        "completion",
        "ownership",
        "duplicate",
    ]
    rule: AttentionRuleV1 | None = None
    recorded_by_member_id: str = Field(min_length=1, max_length=240)
    recorded_at: datetime
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def future_preference_is_explicit(self) -> "TriageAttentionFeedbackRecordedV1":
        if self.recorded_by_member_id != self.member_id:
            raise ValueError("members can correct only their own observations")
        if (self.kind == "future_attention_preference") != (self.rule is not None):
            raise ValueError(
                "only an explicit future preference carries an attention rule"
            )
        return self


registry.register("triage.attention_rule.saved", 1, TriageAttentionRuleSavedV1)
registry.register("triage.attention_rule.reviewed", 1, TriageAttentionRuleReviewedV1)
registry.register(
    "triage.attention_feedback.recorded", 1, TriageAttentionFeedbackRecordedV1
)


class AttentionConditionsV2(AttentionConditionsV1):
    """Retain an exact historical source when an enrollment is reconfigured."""

    protected_source_id: str | None = Field(default=None, min_length=1, max_length=500)


class AttentionRuleV2(AttentionRuleV1):
    conditions: AttentionConditionsV2
    exceptions: list[AttentionConditionsV2] = Field(  # type: ignore[assignment]
        default_factory=list, max_length=20
    )


class TriageAttentionRuleSavedV2(TriageAttentionRuleSavedV1):
    conditions: AttentionConditionsV2
    exceptions: list[AttentionConditionsV2] = Field(  # type: ignore[assignment]
        default_factory=list, max_length=20
    )


class TriageAttentionFeedbackRecordedV2(TriageAttentionFeedbackRecordedV1):
    rule: AttentionRuleV2 | None = None


def _upcast_attention_scope_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Old rules retain their declared scope; never infer a historical source."""

    return dict(payload)


registry.register("triage.attention_rule.saved", 2, TriageAttentionRuleSavedV2)
registry.register(
    "triage.attention_feedback.recorded", 2, TriageAttentionFeedbackRecordedV2
)
registry.register_upcaster(
    "triage.attention_rule.saved", 1, _upcast_attention_scope_v1_to_v2
)
registry.register_upcaster(
    "triage.attention_feedback.recorded", 1, _upcast_attention_scope_v1_to_v2
)
