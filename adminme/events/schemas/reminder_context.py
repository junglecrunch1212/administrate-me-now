"""Deterministic context facts and member-authored reminder cue rules.

Context sources report facts; they do not decide what a member should do.
The Lists projection joins the latest authorized fact to a typed cue rule and
the Reminders read model evaluates the comparison without an LLM. Native
EventKit alarms remain provider state on ``list_item.*`` snapshots; these
events cover optional AdministrateMe cues such as Home Assistant presence,
member-bridge location transitions, weather, device state, network presence,
calendar transitions, or an explicitly recorded energy/capacity state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


ContextSource = Literal[
    "home_assistant",
    "member_bridge",
    "weather",
    "device",
    "network",
    "calendar",
    "member_profile",
    "manual",
]
ContextKind = Literal[
    "location",
    "presence",
    "weather",
    "device_state",
    "network_presence",
    "calendar_transition",
    "energy",
    "capacity",
]
CueOperator = Literal[
    "equals",
    "not_equals",
    "above",
    "below",
    "entered",
    "left",
    "present",
]


class ContextSignalObservedV1(BaseModel):
    model_config = {"extra": "forbid"}
    signal_id: str = Field(min_length=1, max_length=240)
    signal_key: str = Field(min_length=1, max_length=500)
    member_id: str = Field(min_length=1, max_length=240)
    source: ContextSource
    kind: ContextKind
    state: str = Field(min_length=1, max_length=500)
    numeric_value: float | None = None
    unit: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0)
    observed_at: datetime
    expires_at: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coordinates_are_paired(self) -> "ContextSignalObservedV1":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        return self


class ReminderCueConfiguredV1(BaseModel):
    model_config = {"extra": "forbid"}
    cue_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: Literal["apple_reminders", "google_tasks", "apple_notes_checklist"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    list_external_id: str = Field(min_length=1, max_length=1000)
    item_external_id: str = Field(min_length=1, max_length=1000)
    signal_key: str = Field(min_length=1, max_length=500)
    source: ContextSource
    kind: ContextKind
    operator: CueOperator
    expected_state: str | None = Field(default=None, max_length=500)
    expected_number: float | None = None
    enabled: bool = True
    configured_at: datetime

    @model_validator(mode="after")
    def comparison_has_expected_value(self) -> "ReminderCueConfiguredV1":
        if self.operator in {"equals", "not_equals"} and self.expected_state is None:
            raise ValueError(f"{self.operator} requires expected_state")
        if self.operator in {"above", "below"} and self.expected_number is None:
            raise ValueError(f"{self.operator} requires expected_number")
        return self


class ReminderCueClearedV1(BaseModel):
    model_config = {"extra": "forbid"}
    cue_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    cleared_at: datetime


registry.register("context.signal.observed", 1, ContextSignalObservedV1)
registry.register("reminder.cue.configured", 1, ReminderCueConfiguredV1)
registry.register("reminder.cue.cleared", 1, ReminderCueClearedV1)


__all__ = [
    "ContextKind",
    "ContextSignalObservedV1",
    "ContextSource",
    "CueOperator",
    "ReminderCueClearedV1",
    "ReminderCueConfiguredV1",
]
