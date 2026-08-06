"""Typed calendar-directory and deterministic scheduling-policy events.

Provider adapters report calendar capabilities.  Principals configure how a
calendar affects one member's capacity.  Neither event asks a model to decide
whether time is available; the Calendars projection and member read model
evaluate the recorded enums and time windows directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


CalendarProvider = Literal["google_calendar", "apple_calendar"]
CalendarAccessRole = Literal[
    "none", "free_busy", "reader", "writer", "owner"
]
CalendarEffect = Literal["hard_block", "soft_hold", "context", "ignore"]
CalendarPurpose = Literal[
    "personal",
    "work",
    "household",
    "coparenting",
    "property",
    "family_context",
    "holidays",
    "test",
    "other",
]
DestinationKind = Literal[
    "personal", "work", "household", "coparenting", "property"
]


class CalendarSourceObservedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1, max_length=240)
    provider: CalendarProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    calendar_external_id: str = Field(min_length=1, max_length=1000)
    calendar_name: str = Field(min_length=1, max_length=1000)
    access_role: CalendarAccessRole
    primary: bool = False
    selected: bool = False
    hidden: bool = False
    timezone: str | None = Field(default=None, max_length=240)
    color_id: str | None = Field(default=None, max_length=120)
    default_reminders: list[dict[str, Any]] = Field(default_factory=list)
    read_details: bool
    read_free_busy: bool
    write_events: bool
    observed_at: datetime


class CalendarRuleConfiguredV1(BaseModel):
    model_config = {"extra": "forbid"}

    rule_id: str = Field(min_length=8, max_length=240)
    subject_member_id: str = Field(min_length=1, max_length=240)
    source_member_id: str = Field(min_length=1, max_length=240)
    provider: CalendarProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    calendar_external_id: str = Field(min_length=1, max_length=1000)
    purpose: CalendarPurpose
    effect: CalendarEffect
    tentative_effect: CalendarEffect = "soft_hold"
    caregiving_load: CalendarEffect = "ignore"
    caregiving_summary_values: list[str] = Field(default_factory=list, max_length=20)
    buffer_kind: Literal[
        "auto",
        "none",
        "remote",
        "appointment",
        "medical",
        "handoff",
        "deep_work",
    ] = "auto"
    write_target: bool = False
    destination_kind: DestinationKind | None = None
    enabled: bool = True
    configured_at: datetime

    @model_validator(mode="after")
    def write_target_has_destination(self) -> "CalendarRuleConfiguredV1":
        if self.write_target and self.destination_kind is None:
            raise ValueError("write_target requires destination_kind")
        if not self.write_target and self.destination_kind is not None:
            raise ValueError("destination_kind requires write_target")
        if any(not value.strip() for value in self.caregiving_summary_values):
            raise ValueError("caregiving summary values cannot be blank")
        return self


class ScheduleWindowV1(BaseModel):
    model_config = {"extra": "forbid"}

    days: list[int] = Field(min_length=1, max_length=7)
    start_local: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_local: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    kind: Literal[
        "work",
        "protected",
        "school_run",
        "dinner",
        "bedtime",
        "family",
        "chores_if_accepted",
    ]
    effect: CalendarEffect
    label: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def valid_window(self) -> "ScheduleWindowV1":
        if len(set(self.days)) != len(self.days) or any(
            day < 0 or day > 6 for day in self.days
        ):
            raise ValueError("days must be unique integers from 0 through 6")
        if self.start_local == self.end_local:
            raise ValueError("schedule windows cannot have zero duration")
        return self


class CalendarSchedulePolicyConfiguredV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1, max_length=240)
    timezone: str = Field(min_length=1, max_length=240)
    windows: list[ScheduleWindowV1] = Field(default_factory=list, max_length=40)
    buffer_minutes: dict[str, int] = Field(default_factory=dict)
    accepted_chores_required: bool = True
    one_confirm_write: bool = True
    configured_at: datetime

    @model_validator(mode="after")
    def valid_buffers(self) -> "CalendarSchedulePolicyConfiguredV1":
        allowed = {
            "remote_before",
            "remote_after",
            "appointment_before",
            "appointment_after",
            "medical_before",
            "medical_after",
            "handoff_before",
            "handoff_after",
            "deep_work_before",
            "deep_work_after",
        }
        if set(self.buffer_minutes) - allowed:
            raise ValueError("unknown calendar buffer kind")
        if any(value < 0 or value > 360 for value in self.buffer_minutes.values()):
            raise ValueError("calendar buffers must be between 0 and 360 minutes")
        return self


registry.register("calendar.source_observed", 1, CalendarSourceObservedV1)
registry.register("calendar.rule.configured", 1, CalendarRuleConfiguredV1)
registry.register(
    "calendar.schedule_policy.configured", 1, CalendarSchedulePolicyConfiguredV1
)


__all__ = [
    "CalendarEffect",
    "CalendarPurpose",
    "CalendarRuleConfiguredV1",
    "CalendarSchedulePolicyConfiguredV1",
    "CalendarSourceObservedV1",
    "ScheduleWindowV1",
]
