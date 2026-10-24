"""Authorized read models for the ten constitutional household surfaces."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from adminme.calendar import compose_calendar_cockpit, preview_calendar_candidate
from adminme.comms import compose_comms_cockpit
from adminme.context_fabric import policy_scope_digest
from adminme.people import PROVIDER_CONTRACTS as PEOPLE_PROVIDER_CONTRACTS
from adminme.people import compose_people_cockpit
from adminme.reminders import (
    APPLE_OPERATIONS,
    GOOGLE_OPERATIONS,
    PROVIDER_CONTRACTS,
    compose_effective_reminders,
    compose_reminders_cockpit,
)
from adminme.lib.session import Session
from adminme.messages_reply import reply_control_for
from adminme.triage_review import triage_review_state as _triage_review_state
from adminme.products.member.navigation import (
    ADULT_SURFACES,
    CHILD_SURFACES,
    DEVICE_SURFACES,
    SURFACE_LABELS,
    authorize_surface,
    experience_mode,
    experience_semantics,
    navigation,
)
from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.products.core import main as core
from adminme.today import compose_daily_brief
from adminme.triage_contract import TRIAGE_ENRICHMENT_CONTRACT, triage_destination
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
from adminme.projections.commitments.queries import (
    work_history_for_member as commitment_history_for_member,
)
from adminme.projections.interactions.queries import (
    closeness_signals,
    participants_for,
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
from adminme.projections.member_knowledge.queries import grants_for_note, list_visible
from adminme.projections.money.queries import finance_snapshot, flows_in_range
from adminme.projections.parties.queries import (
    all_parties,
    contact_records_for_person,
    get_party,
    get_visible_person,
    identifiers_for_person,
    list_visible_people,
    member_relationship_profile,
    memberships_of,
    people_source_summary,
    relationship_facts_for_person,
    relationships_of,
    search_visible_people,
)
from adminme.projections.places_assets_accounts.queries import (
    linked_to_party,
    registry_items,
)
from adminme.projections.recurrences.queries import (
    completion_history_for_member,
    for_member,
    routine_definitions_for_member,
)
from adminme.projections.tasks.queries import (
    actionable_for_member,
    work_history_for_member,
)
from adminme.projections.triage.queries import pending_for_member as pending_triage
from adminme.projections.triage.queries import (
    pending_for_sources as pending_triage_for_sources,
)
from adminme.projections.triage.queries import recent_for_member as recent_triage
from adminme.projections.triage.queries import (
    get_item as get_triage_item,
    matter_for_proposal as triage_matter_for_proposal,
    triage_page_for_member,
)
from adminme.projections.context_fabric.queries import (
    feedback_pattern_counts,
    policy_rules_for_member,
    source_policy_proposals_for_member,
    source_policy_rules_for_member,
)
from adminme.surface_contracts import surface_freshness, surface_link
from adminme.settings_control import (
    compose_connections,
    compose_settings_control,
    member_label,
    public_capability_summary,
)


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
    # G10 is strictly observational. G11 may add a separately reviewed reply
    # capability; no existing provider command is inherited here.
    "bluebubbles": frozenset(),
    "twilio_sms": frozenset({"reply", "send"}),
    "twilio_whatsapp": frozenset({"reply", "send"}),
}


def _triage_owed_to_party_available(
    runtime: Any, session: Session, item: dict[str, Any]
) -> bool | None:
    """Check an exact Party reference without resolving a name or handle."""
    if item.get("target_kind") != "commitment":
        return None
    party_id = (item.get("candidate") or {}).get("owed_to_party_id")
    if not party_id or runtime.projections is None:
        return False
    return (
        get_party(
            runtime.projections.connection("parties"),
            session,
            party_id=str(party_id),
        )
        is not None
    )


def _json_string_list(value: Any) -> list[str]:
    """Decode a projection JSON list without allowing malformed source data to break a surface."""

    if isinstance(value, list):
        return [str(item) for item in value if item]
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]


def _triage_shadow_rule_value(item: dict[str, Any], scope_kind: str) -> str | None:
    """Return only an already-scoped candidate coordinate for shadow matching."""

    candidate = item.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    route = item.get("route")
    route = route if isinstance(route, dict) else {}
    values = {
        "candidate_kind": item.get("target_kind"),
        "context": item.get("context_id"),
        "account": candidate.get("source_account_id")
        or candidate.get("provider_account_id"),
        "workspace": candidate.get("workspace") or route.get("workspace"),
        "sender": candidate.get("sender_digest"),
    }
    value = values.get(scope_kind)
    return str(value) if value else None


def _annotate_triage_shadow_rules(
    items: list[dict[str, Any]], rules: list[dict[str, Any]]
) -> None:
    """Annotate reversible rule matches without changing queue or truth state."""

    active_rules = [rule for rule in rules if rule.get("status") == "active"]
    for item in items:
        matches: list[str] = []
        dispositions: list[str] = []
        for rule in active_rules:
            scope_kind = str(rule.get("scope_kind") or "")
            value = _triage_shadow_rule_value(item, scope_kind)
            if value is None:
                continue
            if policy_scope_digest(scope_kind, value) != rule.get("scope_value_digest"):
                continue
            matches.append(str(rule["rule_id"]))
            dispositions.append(str(rule["disposition"]))
        item["policy_rule_ids"] = matches
        item["policy_disposition"] = dispositions[0] if dispositions else "none"
        item["policy_shadow_only"] = bool(matches)


def people_accounts(runtime: Any, member_id: str) -> list[dict[str, Any]]:
    """Return member-bound contact sources with truthful capability state."""

    accounts: list[dict[str, Any]] = []
    aliases = {
        "apple-contacts": "apple_contacts",
        "google-people": "google_people",
        "microsoft-graph-contacts": "microsoft_graph",
        "carddav-contacts": "carddav",
    }
    connection_truth = {
        item["id"]: item for item in compose_connections(runtime, member_id)
    }
    for index, entry in enumerate(runtime.settings.adapter_config.get("adapters", [])):
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        adapter_id = str(
            entry.get("id") or entry.get("kind") or entry.get("pack_id") or ""
        ).removeprefix("adapter:")
        provider = next(
            (value for token, value in aliases.items() if token in adapter_id), None
        )
        config = entry.get("config") or {}
        if provider is None or not isinstance(config, dict):
            continue
        if config.get("member_id") != member_id:
            continue
        contract = PEOPLE_PROVIDER_CONTRACTS[provider]
        account_id = str(
            config.get("provider_account_id")
            or config.get("user_email")
            or config.get("account_url")
            or f"{provider}-{index}"
        )
        connection_id = str(entry.get("id") or f"{provider}-{index}")
        truth = connection_truth.get(connection_id)
        account_state = (
            str(truth["setup"])
            if truth and truth["setup"] != "connected"
            else str(truth["runtime"])
            if truth and truth["runtime"] != "running"
            else "observation_only"
            if runtime._observation_active
            else "connected"
        )
        accounts.append(
            {
                "id": connection_id,
                "provider": provider,
                "provider_account_id": account_id,
                "label": str(config.get("account_label") or contract["label"]),
                "runtime": contract["runtime"],
                "operations": list(contract["operations"])
                if account_state in {"connected", "observation_only"}
                else [],
                "limitations": list(contract["limitations"]),
                "state": account_state,
                "connection_truth": truth,
            }
        )
    return accounts


def apple_calendar_destinations(runtime: Any, session: Session) -> list[dict[str, Any]]:
    """Owner write grants are destinations, never fabricated provider facts."""
    from adminme.apple_service_permissions import permission_options, service_permission

    member = session.auth_member_id
    if member != session.view_member_id:
        return []
    grant = service_permission(runtime, member, "apple_calendar")
    if grant is None:
        return []
    options = permission_options(runtime, member, "apple_calendar")
    return [
        {
            "rule_id": f"apple-permission:{grant['event_id']}:{scope['id']}",
            "subject_member_id": member,
            "source_member_id": member,
            "provider": "apple_calendar",
            "provider_account_id": grant["provider_account_id"],
            "calendar_external_id": scope["container_id"],
            "calendar_name": scope["label"],
            "write_target": True,
            "write_events": True,
            "enabled": True,
            "destination_kind": "personal",
            "authority_source": "owner_service_permission",
            "last_event_id": grant["event_id"],
        }
        for scope in options["scopes"]
        if scope["container_id"] in grant["allowed_container_ids"]
    ]


def reminder_accounts(
    runtime: Any,
    member_id: str,
    *,
    projected_items: list[dict[str, Any]],
    projected_lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Negotiate exact provider capabilities without leaking credentials."""

    accounts: dict[tuple[str, str], dict[str, Any]] = {}
    connection_truth = {
        item["id"]: item for item in compose_connections(runtime, member_id)
    }
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
            configured_container = config.get("tasklist_id")
            configured_container_label = config.get("list_name")
        elif "apple-eventkit" in adapter_id:
            provider = "apple_reminders"
            operations = APPLE_OPERATIONS
            account_id = str(config.get("provider_account_id") or member_id)
            label = str(config.get("account_label") or "Apple Reminders")
            runtime_shape = str(config.get("deployment_target") or "central")
            configured_container = config.get("target_list_identifier")
            configured_container_label = config.get("target_list_name")
        else:
            continue
        contract = PROVIDER_CONTRACTS[provider]
        connection_id = str(entry.get("id") or f"{provider}-{index}")
        truth = connection_truth.get(connection_id)
        account_ready = bool(
            truth and truth["setup"] == "connected" and truth["runtime"] == "running"
        )
        account_state = (
            "observation_only"
            if account_ready and runtime._observation_active
            else "writable"
            if account_ready
            else str(truth["setup"])
            if truth and truth["setup"] != "connected"
            else str(truth["runtime"])
            if truth
            else "unknown"
        )
        accounts[(provider, account_id)] = {
            "id": connection_id,
            "provider": provider,
            "provider_account_id": account_id,
            "label": label,
            "runtime": runtime_shape,
            "state": account_state,
            "operations": sorted(operations) if account_ready else [],
            "native": contract["native"],
            "limitations": contract["limitations"],
            "connection_truth": truth,
            "configured_containers": (
                [
                    {
                        "list_external_id": str(configured_container),
                        "list_name": str(
                            configured_container_label or configured_container
                        ),
                    }
                ]
                if configured_container
                else []
            ),
        }

    from adminme.apple_service_permissions import service_permission

    grant = service_permission(runtime, member_id, "apple_reminders")
    if grant is not None:
        contract = PROVIDER_CONTRACTS["apple_reminders"]
        account_id = grant["provider_account_id"]
        names = {
            str(row["list_external_id"]): row.get("list_name")
            for row in projected_lists
            if row.get("provider") == "apple_reminders"
            and row.get("provider_account_id") == account_id
        }
        accounts[("apple_reminders", account_id)] = {
            "id": f"bridge:{grant['enrollment_id']}:reminders-apple-eventkit-bridge",
            "provider": "apple_reminders",
            "provider_account_id": account_id,
            "label": "Apple Reminders",
            "runtime": "member_bridge",
            "state": "observation_only" if runtime._observation_active else "writable",
            "operations": list(grant["operations"]),
            "native": contract["native"],
            "limitations": contract["limitations"],
            "approvals_required": True,
            "configured_containers": [
                {
                    "list_external_id": value,
                    "list_name": names.get(value) or "Selected reminder list",
                }
                for value in grant["allowed_container_ids"]
            ],
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
            "configured_containers": [],
        }
    return sorted(
        accounts.values(),
        key=lambda account: (account["provider"], account["label"].casefold()),
    )


