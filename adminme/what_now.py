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
) -> dict[str, Any]:
    """Rank the same visible rows identically for the same ``as_of`` instant."""

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
        candidates.append(
            {
                "id": row["commitment_id"],
                "kind": "commitment",
                "title": row["description"],
                "due": row.get("due_at"),
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
        status = str(row.get("status") or "inbox")
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
                "due": row.get("due_date"),
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
