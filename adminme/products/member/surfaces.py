"""Authorized read models for the ten constitutional household surfaces."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from adminme.calendar import compose_calendar_cockpit
from adminme.comms import compose_comms_cockpit
from adminme.reminders import (
    APPLE_OPERATIONS,
    GOOGLE_OPERATIONS,
    PROVIDER_CONTRACTS,
    compose_reminders_cockpit,
)
from adminme.lib.session import Session
from adminme.products.core import main as core
from adminme.today import compose_daily_brief
from adminme.projections.artifacts.queries import linked_to, list_recent
from adminme.projections.calendars.queries import (
    events_in_range,
    list_sources,
    rules_for_member,
    schedule_policy_for_member,
    today as calendar_today,
    week,
)
from adminme.projections.commitments.queries import by_party
from adminme.projections.interactions.queries import (
    closeness_signals,
    participants_for,
    recent,
    recent_for_member,
    recent_with,
)
from adminme.projections.lists.queries import (
    context_signals_for_member,
    items_for_member,
    lists_for_member,
    open_items_for_member,
    reminder_cues_for_member,
)
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
    "comms",
    "reminders",
    "calendar",
    "people",
    "knowledge",
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
    "knowledge": "Knowledge",
    "finance": "Finance",
    "calendar": "Calendar",
    "scoreboard": "Scoreboard",
    "settings": "Settings",
}


_COMMUNICATION_PROVIDERS: dict[str, tuple[str, str]] = {
    "messaging-gmail-api": ("gmail", "Email"),
    "messaging-microsoft-graph": ("microsoft_graph", "Email"),
    "messaging-bluebubbles": ("bluebubbles", "Messages"),
    "messaging-twilio-sms": ("twilio_sms", "SMS"),
    "messaging-twilio-whatsapp": ("twilio_whatsapp", "WhatsApp"),
}
_PROVIDER_WRITE_CAPABILITIES: dict[str, frozenset[str]] = {
    "gmail": frozenset({"reply", "send", "mark_read", "archive"}),
    "microsoft_graph": frozenset({"reply", "send", "mark_read", "archive"}),
    "bluebubbles": frozenset({"reply", "send"}),
    "twilio_sms": frozenset({"reply", "send"}),
    "twilio_whatsapp": frozenset({"reply", "send"}),
}


def reminder_accounts(
    runtime: Any,
    member_id: str,
    *,
    projected_items: list[dict[str, Any]],
    projected_lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Negotiate exact provider capabilities without leaking credentials."""

    accounts: dict[tuple[str, str], dict[str, Any]] = {}
    for index, entry in enumerate(runtime.settings.adapter_config.get("adapters", [])):
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        adapter_id = str(
            entry.get("id") or entry.get("kind") or entry.get("pack_id") or ""
        ).removeprefix("adapter:")
        config = entry.get("config") or {}
        if not isinstance(config, dict) or config.get("member_id") != member_id:
            continue
        if "google-tasks" in adapter_id:
            provider = "google_tasks"
            operations = GOOGLE_OPERATIONS
            account_id = str(
                config.get("provider_account_id")
                or config.get("user_email")
                or f"google-tasks-{index}"
            )
            label = str(config.get("account_label") or "Google Tasks")
            runtime_shape = "central_oauth"
        elif "apple-eventkit" in adapter_id:
            provider = "apple_reminders"
            operations = APPLE_OPERATIONS
            account_id = str(config.get("provider_account_id") or member_id)
            label = str(config.get("account_label") or "Apple Reminders")
            runtime_shape = str(config.get("deployment_target") or "central")
        else:
            continue
        contract = PROVIDER_CONTRACTS[provider]
        accounts[(provider, account_id)] = {
            "id": str(entry.get("id") or f"{provider}-{index}"),
            "provider": provider,
            "provider_account_id": account_id,
            "label": label,
            "runtime": runtime_shape,
            "state": "observation_only" if runtime._observation_active else "writable",
            "operations": sorted(operations),
            "native": contract["native"],
            "limitations": contract["limitations"],
        }

    for row in [*projected_lists, *projected_items]:
        provider = str(row.get("provider") or "")
        account_id = str(row.get("provider_account_id") or "")
        if provider not in PROVIDER_CONTRACTS or not account_id:
            continue
        key = (provider, account_id)
        if key in accounts:
            continue
        contract = PROVIDER_CONTRACTS[provider]
        accounts[key] = {
            "id": f"projected:{provider}:{account_id}",
            "provider": provider,
            "provider_account_id": account_id,
            "label": contract["label"],
            "runtime": "projected_only",
            "state": "read_only",
            "operations": [],
            "native": contract["native"],
            "limitations": contract["limitations"],
            "remedy": "Reconnect this member's provider adapter to enable writes.",
        }
    return sorted(
        accounts.values(),
        key=lambda account: (account["provider"], account["label"].casefold()),
    )


