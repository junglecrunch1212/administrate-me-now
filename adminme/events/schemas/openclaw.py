"""Typed audit events emitted by the native OpenClaw seam."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class OpenClawIntegrationStateChangedV1(BaseModel):
    model_config = {"extra": "forbid"}
    state: Literal["healthy", "degraded", "unavailable", "incompatible"]
    observed_at: str = Field(min_length=1)
    openclaw_version: str = Field(min_length=1)
    openclaw_commit: str = Field(min_length=7)
    reason: str | None = Field(default=None, max_length=500)


class OpenClawDomainEventObservedV1(BaseModel):
    model_config = {"extra": "forbid"}
    event_kind: Literal[
        "commitment_candidate",
        "household_action",
        "delivery_outcome",
    ]
    content: str = Field(min_length=1, max_length=2000)
    consent: Literal[True]
    agent_id: str | None = None
    session_key: str | None = None
    session_id: str | None = None
    channel_id: str = Field(min_length=1)
    account_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    correlation_id: str = Field(min_length=1)


class HouseholdActionProposedV1(BaseModel):
    model_config = {"extra": "forbid"}
    proposal_id: str = Field(min_length=1)
    kind: Literal["task", "commitment"]
    title: str = Field(min_length=1, max_length=500)
    owner_member_id: str = Field(min_length=1)
    due: str | None = None
    reason: Literal["explicit_proposal", "observation_mode_active"]
    provenance: dict[str, Any]


class OpenClawStandingOrderTriggeredV1(BaseModel):
    model_config = {"extra": "forbid"}
    program_id: str = Field(min_length=1)
    automation_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    triggered_at: str = Field(min_length=1)
    agent_id: str | None = None
    session_key: str | None = None


class OpenClawModelCallRecordedV1(BaseModel):
    """Metadata-only model provenance; raw prompts/outputs are forbidden."""

    model_config = {"extra": "forbid"}
    phase: Literal["started", "ended"]
    run_id: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    agent_id: str | None = None
    session_key: str | None = None
    channel_id: str = Field(min_length=1)


class OpenClawToolCallRecordedV1(BaseModel):
    """Metadata-only audit link from an explicit OpenClaw call to domain output."""

    model_config = {"extra": "forbid"}
    operation: str = Field(min_length=1, max_length=120)
    result_event_ids: list[str] = Field(default_factory=list)
    actor_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    agent_id: str | None = None
    session_key: str | None = None
    session_id: str | None = None
    channel_id: str = Field(min_length=1)
    account_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    correlation_id: str = Field(min_length=1)
    call_kind: Literal["tool", "dashboard", "slash_command", "standing_order", "bridge"]
    openclaw_version: str = Field(min_length=1)
    openclaw_commit: str = Field(min_length=7)


class OpenClawToolCallRecordedV2(BaseModel):
    """Service-aware audit with independent identity and lineage axes."""

    model_config = {"extra": "forbid"}
    operation: str = Field(min_length=1, max_length=120)
    actor_principal_id: str = Field(min_length=1)
    operator_principal_id: str = Field(min_length=1)
    represented_service_principal_id: str = Field(min_length=1)
    context_scope: Literal[
        "service_private",
        "member_private",
        "household_shared",
        "external_contact",
        "unresolved",
    ]
    context_member_id: str | None = None
    on_behalf_of_member_id: str | None = None
    authority_grant_id: str | None = None
    resource_owner_principal_id: str | None = None
    owner_party_id: str | None = None
    responsible_party_id: str | None = None
    assignee_party_id: str | None = None
    beneficiary_party_id: str | None = None
    provider_account_owner_principal_id: str | None = None
    audience: list[str] = Field(default_factory=list)
    visibility: list[str] = Field(default_factory=list)
    approving_member_ids: list[str] = Field(default_factory=list)
    approval_quorum: Literal["service_self", "member_owner", "all_adult_principals"]
    permissions: list[
        Literal["read_context", "create_internal_work", "manage_internal_work"]
    ] = Field(default_factory=list)
    openclaw_actor_id: str = Field(min_length=1)
    agent_id: str | None = None
    session_key: str | None = None
    session_id: str | None = None
    channel_id: str = Field(min_length=1)
    account_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    request_correlation_id: str = Field(min_length=1)
    causation_event_ids: list[str] = Field(default_factory=list)
    result_event_ids: list[str] = Field(default_factory=list)
    observation_event_ids: list[str] = Field(default_factory=list)
    reconciliation_event_ids: list[str] = Field(default_factory=list)
    call_kind: Literal["tool", "dashboard", "slash_command", "standing_order", "bridge"]
    openclaw_version: str = Field(min_length=1)
    openclaw_commit: str = Field(min_length=7)


class OpenClawApprovalReceivedV1(BaseModel):
    """One resolved, content-bound OpenClaw plugin approval consumed once."""

    model_config = {"extra": "forbid"}

    receipt_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    tool_name: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=120)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    consumed_at: str = Field(min_length=1)
    single_use: Literal[True]
    approval_method: Literal["allow_once", "standing_order"]
    openclaw_version: str = Field(min_length=1)
    openclaw_commit: str = Field(min_length=7)
    provenance: dict[str, Any]


registry.register(
    "openclaw.integration.state_changed", 1, OpenClawIntegrationStateChangedV1
)
registry.register("openclaw.domain_event.observed", 1, OpenClawDomainEventObservedV1)
registry.register("household.action.proposed", 1, HouseholdActionProposedV1)
registry.register(
    "openclaw.standing_order.triggered", 1, OpenClawStandingOrderTriggeredV1
)
registry.register("openclaw.model_call.recorded", 1, OpenClawModelCallRecordedV1)
registry.register("openclaw.tool_call.recorded", 1, OpenClawToolCallRecordedV1)
registry.register("openclaw.tool_call.recorded", 2, OpenClawToolCallRecordedV2)
registry.register("openclaw.approval.received", 1, OpenClawApprovalReceivedV1)


__all__ = []
