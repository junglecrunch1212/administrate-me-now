"""Deterministic member Reminders cockpit composition.

The cockpit is a read-time lens over the Lists projection. It never promotes a
provider list item into a Task, never treats a due date as a promise, and never
asks a model to rank or activate work. Provider-native time/location alarms and
member-authored context cues are explained separately so the member can see why
an item is visible and which system remains authoritative.
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from adminme.surface_contracts import provider_action_state, surface_link


APPLE_OPERATIONS = frozenset(
    {
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_list",
        "update_list",
        "delete_list",
    }
)
GOOGLE_OPERATIONS = frozenset(
    {
        "create_task",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
        "clear_completed_tasks",
        "create_list",
        "update_list",
        "delete_list",
    }
)

PROVIDER_CONTRACTS: dict[str, dict[str, Any]] = {
    "apple_reminders": {
        "label": "Apple Reminders",
        "native": [
            "lists",
            "title",
            "notes",
            "url",
            "priority",
            "completion",
            "floating_or_zoned_dates",
            "multiple_alarms",
            "recurrence",
            "location_geofences",
        ],
        "limitations": [
            "EventKit does not expose tags, sections, subtasks, templates, assignment, or smart-list rules."
        ],
        "operations": sorted(APPLE_OPERATIONS),
    },
    "google_tasks": {
        "label": "Google Tasks",
        "native": [
            "task_lists",
            "title",
            "notes",
            "scheduled_date",
            "completion",
            "nesting",
            "ordering",
            "cross_list_move",
            "source_links",
            "assignment_provenance",
        ],
        "limitations": [
            "The API stores only a scheduled date, not a due time.",
            "The API exposes no priority, alarm, recurrence, or geofence fields.",
            "Assigned tasks can have provider-enforced write restrictions.",
        ],
        "operations": sorted(GOOGLE_OPERATIONS),
    },
    "apple_notes_checklist": {
        "label": "Apple Notes checklist",
        "native": ["checklist_state"],
        "limitations": [
            "Supported automation is limited to adding items and toggling completion."
        ],
        "operations": ["add_item", "toggle_completion"],
    },
}

_BUCKET_RANK = {"now": 0, "scheduled": 1, "places": 2, "anytime": 3, "completed": 4}

CONTEXT_SIGNAL_CATALOG = [
    {
        "kind": "location",
        "label": "Place",
        "sources": ["Apple EventKit geofence", "member bridge location"],
        "contract": "Native geofences stay provider-owned; bridge location is an expiring fact.",
    },
    {
        "kind": "presence",
        "label": "Presence",
        "sources": ["Home Assistant", "member bridge"],
        "contract": "A typed state comparison may activate a cue; no model infers presence.",
    },
    {
        "kind": "device_state",
        "label": "Device",
        "sources": ["Home Assistant", "device bridge"],
        "contract": "Battery, charging, appliance, and other explicit states require a fresh fact.",
    },
    {
        "kind": "network_presence",
        "label": "Network",
        "sources": ["Home Assistant", "trusted network adapter"],
        "contract": "Home/work network presence is an explicit scoped signal, never a guessed location.",
    },
    {
        "kind": "calendar_transition",
        "label": "Calendar",
        "sources": ["authorized calendar projection"],
        "contract": "Before/after-event cues use typed calendar state and do not become calendar events.",
    },
    {
        "kind": "weather",
        "label": "Weather",
        "sources": ["configured weather adapter"],
        "contract": "Threshold rules use observed values with expiry, source, and unit.",
    },
    {
        "kind": "energy",
        "label": "Energy / capacity",
        "sources": ["member profile", "explicit member check-in"],
        "contract": "Member-authored state can filter a lens but never changes provider truth.",
    },
]


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback


def _instant(value: Any, tz: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def _cue_matches(
    cue: dict[str, Any], signal: dict[str, Any] | None, now: datetime
) -> bool:
    if signal is None or not cue.get("enabled"):
        return False
    expires = _instant(signal.get("expires_at"), ZoneInfo("UTC"))
    if expires is not None and expires < now:
        return False
    operator = str(cue.get("operator") or "")
    state = str(signal.get("state") or "")
    if operator == "present":
        return True
    if operator == "equals":
        return state == str(cue.get("expected_state") or "")
    if operator == "not_equals":
        return state != str(cue.get("expected_state") or "")
    if operator == "entered":
        return state == "entered"
    if operator == "left":
        return state == "left"
    value = signal.get("numeric_value")
    expected = cue.get("expected_number")
    if not isinstance(value, (int, float)) or not isinstance(expected, (int, float)):
        return False
    return (
        value > expected
        if operator == "above"
        else value < expected
        if operator == "below"
        else False
    )


def _provider_account(account: dict[str, Any]) -> tuple[str, str]:
    return (
        str(account.get("provider") or ""),
        str(account.get("provider_account_id") or ""),
    )


def _possible_copy_key(item: dict[str, Any]) -> tuple[str, str] | None:
    """Return a conservative provider/title key; never merge provider rows."""

    if item.get("completed"):
        return None
    title = re.sub(r"\s+", " ", str(item.get("title") or "").strip()).casefold()
    if len(title) < 4:
        return None
    return (str(item.get("provider") or ""), title)


def _mark_possible_copies(items: list[dict[str, Any]]) -> None:
    candidate_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        key = _possible_copy_key(item)
        if key is not None:
            candidate_groups.setdefault(key, []).append(item)

    for base_key, candidate_rows in candidate_groups.items():
        known_accounts = sorted(
            {
                str(row.get("provider_account_id") or "")
                for row in candidate_rows
                if str(row.get("provider_account_id") or "")
            }
        )
        if len(known_accounts) <= 1:
            discriminator = known_accounts[0] if known_accounts else "unknown-account"
            partitions = [(discriminator, candidate_rows)]
        else:
            # A missing legacy account id may only join a group when exactly one
            # concrete account is possible.  Multiple concrete accounts remain
            # isolated, and unresolved legacy rows form their own review group.
            partitions = [
                (
                    account_id,
                    [
                        row
                        for row in candidate_rows
                        if str(row.get("provider_account_id") or "") == account_id
                    ],
                )
                for account_id in known_accounts
            ]
            unresolved = [
                row
                for row in candidate_rows
                if not str(row.get("provider_account_id") or "")
            ]
            if unresolved:
                partitions.append(("unknown-account", unresolved))

        for discriminator, rows in partitions:
            containers = {
                (
                    str(row.get("list_external_id") or ""),
                    str(row.get("list_name") or ""),
                )
                for row in rows
            }
            if len(rows) < 2 or len(containers) < 2:
                continue
            digest_key = (*base_key, discriminator)
            digest = hashlib.sha256("\x1f".join(digest_key).encode()).hexdigest()[:12]
            group_id = f"possible-copy:{digest}"
            identities = sorted(str(row["id"]) for row in rows)
            for row in rows:
                row["possible_copy"] = {
                    "contract": "possible-reminder-copy-v1",
                    "group_id": group_id,
                    "count": len(rows),
                    "reason": (
                        "Same normalized title appears in more than one provider list"
                    ),
                    "member_review_required": True,
                    "provider_items_preserved": identities,
                }


def compose_effective_reminders(
    *,
    timezone_name: str,
    items: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Enrich provider rows once for Reminders, Today, and What Now."""

    tz = ZoneInfo(timezone_name)
    now = now.astimezone(timezone.utc)
    local_today = now.astimezone(tz).date()
    signal_by_key = {str(row["signal_key"]): row for row in signals}
    cues_by_item: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for cue in cues:
        cues_by_item.setdefault(
            (str(cue["list_external_id"]), str(cue["item_external_id"])), []
        ).append(cue)

    account_by_key = {_provider_account(account): account for account in accounts}
    enriched: list[dict[str, Any]] = []
    for raw in items:
        row = dict(raw)
        alarms = _json(row.get("alarms_json"), [])
        recurrence = _json(row.get("recurrence_json"), [])
        source_links = _json(row.get("source_links_json"), [])
        provider_data = _json(row.get("provider_data_json"), {})
        item_cues = cues_by_item.get(
            (str(row["list_external_id"]), str(row["item_external_id"])), []
        )
        cue_results = [
            {
                **cue,
                "matched": _cue_matches(
                    cue, signal_by_key.get(str(cue["signal_key"])), now
                ),
                "signal": signal_by_key.get(str(cue["signal_key"])),
            }
            for cue in item_cues
        ]
        due = _instant(row.get("due_at"), tz)
        due_local = due.astimezone(tz) if due is not None else None
        location_alarms = [alarm for alarm in alarms if alarm.get("kind") == "location"]
        matched = [cue for cue in cue_results if cue["matched"]]
        completed = bool(row.get("completed"))
        why: list[str] = []
        if completed:
            bucket = "completed"
            why.append("Completed in the source provider")
        elif matched:
            bucket = "now"
            why.extend(
                f"{cue['kind'].replace('_', ' ').title()} cue matched"
                for cue in matched
            )
        elif due_local is not None and due_local.date() <= local_today:
            bucket = "now"
            why.append(
                "Scheduled for today"
                if due_local.date() == local_today
                else "Scheduled date has passed"
            )
        elif int(row.get("priority") or 0) == 1 or bool(row.get("flagged")):
            bucket = "now"
            why.append("Marked important in the source provider")
        elif location_alarms:
            bucket = "places"
            why.append("Provider-native place reminder is armed")
        elif due is not None or recurrence or alarms:
            bucket = "scheduled"
            why.append("Has a time or repeating cue")
        else:
            bucket = "anytime"
            why.append("Available without an activation cue")

        account = account_by_key.get(
            (str(row.get("provider") or ""), str(row.get("provider_account_id") or ""))
        )
        provider = str(row.get("provider") or "")
        provider_account_id = str(row.get("provider_account_id") or "")
        contract = PROVIDER_CONTRACTS.get(provider, {})
        operations = set(account.get("operations") or []) if account else set()
        can_complete = (
            (provider == "apple_reminders" and "complete_reminder" in operations)
            or (provider == "google_tasks" and "complete_task" in operations)
        ) and not row.get("read_only_reason")
        can_edit = (
            (provider == "apple_reminders" and "update_reminder" in operations)
            or (provider == "google_tasks" and "update_task" in operations)
        ) and not row.get("read_only_reason")
        item_id = (
            f"{provider}:{provider_account_id}:"
            f"{row['list_external_id']}:{row['item_external_id']}"
        )
        evidence_ids = [str(row.get("last_event_id") or "")]
        enriched.append(
            {
                **row,
                "id": item_id,
                "bucket": bucket,
                "why": why,
                "alarms": alarms,
                "recurrence_rules": recurrence,
                "source_links": source_links,
                "provider_data": provider_data,
                "cues": cue_results,
                "account_label": account.get("label")
                if account
                else contract.get("label", provider.replace("_", " ").title()),
                "can_complete": can_complete,
                "can_edit": can_edit,
                "due_sort": due.isoformat() if due else None,
                "surface_link": surface_link(
                    "reminders",
                    object_kind="item",
                    object_id=item_id,
                    evidence_event_ids=evidence_ids,
                ),
                "complete_action": provider_action_state(
                    provider=provider,
                    provider_account_id=provider_account_id,
                    action="complete",
                    available=can_complete,
                    reason=(
                        "Provider capability is available; governance still applies"
                        if can_complete
                        else str(
                            row.get("read_only_reason")
                            or "Provider capability is unavailable"
                        )
                    ),
                ),
                "edit_action": provider_action_state(
                    provider=provider,
                    provider_account_id=provider_account_id,
                    action="edit",
                    available=can_edit,
                    reason=(
                        "Provider capability is available; governance still applies"
                        if can_edit
                        else str(
                            row.get("read_only_reason")
                            or "Provider capability is unavailable"
                        )
                    ),
                ),
            }
        )

    priority_rank = {1: 0, 5: 1, 9: 2, 0: 3}
    enriched.sort(
        key=lambda item: (
            _BUCKET_RANK[item["bucket"]],
            item["due_sort"] is None,
            item["due_sort"] or "",
            priority_rank.get(int(item.get("priority") or 0), 3),
            str(item.get("provider_position") or ""),
            str(item.get("title") or "").casefold(),
            item["id"],
        )
    )
    _mark_possible_copies(enriched)
    return enriched