def communication_accounts(runtime: Any, member_id: str) -> list[dict[str, Any]]:
    """Expose secret-free, member-bound capability negotiation to the UI."""

    accounts: list[dict[str, Any]] = []
    for index, entry in enumerate(runtime.settings.adapter_config.get("adapters", [])):
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled") is False:
            continue
        kind = str(entry.get("kind") or "").removeprefix("adapter:")
        provider_info = _COMMUNICATION_PROVIDERS.get(kind)
        if provider_info is None:
            continue
        config = entry.get("config") or {}
        if not isinstance(config, dict) or config.get("member_id") != member_id:
            continue
        provider, channel = provider_info
        account_id = str(
            config.get("provider_account_id")
            or config.get("user_email")
            or entry.get("id")
            or f"{provider}-{index}"
        )
        workspace = str(config.get("workspace") or "personal")
        writes = bool(config.get("enable_writes"))
        write_capabilities = _PROVIDER_WRITE_CAPABILITIES[provider]
        accounts.append(
            {
                "id": str(entry.get("id") or f"{kind}-{index}"),
                "provider": provider,
                "provider_account_id": account_id,
                "label": str(config.get("account_label") or account_id),
                "channel": channel,
                "workspace": workspace,
                "state": "observation_only"
                if runtime._observation_active
                else "connected",
                "capabilities": {
                    "read": True,
                    "threads": True,
                    "reply": writes and "reply" in write_capabilities,
                    "send": writes and "send" in write_capabilities,
                    "mark_read": writes and "mark_read" in write_capabilities,
                    "archive": writes and "archive" in write_capabilities,
                    "attachments": "metadata_only",
                    "snooze_local": True,
                    "quiet_local": True,
                },
                "write_access_remedy": None
                if writes
                else "Reconnect this account with provider write access.",
            }
        )
    if (
        runtime.fixture_adapter is not None
        and runtime.settings.fixture_member_id == member_id
    ):
        accounts.append(
            {
                "id": "fixture-messaging",
                "provider": "fixture",
                "provider_account_id": "fixture-account",
                "label": "Deterministic fixture",
                "channel": "Test messages",
                "workspace": "personal",
                "state": "observation_only"
                if runtime._observation_active
                else "connected",
                "capabilities": {
                    "read": True,
                    "threads": True,
                    "reply": True,
                    "send": True,
                    "mark_read": False,
                    "archive": False,
                    "attachments": "metadata_only",
                    "snooze_local": True,
                    "quiet_local": True,
                },
                "write_access_remedy": None,
            }
        )
    return accounts


