"""Typed audit receipts for the deterministic member Settings control plane."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from adminme.bridge_enrollment import CAPABILITY_STATES, ENROLLMENT_STATES
from adminme.events.registry import registry


class SettingsProfileChangedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    previous_profile_id: str | None = None
    profile_id: str = Field(min_length=1)
    changed_by_member_id: str = Field(min_length=1)
    authority_changes: Literal[False] = False
    model_routing_changes: Literal[False] = False
    changed_at: str = Field(min_length=1)


class SettingsPreferencesChangedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    changed_by_member_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    values: dict[str, int | bool | str] = Field(default_factory=dict)
    changed_at: str = Field(min_length=1)


class SettingsConnectionLifecycleChangedV1(BaseModel):
    model_config = {"extra": "forbid"}

    connection_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    action: Literal["pause", "resume"]
    revision: int = Field(ge=1)
    runtime_reconciled: bool
    changed_by_member_id: str = Field(min_length=1)
    changed_at: str = Field(min_length=1)


class SettingsBridgeEnrollmentRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    requested_by_member_id: str = Field(min_length=1)
    capabilities: list[
        Literal[
            "apple_calendar",
            "apple_reminders",
            "apple_contacts",
            "apple_notes",
            "apple_voice_memos",
            "obsidian",
        ]
    ] = Field(min_length=1)
    state: Literal["enrollment_preparation_required"]
    revision: int = Field(ge=1)
    requested_at: str = Field(min_length=1)


class SettingsBridgeEnrollmentStartedV1(BaseModel):
    """Owner-authenticated start of one resumable self-service wizard."""

    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    member_id: str = Field(min_length=1)
    requested_by_member_id: str = Field(min_length=1)
    device_class: Literal["this_mac", "another_mac"]
    capabilities: list[
        Literal[
            "apple_calendar",
            "apple_reminders",
            "apple_contacts",
            "apple_notes",
            "apple_voice_memos",
            "obsidian",
        ]
    ] = Field(min_length=1)
    state: Literal["requested"]
    revision: int = Field(ge=1)
    requested_at: str = Field(min_length=1)
    observation_only: Literal[True] = True
    writes_authorized: Literal[False] = False
    provider_effects: Literal[False] = False


class BridgeEnrollmentProgressedV1(BaseModel):
    """Signed, content-free central or target-device wizard progress."""

    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    enrollment_id: str | None = Field(default=None, max_length=160)
    member_id: str = Field(min_length=1)
    previous_state: str = Field(min_length=1)
    state: str = Field(min_length=1)
    revision: int = Field(ge=1)
    progress_at: str = Field(min_length=1)
    reported_by: Literal["central_runtime", "target_helper"]
    signature_verified: bool
    expires_at: str | None = None
    release_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    package_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    capability_set_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    device_identity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    capability_id: str | None = Field(default=None, max_length=80)
    capability_state: str | None = Field(default=None, max_length=80)
    evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=240)
    last_successful_observation: str | None = None
    content_exposed: Literal[False] = False
    writes_authorized: Literal[False] = False
    provider_effects: Literal[False] = False

    @field_validator("previous_state", "state")
    @classmethod
    def validate_enrollment_state(cls, value: str) -> str:
        if value not in ENROLLMENT_STATES:
            raise ValueError("unknown enrollment lifecycle state")
        return value

    @field_validator("capability_state")
    @classmethod
    def validate_capability_state(cls, value: str | None) -> str | None:
        if value is not None and value not in CAPABILITY_STATES:
            raise ValueError("unknown capability lifecycle state")
        return value


class SettingsBridgeEnrollmentControlledV1(BaseModel):
    """Exact owner pause, resume, retry, repair, regenerate, or revocation."""

    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    enrollment_id: str | None = Field(default=None, max_length=160)
    member_id: str = Field(min_length=1)
    action: Literal["pause", "resume", "repair", "retry", "regenerate", "revoke"]
    previous_state: str = Field(min_length=1)
    state: str = Field(min_length=1)
    revision: int = Field(ge=1)
    changed_by_member_id: str = Field(min_length=1)
    changed_at: str = Field(min_length=1)
    content_exposed: Literal[False] = False
    writes_authorized: Literal[False] = False
    provider_effects: Literal[False] = False

    @field_validator("previous_state", "state")
    @classmethod
    def validate_control_state(cls, value: str) -> str:
        if value not in ENROLLMENT_STATES:
            raise ValueError("unknown enrollment lifecycle state")
        return value


class SettingsCandidatePolicyChangedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1, max_length=160)
    mode: Literal["review", "manual", "quiet"]
    language_enabled: bool
    candidate_kinds: list[
        Literal[
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
    ] = Field(min_length=1, max_length=11)
    revision: int = Field(ge=1)
    changed_by_member_id: str = Field(min_length=1)
    changed_at: str = Field(min_length=1)
    source_retention_changed: Literal[False] = False
    routing_authority_changed: Literal[False] = False


class SettingsCapabilityRequestedV1(BaseModel):
    """An inert, secret-free request; every operational effect stays false."""

    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1, max_length=200)
    catalog_version: str = Field(min_length=1, max_length=40)
    desired_outcome: str = Field(min_length=1, max_length=500)
    requested_scope: Literal["member_private", "household_shared"]
    provider_option: str | None = Field(default=None, max_length=200)
    state: Literal["requested"]
    revision: int = Field(ge=1)
    changed_by_member_id: str = Field(min_length=1)
    changed_at: str = Field(min_length=1)
    installs_software: Literal[False] = False
    pairs_bridge: Literal[False] = False
    enrolls_account: Literal[False] = False
    changes_openclaw_configuration: Literal[False] = False
    accesses_credentials: Literal[False] = False
    grants_authority: Literal[False] = False
    provider_effects: Literal[False] = False


class SettingsCapabilityRequestCancelledV1(BaseModel):
    """Terminal cancellation of one request, without revoking unrelated truth."""

    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1, max_length=200)
    catalog_version: str = Field(min_length=1, max_length=40)
    desired_outcome: str = Field(min_length=1, max_length=500)
    requested_scope: Literal["member_private", "household_shared"]
    provider_option: str | None = Field(default=None, max_length=200)
    state: Literal["cancelled"]
    revision: int = Field(ge=1)
    changed_by_member_id: str = Field(min_length=1)
    changed_at: str = Field(min_length=1)
    installs_software: Literal[False] = False
    pairs_bridge: Literal[False] = False
    enrolls_account: Literal[False] = False
    changes_openclaw_configuration: Literal[False] = False
    accesses_credentials: Literal[False] = False
    grants_authority: Literal[False] = False
    provider_effects: Literal[False] = False


registry.register("settings.profile_changed", 1, SettingsProfileChangedV1)
registry.register("settings.preferences_changed", 1, SettingsPreferencesChangedV1)
registry.register(
    "settings.connection_lifecycle_changed", 1, SettingsConnectionLifecycleChangedV1
)
registry.register(
    "settings.bridge_enrollment_requested", 1, SettingsBridgeEnrollmentRequestedV1
)
registry.register(
    "settings.bridge_enrollment_started", 1, SettingsBridgeEnrollmentStartedV1
)
registry.register("bridge.enrollment_progressed", 1, BridgeEnrollmentProgressedV1)
registry.register(
    "settings.bridge_enrollment_controlled",
    1,
    SettingsBridgeEnrollmentControlledV1,
)
registry.register(
    "settings.candidate_policy_changed", 1, SettingsCandidatePolicyChangedV1
)
registry.register("settings.capability_requested", 1, SettingsCapabilityRequestedV1)
registry.register(
    "settings.capability_request_cancelled",
    1,
    SettingsCapabilityRequestCancelledV1,
)


__all__: list[str] = []
