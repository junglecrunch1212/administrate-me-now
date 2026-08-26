"""Behavior tests for the member navigation seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from adminme.lib.session import Session
from adminme.products.member import navigation as navigation_module
from adminme.products.member import surfaces as surface_module
from adminme.products.member.surfaces import (
    ADULT_SURFACES,
    CHILD_SURFACES,
    DEVICE_SURFACES,
    SURFACE_LABELS,
    authorize_surface,
    experience_mode,
    experience_semantics,
    navigation,
)

ADULT_ORDER = (
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
LABELS = {
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


def _session(
    role: str,
    *,
    auth_member: str = "member-a",
    view_member: str | None = None,
    view_role: str | None = None,
) -> Session:
    target = view_member or auth_member
    return Session(
        tenant_id="tenant-fixture",
        auth_member_id=auth_member,
        auth_role=role,  # type: ignore[arg-type]
        view_member_id=target,
        view_role=(view_role or ("principal" if role == "coach_session" else role)),  # type: ignore[arg-type]
        dm_scope="per_channel_peer",
        source="member_portal",
    )


class _Extensions:
    def __init__(self, profiles: dict[str, Any]) -> None:
        self.profiles = profiles

    def profile_for(self, member_id: str) -> Any:
        return self.profiles.get(member_id)


class _Preferences:
    def __init__(self, preferences: dict[str, dict[str, Any]]) -> None:
        self.preferences = preferences

    def preferences_for(self, member_id: str) -> dict[str, Any]:
        return self.preferences.get(member_id, {})


def _runtime(
    profiles: dict[str, Any], preferences: dict[str, dict[str, Any]] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        extensions=_Extensions(profiles),
        settings_control=_Preferences(preferences or {}),
    )


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("principal", ADULT_ORDER),
        ("coach_session", ADULT_ORDER),
        ("child", ("today", "scoreboard")),
        ("device", ("scoreboard",)),
        ("ambient", ("scoreboard",)),
    ],
)
def test_navigation_exact_role_routes_labels_and_hrefs(
    role: str, expected: tuple[str, ...]
) -> None:
    result = navigation(_session(role))

    assert tuple(item["id"] for item in result) == expected
    assert result == [
        {"id": surface, "label": LABELS[surface], "href": f"/member/{surface}"}
        for surface in expected
    ]


def test_registry_order_and_labels_are_exact() -> None:
    assert ADULT_SURFACES == ADULT_ORDER
    assert CHILD_SURFACES == ("today", "scoreboard")
    assert DEVICE_SURFACES == ("scoreboard",)
    assert SURFACE_LABELS == LABELS


def test_legacy_surface_module_imports_forward_to_navigation_module() -> None:
    assert surface_module.ADULT_SURFACES is navigation_module.ADULT_SURFACES
    assert surface_module.CHILD_SURFACES is navigation_module.CHILD_SURFACES
    assert surface_module.DEVICE_SURFACES is navigation_module.DEVICE_SURFACES
    assert surface_module.SURFACE_LABELS is navigation_module.SURFACE_LABELS
    assert surface_module.navigation is navigation_module.navigation
    assert surface_module.authorize_surface is navigation_module.authorize_surface
    assert surface_module.experience_mode is navigation_module.experience_mode
    assert surface_module.experience_semantics is navigation_module.experience_semantics


def test_authorization_uses_authenticated_role_even_while_viewing_as() -> None:
    viewing_child = _session(
        "principal",
        auth_member="adult-a",
        view_member="child-b",
        view_role="child",
    )

    assert tuple(item["id"] for item in navigation(viewing_child)) == ADULT_ORDER
    authorize_surface(viewing_child, "settings")


@pytest.mark.parametrize("role", ["child", "device", "ambient"])
def test_direct_route_denial_is_a_non_enumerating_404(role: str) -> None:
    with pytest.raises(HTTPException) as error:
        authorize_surface(_session(role), "settings")

    assert error.value.status_code == 404
    assert error.value.detail == "surface not found"
    assert "settings" not in str(error.value.detail).lower()


def test_experience_mode_uses_child_role_then_view_member_profile() -> None:
    runtime = _runtime(
        {
            "adult-a": {"id": "adult-profile", "profile_format": "minimal"},
            "child-b": {"id": "child-profile", "profile_format": "fog_aware"},
        }
    )

    assert experience_mode(runtime, _session("child")) == "child"
    assert experience_mode(runtime, _session("principal")) == "compressed"
    assert (
        experience_mode(
            runtime,
            _session(
                "principal",
                auth_member="adult-a",
                view_member="child-b",
                view_role="child",
            ),
        )
        == "carousel"
    )


def test_experience_semantics_preserves_profile_and_preference_precedence() -> None:
    runtime = _runtime(
        {
            "member-a": {
                "id": "adhd-profile",
                "profile_format": "fog_aware",
                "rewards_mode": "milestones",
                "reward_distribution": {"small": 2},
                "behavior": {
                    "quiet_hours": {"start": 21, "end": 6},
                    "nudge_penalty": 4,
                    "available_minutes": 12,
                },
            }
        },
        {
            "member-a": {
                "quiet_start_hour": 20,
                "interruption_budget": 2,
                "information_density": 1,
                "planning_horizon_days": 5,
                "task_chunk_minutes": 9,
                "reminder_escalation": 3,
                "celebration_intensity": 2,
                "digest_hour": 7,
                "location_cues": True,
            }
        },
    )

    assert experience_semantics(runtime, _session("principal")) == {
        "profile_id": "adhd-profile",
        "mode": "carousel",
        "micro_steps": True,
        "endowed_progress": True,
        "optional_zeigarnik_teaser": True,
        "guilt_language_filter": True,
        "fog_checks_local_hours": [15, 17],
        "rewards_mode": "milestones",
        "reward_distribution": {"small": 2},
        "quiet_hours": {"start": 20, "end": 6},
        "nudge_penalty": 4,
        "proactive_cap": 2,
        "information_density": 1,
        "planning_horizon_days": 5,
        "task_chunk_minutes": 9,
        "reminder_escalation": 3,
        "celebration_intensity": 2,
        "digest_hour": 7,
        "location_cues": True,
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


def test_experience_semantics_preserves_defensive_defaults() -> None:
    runtime = _runtime({"member-a": "malformed"})

    semantics = experience_semantics(runtime, _session("principal"))

    assert semantics["profile_id"] is None
    assert semantics["mode"] == "compressed"
    assert semantics["quiet_hours"] == {"start": 22, "end": 7}
    assert semantics["proactive_cap"] == 1
    assert semantics["information_density"] == 2
    assert semantics["task_chunk_minutes"] == 15
