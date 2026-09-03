"""Deterministic composition for the member Comms cockpit.

No model ranks, suppresses, or mutates this view.  NLP pipelines may attach
typed classifications or proposals, but these pure rules decide the visible
bucket, score, and explanation from projected facts and explicit member state.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

from adminme.surface_contracts import attention_explanation, surface_link


_BUCKET_WEIGHT = {
    "alerts": 600,
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


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


def _link_disposition(value: dict[str, Any]) -> dict[str, Any]:
    disposition = value.get("disposition")
    return disposition if isinstance(disposition, dict) else {}


def _clean_message_text(value: Any) -> str | None:
    """Decode transport entities and remove unsafe display controls."""

    if not isinstance(value, str):
        return None
    decoded = html.unescape(value)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", decoded)


def _clean_message_body(value: Any) -> str | None:
    """Return a bounded primary body without quoted-message/signature noise."""

    cleaned = _clean_message_text(value)
    if cleaned is None:
        return None
    retained: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if (
            stripped == "--"
            or re.match(r"^On .{1,240} wrote:$", stripped, re.IGNORECASE)
            or re.match(r"^-{2,}\s*Original Message\s*-{2,}$", stripped, re.IGNORECASE)
            or re.match(r"^From:\s+\S", stripped, re.IGNORECASE)
        ):
            break
        retained.append(line.rstrip())
    primary = "\n".join(retained).strip()
    return primary[:6000] or None


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
        party_labels.get(
            value.get("party_id", ""),
            value.get("party_id") or value.get("participant_id") or "Unknown endpoint",
        )
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
        "subject": _clean_message_text(row.get("subject")),
        "body": _clean_message_body(row.get("body_text")),
        "preview": _clean_message_body(row.get("body_preview"))
        or _clean_message_text(row.get("subject"))
        or "Message content unavailable",
        "from": _people(interaction_id, participants, party_labels, "from"),
        "to": _people(interaction_id, participants, party_labels, "to"),
        "cc": _people(interaction_id, participants, party_labels, "cc"),
        "attachments": _json_list(row.get("attachment_metadata")),
        "unread": bool(row.get("unread")),
        "delivery_status": row.get("delivery_status"),
        "native_message_id": row.get("native_message_id"),
        "native_chat_id": row.get("native_chat_id"),
        "reply_to_native_message_id": row.get("reply_to_native_message_id"),
        "transport": row.get("transport_class"),
        "is_group": bool(row.get("is_group")),
        "message_state": row.get("message_state") or "active",
        "source": {
            "connector_id": row.get("connector_id"),
            "bridge_enrollment_id": row.get("bridge_enrollment_id"),
            "provider_account_id": row.get("provider_account_id"),
            "workspace": row.get("workspace"),
            "protected_source_id": row.get("protected_source_id"),
            "native_revision": row.get("native_revision"),
        },
        "evidence_event_ids": _json_list(row.get("raw_event_ids")),
        "last_event_id": row.get("last_event_id"),
        "triage_status": row.get("assessment_outcome") or "pending",
        "triage_reason_codes": _json_list(row.get("assessment_reason_codes")),
    }


def _active_snooze(value: Any, now: datetime) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) > now
    except ValueError:
        return False


def message_attention_decision(
    latest: dict[str, Any],
    now: datetime,
    *,
    triage_count: int = 0,
    alert_count: int = 0,
    admitted_matters_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Apply Focus v2: unread is presentation state, never urgency by itself."""

    why: list[str] = []
    classification = str(latest.get("classification") or "").casefold()
    features = _json_object(latest.get("semantic_features_json"))
    reply_signal = str(
        features.get("reply_signal") or latest.get("reply_signal") or "unknown"
    ).casefold()
    urgency = str(latest.get("response_urgency") or "").casefold()
    person_to_person = bool(features.get("person_to_person"))
    transactional_due = bool(features.get("transactional_due"))
    security_code = bool(features.get("security_code"))
    occurred_at = _timestamp(latest.get("occurred_at"))
    security_code_age_seconds = (
        max(0, int(now.timestamp() - occurred_at)) if occurred_at else None
    )
    security_code_active = bool(
        security_code
        and security_code_age_seconds is not None
        and security_code_age_seconds <= 15 * 60
    )
    signals = {
        "unread": bool(latest.get("unread")),
        "important": bool(latest.get("important")),
        "reply_signal": reply_signal,
        "response_urgency": urgency or None,
        "person_to_person": person_to_person,
        "transactional_due": transactional_due,
        "security_code": security_code,
        "security_code_active": security_code_active,
        "security_code_age_seconds": security_code_age_seconds,
        "triage_count": triage_count,
        "alert_count": alert_count,
        "admitted_matters_only": admitted_matters_only,
    }

    def decision(
        bucket: str, rule_id: str, reasons: list[str]
    ) -> tuple[str, dict[str, Any]]:
        return bucket, attention_explanation(
            rule_id=rule_id, reasons=reasons, signals=signals
        )

    if bool(latest.get("archived")):
        return decision("archive", "comms.archived", ["Archived at the provider"])
    if bool(latest.get("quieted")) or classification in {"noise", "promotional"}:
        return decision(
            "quieted",
            "comms.quieted",
            ["Quieted by an explicit rule or classification"],
        )
    if _active_snooze(latest.get("snoozed_until"), now):
        return decision("snoozed", "comms.snoozed", ["Snoozed until the chosen time"])
    if alert_count:
        return decision(
            "alerts",
            "comms.alerts-safety-v1",
            [f"{alert_count} admitted safety or time-sensitive alert"],
        )
    if latest.get("direction") == "outbound":
        return decision(
            "waiting",
            "comms.waiting",
            ["Your message is the latest in this thread"],
        )
    if latest.get("direction") == "inbound":
        if admitted_matters_only and not triage_count:
            return decision(
                "later",
                "comms.context-without-admitted-matter-v1",
                ["The conversation remains searchable but has no admitted obligation"],
            )
        if reply_signal == "required":
            why.append("A typed semantic signal says a reply is required")
        if urgency in {"today", "urgent", "high", "critical"}:
            why.append(f"Response urgency: {urgency}")
        if latest.get("suggested_action"):
            why.append("The source record contains an explicit next action")
        if triage_count:
            why.append(
                f"{triage_count} derived item{'s' if triage_count != 1 else ''} await Triage"
            )
        if transactional_due:
            why.append("A typed transactional due signal is present")
        if security_code_active:
            why.append("A typed security-code signal is within its 15-minute window")
        if bool(latest.get("important")) and person_to_person:
            why.append("Provider-important person-to-person message")
        if why:
            return decision("focus", "comms.focus-v2", why)
        quiet_reason = (
            "Unread is visible but does not create urgency without an action signal"
            if bool(latest.get("unread"))
            else "No deterministic action signal"
        )
        return decision("later", "comms.later-v2", [quiet_reason])
    return decision("later", "comms.later-v2", ["No immediate action signal"])


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
    human_centered: bool = False,
) -> dict[str, Any]:
    """Group account-bound interactions into explainable, stable threads."""

    as_of = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_thread_key(row), []).append(row)
    triage_by_source: dict[str, list[dict[str, Any]]] = {}
    for item in triage_items:
        source_event_id = str(item.get("source_event_id") or "")
        if source_event_id:
            triage_by_source.setdefault(source_event_id, []).append(item)
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
        command_row = next(
            (row for row in reversed(ordered) if row.get("last_command_id")), latest
        )
        raw_ids = {
            str(event_id)
            for row in ordered
            for event_id in _json_list(row.get("raw_event_ids"))
        }
        triage_candidates = sorted(
            {
                str(item["proposal_id"]): item
                for source_event_id in raw_ids
                for item in triage_by_source.get(source_event_id, [])
            }.values(),
            key=lambda item: (
                str(item.get("proposed_at") or ""),
                str(item.get("proposal_id") or ""),
            ),
        )
        triage_links = [
            {
                "candidate_id": str(item["proposal_id"]),
                "matter_id": str(item.get("context_id") or item["proposal_id"]),
                "title": item.get("title") or "Review extracted candidate",
                "queue_lane": item.get("queue_lane") or "standard",
                "human_lane": item.get("human_lane") or "low_confidence_review",
                "disposition": item.get("disposition") or {},
                "surface_link": surface_link(
                    "triage",
                    object_kind="item",
                    object_id=str(item["proposal_id"]),
                    params={
                        "matter": str(item.get("context_id") or item["proposal_id"]),
                        "view": "all_candidates",
                    },
                    evidence_event_ids=[str(item.get("source_event_id") or "")],
                ),
            }
            for item in triage_candidates
        ]
        ordinary_links = (
            [
                item
                for item in triage_links
                if bool(_link_disposition(item).get("ordinary_queue"))
            ]
            if human_centered
            else triage_links
        )
        alert_links = (
            [
                item
                for item in triage_links
                if _link_disposition(item).get("disposition") == "alerts"
            ]
            if human_centered
            else []
        )
        triage_count = len(ordinary_links)
        alert_count = len(alert_links)
        bucket, attention = message_attention_decision(
            latest,
            as_of,
            triage_count=triage_count,
            alert_count=alert_count,
            admitted_matters_only=human_centered,
        )
        why = attention["reasons"]
        counterparties = []
        roles = ("from",) if latest.get("direction") == "inbound" else ("to",)
        for role in roles:
            for party in _people(
                str(latest["interaction_id"]), participants, party_labels, role
            ):
                if party != account_id and party not in counterparties:
                    counterparties.append(party)
        title = _clean_message_text(latest.get("subject")) or (
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
                "native_chat_id": latest.get("native_chat_id"),
                "transport": latest.get("transport_class"),
                "is_group": bool(latest.get("is_group")),
                "read_only": not bool(
                    (account or {}).get("capabilities", {}).get("reply")
                ),
                "bucket": bucket,
                "focus_score": _score(bucket, latest),
                "why": why,
                "occurred_at": latest.get("occurred_at"),
                "preview": _clean_message_body(latest.get("body_preview"))
                or _clean_message_text(latest.get("subject"))
                or "Message content unavailable",
                "unread_count": sum(1 for row in ordered if bool(row.get("unread"))),
                "message_count": len(messages),
                "triage_count": triage_count,
                "triage_matter_count": len(
                    {item["matter_id"] for item in triage_links}
                ),
                "triage_candidates": triage_links,
                "triage_status": latest.get("assessment_outcome") or "pending",
                "alert_count": alert_count,
                "snoozed_until": latest.get("snoozed_until"),
                "quieted": bool(latest.get("quieted")),
                "archived": bool(latest.get("archived")),
                "important": bool(latest.get("important")),
                "classification": latest.get("classification"),
                "attention": attention,
                "needs_reply": attention["signals"]["reply_signal"] == "required",
                "command_state": command_row.get("command_state"),
                "last_command_id": command_row.get("last_command_id"),
                "reply_eligibility": {
                    "eligible": bool(
                        (account or {}).get("capabilities", {}).get("reply")
                        and latest.get("native_chat_id")
                        and latest.get("native_message_id")
                        and latest.get("message_state", "active") == "active"
                    ),
                    "reason": (account or {}).get("write_access_remedy"),
                    "destination_locked": True,
                    "account_locked": True,
                    "participant_set_locked": True,
                    "plain_text_only": True,
                    "ask_every_time": True,
                    "external_provider_effect": True,
                    "new_conversation_allowed": False,
                },
                "last_interaction_id": latest["interaction_id"],
                "last_event_id": latest["last_event_id"],
                "capabilities": (account or {}).get("capabilities", {}),
                "messages": messages,
                "surface_link": surface_link(
                    "comms",
                    object_kind="thread",
                    object_id=key,
                    evidence_event_ids=sorted(raw_ids),
                ),
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
        "alerts": sum(1 for item in threads if item["bucket"] == "alerts"),
        "needs_reply": sum(1 for item in threads if item["needs_reply"]),
        "waiting": sum(1 for item in threads if item["bucket"] == "waiting"),
        "snoozed": sum(1 for item in threads if item["bucket"] == "snoozed"),
        "quieted": sum(1 for item in threads if item["bucket"] == "quieted"),
        "triage": sum(int(item["triage_count"]) for item in threads),
        "all": len(threads),
    }
    return {
        "contract": (
            "human-centered-comms-cockpit-v1"
            if human_centered
            else "deterministic-comms-cockpit-v2"
        ),
        "member_id": member_id,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "decision_maker": "rules_not_model",
        "combined_view_is_union_not_copy": True,
        "official_items_require_triage": True,
        "attention_policy": {
            "id": "comms-focus-v2",
            "unread_is_visual_state_only": True,
            "model_decides_attention": False,
            "focus_requires_admitted_matter": human_centered,
        },
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
        if interaction.get("provider") == "bluebubbles":
            if not interaction.get("native_chat_id"):
                raise ValueError("source-derived native chat is unavailable")
            if recipient is not None:
                raise ValueError("Messages reply recipient must remain chat-derived")
        else:
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
            "source_derived_chat"
            if operation == "reply" and interaction.get("provider") == "bluebubbles"
            else "source_derived_recipient"
            if operation == "reply"
            else "provider_target",
            "member_authored_content" if operation == "reply" else "no_outbound_body",
        ],
    }


__all__ = [
    "compose_comms_cockpit",
    "message_attention_decision",
    "validate_outbound_communication",
]
