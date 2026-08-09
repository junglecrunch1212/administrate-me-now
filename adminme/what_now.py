"""Pure deterministic ranking for authorized Tasks and Commitments."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


_EFFORT_MINUTES = {"tiny": 5, "small": 15, "medium": 45, "large": 120}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{value}T23:59:59+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _urgency(due: datetime | None, as_of: datetime) -> tuple[int, str]:
    if due is None:
        return 0, "unscheduled"
    delta = due - as_of
    if delta.total_seconds() <= 0:
        return 500, "overdue"
    if delta <= timedelta(hours=1):
        return 400, "due_within_hour"
    if delta <= timedelta(days=1):
        return 300, "due_today"
    if delta <= timedelta(days=7):
        return 150, "due_this_week"
    return 25, "future"


def _profile_behavior(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    behavior = profile.get("behavior")
    return behavior if isinstance(behavior, dict) else {}


def rank_what_now(
    tasks: list[dict[str, Any]],
    commitments: list[dict[str, Any]],
    *,
    member_id: str,
    profile: dict[str, Any] | None,
    as_of: datetime,
    list_items: list[dict[str, Any]] | None = None,
    recurrences: list[dict[str, Any]] | None = None,
    routines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rank authorized executable work identically for the same instant.

    Calendar events shape the Day Brief rather than competing with household
    work. Provider list items and trackable recurrences are distinct candidate
    kinds with their own interaction contracts; they are never coerced into
    tasks merely to make the ranker simpler.
    """

    as_of = as_of.astimezone(timezone.utc)
    behavior = _profile_behavior(profile)
    available = int(behavior.get("available_minutes", 30))
    current_energy = str(behavior.get("current_energy", "medium"))
    preferred_contexts = {
        str(value) for value in behavior.get("preferred_contexts", [])
    }
    quiet = behavior.get("quiet_hours") or {}
    quiet_start = int(quiet.get("start", 22)) if isinstance(quiet, dict) else 22
    quiet_end = int(quiet.get("end", 7)) if isinstance(quiet, dict) else 7
    in_quiet_hours = as_of.hour >= quiet_start or as_of.hour < quiet_end
    nudge_penalty = int(behavior.get("nudge_penalty", 0))
    outcome_bias = int(behavior.get("recent_outcome_bias", 0))

    candidates: list[dict[str, Any]] = []
    for row in commitments:
        due = _parse_time(row.get("due_at"))
        urgency_score, urgency_reason = _urgency(due, as_of)
        status = str(row.get("status"))
        status_score = {
            "pending": 0,
            "snoozed": -350,
            "deferred": -450,
            "blocked": -900,
        }.get(status, -1000)
        score = 800 + urgency_score + status_score + 200 + outcome_bias
        if in_quiet_hours and row.get("kind") == "reply":
            score -= 75
        lifecycle_actions = {
            "pending": ["complete", "defer", "block", "cancel", "delegate", "evidence"],
            "snoozed": ["complete", "defer", "cancel", "delegate", "evidence"],
            "deferred": ["complete", "cancel", "delegate", "evidence"],
            "blocked": ["complete", "unblock", "cancel", "delegate", "evidence"],
        }.get(status, ["evidence"])
        candidates.append(
            {
                "id": row["commitment_id"],
                "kind": "commitment",
                "title": row["description"],
                "last_event_id": row.get("last_event_id"),
                "due": row.get("due_at"),
                "status": status,
                "micro_step": (
                    "Open the thread and write only the first honest sentence."
                    if row.get("kind") == "reply"
                    else "Open the commitment and do only the first concrete move."
                ),
                "energy": "communication" if row.get("kind") == "reply" else None,
                "effort": "15 min",
                "context": "commitment",
                "actions": lifecycle_actions,
                "_score": score,
                "_due": due,
                "explanation": {
                    "available_time": available,
                    "temporal_horizon": urgency_reason,
                    "priority_urgency": urgency_score,
                    "energy": "reply" if row.get("kind") == "reply" else "unknown",
                    "effort_minutes": 15,
                    "context_location": "communication",
                    "dependencies_waiting": status,
                    "relationship_consequence": row.get("owed_to_party"),
                    "profile_behavior": profile.get("id") if profile else None,
                    "quiet_hours": in_quiet_hours,
                    "nudge_state": "profile_penalty" if nudge_penalty else "clear",
                    "recent_outcomes": outcome_bias,
                    "obligation": "confirmed promise to another party",
                },
            }
        )

    for row in tasks:
        due = _parse_time(row.get("due_date"))
        urgency_score, urgency_reason = _urgency(due, as_of)
        effort = _EFFORT_MINUTES.get(str(row.get("effort")), 30)
        effort_score = 100 if effort <= available else -200
        energy = str(row.get("energy") or "unknown")
        energy_score = 75 if energy == current_energy else 0
        domain = str(row.get("domain") or "general")
        context_score = 60 if domain in preferred_contexts else 0
        status = str(row.get("effective_status") or row.get("status") or "inbox")
        status_score = {"in_progress": 180, "next": 100, "inbox": 0}.get(status, -500)
        try:
            dependencies = json.loads(row.get("depends_on_json") or "[]")
        except json.JSONDecodeError:
            dependencies = ["invalid_dependency_state"]
        waiting = bool(dependencies) or status == "waiting_on" or row.get("waiting_on")
        waiting_score = -700 if waiting else 0
        score = (
            500
            + urgency_score
            + effort_score
            + energy_score
            + context_score
            + status_score
            + waiting_score
            - nudge_penalty
            + outcome_bias
        )
        candidates.append(
            {
                "id": row["task_id"],
                "kind": "task",
                "title": row["title"],
                "last_event_id": row.get("last_event_id"),
                "due": row.get("due_date"),
                "status": status,
                "lifecycle_status": row.get("status"),
                "micro_step": row.get("micro_script")
                or "Open it and do only the first visible step.",
                "energy": energy,
                "effort": f"{effort} min",
                "context": domain,
                "actions": ["complete", "defer", "block", "evidence"],
                "_score": score,
                "_due": due,
                "explanation": {
                    "available_time": available,
                    "temporal_horizon": urgency_reason,
                    "priority_urgency": urgency_score,
                    "energy": energy,
                    "energy_match": energy_score > 0,
                    "effort_minutes": effort,
                    "context_location": domain,
                    "context_match": context_score > 0,
                    "dependencies_waiting": dependencies if waiting else [],
                    "relationship_consequence": None,
                    "profile_behavior": profile.get("id") if profile else None,
                    "quiet_hours": in_quiet_hours,
                    "nudge_state": "profile_penalty" if nudge_penalty else "clear",
                    "recent_outcomes": outcome_bias,
                    "obligation": "household task, not a commitment",
                },
            }
        )

    grouped_list_items: list[dict[str, Any]] = []
    possible_groups: dict[str, list[dict[str, Any]]] = {}
    for row in list_items or []:
        possible_copy = row.get("possible_copy")
        group_id = (
            str(possible_copy.get("group_id"))
            if isinstance(possible_copy, dict) and possible_copy.get("group_id")
            else ""
        )
        if group_id:
            possible_groups.setdefault(group_id, []).append(row)
        else:
            grouped_list_items.append(row)
    for group_id, rows in possible_groups.items():
        representative = min(
            rows,
            key=lambda item: (
                _parse_time(item.get("due_at"))
                or datetime.max.replace(tzinfo=timezone.utc),
                str(item.get("id") or item.get("item_external_id") or ""),
            ),
        )
        grouped_list_items.append(
            {
                **representative,
                "id": group_id,
                "kind": "reminder_group",
                "possible_copy": {
                    **representative["possible_copy"],
                    "items": [
                        {
                            "id": item.get("id"),
                            "title": item.get("title"),
                            "list_name": item.get("list_name"),
                            "due_at": item.get("due_at"),
                            "surface_link": item.get("surface_link"),
                        }
                        for item in rows
                    ],
                },
            }
        )

    for row in grouped_list_items:
        due = _parse_time(row.get("due_at"))
        urgency_score, urgency_reason = _urgency(due, as_of)
        priority_score = {1: 125, 5: 50, 9: 0, 0: 0}.get(
            int(row.get("priority") or 0), 0
        )
        provider = str(row.get("provider") or "external_list")
        is_group = row.get("kind") == "reminder_group"
        actions = ["open_reminder", "evidence"] if is_group else ["evidence"]
        if not is_group and bool(
            row.get("can_complete", provider == "apple_reminders")
        ):
            actions.insert(0, "complete_external")
        identifier = str(
            row.get("id") or f"{row['list_external_id']}:{row['item_external_id']}"
        )
        candidates.append(
            {
                "id": identifier,
                "kind": "reminder_group" if is_group else "list_item",
                "title": row["title"],
                "last_event_id": row.get("last_event_id"),
                "due": row.get("due_at"),
                "status": "open",
                "micro_step": "Open the reminder and do only the first physical step.",
                "energy": "unknown",
                "effort": "short",
                "context": row.get("list_name") or "list",
                "actions": actions,
                "provider": row.get("provider"),
                "provider_account_id": row.get("provider_account_id"),
                "list_external_id": row.get("list_external_id"),
                "item_external_id": row.get("item_external_id"),
                "provider_revision": row.get("provider_revision"),
                "surface_link": row.get("surface_link"),
                "possible_copy": row.get("possible_copy"),
                "_score": 450 + urgency_score + priority_score + outcome_bias,
                "_due": due,
                "explanation": {
                    "available_time": available,
                    "temporal_horizon": urgency_reason,
                    "priority_urgency": urgency_score + priority_score,
                    "energy": "unknown",
                    "effort_minutes": 15,
                    "context_location": row.get("list_name") or "list",
                    "dependencies_waiting": [],
                    "relationship_consequence": None,
                    "profile_behavior": profile.get("id") if profile else None,
                    "quiet_hours": in_quiet_hours,
                    "nudge_state": "profile_penalty" if nudge_penalty else "clear",
                    "recent_outcomes": outcome_bias,
                    "obligation": (
                        "possible provider copies awaiting member review; no rows were merged"
                        if is_group
                        else "provider list item, not a household task"
                    ),
                },
            }
        )

    for row in recurrences or []:
        if not row.get("trackable") or row.get("status", "active") != "active":
            continue
        due = _parse_time(row.get("next_occurrence"))
        urgency_score, urgency_reason = _urgency(due, as_of)
        kind = str(row.get("kind") or "routine")
        candidates.append(
            {
                "id": row["recurrence_id"],
                "kind": "recurrence",
                "title": kind.replace("_", " ").strip().title(),
                "last_event_id": row.get("last_event_id"),
                "due": row.get("next_occurrence"),
                "status": "due",
                "micro_step": row.get("notes") or "Start the first repeatable step.",
                "energy": "routine",
                "effort": "repeatable",
                "context": "routine",
                "actions": ["complete", "pause", "evidence"],
                "_score": 475 + urgency_score + outcome_bias,
                "_due": due,
                "explanation": {
                    "available_time": available,
                    "temporal_horizon": urgency_reason,
                    "priority_urgency": urgency_score,
                    "energy": "routine",
                    "effort_minutes": 15,
                    "context_location": "routine",
                    "dependencies_waiting": [],
                    "relationship_consequence": None,
                    "profile_behavior": profile.get("id") if profile else None,
                    "quiet_hours": in_quiet_hours,
                    "nudge_state": "profile_penalty" if nudge_penalty else "clear",
                    "recent_outcomes": outcome_bias,
                    "obligation": "trackable recurrence, not a task or commitment",
                },
            }
        )

    for row in routines or []:
        current_step = row.get("current_step")
        if not isinstance(current_step, dict):
            continue
        start = _parse_time(row.get("window_start_at"))
        end = _parse_time(row.get("window_end_at"))
        status = str(row.get("status") or "upcoming")
        if status == "in_progress":
            window_score = 900
        elif status == "overdue":
            window_score = 750
        elif start is not None and start <= as_of + timedelta(minutes=30):
            window_score = 650
        else:
            window_score = 200
        candidates.append(
            {
                "id": row["routine_id"],
                "kind": "routine",
                "title": row["title"],
                "last_event_id": row.get("last_event_id"),
                "evidence_event_id": row.get("evidence_event_id")
                or row.get("last_event_id"),
                "due": row.get("window_start_at"),
                "window_start_at": row.get("window_start_at"),
                "window_end_at": row.get("window_end_at"),
                "time_label": row.get("time_label"),
                "status": status,
                "micro_step": current_step.get("micro_step")
                or current_step.get("title")
                or "Start only this step.",
                "energy": "routine",
                "effort": f"step {int(current_step.get('position', 0)) + 1} of {row.get('total_steps', 1)}",
                "context": "ordered routine",
                "actions": ["complete_step", "skip_step", "pause", "evidence"],
                "occurrence_date": row["occurrence_date"],
                "current_step": current_step,
                "steps": row.get("steps") or [],
                "completed_steps": row.get("completed_steps", 0),
                "skipped_steps": row.get("skipped_steps", 0),
                "total_steps": row.get("total_steps", 0),
                "_score": 575 + window_score + outcome_bias,
                "_due": start or end,
                "explanation": {
                    "available_time": available,
                    "temporal_horizon": f"routine_window_{status}",
                    "priority_urgency": window_score,
                    "energy": "routine",
                    "effort_minutes": None,
                    "context_location": "ordered routine",
                    "dependencies_waiting": [],
                    "relationship_consequence": None,
                    "profile_behavior": profile.get("id") if profile else None,
                    "quiet_hours": in_quiet_hours,
                    "nudge_state": "profile_penalty" if nudge_penalty else "clear",
                    "recent_outcomes": outcome_bias,
                    "obligation": "routine occurrence, not a task or recurrence completion",
                },
            }
        )

    candidates.sort(
        key=lambda item: (
            -item["_score"],
            item["_due"] or datetime.max.replace(tzinfo=timezone.utc),
            item["kind"],
            item["id"],
        )
    )
    ordered = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in candidates
    ]

    def at(index: int) -> dict[str, Any] | None:
        return ordered[index] if index < len(ordered) else None

    # Preserve the established product aliases while publishing the explicit
    # now/hour/daypart/day/week contract used by proactive surfaces.
    horizons = {
        "right_now": at(0),
        "next": at(1),
        "today": at(2),
        "this_week": at(3),
        "this_month": at(4),
        "someday": at(5),
    }
    return {
        "member_id": member_id,
        "generated_by": "deterministic-ranker-v2",
        "as_of": as_of.isoformat(),
        "profile_id": profile.get("id") if profile else None,
        "ranked": ordered,
        "horizons": horizons,
        "planning_horizons": {
            "now": at(0),
            "hour": at(1),
            "daypart": at(2),
            "day": at(3),
            "week": at(4),
        },
        "candidate_count": len(ordered),
    }


__all__ = ["rank_what_now"]
