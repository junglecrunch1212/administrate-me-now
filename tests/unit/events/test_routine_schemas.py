"""D27 ordered Routine schema contracts."""

from __future__ import annotations

import pytest

from adminme.events.registry import EventValidationError, ensure_autoloaded, registry
from adminme.events.schemas.routines import (
    RoutineDefinedV1,
    RoutineStepOutcomeRecordedV1,
)


def _definition() -> dict:
    return {
        "routine_id": "routine-leaving",
        "member_id": "member-a",
        "title": "Leave the house",
        "timezone": "America/New_York",
        "starts_on": "2034-03-02",
        "rrule": "FREQ=DAILY",
        "window_start_local": "07:30",
        "window_end_local": "08:00",
        "steps": [
            {"step_id": "shoes", "title": "Put on shoes", "position": 0},
            {"step_id": "keys", "title": "Pick up keys", "position": 1},
        ],
    }


def test_routine_schemas_are_registered_and_accept_ordered_contracts() -> None:
    ensure_autoloaded()
    assert registry.get("routine.defined", 1) is RoutineDefinedV1
    assert (
        registry.get("routine.step_outcome_recorded", 1) is RoutineStepOutcomeRecordedV1
    )
    assert registry.validate("routine.defined", 1, _definition()).routine_id == (
        "routine-leaving"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"timezone": "Not/A_Timezone"},
        {
            "steps": [
                {"step_id": "shoes", "title": "Put on shoes", "position": 0},
                {"step_id": "keys", "title": "Pick up keys", "position": 2},
            ]
        },
        {"rrule": "NOT-A-RRULE"},
    ],
)
def test_routine_definition_rejects_ambiguous_schedule_or_order(
    update: dict,
) -> None:
    payload = {**_definition(), **update}
    with pytest.raises(EventValidationError):
        registry.validate("routine.defined", 1, payload)
