"""Deterministic member surface registry, authorization, and experience semantics."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from adminme.lib.session import Session

ADULT_SURFACES = (
    "today",
    "triage",
    "comms",
    "reminders",
    "calendar",
    "people",
    "notes",
    "finance",
    "scoreboard",
    "settings",
)
CHILD_SURFACES = ("today", "scoreboard")
DEVICE_SURFACES = ("scoreboard",)

SURFACE_LABELS = {
    "today": "Today",
    "triage": "Triage",
    "comms": "Comms",
    "reminders": "Reminders",
    "people": "People",
    "notes": "Notes",
    "finance": "Finance",
    "calendar": "Calendar",
    "scoreboard": "Scoreboard",
    "settings": "Settings",
}


def navigation(session: Session) -> list[dict[str, str]]:
    names = (
        CHILD_SURFACES
        if session.auth_role == "child"
        else DEVICE_SURFACES
        if session.auth_role in {"device", "ambient"}
        else ADULT_SURFACES
    )
    return [
        {"id": name, "label": SURFACE_LABELS[name], "href": f"/member/{name}"}
        for name in names
    ]


def authorize_surface(session: Session, surface: str) -> None:
    allowed = {item["id"] for item in navigation(session)}
    if surface not in allowed:
        raise HTTPException(404, "surface not found")


def experience_mode(runtime: Any, session: Session) -> str:
    if session.auth_role == "child":
        return "child"
    loaded = runtime.extensions.profile_for(session.view_member_id)
    profile: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
    value = profile.get("profile_format")
    return "carousel" if value == "fog_aware" else "compressed"


def experience_semantics(runtime: Any, session: Session) -> dict[str, Any]:
    loaded = runtime.extensions.profile_for(session.view_member_id)
    profile: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
    behavior_value = profile.get("behavior")
    behavior: dict[str, Any] = (
        behavior_value if isinstance(behavior_value, dict) else {}
    )
    preferences = runtime.settings_control.preferences_for(session.view_member_id)
    quiet_hours = behavior.get("quiet_hours", {"start": 22, "end": 7})
    if "quiet_start_hour" in preferences or "quiet_end_hour" in preferences:
        quiet_hours = {
            "start": preferences.get("quiet_start_hour", quiet_hours.get("start", 22)),
            "end": preferences.get("quiet_end_hour", quiet_hours.get("end", 7)),
        }
    return {
        "profile_id": profile.get("id"),
        "mode": experience_mode(runtime, session),
        "micro_steps": True,
        "endowed_progress": True,
        "optional_zeigarnik_teaser": True,
        "guilt_language_filter": True,
        "fog_checks_local_hours": [15, 17],
        "rewards_mode": profile.get("rewards_mode", "event_based"),
        "reward_distribution": profile.get("reward_distribution", {}),
        "quiet_hours": quiet_hours,
        "nudge_penalty": behavior.get("nudge_penalty", 0),
        "proactive_cap": preferences.get(
            "interruption_budget",
            1 if experience_mode(runtime, session) == "compressed" else 3,
        ),
        "information_density": preferences.get("information_density", 2),
        "planning_horizon_days": preferences.get("planning_horizon_days", 7),
        "task_chunk_minutes": preferences.get(
            "task_chunk_minutes", behavior.get("available_minutes", 15)
        ),
        "reminder_escalation": preferences.get("reminder_escalation", 1),
        "celebration_intensity": preferences.get("celebration_intensity", 1),
        "digest_hour": preferences.get("digest_hour", 8),
        "location_cues": preferences.get("location_cues", False),
        "temporal_horizons": [
            "now",
            "next_hour",
            "morning",
            "afternoon",
            "evening",
            "day",
            "week",
            "longer",
        ],
    }


__all__ = [
    "ADULT_SURFACES",
    "CHILD_SURFACES",
    "DEVICE_SURFACES",
    "SURFACE_LABELS",
    "authorize_surface",
    "experience_mode",
    "experience_semantics",
    "navigation",
]
