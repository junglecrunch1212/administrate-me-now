"""Exact, fixture-only delegated execution policy for the household CoS.

The catalog names action semantics and the reviewed production adapter route
without granting authority.  Authority remains in a service-context grant plus
either one exact member approval or one exact standing envelope.  OpenClaw
permission, the provider command, its result, later observation, and
reconciliation are separate event objects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal


ActionClass = Literal[
    "google_calendar_create",
    "google_calendar_update",
    "google_calendar_move",
    "google_calendar_withdraw",
    "google_calendar_recurrence",
    "google_tasks_create",
    "google_tasks_update",
    "google_tasks_complete",
    "google_tasks_reopen",
    "google_tasks_withdraw",
    "google_tasks_list_create",
    "google_tasks_list_update",
    "google_tasks_list_withdraw",
    "google_tasks_reminder_create",
    "google_tasks_reminder_update",
    "google_tasks_reminder_complete",
    "google_tasks_reminder_reopen",
    "google_tasks_reminder_withdraw",
    "apple_calendar_create",
    "apple_calendar_update",
    "apple_calendar_move",
    "apple_calendar_withdraw",
    "apple_calendar_recurrence",
    "apple_reminders_create",
    "apple_reminders_update",
    "apple_reminders_complete",
    "apple_reminders_reopen",
    "apple_reminders_withdraw",
    "communication_draft",
    "communication_hold",
]
RiskClass = Literal[
    "ordinary",
    "money",
    "nonrefundable_travel",
    "legal_signature",
    "clinical_decision",
    "child_consent",
    "life_safety_system",
    "public_statement",
    "another_adult_source",
    "infrastructure_administration",
]
ExecutionMode = Literal["execute", "desired_only", "shadow"]
RepresentationKind = Literal["provider_object", "cos_operational", "member_delegated"]
RecurrenceStrategy = Literal["none", "provider_series", "individual_occurrence"]

EXECUTION_POLICY_VERSION = "delegated-fixture-execution-v1"
EXECUTION_FACT_MAX_AGE_SECONDS = 300


@dataclass(frozen=True)
class ActionSpec:
    action_class: ActionClass
    provider_family: str
    operation: str
    production_adapter: str | None
    architecture: Literal["central_cat_b", "member_bridge", "held_only"]
    fixture_executable: bool
    canonical_object_kinds: frozenset[str]
    requires_target: bool = False
    recurrence: bool = False


def _spec(
    action_class: ActionClass,
    provider_family: str,
    operation: str,
    production_adapter: str | None,
    architecture: Literal["central_cat_b", "member_bridge", "held_only"],
    fixture_executable: bool,
    canonical_object_kinds: tuple[str, ...],
    *,
    requires_target: bool = False,
    recurrence: bool = False,
) -> ActionSpec:
    return ActionSpec(
        action_class=action_class,
        provider_family=provider_family,
        operation=operation,
        production_adapter=production_adapter,
        architecture=architecture,
        fixture_executable=fixture_executable,
        canonical_object_kinds=frozenset(canonical_object_kinds),
        requires_target=requires_target,
        recurrence=recurrence,
    )


_CALENDAR_KINDS = ("task", "calendar_event", "coordination")
_TASK_KINDS = ("task", "list_item", "coordination")
_COMMUNICATION_KINDS = ("commitment", "coordination")

ACTION_SPECS: dict[ActionClass, ActionSpec] = {
    "google_calendar_create": _spec(
        "google_calendar_create",
        "google_calendar",
        "create_event",
        "calendaring-google-calendar",
        "central_cat_b",
        True,
        _CALENDAR_KINDS,
    ),
    "google_calendar_update": _spec(
        "google_calendar_update",
        "google_calendar",
        "update_event",
        "calendaring-google-calendar",
        "central_cat_b",
        True,
        _CALENDAR_KINDS,
        requires_target=True,
    ),
    "google_calendar_move": _spec(
        "google_calendar_move",
        "google_calendar",
        "move_event",
        "calendaring-google-calendar",
        "central_cat_b",
        True,
        _CALENDAR_KINDS,
        requires_target=True,
    ),
    "google_calendar_withdraw": _spec(
        "google_calendar_withdraw",
        "google_calendar",
        "delete_event",
        "calendaring-google-calendar",
        "central_cat_b",
        True,
        _CALENDAR_KINDS,
        requires_target=True,
    ),
    "google_calendar_recurrence": _spec(
        "google_calendar_recurrence",
        "google_calendar",
        "create_event",
        "calendaring-google-calendar",
        "central_cat_b",
        True,
        _CALENDAR_KINDS,
        recurrence=True,
    ),
    "google_tasks_create": _spec(
        "google_tasks_create",
        "google_tasks",
        "create_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
    ),
    "google_tasks_update": _spec(
        "google_tasks_update",
        "google_tasks",
        "update_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_complete": _spec(
        "google_tasks_complete",
        "google_tasks",
        "complete_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_reopen": _spec(
        "google_tasks_reopen",
        "google_tasks",
        "uncomplete_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_withdraw": _spec(
        "google_tasks_withdraw",
        "google_tasks",
        "delete_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_list_create": _spec(
        "google_tasks_list_create",
        "google_tasks",
        "create_list",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
    ),
    "google_tasks_list_update": _spec(
        "google_tasks_list_update",
        "google_tasks",
        "update_list",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_list_withdraw": _spec(
        "google_tasks_list_withdraw",
        "google_tasks",
        "delete_list",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_reminder_create": _spec(
        "google_tasks_reminder_create",
        "google_tasks",
        "create_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
    ),
    "google_tasks_reminder_update": _spec(
        "google_tasks_reminder_update",
        "google_tasks",
        "update_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_reminder_complete": _spec(
        "google_tasks_reminder_complete",
        "google_tasks",
        "complete_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_reminder_reopen": _spec(
        "google_tasks_reminder_reopen",
        "google_tasks",
        "uncomplete_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "google_tasks_reminder_withdraw": _spec(
        "google_tasks_reminder_withdraw",
        "google_tasks",
        "delete_task",
        "tasks-google-tasks",
        "central_cat_b",
        True,
        _TASK_KINDS,
        requires_target=True,
    ),
    "apple_calendar_create": _spec(
        "apple_calendar_create",
        "apple_calendar",
        "create_event",
        "calendaring-apple-calendar",
        "member_bridge",
        False,
        _CALENDAR_KINDS,
    ),
    "apple_calendar_update": _spec(
        "apple_calendar_update",
        "apple_calendar",
        "update_event",
        "calendaring-apple-calendar",
        "member_bridge",
        False,
        _CALENDAR_KINDS,
        requires_target=True,
    ),
    "apple_calendar_move": _spec(
        "apple_calendar_move",
        "apple_calendar",
        "move_event",
        "calendaring-apple-calendar",
        "member_bridge",
        False,
        _CALENDAR_KINDS,
        requires_target=True,
    ),
    "apple_calendar_withdraw": _spec(
        "apple_calendar_withdraw",
        "apple_calendar",
        "delete_event",
        "calendaring-apple-calendar",
        "member_bridge",
        False,
        _CALENDAR_KINDS,
        requires_target=True,
    ),
    "apple_calendar_recurrence": _spec(
        "apple_calendar_recurrence",
        "apple_calendar",
        "create_event",
        "calendaring-apple-calendar",
        "member_bridge",
        False,
        _CALENDAR_KINDS,
        recurrence=True,
    ),
    "apple_reminders_create": _spec(
        "apple_reminders_create",
        "apple_reminders",
        "create_reminder",
        "reminders-apple-eventkit",
        "member_bridge",
        False,
        _TASK_KINDS,
    ),
    "apple_reminders_update": _spec(
        "apple_reminders_update",
        "apple_reminders",
        "update_reminder",
        "reminders-apple-eventkit",
        "member_bridge",
        False,
        _TASK_KINDS,
        requires_target=True,
    ),
    "apple_reminders_complete": _spec(
        "apple_reminders_complete",
        "apple_reminders",
        "complete_reminder",
        "reminders-apple-eventkit",
        "member_bridge",
        False,
        _TASK_KINDS,
        requires_target=True,
    ),
    "apple_reminders_reopen": _spec(
        "apple_reminders_reopen",
        "apple_reminders",
        "uncomplete_reminder",
        "reminders-apple-eventkit",
        "member_bridge",
        False,
        _TASK_KINDS,
        requires_target=True,
    ),
    "apple_reminders_withdraw": _spec(
        "apple_reminders_withdraw",
        "apple_reminders",
        "delete_reminder",
        "reminders-apple-eventkit",
        "member_bridge",
        False,
        _TASK_KINDS,
        requires_target=True,
    ),
    "communication_draft": _spec(
        "communication_draft",
        "communications",
        "create_draft",
        None,
        "held_only",
        False,
        _COMMUNICATION_KINDS,
    ),
    "communication_hold": _spec(
        "communication_hold",
        "communications",
        "hold_draft",
        None,
        "held_only",
        False,
        _COMMUNICATION_KINDS,
    ),
}

HARD_CEILING_RISKS = frozenset(
    {
        "money",
        "nonrefundable_travel",
        "legal_signature",
        "clinical_decision",
        "child_consent",
        "life_safety_system",
        "public_statement",
        "another_adult_source",
        "infrastructure_administration",
    }
)


def execution_policy_digest() -> str:
    material = {
        "version": EXECUTION_POLICY_VERSION,
        "fact_max_age_seconds": EXECUTION_FACT_MAX_AGE_SECONDS,
        "hard_ceilings": sorted(HARD_CEILING_RISKS),
        "actions": {
            key: {
                "provider_family": value.provider_family,
                "operation": value.operation,
                "production_adapter": value.production_adapter,
                "architecture": value.architecture,
                "fixture_executable": value.fixture_executable,
                "canonical_object_kinds": sorted(value.canonical_object_kinds),
                "requires_target": value.requires_target,
                "recurrence": value.recurrence,
            }
            for key, value in sorted(ACTION_SPECS.items())
        },
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def desired_state_for(
    spec: ActionSpec,
    *,
    desired_payload: dict[str, Any],
    destination_id: str,
) -> dict[str, Any]:
    """Return the exact provider state that a later observation must match."""

    if spec.operation in {
        "delete_event",
        "delete_task",
        "delete_list",
        "delete_reminder",
    }:
        return {"withdrawn": True, "destination_id": destination_id}
    if spec.operation in {"complete_task", "complete_reminder"}:
        return {"completed": True, "destination_id": destination_id}
    if spec.operation in {"uncomplete_task", "uncomplete_reminder"}:
        return {"completed": False, "destination_id": destination_id}
    state = json.loads(json.dumps(desired_payload, sort_keys=True, default=str))
    state["destination_id"] = destination_id
    return state


def action_fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


__all__ = [
    "ACTION_SPECS",
    "EXECUTION_FACT_MAX_AGE_SECONDS",
    "EXECUTION_POLICY_VERSION",
    "HARD_CEILING_RISKS",
    "ActionClass",
    "ActionSpec",
    "ExecutionMode",
    "RecurrenceStrategy",
    "RepresentationKind",
    "RiskClass",
    "action_fingerprint",
    "desired_state_for",
    "execution_policy_digest",
]
