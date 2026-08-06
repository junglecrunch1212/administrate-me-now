"""Ordered, time-windowed routine contracts.

A routine is a recurring ordered execution plan.  It is not a Task, a
calendar event, or the existing single Recurrence completion marker.  The
definition is durable household truth; each step outcome is an explicit
member fact for one dated occurrence.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


class RoutineStepV1(BaseModel):
    model_config = {"extra": "forbid"}

    step_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    position: int = Field(ge=0, le=99)
    micro_step: str | None = Field(default=None, max_length=500)
    linked_task_id: str | None = Field(default=None, max_length=160)


class RoutineDefinedV1(BaseModel):
    model_config = {"extra": "forbid"}

    routine_id: str = Field(min_length=1, max_length=160)
    member_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    timezone: str = Field(min_length=1, max_length=100)
    starts_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rrule: str = Field(min_length=1, max_length=1000)
    window_start_local: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    window_end_local: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    steps: list[RoutineStepV1] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_ordered_steps(self) -> "RoutineDefinedV1":
        ids = [step.step_id for step in self.steps]
        positions = [step.position for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("routine step_id values must be unique")
        if len(positions) != len(set(positions)):
            raise ValueError("routine step positions must be unique")
        if sorted(positions) != list(range(len(positions))):
            raise ValueError("routine step positions must be contiguous from zero")
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("routine timezone must be a known IANA zone") from exc
        starts_on = date.fromisoformat(self.starts_on)
        starts_at = datetime.combine(
            starts_on, time.fromisoformat(self.window_start_local), tzinfo=zone
        )
        try:
            rule = rrulestr(self.rrule, dtstart=starts_at)
            if rule.after(starts_at, inc=True) is None:
                raise ValueError("routine rrule has no occurrence")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "routine rrule must be valid RFC 5545 recurrence text"
            ) from exc
        return self


class RoutineStepOutcomeRecordedV1(BaseModel):
    model_config = {"extra": "forbid"}

    routine_id: str = Field(min_length=1, max_length=160)
    occurrence_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    step_id: str = Field(min_length=1, max_length=120)
    outcome: Literal["completed", "skipped"]
    recorded_at: str = Field(min_length=1)
    recorded_by_member_id: str = Field(min_length=1, max_length=160)


registry.register("routine.defined", 1, RoutineDefinedV1)
registry.register("routine.step_outcome_recorded", 1, RoutineStepOutcomeRecordedV1)


__all__ = [
    "RoutineDefinedV1",
    "RoutineStepOutcomeRecordedV1",
    "RoutineStepV1",
]
