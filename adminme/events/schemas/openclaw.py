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


__all__ = []
