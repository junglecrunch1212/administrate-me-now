"""Durable authority, intent, observation, and reconciliation for G07.

These events deliberately do not replace ``external_sync`` commands/results.
They bind the CoS domain lineage around that existing provider spine.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.delegated_execution import (
    ActionClass,
    ExecutionMode,
    RecurrenceStrategy,
    RepresentationKind,
    RiskClass,
)
from adminme.events.registry import registry


DelegatedActionState = Literal[
    "prohibited",
    "dashboard_only",
    "shadow",
    "desired",
    "awaiting_member_approval",
    "awaiting_openclaw_approval",
    "dispatched",
    "awaiting_result",
    "awaiting_observation",
    "active",
    "drifted",
    "conflicted",
    "failed",
    "suppressed",
    "withdrawn",
    "reconciled",
]


class ExecutionEnvelopeGrantedV1(BaseModel):
    """One exact standing domain-authority envelope; never OpenClaw permission."""

    model_config = {"extra": "forbid"}

    envelope_id: str = Field(min_length=1, max_length=200)
    authority_grant_id: str = Field(min_length=1, max_length=200)
    granted_by_member_id: str = Field(min_length=1, max_length=200)
    represented_service_principal_id: str = Field(min_length=1, max_length=200)
    action_class: ActionClass
    canonical_object_kind: str = Field(min_length=1, max_length=80)
    canonical_object_id: str = Field(min_length=1, max_length=240)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=240)
    series_id: str | None = Field(default=None, min_length=1, max_length=240)
    recurrence_strategy: RecurrenceStrategy = "none"
    desired_representation_id: str = Field(min_length=1, max_length=240)
    provider_family: str = Field(min_length=1, max_length=120)
    provider_account_id: str = Field(min_length=1, max_length=500)
    provider_account_owner_principal_id: str = Field(min_length=1, max_length=200)
    destination_id: str = Field(min_length=1, max_length=500)
    audience_ids: list[str] = Field(min_length=1, max_length=20)
    representation_kind: RepresentationKind
    target_external_id: str | None = Field(default=None, max_length=500)
    expected_provider_revision: str | None = Field(default=None, max_length=500)
    desired_payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    granted_at: AwareDatetime
    expires_at: AwareDatetime
    provider_execution_scope: Literal["fixture_only"] = "fixture_only"
    openclaw_permission_included: Literal[False] = False
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_window_and_recurrence(self) -> "ExecutionEnvelopeGrantedV1":
        if self.expires_at <= self.granted_at:
            raise ValueError("execution envelope must expire after it is granted")
        if len(self.audience_ids) != len(set(self.audience_ids)):
            raise ValueError("execution envelope audience must be unique")
        if self.recurrence_strategy == "provider_series" and not self.series_id:
            raise ValueError("provider recurrence requires exact series identity")
        if (
            self.recurrence_strategy == "individual_occurrence"
            and not self.occurrence_id
        ):
            raise ValueError("variable recurrence requires exact occurrence identity")
        if self.recurrence_strategy == "none" and (
            self.series_id or self.occurrence_id
        ):
            raise ValueError("non-recurring envelope cannot carry recurrence identity")
        return self


class ExecutionEnvelopeRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}

    envelope_id: str = Field(min_length=1, max_length=200)
    revoked_by_member_id: str = Field(min_length=1, max_length=200)
    revoked_at: AwareDatetime
    reason_code: Literal["member_request", "policy_change", "scope_change", "other"]


class DelegatedActionPreparedV1(BaseModel):
    """Desired representation and full attribution before any approval/command."""

    model_config = {"extra": "forbid"}

    action_id: str = Field(min_length=1, max_length=240)
    action_revision: int = Field(ge=1)
    canonical_object_kind: str = Field(min_length=1, max_length=80)
    canonical_object_id: str = Field(min_length=1, max_length=240)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=240)
    series_id: str | None = Field(default=None, min_length=1, max_length=240)
    recurrence_strategy: RecurrenceStrategy
    coordination_id: str | None = Field(default=None, min_length=1, max_length=240)
    desired_representation_id: str = Field(min_length=1, max_length=240)
    actor_principal_id: str = Field(min_length=1, max_length=200)
    actor_party_id: str = Field(min_length=1, max_length=200)
    operator_principal_id: str = Field(min_length=1, max_length=200)
    operator_kind: Literal["openclaw_automation", "simulated_human"]
    represented_service_principal_id: str = Field(min_length=1, max_length=200)
    active_context_scope: Literal["member_private", "household_shared"]
    active_context_member_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    on_behalf_of_member_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    authority_grant_id: str = Field(min_length=1, max_length=200)
    authority_grant_event_id: str = Field(min_length=1, max_length=240)
    resource_owner_principal_id: str = Field(min_length=1, max_length=200)
    provider_account_owner_principal_id: str = Field(min_length=1, max_length=200)
    provider: Literal["fixture"] = "fixture"
    provider_family: str = Field(min_length=1, max_length=120)
    provider_account_id: str = Field(min_length=1, max_length=500)
    production_adapter: str | None = Field(default=None, min_length=1, max_length=200)
    adapter_architecture: Literal["central_cat_b", "member_bridge", "held_only"]
    destination_id: str = Field(min_length=1, max_length=500)
    audience_ids: list[str] = Field(min_length=1, max_length=20)
    action_class: ActionClass
    operation: str = Field(min_length=1, max_length=120)
    representation_kind: RepresentationKind
    expected_provider_revision: str | None = Field(default=None, max_length=500)
    target_external_id: str | None = Field(default=None, max_length=500)
    desired_payload: dict[str, Any]
    desired_state: dict[str, Any]
    desired_payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    command_id: str = Field(min_length=1, max_length=240)
    command_idempotency_key: str = Field(min_length=1, max_length=240)
    fact_event_ids: list[str] = Field(min_length=1, max_length=100)
    facts_observed_at: AwareDatetime
    facts_valid_until: AwareDatetime
    policy_version: str = Field(min_length=1, max_length=120)
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    privacy_guard_version: str = Field(min_length=1, max_length=120)
    content_guard_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_class: RiskClass
    execution_mode: ExecutionMode
    initial_state: Literal[
        "prohibited",
        "dashboard_only",
        "shadow",
        "desired",
        "awaiting_member_approval",
    ]
    prepared_at: AwareDatetime
    expires_at: AwareDatetime
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_lineage(self) -> "DelegatedActionPreparedV1":
        if (
            self.expires_at <= self.prepared_at
            or self.facts_valid_until <= self.facts_observed_at
        ):
            raise ValueError("delegated action freshness/expiry window is invalid")
        if len(self.audience_ids) != len(set(self.audience_ids)):
            raise ValueError("delegated action audience must be unique")
        if len(self.fact_event_ids) != len(set(self.fact_event_ids)):
            raise ValueError("delegated action fact lineage must be unique")
        if self.active_context_scope == "member_private":
            if (
                not self.active_context_member_id
                or self.on_behalf_of_member_id != self.active_context_member_id
                or self.resource_owner_principal_id != self.active_context_member_id
            ):
                raise ValueError(
                    "member-private execution requires exact member ownership"
                )
        elif (
            self.active_context_member_id is not None
            or self.on_behalf_of_member_id is not None
        ):
            raise ValueError("household-shared execution cannot impersonate a member")
        if self.recurrence_strategy == "provider_series" and not self.series_id:
            raise ValueError("stable provider recurrence requires series identity")
        if (
            self.recurrence_strategy == "individual_occurrence"
            and not self.occurrence_id
        ):
            raise ValueError("variable recurrence requires occurrence identity")
        if self.recurrence_strategy == "none" and (
            self.series_id or self.occurrence_id
        ):
            raise ValueError("non-recurring action cannot carry recurrence identity")
        if self.risk_class != "ordinary" and self.initial_state != "prohibited":
            raise ValueError("hard-ceiling action must be prohibited")
        return self


class DelegatedActionDomainApprovedV1(BaseModel):
    model_config = {"extra": "forbid"}

    action_id: str = Field(min_length=1, max_length=240)
    action_revision: int = Field(ge=1)
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_kind: Literal["one_off", "standing_envelope"]
    approved_by_member_id: str = Field(min_length=1, max_length=200)
    approval_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    execution_envelope_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    openclaw_permission_included: Literal[False] = False

    @model_validator(mode="after")
    def validate_approval(self) -> "DelegatedActionDomainApprovedV1":
        if self.expires_at <= self.approved_at:
            raise ValueError("delegated action approval must expire in the future")
        if (self.approval_kind == "standing_envelope") != bool(
            self.execution_envelope_id
        ):
            raise ValueError("standing approval requires exactly one envelope")
        if self.approval_kind == "one_off" and self.execution_envelope_id is not None:
            raise ValueError("one-off approval cannot name a standing envelope")
        return self


class DelegatedActionDispatchAuthorizedV1(BaseModel):
    model_config = {"extra": "forbid"}

    action_id: str = Field(min_length=1, max_length=240)
    action_revision: int = Field(ge=1)
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    command_id: str = Field(min_length=1, max_length=240)
    command_idempotency_key: str = Field(min_length=1, max_length=240)
    operator_principal_id: str = Field(min_length=1, max_length=200)
    authorized_at: AwareDatetime


class DelegatedActionObservedV1(BaseModel):
    model_config = {"extra": "forbid"}

    observation_id: str = Field(min_length=1, max_length=240)
    action_id: str = Field(min_length=1, max_length=240)
    command_id: str = Field(min_length=1, max_length=240)
    result_event_id: str = Field(min_length=1, max_length=240)
    provider: Literal["fixture"] = "fixture"
    provider_family: str = Field(min_length=1, max_length=120)
    provider_account_id: str = Field(min_length=1, max_length=500)
    destination_id: str = Field(min_length=1, max_length=500)
    external_id: str = Field(min_length=1, max_length=500)
    provider_revision: str = Field(min_length=1, max_length=500)
    observed_state: dict[str, Any]
    observation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: AwareDatetime
    direct_provider_edit_detected: bool = False


class DelegatedActionCanonicalOutcomeReturnedV1(BaseModel):
    """Return an exact provider completion/reopening to canonical task truth."""

    model_config = {"extra": "forbid"}

    action_id: str = Field(min_length=1, max_length=240)
    canonical_object_kind: Literal["task"]
    canonical_object_id: str = Field(min_length=1, max_length=240)
    outcome: Literal["completed", "reopened"]
    observation_event_id: str = Field(min_length=1, max_length=240)
    represented_service_principal_id: str = Field(min_length=1, max_length=200)
    represented_service_party_id: str = Field(min_length=1, max_length=200)
    operator_principal_id: str = Field(min_length=1, max_length=200)
    operator_kind: Literal["openclaw_automation", "simulated_human"]
    on_behalf_of_member_id: str = Field(min_length=1, max_length=200)
    authority_grant_id: str = Field(min_length=1, max_length=200)
    returned_at: AwareDatetime


class DelegatedActionReconciledV1(BaseModel):
    model_config = {"extra": "forbid"}

    reconciliation_id: str = Field(min_length=1, max_length=240)
    action_id: str = Field(min_length=1, max_length=240)
    observation_event_id: str = Field(min_length=1, max_length=240)
    result_event_id: str = Field(min_length=1, max_length=240)
    desired_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: Literal["reconciled", "drifted", "conflicted"]
    canonical_outcome: Literal["none", "completed", "reopened", "withdrawn", "active"]
    canonical_outcome_event_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )
    reason_code: Literal[
        "exact_match",
        "provider_state_drift",
        "direct_provider_edit",
        "provider_revision_conflict",
    ]
    reconciled_at: AwareDatetime


class DelegatedActionWithdrawnV1(BaseModel):
    model_config = {"extra": "forbid"}

    action_id: str = Field(min_length=1, max_length=240)
    withdrawn_by_member_id: str = Field(min_length=1, max_length=200)
    withdrawn_at: AwareDatetime
    reason_code: Literal["member_request", "superseded", "no_longer_needed", "other"]


registry.register("cos.execution_envelope.granted", 1, ExecutionEnvelopeGrantedV1)
registry.register("cos.execution_envelope.revoked", 1, ExecutionEnvelopeRevokedV1)
registry.register("cos.delegated_action.prepared", 1, DelegatedActionPreparedV1)
registry.register(
    "cos.delegated_action.domain_approved", 1, DelegatedActionDomainApprovedV1
)
registry.register(
    "cos.delegated_action.dispatch_authorized", 1, DelegatedActionDispatchAuthorizedV1
)
registry.register("cos.delegated_action.observed", 1, DelegatedActionObservedV1)
registry.register(
    "cos.delegated_action.canonical_outcome_returned",
    1,
    DelegatedActionCanonicalOutcomeReturnedV1,
)
registry.register("cos.delegated_action.reconciled", 1, DelegatedActionReconciledV1)
registry.register("cos.delegated_action.withdrawn", 1, DelegatedActionWithdrawnV1)


__all__ = [
    "DelegatedActionDispatchAuthorizedV1",
    "DelegatedActionCanonicalOutcomeReturnedV1",
    "DelegatedActionDomainApprovedV1",
    "DelegatedActionObservedV1",
    "DelegatedActionPreparedV1",
    "DelegatedActionReconciledV1",
    "DelegatedActionState",
    "DelegatedActionWithdrawnV1",
    "ExecutionEnvelopeGrantedV1",
    "ExecutionEnvelopeRevokedV1",
]
