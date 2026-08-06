"""Authorized read models for the eight constitutional household surfaces."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from adminme.lib.session import Session
from adminme.products.core import main as core
from adminme.today import compose_daily_brief
from adminme.projections.artifacts.queries import linked_to, list_recent
from adminme.projections.calendars.queries import today as calendar_today
from adminme.projections.calendars.queries import week
from adminme.projections.commitments.queries import by_party
from adminme.projections.interactions.queries import (
    closeness_signals,
    recent,
    recent_for_member,
    recent_with,
)
from adminme.projections.lists.queries import open_items_for_member
from adminme.projections.member_knowledge.queries import list_visible
from adminme.projections.money.queries import flows_in_range
from adminme.projections.parties.queries import (
    all_parties,
    get_party,
    identifiers_of,
    memberships_of,
    relationships_of,
)
from adminme.projections.places_assets_accounts.queries import linked_to_party
from adminme.projections.recurrences.queries import for_member
from adminme.projections.tasks.queries import open_for_member
from adminme.projections.triage.queries import pending_for_member as pending_triage


ADULT_SURFACES = (
    "today",
    "triage",
    "people",
    "knowledge",
    "finance",
    "calendar",
    "scoreboard",
    "settings",
)
CHILD_SURFACES = ("today", "scoreboard")
DEVICE_SURFACES = ("scoreboard",)

SURFACE_LABELS = {
    "today": "Today",
    "triage": "Triage",
    "people": "People",
    "knowledge": "Knowledge",
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
        "quiet_hours": behavior.get("quiet_hours", {"start": 22, "end": 7}),
        "nudge_penalty": behavior.get("nudge_penalty", 0),
        "proactive_cap": 1 if experience_mode(runtime, session) == "compressed" else 3,
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


def _workbook_health(runtime: Any) -> list[dict[str, Any]]:
    root: Path = runtime.settings.instance.xlsx_workbooks_dir
    values: list[dict[str, Any]] = []
    for name in ("adminme-ops.xlsx", "adminme-finance.xlsx"):
        path = root / name
        values.append(
            {
                "name": name,
                "state": "ready" if path.is_file() else "not_built",
                "updated_at": (
                    datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat()
                    if path.is_file()
                    else None
                ),
                "reverse_sync": "sidecar_ready"
                if (root.parent / ".xlsx-state" / name).is_dir()
                else "not_initialized",
            }
        )
    return values


def build_surface(runtime: Any, session: Session, surface: str) -> dict[str, Any]:
    authorize_surface(session, surface)
    if runtime.projections is None:
        raise HTTPException(503, "domain views are unavailable")
    now = datetime.now(timezone.utc)
    member = session.view_member_id
    base: dict[str, Any] = {
        "surface": surface,
        "label": SURFACE_LABELS[surface],
        "member_id": member,
        "mode": experience_mode(runtime, session),
        "state": "ready",
        "semantics": experience_semantics(runtime, session),
    }
    if surface == "today":
        what_now = core._what_now(runtime, session)
        calendar_events = calendar_today(
            runtime.projections.connection("calendars"),
            session,
            member_party_id=member,
            today_iso=now.astimezone(ZoneInfo(runtime.settings.instance.timezone))
            .date()
            .isoformat(),
            tz_name=runtime.settings.instance.timezone,
        )
        interactions = (
            []
            if session.auth_role == "child"
            else recent_for_member(
                runtime.projections.connection("interactions"),
                session,
                member_id=member,
                limit=30,
            )
        )
        questions = (
            []
            if session.auth_role == "child"
            else pending_triage(
                runtime.projections.connection("triage"), session, member_id=member
            )
        )
        base["what_now"] = what_now
        base["brief"] = compose_daily_brief(
            member_id=member,
            timezone_name=runtime.settings.instance.timezone,
            as_of=now,
            what_now=what_now,
            calendar_events=calendar_events,
            interactions=interactions,
            triage_items=questions,
        )
    elif surface == "triage":
        base["items"] = pending_triage(
            runtime.projections.connection("triage"), session, member_id=member
        )
        base["contract"] = {
            "facts_are_not_queued": True,
            "human_gate_required": True,
            "states": ["pending_review", "confirmed", "dismissed"],
        }
    elif surface == "people":
        base["people"] = all_parties(runtime.projections.connection("parties"), session)
        base["recent_interactions"] = recent(
            runtime.projections.connection("interactions"), session, limit=20
        )
    elif surface == "knowledge":
        base["knowledge"] = list_visible(
            runtime.projections.connection("member_knowledge"), session
        )
        base["artifacts"] = list_recent(
            runtime.projections.connection("artifacts"), session, limit=30
        )
        base["lists"] = open_items_for_member(
            runtime.projections.connection("lists"), session, member_id=member
        )
    elif surface == "finance":
        if session.auth_role == "child":
            raise HTTPException(404, "surface not found")
        base["flows"] = flows_in_range(
            runtime.projections.connection("money"),
            session,
            start_iso=(now - timedelta(days=90)).isoformat().replace("+00:00", "Z"),
            end_iso=now.isoformat().replace("+00:00", "Z"),
        )
    elif surface == "calendar":
        base["events"] = week(
            runtime.projections.connection("calendars"),
            session,
            member_party_id=member,
            start_date_iso=now.isoformat().replace("+00:00", "Z"),
        )
    elif surface == "scoreboard":
        tasks = open_for_member(
            runtime.projections.connection("tasks"), session, member_party_id=member
        )
        base["scoreboard"] = {
            "open_count": len(tasks),
            "next_steps": tasks[:3],
            "reward_language": "warm" if session.auth_role == "child" else "calm",
        }
    elif surface == "settings":
        if session.auth_role != "principal":
            base["state"] = "limited"
        base["capabilities"] = [
            record.public() for _, record in sorted(runtime.capabilities.items())
        ]
        base["workbooks"] = _workbook_health(runtime)
        base["observation_mode"] = runtime._observation_active
        base["chat"] = {
            "name": "Member chat",
            **runtime.member_chat_policy(session.auth_member_id, session.auth_role),
        }
    return base


def crm_detail(runtime: Any, session: Session, party_id: str) -> dict[str, Any]:
    authorize_surface(session, "people")
    party = get_party(
        runtime.projections.connection("parties"), session, party_id=party_id
    )
    if party is None:
        raise HTTPException(404, "person not found")
    interactions = recent_with(
        runtime.projections.connection("interactions"),
        session,
        party_id=party_id,
        days=365,
    )
    signals = closeness_signals(
        runtime.projections.connection("interactions"),
        session,
        party_id=party_id,
        since_iso=(datetime.now(timezone.utc) - timedelta(days=365))
        .isoformat()
        .replace("+00:00", "Z"),
    )
    recurrences = [
        item
        for item in for_member(
            runtime.projections.connection("recurrences"),
            session,
            member_party_id=party_id,
        )
        if item.get("linked_id") == party_id or item.get("linked_kind") == "household"
    ]
    return {
        "party": party,
        "identifiers": identifiers_of(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "memberships": memberships_of(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "relationships": relationships_of(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "interactions": interactions,
        "commitments": by_party(
            runtime.projections.connection("commitments"), session, party_id=party_id
        ),
        "artifacts": linked_to(
            runtime.projections.connection("artifacts"),
            session,
            linked_kind="party",
            linked_id=party_id,
        ),
        "summary": party.get("attributes", {}).get("summary")
        if isinstance(party.get("attributes"), dict)
        else None,
        "tier": party.get("tier"),
        "contact_gap": signals,
        "hosting_balance": party.get("attributes", {}).get("hosting_balance")
        if isinstance(party.get("attributes"), dict)
        else None,
        "upcoming_recurrence": recurrences[:1],
        "linked": linked_to_party(
            runtime.projections.connection("places_assets_accounts"),
            session,
            party_id=party_id,
        ),
        "evidence_policy": "scope-filtered projection provenance",
    }


def evaluate_query(
    runtime: Any, session: Session, name: str, params: dict[str, Any]
) -> Any:
    """Evaluate one saved-view query through an existing scope-filtered seam."""

    del params  # Gate 7 declarations intentionally accept no cross-scope identifiers.
    if name == "what_now":
        return core._what_now(runtime, session)
    if name == "tasks.open":
        return open_for_member(
            runtime.projections.connection("tasks"),
            session,
            member_party_id=session.view_member_id,
        )
    if name == "lists.open":
        return open_items_for_member(
            runtime.projections.connection("lists"),
            session,
            member_id=session.view_member_id,
        )
    if name == "calendar.week":
        return week(
            runtime.projections.connection("calendars"),
            session,
            member_party_id=session.view_member_id,
            start_date_iso=datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        )
    if name == "finance.recent":
        if session.auth_role in {"child", "device", "ambient"}:
            raise HTTPException(404, "temporary surface not found")
        now = datetime.now(timezone.utc)
        return flows_in_range(
            runtime.projections.connection("money"),
            session,
            start_iso=(now - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
            end_iso=now.isoformat().replace("+00:00", "Z"),
        )
    raise HTTPException(422, "query is outside the temporary-surface allowlist")


__all__ = [
    "ADULT_SURFACES",
    "CHILD_SURFACES",
    "DEVICE_SURFACES",
    "authorize_surface",
    "build_surface",
    "crm_detail",
    "evaluate_query",
    "experience_mode",
    "navigation",
]