def compose_reminders_cockpit(
    *,
    member_id: str,
    timezone_name: str,
    items: list[dict[str, Any]],
    lists: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Return one stable member-isolated reminders read contract."""

    del member_id  # all rows arrive through member/scope-filtered queries.
    now = now.astimezone(timezone.utc)
    enriched = compose_effective_reminders(
        timezone_name=timezone_name,
        items=items,
        accounts=accounts,
        cues=cues,
        signals=signals,
        now=now,
    )
    counts = {
        bucket: sum(1 for item in enriched if item["bucket"] == bucket)
        for bucket in _BUCKET_RANK
    }
    return {
        "version": "deterministic-reminders-cockpit-v1",
        "generated_at": now.isoformat(),
        "counts": counts,
        "items": enriched,
        "lists": lists,
        "accounts": accounts,
        "context_signal_catalog": CONTEXT_SIGNAL_CATALOG,
        "active_signal_count": len(signals),
        "contract": {
            "provider_is_source_of_truth": True,
            "list_items_are_not_tasks": True,
            "model_ranking": False,
            "cue_evaluation": "typed operator against latest unexpired scoped fact",
        },
    }


__all__ = [
    "APPLE_OPERATIONS",
    "CONTEXT_SIGNAL_CATALOG",
    "compose_effective_reminders",
    "GOOGLE_OPERATIONS",
    "PROVIDER_CONTRACTS",
    "compose_reminders_cockpit",
]
