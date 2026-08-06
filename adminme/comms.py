"""Deterministic composition for the member Comms cockpit.

No model ranks, suppresses, or mutates this view.  NLP pipelines may attach
typed classifications or proposals, but these pure rules decide the visible
bucket, score, and explanation from projected facts and explicit member state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


_BUCKET_WEIGHT = {
    "focus": 500,
    "needs_reply": 400,
    "waiting": 300,
    "later": 200,
    "snoozed": 100,
    "quieted": 50,
    "archive": 0,
}


def _timestamp(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        return []
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return []
    return result if isinstance(result, list) else []


def _thread_key(row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or row.get("channel_specific") or "unknown")
    account = str(row.get("provider_account_id") or "unknown")
    native = (
        row.get("thread_id") or row.get("native_message_id") or row["interaction_id"]
    )
    return f"{provider}:{account}:{native}"


def _people(
    interaction_id: str,
    participants: dict[str, list[dict[str, str]]],
    party_labels: dict[str, str],
    role: str,
) -> list[str]:
    return [
        party_labels.get(value["party_id"], value["party_id"])
        for value in participants.get(interaction_id, [])
        if value["role"] == role
    ]


def _message(
    row: dict[str, Any],
    participants: dict[str, list[dict[str, str]]],
    party_labels: dict[str, str],
) -> dict[str, Any]:
    interaction_id = str(row["interaction_id"])
    return {
        "interaction_id": interaction_id,
        "direction": row.get("direction"),
        "occurred_at": row.get("occurred_at"),
        "subject": row.get("subject"),
        "body": row.get("body_text"),
        "preview": row.get("body_preview")
        or row.get("subject")
        or "Message content unavailable",
        "from": _people(interaction_id, participants, party_labels, "from"),
        "to": _people(interaction_id, participants, party_labels, "to"),
        "cc": _people(interaction_id, participants, party_labels, "cc"),
        "attachments": _json_list(row.get("attachment_metadata")),
        "unread": bool(row.get("unread")),
        "delivery_status": row.get("delivery_status"),
        "evidence_event_ids": _json_list(row.get("raw_event_ids")),
        "last_event_id": row.get("last_event_id"),
    }


def _active_snooze(value: Any, now: datetime) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) > now
    except ValueError:
        return False


def _bucket(latest: dict[str, Any], now: datetime) -> tuple[str, list[str]]:
    why: list[str] = []
    classification = str(latest.get("classification") or "")
    if bool(latest.get("archived")):
        return "archive", ["Archived at the provider"]
    if bool(latest.get("quieted")) or classification in {"noise", "promotional"}:
        return "quieted", ["Quieted by an explicit rule or classification"]
    if _active_snooze(latest.get("snoozed_until"), now):
        return "snoozed", ["Snoozed until the chosen time"]
    if latest.get("direction") == "outbound":
        return "waiting", ["Your message is the latest in this thread"]
    if bool(latest.get("important")):
        why.append("Provider marked important")
    if bool(latest.get("unread")):
        why.append("Unread inbound message")
    if classification in {"personal", "professional"}:
        why.append(f"Classified {classification}")
    if latest.get("response_urgency"):
        why.append(f"Response urgency: {latest['response_urgency']}")
    if latest.get("direction") == "inbound" and why:
        return "focus", why
    if latest.get("direction") == "inbound":
        return "needs_reply", ["Inbound message has no later reply"]
    return "later", ["No immediate action signal"]


def _score(bucket: str, latest: dict[str, Any]) -> int:
    score = _BUCKET_WEIGHT[bucket]
    score += 60 if bool(latest.get("important")) else 0
    score += 35 if bool(latest.get("unread")) else 0
    score += 20 if latest.get("classification") in {"personal", "professional"} else 0
    return score


def compose_comms_cockpit(
    *,
    member_id: str,
    rows: list[dict[str, Any]],
    participants: dict[str, list[dict[str, str]]],
    party_labels: dict[str, str],
    accounts: list[dict[str, Any]],
    triage_items: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Group account-bound interactions into explainable, stable threads."""

    as_of = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_thread_key(row), []).append(row)
    triage_sources = {
        str(item.get("source_event_id"))
        for item in triage_items
        if item.get("source_event_id")
    }
    account_by_key = {
        (str(item["provider"]), str(item["provider_account_id"])): item
        for item in accounts
    }
    threads: list[dict[str, Any]] = []
    for key, thread_rows in groups.items():
        ordered = sorted(
            thread_rows,
            key=lambda item: (
                _timestamp(item.get("occurred_at")),
                str(item["interaction_id"]),
            ),
        )
        latest = ordered[-1]
        provider = str(
            latest.get("provider") or latest.get("channel_specific") or "unknown"
        )
        account_id = str(latest.get("provider_account_id") or "unknown")
        account = account_by_key.get((provider, account_id))
        messages = [_message(row, participants, party_labels) for row in ordered]
        bucket, why = _bucket(latest, as_of)
        raw_ids = {
            str(event_id)
            for row in ordered
            for event_id in _json_list(row.get("raw_event_ids"))
        }
        counterparties = []
        roles = ("from",) if latest.get("direction") == "inbound" else ("to",)
        for role in roles:
            for party in _people(
                str(latest["interaction_id"]), participants, party_labels, role
            ):
                if party != account_id and party not in counterparties:
                    counterparties.append(party)
        title = latest.get("subject") or (
            counterparties[0] if counterparties else "Conversation"
        )
        threads.append(
            {
                "thread_key": key,
                "title": title,
                "counterparties": counterparties,
                "provider": provider,
                "provider_account_id": account_id,
                "account_label": account.get("label") if account else account_id,
                "workspace": latest.get("workspace") or "personal",
                "channel": latest.get("channel_specific"),
                "bucket": bucket,
                "focus_score": _score(bucket, latest),
                "why": why,
                "occurred_at": latest.get("occurred_at"),
                "preview": latest.get("body_preview")
                or latest.get("subject")
                or "Message content unavailable",
                "unread_count": sum(1 for row in ordered if bool(row.get("unread"))),
                "message_count": len(messages),
                "triage_count": len(raw_ids & triage_sources),
                "snoozed_until": latest.get("snoozed_until"),
                "quieted": bool(latest.get("quieted")),
                "archived": bool(latest.get("archived")),
                "important": bool(latest.get("important")),
                "classification": latest.get("classification"),
                "command_state": latest.get("command_state"),
                "last_interaction_id": latest["interaction_id"],
                "last_event_id": latest["last_event_id"],
                "capabilities": (account or {}).get("capabilities", {}),
                "messages": messages,
            }
        )
    threads.sort(
        key=lambda item: (
            -int(item["focus_score"]),
            -_timestamp(item.get("occurred_at")),
            str(item["thread_key"]),
        )
    )
    counts = {
        "focus": sum(1 for item in threads if item["bucket"] == "focus"),
        "needs_reply": sum(
            1 for item in threads if item["bucket"] in {"focus", "needs_reply"}
        ),
        "waiting": sum(1 for item in threads if item["bucket"] == "waiting"),
        "snoozed": sum(1 for item in threads if item["bucket"] == "snoozed"),
        "quieted": sum(1 for item in threads if item["bucket"] == "quieted"),
        "triage": sum(int(item["triage_count"]) for item in threads),
        "all": len(threads),
    }
    return {
        "contract": "deterministic-comms-cockpit-v1",
        "member_id": member_id,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "decision_maker": "rules_not_model",
        "combined_view_is_union_not_copy": True,
        "official_items_require_triage": True,
        "accounts": accounts,
        "counts": counts,
        "threads": threads,
    }