def communication_accounts(runtime: Any, member_id: str) -> list[dict[str, Any]]:
    """Expose secret-free, member-bound capability negotiation to the UI."""

    accounts: list[dict[str, Any]] = []
    connection_truth = {
        item["id"]: item for item in compose_connections(runtime, member_id)
    }
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
        connection_id = str(entry.get("id") or f"{kind}-{index}")
        truth = connection_truth.get(connection_id)
        account_ready = bool(
            truth and truth["setup"] == "connected" and truth["runtime"] == "running"
        )
        writes = bool(config.get("enable_writes")) and account_ready
        write_capabilities = _PROVIDER_WRITE_CAPABILITIES[provider]
        accounts.append(
            {
                "id": connection_id,
                "provider": provider,
                "provider_account_id": account_id,
                "label": str(config.get("account_label") or account_id),
                "channel": channel,
                "workspace": workspace,
                "state": "observation_only"
                if account_ready and runtime._observation_active
                else "connected"
                if account_ready
                else str(truth["setup"])
                if truth and truth["setup"] != "connected"
                else str(truth["runtime"])
                if truth
                else "unknown",
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
                "connection_truth": truth,
            }
        )
    for identity, record in sorted(
        getattr(runtime.settings, "bridge_enrollments", {}).items()
    ):
        if (
            not isinstance(record, dict)
            or record.get("status") != "active"
            or record.get("member_id") != member_id
            or "messaging-bluebubbles" not in record.get("configured_packs", [])
        ):
            continue
        config = (record.get("pack_config") or {}).get("messaging-bluebubbles")
        if not isinstance(config, dict):
            continue
        authorized = "messaging-bluebubbles" in record.get("authorized_packs", [])
        enabled = "messaging-bluebubbles" in record.get("enabled_packs", [])
        active = "messaging-bluebubbles" in record.get("adapters_active", [])
        bridge_request_for = getattr(
            runtime.settings_control, "bridge_request_for", None
        )
        request = bridge_request_for(member_id) if callable(bridge_request_for) else {}
        if not isinstance(request, dict):
            request = {}
        capability_states = request.get("capability_states")
        if not isinstance(capability_states, dict):
            capability_states = {}
        capability = capability_states.get("apple_messages_read")
        if not isinstance(capability, dict):
            capability = {}
        state = str(capability.get("state") or "configured")
        account_id = str(config.get("provider_account_id") or "")
        if not account_id:
            continue
        reply_control = reply_control_for(runtime, member_id)
        reply_enabled = (
            reply_control["state"] == "enabled_ask_every_time"
            and reply_control.get("bridge_enrollment_id") == record.get("enrollment_id")
            and reply_control.get("provider_account_id") == account_id
        )
        accounts.append(
            {
                "id": f"bridge:{record.get('enrollment_id')}:messaging-bluebubbles",
                "provider": "bluebubbles",
                "provider_account_id": account_id,
                "label": "Apple Messages",
                "channel": "Messages",
                "workspace": "personal",
                "state": (
                    "active"
                    if authorized
                    and enabled
                    and active
                    and state in {"active", "settled"}
                    else "settling"
                    if authorized and enabled and state in {"observing", "settling"}
                    else "degraded"
                    if state == "degraded"
                    else state
                ),
                "capabilities": {
                    "read": authorized and enabled,
                    "threads": authorized and enabled,
                    "reply": reply_enabled,
                    "send": False,
                    "mark_read": False,
                    "archive": False,
                    "reaction": False,
                    "edit": False,
                    "unsend": False,
                    "chat_create": False,
                    "attachments": "metadata_only",
                    "snooze_local": True,
                    "quiet_local": True,
                },
                "write_access_remedy": None
                if reply_enabled
                else reply_control.get("reason")
                or "Enable ask-every-time replies in Settings.",
                "reply_control": reply_control,
                "connection_truth": {
                    "runtime": "member_bridge",
                    "identity_bound": bool(identity),
                    "enrollment_id": record.get("enrollment_id"),
                    "connector_id": "messaging-bluebubbles",
                    "protected_source_id": config.get("protected_source_id"),
                    "transport_version": config.get("transport_version"),
                    "observation_only": runtime._observation_active,
                    "writes_authorized": reply_enabled,
                },
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
                "incoming_observation",
                "outgoing_observation",
                "group_conversations",
                "reply_continuity",
                "attachment_metadata",
                "exact_existing_chat_text_reply_ask_every_time",
            ],
            "limitations": [
                "G11 adds only exact-text reply to an observed chat; new chats, attachments, autonomous replies, reactions, edits, unsend, read state, and group changes remain unavailable",
                "bounded best-effort history has no provider replay cursor",
                "RCS and per-message account identity are not contracted by the pinned transport",
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


def _latest_time(rows: list[dict[str, Any]], *keys: str) -> datetime | None:
    values: list[datetime] = []
    for row in rows:
        for key in keys:
            value = row.get(key)
            if not isinstance(value, str) or not value:
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            values.append(
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)
            )
            break
    return max(values) if values else None


def _add_triage_requirement(item: dict[str, Any], requirement: str) -> None:
    review = item["review_state"]
    missing = list(review.get("missing") or [])
    if requirement not in missing:
        missing.append(requirement)
    review["missing"] = missing
    review["state"] = "needs_details"


def build_surface(
    runtime: Any,
    session: Session,
    surface: str,
    *,
    calendar_anchor: date | None = None,
    calendar_view: str = "week",
    triage_lens: str = "all_candidates",
    triage_sort: str = "attention",
    triage_source: str | None = None,
    triage_kind: str | None = None,
    triage_cursor: str | None = None,
    triage_limit: int = 30,
    triage_item: str | None = None,
) -> dict[str, Any]:
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
        "communication_intake": {
            "mode": "historical_compatibility"
            if runtime.settings.legacy_personal_obligation_compatibility
            else "member_forwarded",
            "member_selection_required": not runtime.settings.legacy_personal_obligation_compatibility,
        },
        "observation": {
            "active": bool(runtime._observation_active),
            "writes_execute": not bool(runtime._observation_active),
            "explanation": (
                "Observation mode records what would happen without changing providers."
                if runtime._observation_active
                else "Approved writes may execute after every governance gate passes."
            ),
            "evidence_href": "/api/core/v1/activity",
        },
    }
    if surface == "today":
        lists_conn = runtime.projections.connection("lists")
        reminder_items = items_for_member(lists_conn, session, member_id=member)
        reminder_lists = lists_for_member(lists_conn, session, member_id=member)
        reminder_account_rows = reminder_accounts(
            runtime,
            member,
            projected_items=reminder_items,
            projected_lists=reminder_lists,
        )
        effective_reminders = compose_effective_reminders(
            timezone_name=runtime.settings.instance.timezone,
            items=reminder_items,
            accounts=reminder_account_rows,
            cues=reminder_cues_for_member(lists_conn, session, member_id=member),
            signals=context_signals_for_member(lists_conn, session, member_id=member),
            now=now,
        )
        what_now = core._what_now(
            runtime,
            session,
            effective_list_items=[
                item for item in effective_reminders if not item.get("completed")
            ],
        )
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
            write_permissions=apple_calendar_destinations(runtime, session),
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
                runtime.projections.connection("triage"),
                session,
                member_id=member,
                limit=30,
            )
        )
        base["what_now"] = what_now
        base["lifecycle_options"] = {
            "commitment_delegates": [
                {
                    "party_id": party["party_id"],
                    "label": party.get("display_name") or "Unnamed person",
                    "kind": party.get("kind") or "person",
                }
                for party in all_parties(
                    runtime.projections.connection("parties"), session
                )
                if party["party_id"] != session.auth_member_id
                and party.get("kind") in {"person", "household", "organization"}
            ]
        }
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
        today_rows = [*calendar_events, *interactions, *questions]
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=_latest_time(
                today_rows,
                "provider_updated_at",
                "updated_at",
                "occurred_at",
                "proposed_at",
            ),
            stale_after_seconds=172800,
            row_count=len(today_rows),
        )
    elif surface == "triage":
        triage_conn = runtime.projections.connection("triage")
        try:
            triage_page = triage_page_for_member(
                triage_conn,
                session,
                member_id=member,
                lens=triage_lens,
                sort=triage_sort,
                source_id=triage_source,
                target_kind=triage_kind,
                cursor=triage_cursor,
                limit=triage_limit,
                human_centered=runtime.settings.human_triage_management_enabled,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        focused_item = (
            get_triage_item(triage_conn, session, proposal_id=triage_item)
            if triage_item
            else None
        )
        if focused_item is not None and focused_item.get("owner_member_id") != member:
            focused_item = None
        focused_matter = (
            triage_matter_for_proposal(
                triage_conn,
                session,
                member_id=member,
                proposal_id=triage_item,
            )
            if triage_item
            else None
        )
        if focused_matter is not None and not any(
            matter["context_id"] == focused_matter["context_id"]
            for matter in triage_page["matters"]
        ):
            triage_page["matters"].insert(0, focused_matter)
        items = [
            item for matter in triage_page["matters"] for item in matter["candidates"]
        ]
        recent_items = recent_triage(
            triage_conn,
            session,
            member_id=member,
            human_centered=runtime.settings.human_triage_management_enabled,
        )
        if (
            focused_item is not None
            and focused_item.get("state")
            not in {"pending_interpretation", "pending_review"}
            and not any(
                item["proposal_id"] == focused_item["proposal_id"]
                for item in recent_items
            )
        ):
            recent_items.insert(0, focused_item)
        for item in [*items, *recent_items]:
            item["review_state"] = _triage_review_state(
                item,
                owed_to_party_available=_triage_owed_to_party_available(
                    runtime, session, item
                ),
            )
            item["surface_link"] = surface_link(
                "triage",
                object_kind="item",
                object_id=str(item["proposal_id"]),
                params={
                    "matter": str(item.get("context_id") or item["proposal_id"]),
                    "view": (
                        "recent"
                        if item.get("state")
                        not in {"pending_interpretation", "pending_review"}
                        else "all_candidates"
                    ),
                },
                evidence_event_ids=[str(item.get("source_event_id") or "")],
            )
        for item in recent_items:
            if item.get("materialized_event_ids") or item.get(
                "domain_approval_event_id"
            ):
                item["destination"] = {
                    **triage_destination(item),
                    "state": (
                        "materialized"
                        if item.get("materialized_event_ids")
                        else "awaiting_provider_result"
                    ),
                }
        base["items"] = items
        base["recent_items"] = recent_items
        base["triage"] = triage_page
        base["focus"] = {
            "requested_candidate_id": triage_item,
            "state": (
                "not_requested"
                if not triage_item
                else "not_found"
                if focused_item is None
                else "pending"
                if focused_item.get("state")
                in {"pending_interpretation", "pending_review"}
                else "resolved"
            ),
            "candidate_state": focused_item.get("state") if focused_item else None,
            "matter_id": (
                str(focused_item.get("context_id") or focused_item["proposal_id"])
                if focused_item
                else None
            ),
        }
        feedback_patterns = feedback_pattern_counts(
            runtime.projections.connection("context_fabric"),
            session,
            member_id=member,
        )
        policy_rules = policy_rules_for_member(
            runtime.projections.connection("context_fabric"),
            session,
            member_id=member,
        )
        source_policy_proposals = source_policy_proposals_for_member(
            runtime.projections.connection("context_fabric"),
            session,
            member_id=member,
        )
        source_policy_rules = source_policy_rules_for_member(
            runtime.projections.connection("context_fabric"),
            session,
            member_id=member,
        )
        _annotate_triage_shadow_rules(items, policy_rules)
        if triage_lens == "learned_rules":
            matched_matters: list[dict[str, Any]] = []
            matched_items: list[dict[str, Any]] = []
            for matter in triage_page["matters"]:
                candidates = [
                    item
                    for item in matter["candidates"]
                    if item.get("policy_shadow_only")
                ]
                if not candidates:
                    continue
                matched_items.extend(candidates)
                matched_matters.append(
                    {
                        **matter,
                        "root_proposal_id": candidates[0]["proposal_id"],
                        "candidate_count": len(candidates),
                        "source_count": len(
                            {str(item["source_event_id"]) for item in candidates}
                        ),
                        "candidates": candidates,
                    }
                )
            items = matched_items
            triage_page["matters"] = matched_matters
            base["items"] = items
        base["triage"]["feedback_suggestions"] = []
        for pattern in feedback_patterns:
            if int(pattern["occurrences"]) < 3:
                continue
            scope_kind = pattern.get("scope_kind")
            scope_value = pattern.get("scope_value")
            if not scope_value:
                continue
            scope_digest = policy_scope_digest(str(scope_kind), str(scope_value))
            prior_rules = [
                rule
                for rule in policy_rules
                if rule["scope_kind"] == scope_kind
                and rule["scope_value_digest"] == scope_digest
            ]
            if any(
                rule["status"] == "active"
                or int(pattern["occurrences"]) <= int(rule["preview_affected_count"])
                for rule in prior_rules
            ):
                continue
            base["triage"]["feedback_suggestions"].append(
                {
                    **pattern,
                    "scope_kind": scope_kind,
                    "scope_value": scope_value,
                    "suggested_disposition": "route_to_low_signal",
                    "state": "suggestion_only",
                    "explanation": (
                        "This pattern can become a reversible shadow rule only "
                        "after your approval."
                    ),
                }
            )
        base["triage"]["policy_rules"] = policy_rules
        base["triage"]["source_policy_proposals"] = source_policy_proposals
        base["triage"]["source_policy_rules"] = source_policy_rules
        base["human_triage_management"] = {
            "active": runtime.settings.human_triage_management_enabled,
            "default_off": False,
            "backlog_preview_available": True,
            "backlog_application_available": False,
            "provider_effects": 0,
        }
        base["contract"] = {
            "facts_are_not_queued": True,
            "human_gate_required": True,
            "states": [
                "pending_interpretation",
                "pending_review",
                "approved",
                "confirmed",
                "dismissed",
                "failed",
            ],
        }
        base["enrichment_schema"] = TRIAGE_ENRICHMENT_CONTRACT
        calendar_conn = runtime.projections.connection("calendars")
        calendar_sources = list_sources(calendar_conn, session)
        calendar_rules = rules_for_member(calendar_conn, session, member_id=member)
        calendar_policy = schedule_policy_for_member(
            calendar_conn, session, member_id=member
        )
        calendar_destinations = compose_calendar_cockpit(
            write_permissions=apple_calendar_destinations(runtime, session),
            member_id=member,
            timezone_name=runtime.settings.instance.timezone,
            as_of=now,
            range_start=now,
            range_end=now + timedelta(days=30),
            events=[],
            sources=calendar_sources,
            rules=calendar_rules,
            schedule_policy=calendar_policy,
        )["write_destinations"]
        for item in items:
            if item["target_kind"] != "calendar_event":
                continue
            candidate = item.get("candidate") or {}
            start_value = candidate.get("start") or candidate.get("start_at")
            end_value = candidate.get("end") or candidate.get("end_at")
            candidate_events: list[dict[str, Any]] = []
            try:
                candidate_start = datetime.fromisoformat(
                    str(start_value).replace("Z", "+00:00")
                )
                candidate_end = datetime.fromisoformat(
                    str(end_value).replace("Z", "+00:00")
                )
                if candidate_start.tzinfo is None:
                    candidate_start = candidate_start.replace(
                        tzinfo=ZoneInfo(runtime.settings.instance.timezone)
                    )
                if candidate_end.tzinfo is None:
                    candidate_end = candidate_end.replace(
                        tzinfo=ZoneInfo(runtime.settings.instance.timezone)
                    )
                candidate_events = events_in_range(
                    calendar_conn,
                    session,
                    member_party_id=member,
                    range_start_iso=(candidate_start - timedelta(days=1))
                    .astimezone(timezone.utc)
                    .isoformat(),
                    range_end_iso=(candidate_end + timedelta(days=1))
                    .astimezone(timezone.utc)
                    .isoformat(),
                )
            except (TypeError, ValueError):
                pass
            item["calendar_preview"] = preview_calendar_candidate(
                write_permissions=apple_calendar_destinations(runtime, session),
                member_id=member,
                timezone_name=runtime.settings.instance.timezone,
                as_of=now,
                item=item,
                events=candidate_events,
                sources=calendar_sources,
                rules=calendar_rules,
                schedule_policy=calendar_policy,
                observation_active=bool(runtime._observation_active),
            )
        lists_conn = runtime.projections.connection("lists")
        reminder_items = items_for_member(lists_conn, session, member_id=member)
        reminder_lists = lists_for_member(lists_conn, session, member_id=member)
        reminder_account_rows = reminder_accounts(
            runtime,
            member,
            projected_items=reminder_items,
            projected_lists=reminder_lists,
        )
        reminder_destinations = [dict(row) for row in reminder_lists]
        known_reminder_routes = {
            (
                str(row.get("provider") or ""),
                str(row.get("provider_account_id") or ""),
                str(row.get("list_external_id") or ""),
            )
            for row in reminder_destinations
        }
        for configured_account in reminder_account_rows:
            for container in configured_account.get("configured_containers") or []:
                configured_route = (
                    str(configured_account["provider"]),
                    str(configured_account["provider_account_id"]),
                    str(container["list_external_id"]),
                )
                if configured_route in known_reminder_routes:
                    continue
                known_reminder_routes.add(configured_route)
                reminder_destinations.append(
                    {
                        "provider": configured_route[0],
                        "provider_account_id": configured_route[1],
                        "list_external_id": configured_route[2],
                        "list_name": container["list_name"],
                        "destination_source": "configured_adapter",
                    }
                )
        parties = all_parties(runtime.projections.connection("parties"), session)
        parties_by_id = {str(party["party_id"]): party for party in parties}
        base["calendar_destinations"] = calendar_destinations
        base["destinations"] = {
            "members": [
                {
                    "member_id": member_id,
                    "label": (
                        parties_by_id.get(member_id, {}).get("display_name")
                        or member_label(runtime, member_id)
                    ),
                    "role": role,
                }
                for member_id, role in runtime.settings.member_roles.items()
                if role not in {"device", "ambient"}
            ],
            "people": [
                {
                    "party_id": party["party_id"],
                    "label": party.get("display_name") or "Unnamed person",
                    "kind": party.get("kind") or "person",
                }
                for party in parties
            ],
            "calendars": calendar_destinations,
            "reminder_accounts": reminder_account_rows,
            "reminder_lists": reminder_destinations,
        }
        calendar_routes = {
            (
                str(row.get("provider") or ""),
                str(row.get("provider_account_id") or ""),
                str(row.get("calendar_external_id") or ""),
            )
            for row in calendar_destinations
        }
        reminder_accounts_by_route = {
            (
                str(row.get("provider") or ""),
                str(row.get("provider_account_id") or ""),
            ): row
            for row in reminder_account_rows
        }
        for item in items:
            item_route = item.get("route") or {}
            target = str(item.get("target_kind") or "")
            route_key = (
                str(item_route.get("provider") or ""),
                str(item_route.get("provider_account_id") or ""),
                str(item_route.get("container_external_id") or ""),
            )
            if target == "calendar_event":
                preview = item.get("calendar_preview") or {}
                if route_key not in calendar_routes or preview.get("state") != "ready":
                    _add_triage_requirement(item, "approved_calendar_destination")
            elif target in {"reminder", "list_item", "list"}:
                routed_account = reminder_accounts_by_route.get(route_key[:2])
                required_operation = (
                    "create_list"
                    if target == "list"
                    else "create_reminder"
                    if route_key[0] == "apple_reminders"
                    else "create_task"
                )
                account_writable = bool(
                    routed_account
                    and routed_account.get("state") != "read_only"
                    and required_operation
                    in set(routed_account.get("operations") or [])
                )
                container_ready = target == "list" or route_key in known_reminder_routes
                if not account_writable or not container_ready:
                    _add_triage_requirement(item, "approved_reminder_destination")
        triage_processing = runtime.capabilities.get("surface:triage-processing")
        triage_projection = runtime.capabilities.get("projection:triage")
        triage_details = (
            triage_projection.details if triage_projection is not None else {}
        )
        base["processing_health"] = (
            triage_processing.public()
            if triage_processing is not None
            else {
                "name": "triage-processing",
                "state": "degraded",
                "reason": "Triage processing capability is unavailable",
                "details": {},
            }
        )
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=_latest_time(
                [*items, *recent_items], "proposed_at", "resolved_at"
            ),
            stale_after_seconds=604800,
            row_count=len(items),
            degraded_reason=(
                triage_processing.reason
                if triage_processing is not None
                and triage_processing.state in {"degraded", "blocked"}
                else None
            ),
            checkpoint_event_id=triage_details.get("checkpoint_event_id"),
            checkpoint_required=True,
            lag_count=triage_details.get("lag_count"),
        )
    elif surface == "comms":
        interactions_conn = runtime.projections.connection("interactions")
        rows = recent_for_member(
            interactions_conn,
            session,
            member_id=member,
            limit=500,
        )
        comms_source_ids = [
            str(source_event_id)
            for row in rows
            for source_event_id in _json_string_list(row.get("raw_event_ids"))
        ]
        triage_items = pending_triage_for_sources(
            runtime.projections.connection("triage"),
            session,
            member_id=member,
            source_event_ids=comms_source_ids,
            human_centered=runtime.settings.human_triage_management_enabled,
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
            human_centered=runtime.settings.human_triage_management_enabled,
        )
        base["human_triage_management"] = {
            "active": runtime.settings.human_triage_management_enabled,
            "focus_requires_admitted_matter": (
                runtime.settings.human_triage_management_enabled
            ),
            "provider_effects": 0,
        }
        base["connector_catalog"] = _connector_catalog()
        interactions_capability = runtime.capabilities.get("projection:interactions")
        interaction_details = (
            interactions_capability.details
            if interactions_capability is not None
            else {}
        )
        messaging_failures = [
            record
            for key, record in runtime.capabilities.items()
            if key.startswith("adapter:messaging-")
            and record.state in {"degraded", "blocked"}
        ]
        comms_degraded_reason = (
            "New provider messages may be missing because a messaging adapter is degraded"
            if messaging_failures
            else (
                interactions_capability.reason
                if interactions_capability is not None
                and interactions_capability.state in {"degraded", "blocked"}
                else None
            )
        )
        base["processing_health"] = {
            "state": "degraded" if comms_degraded_reason else "healthy",
            "reason": comms_degraded_reason,
            "adapter_states": [record.public() for record in messaging_failures],
            "projection": (
                interactions_capability.public()
                if interactions_capability is not None
                else None
            ),
        }
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=_latest_time(rows, "occurred_at"),
            stale_after_seconds=172800,
            row_count=len(rows),
            configured=bool(base["cockpit"]["accounts"]),
            degraded_reason=comms_degraded_reason,
            checkpoint_event_id=interaction_details.get("checkpoint_event_id"),
            checkpoint_required=bool(base["cockpit"]["accounts"]),
            lag_count=interaction_details.get("lag_count"),
        )
    elif surface == "reminders":
        lists_conn = runtime.projections.connection("lists")
        tasks = work_history_for_member(
            runtime.projections.connection("tasks"),
            session,
            member_party_id=member,
        )
        reminder_items = items_for_member(lists_conn, session, member_id=member)
        reminder_lists = lists_for_member(lists_conn, session, member_id=member)
        accounts = reminder_accounts(
            runtime,
            member,
            projected_items=reminder_items,
            projected_lists=reminder_lists,
        )
        cockpit_accounts = [
            {
                "provider": "administrateme",
                "provider_account_id": "administrateme",
                "label": "AdministrateMe tasks",
                "state": "local",
                "native": [
                    "typed household work",
                    "due dates and times",
                    "energy and effort",
                    "work type and domain",
                    "blocking, deferring, and completion",
                    "event provenance",
                ],
                "limitations": [
                    "Local tasks are not provider-synced unless a separately governed link is created."
                ],
                "operations": [],
            },
            *accounts,
        ]
        base["cockpit"] = compose_reminders_cockpit(
            member_id=member,
            timezone_name=runtime.settings.instance.timezone,
            items=reminder_items,
            lists=reminder_lists,
            accounts=cockpit_accounts,
            cues=reminder_cues_for_member(lists_conn, session, member_id=member),
            signals=context_signals_for_member(lists_conn, session, member_id=member),
            now=now,
            tasks=tasks,
        )
        recurrence_conn = runtime.projections.connection("recurrences")
        rhythms = [
            {
                **item,
                "id": item["recurrence_id"],
                "object_kind": "recurrence",
                "title": str(item.get("kind") or "recurrence")
                .replace("_", " ")
                .title(),
            }
            for item in for_member(
                recurrence_conn,
                session,
                member_party_id=member,
            )
        ]
        rhythms.extend(
            {
                **item,
                "id": item["routine_id"],
                "object_kind": "routine",
            }
            for item in routine_definitions_for_member(
                recurrence_conn,
                session,
                member_party_id=member,
            )
        )
        base["cockpit"]["rhythms"] = rhythms
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=_latest_time(
                [*reminder_items, *tasks],
                "updated_at",
                "completed_at",
                "due_at",
                "created_at",
            ),
            stale_after_seconds=604800,
            row_count=len(reminder_items) + len(tasks),
            configured=bool(accounts) or bool(tasks),
        )
    elif surface == "people":
        parties_conn = runtime.projections.connection("parties")
        interactions_conn = runtime.projections.connection("interactions")
        people = list_visible_people(parties_conn, session)
        interactions = recent_for_member(
            interactions_conn, session, member_id=member, limit=1000
        )
        participant_map = participants_for(
            interactions_conn,
            session,
            interaction_ids=[str(row["interaction_id"]) for row in interactions],
        )
        people_by_id = {str(row["party_id"]): row for row in people}
        for interaction in interactions:
            occurred_at = interaction.get("occurred_at")
            for participant in participant_map.get(
                str(interaction["interaction_id"]), []
            ):
                person = people_by_id.get(str(participant["party_id"]))
                if person is None:
                    continue
                if not person.get("last_contact_at") or str(occurred_at or "") > str(
                    person["last_contact_at"]
                ):
                    person["last_contact_at"] = occurred_at
        unconfirmed_count = int(
            parties_conn.execute(
                "SELECT COUNT(*) FROM people_endpoints WHERE tenant_id=? AND member_id=? "
                "AND classification='unresolved'",
                (session.tenant_id, member),
            ).fetchone()[0]
        )
        people_sources = people_source_summary(parties_conn, session)
        base["cockpit"] = compose_people_cockpit(
            member_id=member,
            people=people,
            sources=people_sources,
            configured_sources=people_accounts(runtime, member),
            unconfirmed_endpoint_count=unconfirmed_count,
            now=now,
        )
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=_latest_time(people_sources, "latest_at"),
            stale_after_seconds=604800,
            row_count=len(people),
            configured=bool(base["cockpit"]["sources"]),
        )
    elif surface == "notes":
        knowledge_conn = runtime.projections.connection("member_knowledge")
        notes = list_visible(knowledge_conn, session)
        triage_items = pending_triage_for_sources(
            runtime.projections.connection("triage"),
            session,
            member_id=member,
            source_event_ids=[str(note.get("last_event_id") or "") for note in notes],
        )
        triage_by_source: dict[str, list[dict[str, Any]]] = {}
        for item in triage_items:
            triage_by_source.setdefault(
                str(item.get("source_event_id") or ""), []
            ).append(item)
        for note in notes:
            note_candidates = triage_by_source.get(
                str(note.get("last_event_id") or ""), []
            )
            note["triage_count"] = len(note_candidates)
            note["triage_href"] = (
                surface_link(
                    "triage",
                    object_kind="item",
                    object_id=str(note_candidates[0]["proposal_id"]),
                    params={
                        "matter": str(
                            note_candidates[0].get("context_id")
                            or note_candidates[0]["proposal_id"]
                        ),
                        "view": "all_candidates",
                    },
                    evidence_event_ids=[
                        str(note_candidates[0].get("source_event_id") or "")
                    ],
                )["href"]
                if note_candidates
                else None
            )
            note["grants"] = (
                grants_for_note(
                    knowledge_conn, session, knowledge_id=str(note["knowledge_id"])
                )
                if note.get("access_mode") == "owner"
                else []
            )
        source_rows: dict[tuple[str, str], dict[str, Any]] = {}
        for note in notes:
            key = (
                str(note.get("source_kind") or "unknown"),
                str(
                    note.get("account_external_id")
                    or note.get("source_account_id")
                    or ""
                ),
            )
            source = source_rows.setdefault(
                key,
                {
                    "source_kind": key[0],
                    "account_external_id": key[1],
                    "account_name": note.get("account_name"),
                    "count": 0,
                    "latest_at": None,
                    "state": "ready",
                    "warnings": [],
                },
            )
            source["count"] += 1
            captured = note.get("provider_modified_at") or note.get("captured_at")
            if captured and (
                source["latest_at"] is None or str(captured) > source["latest_at"]
            ):
                source["latest_at"] = str(captured)
            raw_warnings = note.get("capability_warnings_json") or "[]"
            try:
                warnings = (
                    json.loads(raw_warnings)
                    if isinstance(raw_warnings, str)
                    else raw_warnings
                )
            except json.JSONDecodeError:
                warnings = ["provider_capability_unknown"]
            if warnings:
                source["state"] = "limited"
                source["warnings"] = sorted(
                    set([*source["warnings"], *[str(value) for value in warnings]])
                )
        base["notes"] = notes
        base["meeting_artifacts"] = list_recent(
            runtime.projections.connection("artifacts"), session, limit=30
        )
        base["sources"] = sorted(
            source_rows.values(),
            key=lambda row: (str(row["source_kind"]), str(row["account_external_id"])),
        )
        parties = all_parties(runtime.projections.connection("parties"), session)
        base["sharing_members"] = [
            {
                "member_id": party["party_id"],
                "label": party.get("display_name") or "Household member",
                "role": runtime.settings.member_roles.get(party["party_id"]),
            }
            for party in parties
            if party["party_id"] != session.auth_member_id
            and runtime.settings.member_roles.get(party["party_id"])
            not in {None, "device", "child"}
        ]
        base["counts"] = {
            "notes": len(notes),
            "shared_with_me": sum(
                1 for note in notes if note.get("access_mode") == "shared_reference"
            ),
            "meeting_notes": sum(
                1 for note in notes if note.get("source_type") == "meeting_note"
            ),
            "triage": sum(int(note.get("triage_count") or 0) for note in notes),
        }
        base["contract"] = {
            "source_apps_authoritative": True,
            "provider_sharing_is_not_authorization": True,
            "triage_is_only_promotion_gate": True,
            "model_decides_scope_or_route": False,
            "privileged_model_processing": False,
        }
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=_latest_time(notes, "provider_modified_at", "captured_at"),
            stale_after_seconds=604800,
            row_count=len(notes),
            configured=bool(source_rows),
        )
    elif surface == "finance":
        if session.auth_role == "child":
            raise HTTPException(404, "surface not found")
        from adminme.finance.access_control import books_enabled, bound_book

        enabled = books_enabled(runtime, session) and session.service_context is None
        cockpit = finance_snapshot(
            runtime.projections.connection("money"),
            session,
            as_of=now,
            accepted_books_enabled=enabled,
            accepted_book_id=bound_book(runtime, session) if enabled else None,
        )
        base["cockpit"] = cockpit
        base["contract"] = {
            "workbook_planning_inputs_authoritative": True,
            "provider_observations_protected": True,
            "provider_writeback": False,
            "model_decides_financial_action": False,
            "external_financial_writes": False,
        }
        accepted_books = cockpit["accepted_books"]
        has_accepted_books = bool(accepted_books.get("publication_id"))
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=(
                _latest_time(
                    [accepted_books["source_clock_summary"]], "oldest_observed_at"
                )
                if has_accepted_books
                else _latest_time([cockpit["data_health"]], "latest_source_at")
            ),
            stale_after_seconds=172800,
            row_count=(
                sum(len(report["metrics"]) for report in accepted_books["reports"])
                if has_accepted_books
                else cockpit["data_health"]["transaction_count"]
            ),
            degraded_reason=(
                "Accepted historical snapshot; current source freshness is unverified."
                if has_accepted_books
                else None
            ),
            configured=bool(
                has_accepted_books
                or cockpit["data_health"]["transaction_count"]
                or cockpit["data_health"]["account_count"]
                or cockpit["workbook"]
            ),
        )
    elif surface == "calendar":
        calendar_conn = runtime.projections.connection("calendars")
        zone = ZoneInfo(runtime.settings.instance.timezone)
        local_date = calendar_anchor or now.astimezone(zone).date()
        if calendar_view == "week":
            local_date -= timedelta(days=local_date.weekday())
            range_days = 7
        elif calendar_view == "month":
            local_date = local_date.replace(day=1)
            local_date -= timedelta(days=local_date.weekday())
            range_days = 42
        elif calendar_view in {"agenda", "find"}:
            range_days = 14
        else:
            range_days = 1 if calendar_view == "day" else 7
        range_start = datetime.combine(local_date, time.min, tzinfo=zone)
        range_end = range_start + timedelta(days=range_days)
        rows = events_in_range(
            calendar_conn,
            session,
            member_party_id=member,
            range_start_iso=range_start.astimezone(timezone.utc).isoformat(),
            range_end_iso=range_end.astimezone(timezone.utc).isoformat(),
        )
        base["events"] = rows
        base["calendar_view"] = calendar_view
        base["calendar_anchor"] = (
            calendar_anchor or now.astimezone(zone).date()
        ).isoformat()
        base["calendar_today"] = now.astimezone(zone).date().isoformat()
        base["cockpit"] = compose_calendar_cockpit(
            write_permissions=apple_calendar_destinations(runtime, session),
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
        base["freshness"] = surface_freshness(
            generated_at=now,
            latest_source_at=_latest_time(rows, "provider_updated_at", "updated_at"),
            stale_after_seconds=172800,
            row_count=len(rows),
            configured=bool(base["cockpit"]["sources"]),
        )
    elif surface == "scoreboard":
        tasks_conn = runtime.projections.connection("tasks")
        tasks = actionable_for_member(
            tasks_conn,
            session,
            member_party_id=member,
            as_of_iso=now.isoformat(),
        )
        history = work_history_for_member(
            tasks_conn,
            session,
            member_party_id=member,
            limit=100,
        )
        outcomes = []
        for task in history:
            if task.get("status") not in {"done", "cancelled", "dismissed"}:
                continue
            outcome = dict(task)
            outcome["reason"] = task.get("lifecycle_reason")
            outcome["outcome_kind"] = (
                "completed" if task.get("status") == "done" else "abandoned"
            )
            outcomes.append(outcome)
        commitment_history = commitment_history_for_member(
            runtime.projections.connection("commitments"),
            session,
            member_party_id=member,
            limit=100,
        )
        for commitment in commitment_history:
            if commitment.get("status") not in {
                "done",
                "cancelled",
                "delegated",
                "expired",
            }:
                continue
            outcome = dict(commitment)
            outcome["id"] = commitment["commitment_id"]
            outcome["title"] = commitment["description"]
            outcome["object_kind"] = "commitment"
            outcome["reason"] = commitment.get("lifecycle_reason")
            outcome["outcome_kind"] = {
                "done": "completed",
                "cancelled": "cancelled",
                "delegated": "delegated",
                "expired": "expired",
            }[str(commitment["status"])]
            outcomes.append(outcome)
        outcomes.sort(
            key=lambda item: str(
                item.get("lifecycle_at") or item.get("completed_at") or ""
            ),
            reverse=True,
        )
        recurrence_conn = runtime.projections.connection("recurrences")
        rhythms = [
            {
                **item,
                "id": item["recurrence_id"],
                "object_kind": "recurrence",
                "title": str(item.get("kind") or "recurrence")
                .replace("_", " ")
                .title(),
            }
            for item in for_member(
                recurrence_conn,
                session,
                member_party_id=member,
            )
        ]
        rhythms.extend(
            {
                **item,
                "id": item["routine_id"],
                "object_kind": "routine",
            }
            for item in routine_definitions_for_member(
                recurrence_conn,
                session,
                member_party_id=member,
            )
        )
        rhythm_outcomes = completion_history_for_member(
            recurrence_conn,
            session,
            member_party_id=member,
            limit=50,
        )
        next_steps = []
        for task in tasks[:3]:
            task_id = str(task.get("task_id") or task.get("id") or "")
            next_steps.append(
                {
                    **task,
                    "surface_link": surface_link(
                        "reminders",
                        object_kind="item",
                        object_id=f"task:{task_id}",
                        evidence_event_ids=[str(task.get("last_event_id") or "")],
                    ),
                }
            )
        for outcome in outcomes:
            object_id = str(outcome.get("id") or outcome.get("task_id") or "")
            object_kind = str(outcome.get("object_kind") or "task")
            if object_kind == "task":
                outcome["surface_link"] = surface_link(
                    "reminders",
                    object_kind="item",
                    object_id=f"task:{object_id}",
                    params={"view": "completed"},
                    evidence_event_ids=[str(outcome.get("last_event_id") or "")],
                )
        base["scoreboard"] = {
            "open_count": len(tasks),
            "next_steps": next_steps,
            "completed_count": sum(
                1 for task in outcomes if task["outcome_kind"] == "completed"
            ),
            "abandoned_count": sum(
                1 for task in outcomes if task["outcome_kind"] == "abandoned"
            ),
            "closed_count": sum(
                1
                for item in outcomes
                if item["outcome_kind"] in {"abandoned", "cancelled", "expired"}
            ),
            "redirected_count": sum(
                1 for item in outcomes if item["outcome_kind"] == "delegated"
            ),
            "recent_outcomes": outcomes[:20],
            "rhythms": rhythms,
            "rhythm_outcomes": rhythm_outcomes,
            "reward_language": "warm" if session.auth_role == "child" else "calm",
        }
    elif surface == "settings":
        if session.auth_role != "principal":
            base["state"] = "limited"
        base["control"] = compose_settings_control(runtime, session)
        base["system"] = {
            "capabilities": list(
                public_capability_summary(
                    {
                        key: record.public()
                        for key, record in sorted(runtime.capabilities.items())
                    }
                ).values()
            ),
            "workbooks": _workbook_health(runtime),
            "observation": base["observation"],
            "chat": {
                "name": "Member chat",
                **runtime.member_chat_policy(session.auth_member_id, session.auth_role),
            },
            "runtime": {
                "state": "ready" if runtime.started else "starting",
                "openclaw": runtime.openclaw_state,
                "projection_count": len(runtime.projections.projection_names()),
                "architecture": "one-python-runtime-one-openclaw-gateway",
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
                "member_agent_id": base["control"]["identity"]["member_agent_id"],
                "isolated_workspace": True,
                "peer_session_scope": "per_account_channel_peer",
                "compatibility": "exact_pin",
                "release_state": "candidate_not_deployed",
                "backup_state": "deployment_gate_not_run",
            },
        }
        base["registry"] = registry_items(
            runtime.projections.connection("places_assets_accounts"), session
        )
        # Compatibility fields remain for pinned clients while the canonical
        # Settings contract is the structured control/system pair above.
        base["capabilities"] = base["system"]["capabilities"]
        base["workbooks"] = base["system"]["workbooks"]
        base["observation_mode"] = runtime._observation_active
        base["chat"] = base["system"]["chat"]
    return base


def crm_detail(runtime: Any, session: Session, party_id: str) -> dict[str, Any]:
    authorize_surface(session, "people")
    party = get_visible_person(
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
        "identifiers": identifiers_for_person(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "memberships": memberships_of(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "relationships": relationships_of(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "contact_records": contact_records_for_person(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "relationship_facts": relationship_facts_for_person(
            runtime.projections.connection("parties"), session, party_id=party_id
        ),
        "member_profile": member_relationship_profile(
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


def crm_search(runtime: Any, session: Session, query: str) -> dict[str, Any]:
    """Search only the caller-authorized People index."""

    authorize_surface(session, "people")
    return {
        "query": query,
        "results": search_visible_people(
            runtime.projections.connection("parties"),
            session,
            query=query,
        ),
        "contract": "authorized-people-search-v1",
    }


def evaluate_query(
    runtime: Any, session: Session, name: str, params: dict[str, Any]
) -> Any:
    """Evaluate one saved-view query through an existing scope-filtered seam."""

    del params  # Gate 7 declarations intentionally accept no cross-scope identifiers.
    if name == "what_now":
        return core._what_now(runtime, session)
    if name == "tasks.open":
        return actionable_for_member(
            runtime.projections.connection("tasks"),
            session,
            member_party_id=session.view_member_id,
            as_of_iso=datetime.now(timezone.utc).isoformat(),
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
    "crm_search",
    "evaluate_query",
    "experience_mode",
    "navigation",
]
