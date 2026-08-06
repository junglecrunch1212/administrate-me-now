"""
Interactions projection handlers.

Per ADMINISTRATEME_BUILD.md §3.2 and SYSTEM_INVARIANTS.md §3 invariant 5.

Handlers derive the interaction_id deterministically from the source
envelope's event_id so replay produces the same rows. Participants preserve
the normalized source identifiers supplied by the communication adapter;
identity resolution emits separate Party/Identifier facts rather than
rewriting historical interaction provenance.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    if event_type == "messaging.received":
        _handle_messaging(envelope, conn, direction="inbound", family="messaging")
    elif event_type == "messaging.sent":
        _handle_messaging(envelope, conn, direction="outbound", family="messaging")
    elif event_type == "telephony.sms_received":
        _handle_sms(envelope, conn)
    elif event_type == "interaction.participant_resolved":
        _handle_participant_resolved(envelope, conn)
    elif event_type == "messaging.classified":
        _handle_classified(envelope, conn)
    elif event_type == "communication.state_changed":
        _handle_state_changed(envelope, conn)
    elif event_type == "external_sync.write_requested":
        _handle_command_requested(envelope, conn)
    elif event_type == "external_sync.write_completed":
        _handle_command_completed(envelope, conn)
    elif event_type == "external_sync.write_failed":
        _handle_command_failed(envelope, conn)
    else:
        return


def _interaction_id_for(envelope: dict[str, Any]) -> str:
    # Deterministic: one raw event → one interaction at v1.
    return f"ix_{envelope['event_id']}"


def _member_id(envelope: dict[str, Any], payload: dict[str, Any]) -> str | None:
    explicit = payload.get("member_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    owner_scope = str(envelope.get("owner_scope") or "")
    return (
        owner_scope.removeprefix("private:")
        if owner_scope.startswith("private:")
        else None
    )


def _provider(channel: str, payload: dict[str, Any]) -> str:
    explicit = payload.get("provider")
    if isinstance(explicit, str) and explicit:
        return explicit
    lowered = channel.casefold()
    if "gmail" in lowered:
        return "gmail"
    if "outlook" in lowered or "microsoft" in lowered:
        return "microsoft_graph"
    if "imessage" in lowered or "bluebubbles" in lowered:
        return "bluebubbles"
    if "whatsapp" in lowered:
        return "twilio_whatsapp"
    if "sms" in lowered or "twilio" in lowered:
        return "twilio_sms"
    if "fixture" in lowered:
        return "fixture"
    return lowered


def _preview(value: Any, *, limit: int = 320) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return (
        normalized
        if len(normalized) <= limit
        else normalized[: limit - 1].rstrip() + "…"
    )


def _handle_messaging(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    *,
    direction: str,
    family: str,
) -> None:
    p = envelope["payload"]
    interaction_id = _interaction_id_for(envelope)
    channel_specific = p["source_channel"]
    occurred_at = p.get("received_at") or p.get("sent_at") or envelope["occurred_at"]
    subject = p.get("subject")
    body_text = p.get("body_text")
    body_ref = None
    if body_text:
        body_ref = "inline"
    labels = [str(value) for value in (p.get("provider_labels") or [])]
    member_id = _member_id(envelope, p)
    provider = _provider(channel_specific, p)
    provider_account_id = str(
        p.get("provider_account_id")
        or envelope.get("source_account_id")
        or channel_specific
    )

    conn.execute(
        """
        INSERT INTO interactions (
            interaction_id, tenant_id, direction, channel_family,
            channel_specific, occurred_at, subject, summary, body_ref, body_text,
            body_preview, thread_id, native_message_id, member_id, provider,
            provider_account_id, workspace, unread, archived, starred, important,
            rfc_message_id, reply_to_identifier, provider_labels,
            attachment_metadata, delivery_status,
            raw_event_ids, owner_scope, visibility_scope,
            sensitivity, response_urgency, suggested_action, auto_handled,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, interaction_id) DO UPDATE SET
            direction        = excluded.direction,
            channel_family   = excluded.channel_family,
            channel_specific = excluded.channel_specific,
            occurred_at      = excluded.occurred_at,
            subject          = excluded.subject,
            summary          = excluded.summary,
            body_ref         = excluded.body_ref,
            body_text        = excluded.body_text,
            body_preview     = excluded.body_preview,
            thread_id        = excluded.thread_id,
            native_message_id = excluded.native_message_id,
            member_id        = excluded.member_id,
            provider         = excluded.provider,
            provider_account_id = excluded.provider_account_id,
            workspace        = excluded.workspace,
            unread           = excluded.unread,
            archived         = excluded.archived,
            starred          = excluded.starred,
            important        = excluded.important,
            rfc_message_id   = excluded.rfc_message_id,
            reply_to_identifier = excluded.reply_to_identifier,
            provider_labels  = excluded.provider_labels,
            attachment_metadata = excluded.attachment_metadata,
            delivery_status  = excluded.delivery_status,
            raw_event_ids    = excluded.raw_event_ids,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        WHERE interactions.last_event_id < excluded.last_event_id
        """,
        (
            interaction_id,
            envelope["tenant_id"],
            direction,
            family,
            channel_specific,
            occurred_at,
            subject,
            None,
            body_ref,
            body_text,
            _preview(body_text),
            p.get("thread_id"),
            p.get("native_message_id"),
            member_id,
            provider,
            provider_account_id,
            p.get("workspace") or "personal",
            int(bool(p.get("unread", direction == "inbound"))),
            int(bool(p.get("archived", False))),
            int(bool(p.get("starred", "STARRED" in labels))),
            int(bool(p.get("important", "IMPORTANT" in labels))),
            p.get("rfc_message_id"),
            p.get("reply_to_identifier"),
            json.dumps(labels, separators=(",", ":")),
            json.dumps(p.get("attachments") or [], separators=(",", ":")),
            p.get("delivery_status"),
            json.dumps([envelope["event_id"]], separators=(",", ":")),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            None,
            None,
            0,
            envelope["event_id"],
        ),
    )

    # Participants: 'from' and 'to' for v1. Deduplicate per the PK so
    # idempotent replay is a no-op.
    role_values = {
        "from": [p.get("from_identifier")],
        "to": list(p.get("to_identifiers") or [p.get("to_identifier")]),
        "cc": list(p.get("cc_identifiers") or []),
        "bcc": list(p.get("bcc_identifiers") or []),
    }
    for role_key, raw_values in role_values.items():
        for participant in [
            value for value in raw_values if isinstance(value, str) and value
        ]:
            conn.execute(
                "INSERT INTO interaction_participants "
                "(tenant_id, interaction_id, party_id, role) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, interaction_id, party_id, role) DO NOTHING",
                (envelope["tenant_id"], interaction_id, participant, role_key),
            )


def _handle_sms(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    interaction_id = _interaction_id_for(envelope)
    conn.execute(
        """
        INSERT INTO interactions (
            interaction_id, tenant_id, direction, channel_family,
            channel_specific, occurred_at, subject, summary, body_ref, body_text,
            body_preview, thread_id, native_message_id, member_id, provider,
            provider_account_id, workspace, unread, archived, starred, important,
            provider_labels, raw_event_ids, owner_scope, visibility_scope,
            sensitivity, response_urgency, suggested_action, auto_handled,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, interaction_id) DO UPDATE SET
            direction        = excluded.direction,
            channel_family   = excluded.channel_family,
            channel_specific = excluded.channel_specific,
            occurred_at      = excluded.occurred_at,
            subject          = excluded.subject,
            summary          = excluded.summary,
            body_ref         = excluded.body_ref,
            body_text        = excluded.body_text,
            body_preview     = excluded.body_preview,
            thread_id        = excluded.thread_id,
            raw_event_ids    = excluded.raw_event_ids,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        WHERE interactions.last_event_id < excluded.last_event_id
        """,
        (
            interaction_id,
            envelope["tenant_id"],
            "inbound",
            "telephony",
            "sms",
            p.get("received_at") or envelope["occurred_at"],
            None,
            None,
            "inline" if p.get("body") else None,
            p.get("body"),
            _preview(p.get("body")),
            None,
            p.get("carrier_message_id"),
            _member_id(envelope, p),
            "twilio_sms",
            str(envelope.get("source_account_id") or p.get("to_number") or "sms"),
            "personal",
            1,
            0,
            0,
            0,
            "[]",
            json.dumps([envelope["event_id"]], separators=(",", ":")),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            None,
            None,
            0,
            envelope["event_id"],
        ),
    )
    for role_key, payload_key in (("from", "from_number"), ("to", "to_number")):
        conn.execute(
            "INSERT INTO interaction_participants "
            "(tenant_id, interaction_id, party_id, role) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, interaction_id, party_id, role) DO NOTHING",
            (envelope["tenant_id"], interaction_id, p[payload_key], role_key),
        )


def _handle_participant_resolved(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    advanced = conn.execute(
        "UPDATE interactions SET last_event_id=? "
        "WHERE tenant_id=? AND interaction_id=? AND last_event_id < ?",
        (
            envelope["event_id"],
            envelope["tenant_id"],
            p["interaction_id"],
            envelope["event_id"],
        ),
    ).rowcount
    if not advanced:
        return
    conn.execute(
        "DELETE FROM interaction_participants "
        "WHERE tenant_id=? AND interaction_id=? AND role=?",
        (envelope["tenant_id"], p["interaction_id"], p["role"]),
    )
    conn.execute(
        "INSERT INTO interaction_participants "
        "(tenant_id, interaction_id, party_id, role) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(tenant_id, interaction_id, party_id, role) DO NOTHING",
        (
            envelope["tenant_id"],
            p["interaction_id"],
            p["party_id"],
            p["role"],
        ),
    )


def _handle_classified(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    interaction_id = f"ix_{p['source_event_id']}"
    conn.execute(
        "UPDATE interactions SET classification=?, classification_confidence=?, "
        "last_event_id=? WHERE tenant_id=? AND interaction_id=? "
        "AND last_event_id < ?",
        (
            p["classification"],
            p["confidence"],
            envelope["event_id"],
            envelope["tenant_id"],
            interaction_id,
            envelope["event_id"],
        ),
    )


_COMMS_PROVIDERS = {
    "gmail",
    "microsoft_graph",
    "bluebubbles",
    "twilio_sms",
    "twilio_whatsapp",
    "fixture",
}


def _handle_state_changed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    operation = p["operation"]
    values: dict[str, Any] = {}
    if operation in {"snooze", "unsnooze"}:
        values["snoozed_until"] = (
            p.get("snoozed_until") if operation == "snooze" else None
        )
    if operation in {"quiet", "unquiet"}:
        values["quieted"] = 1 if operation == "quiet" else 0
    assignments = ", ".join(f"{name}=?" for name in values)
    conn.execute(
        f"UPDATE interactions SET {assignments}, last_event_id=? "  # noqa: S608 - fixed keys
        "WHERE tenant_id=? AND interaction_id=? AND last_event_id=?",
        (
            *values.values(),
            envelope["event_id"],
            envelope["tenant_id"],
            p["interaction_id"],
            p["expected_last_event_id"],
        ),
    )


def _command_target(payload: dict[str, Any]) -> str | None:
    nested = payload.get("payload") or {}
    value = nested.get("interaction_id")
    return str(value) if value else None


def _handle_command_requested(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if p.get("provider") not in _COMMS_PROVIDERS:
        return
    interaction_id = _command_target(p)
    if interaction_id is None:
        return
    expected = (p.get("payload") or {}).get("expected_last_event_id")
    conn.execute(
        "UPDATE interactions SET command_state='pending', last_command_id=?, last_event_id=? "
        "WHERE tenant_id=? AND interaction_id=? AND (? IS NULL OR last_event_id=?)",
        (
            p["command_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            interaction_id,
            expected,
            expected,
        ),
    )


def _handle_command_completed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if p.get("provider") not in _COMMS_PROVIDERS:
        return
    result = p.get("result") or {}
    interaction_id = result.get("interaction_id")
    if not interaction_id:
        return
    state = result.get("state") or {}
    updates = {
        key: int(bool(value))
        for key, value in state.items()
        if key in {"unread", "archived", "starred", "important"}
    }
    assignments = ["command_state=?", "last_command_id=?", "last_event_id=?"]
    values: list[Any] = [
        "suppressed" if p.get("suppressed") else "completed",
        p["command_id"],
        envelope["event_id"],
    ]
    for key, value in updates.items():
        assignments.append(f"{key}=?")
        values.append(value)
    conn.execute(
        f"UPDATE interactions SET {', '.join(assignments)} "  # noqa: S608 - fixed keys
        "WHERE tenant_id=? AND interaction_id=? AND last_command_id=?",
        (*values, envelope["tenant_id"], interaction_id, p["command_id"]),
    )


def _handle_command_failed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if p.get("provider") not in _COMMS_PROVIDERS:
        return
    conn.execute(
        "UPDATE interactions SET command_state='failed', last_event_id=? "
        "WHERE tenant_id=? AND last_command_id=?",
        (envelope["event_id"], envelope["tenant_id"], p["command_id"]),
    )