def _connector_catalog() -> list[dict[str, Any]]:
    """Reviewed capability ceiling; configured accounts may expose less."""

    return [
        {
            "provider": "gmail",
            "label": "Gmail / Google Workspace",
            "runtime": "central",
            "supported": [
                "sync",
                "threads",
                "reply",
                "send",
                "read_state",
                "archive",
                "labels",
                "attachment_metadata",
            ],
            "limitations": [
                "push requires Pub/Sub watch renewal and history fallback",
                "attachment content/download is not yet installed",
            ],
        },
        {
            "provider": "microsoft_graph",
            "label": "Outlook / Microsoft 365",
            "runtime": "central",
            "supported": [
                "sync",
                "threads",
                "reply",
                "send",
                "read_state",
                "folders",
                "flags",
            ],
            "limitations": [
                "shared-mailbox subscriptions require application permissions"
            ],
        },
        {
            "provider": "bluebubbles",
            "label": "iMessage / Apple Messages",
            "runtime": "member_bridge",
            "supported": [
                "sync",
                "threads",
                "reply",
                "send",
                "attachments",
                "delivery_state",
            ],
            "limitations": [
                "no supported Apple history API; requires an explicitly trusted local bridge"
            ],
        },
        {
            "provider": "twilio_sms",
            "label": "Twilio SMS / MMS",
            "runtime": "central_webhook",
            "supported": ["inbound", "reply", "send", "media", "delivery_state"],
            "limitations": ["provider number is the sender identity"],
        },
        {
            "provider": "twilio_whatsapp",
            "label": "WhatsApp via Twilio",
            "runtime": "central_webhook",
            "supported": ["inbound", "reply", "send", "media", "delivery_state"],
            "limitations": [
                "free-form replies are limited to the 24-hour service window; templates apply outside it"
            ],
        },
    ]


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
        zone = ZoneInfo(runtime.settings.instance.timezone)
        local_now = now.astimezone(zone)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        calendar_conn = runtime.projections.connection("calendars")
        calendar_events = calendar_today(
            calendar_conn,
            session,
            member_party_id=member,
            today_iso=now.astimezone(ZoneInfo(runtime.settings.instance.timezone))
            .date()
            .isoformat(),
            tz_name=runtime.settings.instance.timezone,
        )
        calendar_cockpit = compose_calendar_cockpit(
            member_id=member,
            timezone_name=runtime.settings.instance.timezone,
            as_of=now,
            range_start=local_start,
            range_end=local_end,
            events=calendar_events,
            sources=list_sources(calendar_conn, session),
            rules=rules_for_member(calendar_conn, session, member_id=member),
            schedule_policy=schedule_policy_for_member(
                calendar_conn, session, member_id=member
            ),
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
            calendar_events=calendar_cockpit["events"],
            interactions=interactions,
            triage_items=questions,
            calendar_guidance={
                "current_guardrail": calendar_cockpit["current_guardrail"],
                "open_windows": calendar_cockpit["open_windows"],
                "counts": calendar_cockpit["counts"],
            },
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
        calendar_conn = runtime.projections.connection("calendars")
        base["calendar_destinations"] = compose_calendar_cockpit(
            member_id=member,
            timezone_name=runtime.settings.instance.timezone,
            as_of=now,
            range_start=now,
            range_end=now + timedelta(days=30),
            events=[],
            sources=list_sources(calendar_conn, session),
            rules=rules_for_member(calendar_conn, session, member_id=member),
            schedule_policy=schedule_policy_for_member(
                calendar_conn, session, member_id=member
            ),
        )["write_destinations"]
    elif surface == "comms":
        interactions_conn = runtime.projections.connection("interactions")
        rows = recent_for_member(
            interactions_conn,
            session,
            member_id=member,
            limit=500,
        )
        triage_items = pending_triage(
            runtime.projections.connection("triage"), session, member_id=member
        )
        party_labels = {
            str(party["party_id"]): str(party.get("display_name") or "Contact")
            for party in all_parties(runtime.projections.connection("parties"), session)
        }
        base["cockpit"] = compose_comms_cockpit(
            member_id=member,
            rows=rows,
            participants=participants_for(
                interactions_conn,
                session,
                interaction_ids=[str(row["interaction_id"]) for row in rows],
            ),
            party_labels=party_labels,
            accounts=communication_accounts(runtime, member),
            triage_items=triage_items,
            now=now,
        )
        base["connector_catalog"] = _connector_catalog()
    elif surface == "reminders":
        lists_conn = runtime.projections.connection("lists")
        reminder_items = items_for_member(lists_conn, session, member_id=member)
        reminder_lists = lists_for_member(lists_conn, session, member_id=member)
        accounts = reminder_accounts(
            runtime,
            member,
            projected_items=reminder_items,
            projected_lists=reminder_lists,
        )
        base["cockpit"] = compose_reminders_cockpit(
            member_id=member,
            timezone_name=runtime.settings.instance.timezone,
            items=reminder_items,
            lists=reminder_lists,
            accounts=accounts,
            cues=reminder_cues_for_member(lists_conn, session, member_id=member),
            signals=context_signals_for_member(lists_conn, session, member_id=member),
            now=now,
        )
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
        calendar_conn = runtime.projections.connection("calendars")
        zone = ZoneInfo(runtime.settings.instance.timezone)
        range_start = now.astimezone(zone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        range_end = range_start + timedelta(days=7)
        rows = events_in_range(
            calendar_conn,
            session,
            member_party_id=member,
            range_start_iso=range_start.astimezone(timezone.utc).isoformat(),
            range_end_iso=range_end.astimezone(timezone.utc).isoformat(),
        )
        base["events"] = rows
        base["cockpit"] = compose_calendar_cockpit(
            member_id=member,
            timezone_name=runtime.settings.instance.timezone,
            as_of=now,
            range_start=range_start,
            range_end=range_end,
            events=rows,
            sources=list_sources(calendar_conn, session),
            rules=rules_for_member(calendar_conn, session, member_id=member),
            schedule_policy=schedule_policy_for_member(
                calendar_conn, session, member_id=member
            ),
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
