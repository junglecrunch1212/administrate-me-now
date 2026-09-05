"""Production-v2 event contracts for the single-use B03 write envelope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.registry import registry


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class GoogleTasksExecutionPreparedV2(_Strict):
    execution_policy_version: Literal["google-tasks-create-production-v2"]
    action_class: Literal["google_tasks_create"]
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    execution_envelope_revision: int = Field(ge=1)
    represented_cos: str = Field(min_length=1, max_length=240)
    actual_operator: str = Field(min_length=1, max_length=240)
    active_context: Literal["member_private", "household_shared"]
    on_behalf_of: str = Field(min_length=1, max_length=240)
    authority_basis: str = Field(min_length=1, max_length=240)
    operating_agreement_id: str = Field(min_length=1, max_length=240)
    resource_owner: str = Field(min_length=1, max_length=240)
    provider_owner: str = Field(min_length=1, max_length=240)
    provider: Literal["google_tasks"]
    provider_account_coordinate: str = Field(min_length=1, max_length=500)
    adapter_identity: Literal["tasks-google-tasks"]
    destination_task_list_coordinate: str = Field(min_length=1, max_length=500)
    approved_task_content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    desired_task_content: dict[str, Any]
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: None = None
    created_at: AwareDatetime
    valid_from: AwareDatetime
    expires_at: AwareDatetime
    revoked: Literal[False] = False
    used: Literal[False] = False
    idempotency_key: str = Field(min_length=1, max_length=240)
    provider_contact_state: Literal["not_contacted"] = "not_contacted"
    terminal_result_state: Literal["missing"] = "missing"
    later_observation_state: Literal["missing"] = "missing"
    reconciliation_state: Literal["not_applicable"] = "not_applicable"
    maximum_effect_count: Literal[1] = 1
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def _window(self) -> "GoogleTasksExecutionPreparedV2":
        if not (
            self.valid_from < self.expires_at and self.created_at < self.expires_at
        ):
            raise ValueError("B03 execution window is invalid")
        return self


class GoogleTasksExecutionDomainApprovedV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    execution_envelope_revision: int = Field(ge=1)
    action_class: Literal["google_tasks_create"]
    represented_cos: str = Field(min_length=1, max_length=240)
    actual_operator: str = Field(min_length=1, max_length=240)
    active_context: Literal["member_private", "household_shared"]
    on_behalf_of: str = Field(min_length=1, max_length=240)
    authority_grant_id: str = Field(min_length=1, max_length=240)
    operating_agreement_id: str = Field(min_length=1, max_length=240)
    resource_owner: str = Field(min_length=1, max_length=240)
    provider_owner: str = Field(min_length=1, max_length=240)
    provider: Literal["google_tasks"]
    provider_account_coordinate: str = Field(min_length=1, max_length=500)
    adapter_identity: Literal["tasks-google-tasks"]
    destination_task_list_coordinate: str = Field(min_length=1, max_length=500)
    approved_task_content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    desired_task_content: dict[str, Any]
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by_member_id: str = Field(min_length=1, max_length=240)
    approved_at: AwareDatetime
    valid_from: AwareDatetime
    expires_at: AwareDatetime
    maximum_effect_count: Literal[1] = 1
    permission: Literal["execute_google_tasks_create_trial"]
    single_use: Literal[True] = True
    revoked: Literal[False] = False
    used: Literal[False] = False


class GoogleTasksExecutionOpenClawApprovedV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    execution_envelope_revision: int = Field(ge=1)
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    maximum_effect_count: Literal[1] = 1


class GoogleTasksExecutionContactCommittedV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    execution_envelope_revision: int = Field(ge=1)
    initiating_event_id: str = Field(min_length=1, max_length=240)
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    scoped_admission_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["google_tasks"]
    provider_account_coordinate: str = Field(min_length=1, max_length=500)
    operation: Literal["create_task"]
    destination_task_list_coordinate: str = Field(min_length=1, max_length=500)
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_scope: str = Field(min_length=1, max_length=500)
    visibility_scope: str = Field(min_length=1, max_length=500)
    expires_at: AwareDatetime
    maximum_effect_count: Literal[1] = 1
    committed_at: AwareDatetime
    retry_permitted: Literal[False] = False


class GoogleTasksExecutionProviderResultReceivedV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    command_event_id: str = Field(min_length=1, max_length=240)
    contact_attempt_event_id: str = Field(min_length=1, max_length=240)
    provider: Literal["google_tasks"]
    provider_account_coordinate: str = Field(min_length=1, max_length=500)
    task_list_id: str = Field(min_length=1, max_length=500)
    native_task_id: str = Field(min_length=1, max_length=1000)
    provider_revision: str | None = Field(default=None, max_length=1000)
    provider_task_status: str | None = Field(default=None, max_length=120)
    desired_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    returned_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_contact_at: AwareDatetime
    result_received_at: AwareDatetime
    applied: Literal[True] = True
    effect_count: Literal[1] = 1
    retry_permitted: Literal[False] = False


class GoogleTasksExecutionTerminalV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    command_event_id: str = Field(min_length=1, max_length=240)
    contact_attempt_event_id: str | None = Field(default=None, max_length=240)
    provider_result_event_id: str | None = Field(default=None, max_length=240)
    outcome: Literal[
        "success",
        "failure_before_contact",
        "ambiguous_after_possible_contact",
        "suppressed",
    ]
    provider: Literal["google_tasks"]
    provider_account_coordinate: str = Field(min_length=1, max_length=500)
    task_list_id: str = Field(min_length=1, max_length=500)
    native_task_id: str | None = Field(default=None, max_length=1000)
    provider_revision: str | None = Field(default=None, max_length=1000)
    provider_task_status: str | None = Field(default=None, max_length=120)
    desired_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    returned_state_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    provider_contact_at: AwareDatetime | None = None
    terminal_at: AwareDatetime
    provider_called: bool
    applied: bool
    suppressed: bool
    effect_count: int = Field(ge=0, le=1)
    retry_permitted: Literal[False] = False
    reason_code: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _evidence(self) -> "GoogleTasksExecutionTerminalV2":
        if self.outcome == "success" and (
            not self.provider_result_event_id
            or not self.native_task_id
            or not self.applied
            or self.effect_count != 1
        ):
            raise ValueError(
                "successful B03 result requires one provider-native effect"
            )
        if self.outcome == "failure_before_contact" and self.provider_called:
            raise ValueError("pre-contact failure cannot report provider contact")
        if self.effect_count and not self.provider_called:
            raise ValueError("provider effect requires provider contact")
        return self


class GoogleTasksExecutionObservedV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    terminal_result_event_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    provider: Literal["google_tasks"]
    provider_account_coordinate: str = Field(min_length=1, max_length=500)
    resource_owner: str = Field(min_length=1, max_length=240)
    task_list_id: str = Field(min_length=1, max_length=500)
    native_task_id: str = Field(min_length=1, max_length=1000)
    provider_revision: str | None = Field(default=None, max_length=1000)
    desired_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: AwareDatetime
    independent_source_observation: Literal[True] = True


class GoogleTasksExecutionReconciledV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    terminal_result_event_id: str | None = Field(default=None, max_length=240)
    observation_event_id: str | None = Field(default=None, max_length=240)
    outcome: Literal[
        "matched", "missing", "drifted", "conflicted", "ambiguous", "failed"
    ]
    desired_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_state_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    duplicate_provider_objects: int = Field(default=0, ge=0)
    reason_code: str = Field(min_length=1, max_length=500)
    reconciled_at: AwareDatetime
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def _matched(self) -> "GoogleTasksExecutionReconciledV2":
        if self.outcome == "matched" and (
            not self.terminal_result_event_id
            or not self.observation_event_id
            or self.duplicate_provider_objects
            or self.observed_state_fingerprint != self.desired_state_fingerprint
        ):
            raise ValueError(
                "matched reconciliation requires one independent exact observation"
            )
        return self


class GoogleTasksExecutionRevokedV2(_Strict):
    command_id: str = Field(min_length=1, max_length=240)
    execution_envelope_id: str = Field(min_length=1, max_length=240)
    execution_envelope_revision: int = Field(ge=1)
    revoked_by: str = Field(min_length=1, max_length=240)
    revoked_at: AwareDatetime
    reason_code: str = Field(min_length=1, max_length=500)
    provider_effect_count: Literal[0] = 0


registry.register(
    "cos.google_tasks_execution.prepared", 2, GoogleTasksExecutionPreparedV2
)
registry.register(
    "cos.google_tasks_execution.domain_approved",
    2,
    GoogleTasksExecutionDomainApprovedV2,
)
registry.register(
    "cos.google_tasks_execution.openclaw_approved",
    2,
    GoogleTasksExecutionOpenClawApprovedV2,
)
registry.register(
    "cos.google_tasks_execution.contact_attempt_committed",
    2,
    GoogleTasksExecutionContactCommittedV2,
)
registry.register(
    "cos.google_tasks_execution.provider_result_received",
    2,
    GoogleTasksExecutionProviderResultReceivedV2,
)
registry.register(
    "cos.google_tasks_execution.terminal", 2, GoogleTasksExecutionTerminalV2
)
registry.register(
    "cos.google_tasks_execution.observed", 2, GoogleTasksExecutionObservedV2
)
registry.register(
    "cos.google_tasks_execution.reconciled", 2, GoogleTasksExecutionReconciledV2
)
registry.register(
    "cos.google_tasks_execution.revoked", 2, GoogleTasksExecutionRevokedV2
)


__all__ = [name for name in globals() if name.startswith("GoogleTasksExecution")]