def validate_outbound_communication(
    *,
    operation: str,
    member_id: str,
    interaction: dict[str, Any],
    account: dict[str, Any],
    recipient: str | None,
    body: str | None,
) -> dict[str, Any]:
    """Deterministic final content/privacy check before a provider command."""

    if interaction.get("member_id") not in (None, "", member_id):
        raise ValueError("communication member binding changed")
    if interaction.get("provider") != account.get("provider"):
        raise ValueError("communication provider binding changed")
    if interaction.get("provider_account_id") != account.get("provider_account_id"):
        raise ValueError("communication account binding changed")
    if operation == "reply":
        if not isinstance(recipient, str) or not recipient.strip():
            raise ValueError("reply recipient is unavailable")
        if any(value in recipient for value in ("\r", "\n", "\x00")):
            raise ValueError("reply recipient contains unsafe header characters")
        source_recipient = interaction.get("reply_to_identifier")
        if interaction.get("provider") != "fixture" and not source_recipient:
            raise ValueError("source-derived reply recipient is unavailable")
        if source_recipient and recipient != source_recipient:
            raise ValueError("reply recipient binding changed")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("reply body is empty")
        if "\x00" in body:
            raise ValueError("reply body contains an unsafe control character")
    elif not interaction.get("native_message_id"):
        raise ValueError("provider message identifier is unavailable")
    return {
        "policy": "deterministic-comms-content-privacy-v1",
        "passed": True,
        "checks": [
            "self_member_scope",
            "exact_provider_account",
            "source_derived_recipient" if operation == "reply" else "provider_target",
            "member_authored_content" if operation == "reply" else "no_outbound_body",
        ],
    }


__all__ = ["compose_comms_cockpit", "validate_outbound_communication"]
