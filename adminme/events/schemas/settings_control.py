"""Typed audit receipts for the deterministic member Settings control plane."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
            "apple_notes",
            "apple_voice_memos",
            "obsidian",
        ]
    ] = Field(min_length=1)
    state: Literal["enrollment_preparation_required"]
    revision: int = Field(ge=1)
    requested_at: str = Field(min_length=1)


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


registry.register("settings.profile_changed", 1, SettingsProfileChangedV1)
registry.register("settings.preferences_changed", 1, SettingsPreferencesChangedV1)
registry.register(
    "settings.connection_lifecycle_changed", 1, SettingsConnectionLifecycleChangedV1
)
registry.register(
    "settings.bridge_enrollment_requested", 1, SettingsBridgeEnrollmentRequestedV1
)
registry.register(
    "settings.candidate_policy_changed", 1, SettingsCandidatePolicyChangedV1
)


__all__: list[str] = []
