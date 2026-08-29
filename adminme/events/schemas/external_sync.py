"""Typed command/result events for provider state mirrors.

The command spine is shared by central Google adapters and per-member macOS
bridges.  It keeps intent and provider side effects in the append-only log,
supports optimistic provider revisions, and lets observation mode suppress the
last outbound step without losing the requested action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


ExternalProvider = Literal[
    "google_calendar",
    "google_tasks",
    "apple_calendar",
    "apple_contacts",
    "apple_reminders",
    "gmail",
    "microsoft_graph",
    "bluebubbles",
    "twilio_sms",
    "twilio_whatsapp",
    "fixture",
]


class ExternalWriteRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}
    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ExternalProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    operation: str = Field(min_length=1, max_length=120)
    target_external_id: str | None = Field(default=None, max_length=1000)
    expected_provider_revision: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime


class ExternalWriteRequestedV2(ExternalWriteRequestedV1):
    model_config = {"extra": "forbid"}

    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    privacy_guard_version: str = Field(min_length=1, max_length=120)
    origin_proposal_id: str | None = Field(default=None, min_length=1, max_length=240)


class MemberProviderActionConfirmedV1(BaseModel):
    """Member-domain approval stored before an external command may exist."""

    model_config = {"extra": "forbid"}

    action_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ExternalProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    operation: str = Field(min_length=1, max_length=120)
    target_external_id: str | None = Field(default=None, max_length=1000)
    expected_provider_revision: str | None = Field(default=None, max_length=1000)
    command_payload: dict[str, Any] = Field(default_factory=dict)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_at: datetime
    origin_proposal_id: str | None = Field(default=None, min_length=1, max_length=240)


class ExternalWriteCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ExternalProvider
    operation: str = Field(min_length=1, max_length=120)
    external_id: str | None = None
    provider_revision: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime
    suppressed: bool = False


class ExternalWriteCompletedV2(ExternalWriteCompletedV1):
    model_config = {"extra": "forbid"}
    command_event_id: str = Field(min_length=1, max_length=240)
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=500)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_called: bool
    origin_proposal_id: str | None = Field(default=None, min_length=1, max_length=240)


class ExternalWriteFailedV1(BaseModel):
    model_config = {"extra": "forbid"}
    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ExternalProvider
    operation: str = Field(min_length=1, max_length=120)
    error_kind: Literal[
        "conflict",
        "permission",
        "unsupported",
        "not_found",
        "validation",
        "provider",
    ]
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False
    failed_at: datetime


class ExternalWriteFailedV2(ExternalWriteFailedV1):
    model_config = {"extra": "forbid"}
    command_event_id: str = Field(min_length=1, max_length=240)
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=500)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_called: bool
    origin_proposal_id: str | None = Field(default=None, min_length=1, max_length=240)


class LegacyProviderEffectReconciledV1(BaseModel):
    """Additive evidence for one pre-governance v1 request/result pair.

    This event does not manufacture approvals that did not exist.  It binds a
    reviewed classification to the exact immutable request and terminal facts
    so current integrity accounting can distinguish legacy absence from an
    unknown provider outcome.
    """

    model_config = {"extra": "forbid"}

    request_event_id: str = Field(min_length=1, max_length=240)
    terminal_event_id: str = Field(min_length=1, max_length=240)
    command_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=500)
    provider_called: bool
    suppressed: bool
    legacy_approval_state: Literal["pre_governance_absent"]
    reconciliation_basis: Literal["v1_completed_terminal_evidence"]
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reconciled_at: datetime


registry.register("external_sync.write_requested", 1, ExternalWriteRequestedV1)
registry.register("external_sync.write_requested", 2, ExternalWriteRequestedV2)
registry.register("external_sync.write_completed", 1, ExternalWriteCompletedV1)
registry.register("external_sync.write_completed", 2, ExternalWriteCompletedV2)
registry.register("external_sync.write_failed", 1, ExternalWriteFailedV1)
registry.register("external_sync.write_failed", 2, ExternalWriteFailedV2)
registry.register(
    "external_sync.legacy_effect_reconciled", 1, LegacyProviderEffectReconciledV1
)
registry.register(
    "member.provider_action.confirmed", 1, MemberProviderActionConfirmedV1
)


__all__ = [
    "ExternalProvider",
    "ExternalWriteCompletedV1",
    "ExternalWriteCompletedV2",
    "ExternalWriteFailedV1",
    "ExternalWriteFailedV2",
    "ExternalWriteRequestedV1",
    "ExternalWriteRequestedV2",
    "LegacyProviderEffectReconciledV1",
    "MemberProviderActionConfirmedV1",
]
