"""Typed audit events emitted by the native OpenClaw seam."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry
from adminme.events.schemas.domain import ServiceWorkSourceAuthority


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
        Literal[
            "read_context",
            "create_internal_work",
            "manage_internal_work",
            "manage_member_obligations",
            "coordinate_household_work",
            "prepare_external_actions",
            "execute_fixture_actions",
            "execute_google_tasks_create_trial",
        ]
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


class OpenClawToolCallRecordedV3(OpenClawToolCallRecordedV2):
    """Trusted read receipt: no body, raw session key or new disclosure grant."""

    source_read_session_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_read_authorities: list[ServiceWorkSourceAuthority] = Field(max_length=4000)


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


class RequestedActionGroundedV1(BaseModel):
    """Source-bound semantic evidence, not provider permission or execution."""

    model_config = {"extra": "forbid"}
    member_id: str
    source_event_id: str
    action_id: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_id: Literal["current-human-request.apple-reminder-create.v1"]
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_event_id: str
    action: dict[str, Any]
    evidence: list[dict[str, Any]] = Field(min_length=1, max_length=8)
    issued_at: str
    expires_at: str


class OpenClawApprovalReceivedV2(OpenClawApprovalReceivedV1):
    """Machine policy and legacy decisions retain their distinct provenance."""

    approval_method: Literal["allow_once", "standing_order", "authenticated_request"]  # type: ignore[assignment]
    request_authorization_event_id: str | None = None
    source_event_id: str | None = None
    domain_approval_event_id: str | None = None
    action_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rule_id: Literal["current-human-request.apple-reminder-create.v1"] | None = None

    @model_validator(mode="after")
    def distinct_authority(self) -> "OpenClawApprovalReceivedV2":
        fields = (
            self.request_authorization_event_id,
            self.source_event_id,
            self.domain_approval_event_id,
            self.action_fingerprint,
            self.rule_id,
        )
        if self.approval_method == "authenticated_request":
            if not all(fields):
                raise ValueError(
                    "authenticated request requires exact source/action policy binding"
                )
        elif any(fields):
            raise ValueError("legacy approval cannot borrow current request authority")
        return self


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
registry.register("openclaw.tool_call.recorded", 3, OpenClawToolCallRecordedV3)
registry.register("openclaw.approval.received", 1, OpenClawApprovalReceivedV1)
registry.register("openclaw.approval.received", 2, OpenClawApprovalReceivedV2)
registry.register_upcaster(
    "openclaw.approval.received", 1, lambda payload: dict(payload)
)
registry.register("cos.action.request_grounded", 1, RequestedActionGroundedV1)


__all__ = []
