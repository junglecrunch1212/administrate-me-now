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
    elif event_type == "messaging.semantic_features_extracted":
        _handle_semantic_features(envelope, conn)
    elif event_type == "triage.intent.features_extracted":
        _handle_assessment(envelope, conn)
    elif event_type == "communication.state_changed":
        _handle_state_changed(envelope, conn)
    elif event_type == "external_sync.write_requested":
        _handle_command_requested(envelope, conn)
    elif event_type == "external_sync.write_completed":
        _handle_command_completed(envelope, conn)
    elif event_type == "external_sync.write_failed":
        _handle_command_failed(envelope, conn)
    elif event_type == "member.provider_action.confirmed":
        _handle_member_command_approved(envelope, conn)
    elif event_type == "messaging.reply.canceled":
        _handle_reply_lifecycle(envelope, conn, "canceled")
    elif event_type == "messaging.reply.observed":
        _handle_reply_lifecycle(envelope, conn, "observed")
    elif event_type == "messaging.reply.reconciled":
        _handle_reply_lifecycle(envelope, conn, "reconciled")
    else:
        return


def _interaction_id_for(envelope: dict[str, Any]) -> str:
    # Deterministic: one raw event → one interaction at v1.
    return f"ix_{envelope['event_id']}"


def _native_interaction_identity(
    conn: sqlcipher3.Connection,
    envelope: dict[str, Any],
    *,
    native_message_id: Any,
    member_id: str | None,
    provider: str,
    provider_account_id: str,
) -> tuple[str, str]:
    """Resolve repeated provider observations to one durable interaction row.

    Adapters may legitimately observe the same native message again after a
    restart or provider-label change. The event log keeps every observation;
    the projection folds them into one member/account-scoped interaction while
    retaining all source event ids. The first event-derived id is preserved so
    existing links never need to be rewritten.
    """

    event_id = str(envelope["event_id"])
    if isinstance(native_message_id, str) and native_message_id:
        existing = conn.execute(
            """
            SELECT interaction_id, raw_event_ids
              FROM interactions
             WHERE tenant_id=?
               AND member_id IS ?
               AND provider=?
               AND provider_account_id=?
               AND native_message_id=?
             ORDER BY occurred_at, interaction_id
             LIMIT 1
            """,
            (
                envelope["tenant_id"],
                member_id,
                provider,
                provider_account_id,
                native_message_id,
            ),
        ).fetchone()
        if existing is not None:
            try:
                raw_ids = [str(value) for value in json.loads(existing[1] or "[]")]
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_ids = []
            if event_id not in raw_ids:
                raw_ids.append(event_id)
            return str(existing[0]), json.dumps(raw_ids, separators=(",", ":"))
    return _interaction_id_for(envelope), json.dumps([event_id], separators=(",", ":"))


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
    if p.get("service_principal_id"):
        _handle_service_messaging(envelope, conn, direction=direction)
        return
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
    interaction_id, raw_event_ids = _native_interaction_identity(
        conn,
        envelope,
        native_message_id=p.get("native_message_id"),
        member_id=member_id,
        provider=provider,
        provider_account_id=provider_account_id,
    )

    advanced = conn.execute(
        """
        INSERT INTO interactions (
            interaction_id, tenant_id, direction, channel_family,
            channel_specific, occurred_at, subject, summary, body_ref, body_text,
            body_preview, thread_id, native_message_id,
            native_chat_id, native_revision, native_revision_ms,
            reply_to_native_message_id, transport_class, is_group, message_state,
            bridge_enrollment_id, device_identity_sha256, connector_id,
            protected_source_id, transport_version, transport_commit, correlation_id,
            member_id, provider, provider_account_id, workspace,
            unread, archived, starred, important,
            rfc_message_id, reply_to_identifier, provider_labels,
            attachment_metadata, delivery_status,
            raw_event_ids, owner_scope, visibility_scope,
            sensitivity, response_urgency, suggested_action, auto_handled,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            native_chat_id   = excluded.native_chat_id,
            native_revision  = excluded.native_revision,
            native_revision_ms = excluded.native_revision_ms,
            reply_to_native_message_id = excluded.reply_to_native_message_id,
            transport_class  = excluded.transport_class,
            is_group         = excluded.is_group,
            message_state    = excluded.message_state,
            bridge_enrollment_id = excluded.bridge_enrollment_id,
            device_identity_sha256 = excluded.device_identity_sha256,
            connector_id     = excluded.connector_id,
            protected_source_id = excluded.protected_source_id,
            transport_version = excluded.transport_version,
            transport_commit = excluded.transport_commit,
            correlation_id   = excluded.correlation_id,
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
        WHERE (
            excluded.native_revision_ms > 0
            AND interactions.native_revision_ms < excluded.native_revision_ms
        ) OR (
            excluded.native_revision_ms = 0
            AND interactions.last_event_id < excluded.last_event_id
        )
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
            p.get("native_chat_id"),
            p.get("native_revision"),
            int(p.get("native_revision_ms") or 0),
            p.get("reply_to_native_message_id"),
            p.get("transport_class"),
            int(bool(p.get("is_group", False))),
            p.get("message_state") or "active",
            p.get("bridge_enrollment_id"),
            p.get("device_identity_sha256"),
            p.get("connector_id"),
            p.get("protected_source_id"),
            p.get("transport_version"),
            p.get("transport_commit"),
            p.get("correlation_id"),
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
            raw_event_ids,
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            None,
            None,
            0,
            envelope["event_id"],
        ),
    ).rowcount
    if not advanced:
        return

    typed_participants = p.get("participants")
    if isinstance(typed_participants, list):
        conn.execute(
            "DELETE FROM interaction_source_participants "
            "WHERE tenant_id=? AND interaction_id=?",
            (envelope["tenant_id"], interaction_id),
        )
        sender_id = str(p.get("sender_id") or "")
        source_participants: list[tuple[str, str, bool]] = []
        for value in typed_participants:
            if not isinstance(value, dict) or not value.get("participant_id"):
                continue
            participant_id = str(value["participant_id"])
            source_participants.append(
                (
                    participant_id,
                    "from" if participant_id == sender_id else "to",
                    bool(value.get("is_self")),
                )
            )
        for participant_id, role_key, is_self in source_participants:
            conn.execute(
                "INSERT INTO interaction_source_participants "
                "(tenant_id, interaction_id, participant_id, role, is_self) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, interaction_id, participant_id, role) "
                "DO UPDATE SET is_self=excluded.is_self",
                (
                    envelope["tenant_id"],
                    interaction_id,
                    participant_id,
                    role_key,
                    int(is_self),
                ),
            )
        return

    # Preserve the established legacy contract for older provider-neutral
    # schemas. Only the new typed Messages participant records distinguish raw
    # endpoints from evidence-resolved Parties.
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


def _handle_service_messaging(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    *,
    direction: str,
) -> None:
    """Store CoS mailbox content only in its service-private projection."""

    p = envelope["payload"]
    provider = _provider(str(p.get("source_channel") or "messaging"), p)
    account = str(
        p.get("provider_account_id") or envelope.get("source_account_id") or provider
    )
    native_id = p.get("native_message_id")
    existing = None
    if isinstance(native_id, str) and native_id:
        existing = conn.execute(
            """SELECT interaction_id FROM service_interactions
                 WHERE tenant_id=? AND service_principal_id=? AND provider=?
                   AND provider_account_id=? AND native_message_id=?""",
            (
                envelope["tenant_id"],
                p["service_principal_id"],
                provider,
                account,
                native_id,
            ),
        ).fetchone()
    interaction_id = (
        str(existing[0]) if existing is not None else _interaction_id_for(envelope)
    )
    conn.execute(
        """
        INSERT INTO service_interactions (
            tenant_id, interaction_id, service_principal_id, provider,
            provider_account_id, channel_id, native_message_id, subject,
            body_text, occurred_at, trusted_route_key, routing_scope,
            routing_target_id, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, interaction_id) DO UPDATE SET
            subject=excluded.subject,
            body_text=excluded.body_text,
            occurred_at=excluded.occurred_at,
            trusted_route_key=excluded.trusted_route_key,
            routing_scope=excluded.routing_scope,
            routing_target_id=excluded.routing_target_id,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity,
            last_event_id=excluded.last_event_id
        WHERE service_interactions.last_event_id < excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            interaction_id,
            p["service_principal_id"],
            provider,
            account,
            str(p.get("channel_id") or "service-channel"),
            native_id,
            p.get("subject"),
            p.get("body_text"),
            p.get("received_at") or p.get("sent_at") or envelope["occurred_at"],
            p.get("trusted_route_key"),
            p.get("routing_scope") or "unresolved",
            p.get("routing_target_id"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def _handle_sms(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    member_id = _member_id(envelope, p)
    provider_account_id = str(
        envelope.get("source_account_id") or p.get("to_number") or "sms"
    )
    interaction_id, raw_event_ids = _native_interaction_identity(
        conn,
        envelope,
        native_message_id=p.get("carrier_message_id"),
        member_id=member_id,
        provider="twilio_sms",
        provider_account_id=provider_account_id,
    )
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
            member_id,
            "twilio_sms",
            provider_account_id,
            "personal",
            1,
            0,
            0,
            0,
            "[]",
            raw_event_ids,
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
    # Older schemas stored unresolved endpoint text in the Party table. Replace
    # that legacy role once exact resolution is proven. Typed Messages endpoint
    # provenance lives in interaction_source_participants and is retained.
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
    interaction_id = _interaction_for_source(
        conn, tenant_id=envelope["tenant_id"], source_event_id=p["source_event_id"]
    )
    if interaction_id is None:
        return
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


def _handle_assessment(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    """Attach the terminal candidate disposition without creating a Triage item."""

    p = envelope["payload"]
    interaction_id = _interaction_for_source(
        conn, tenant_id=envelope["tenant_id"], source_event_id=p["source_event_id"]
    )
    if interaction_id is None:
        return
    outcome = p.get("assessment_outcome")
    if outcome is None:
        outcome = (
            "failed"
            if p.get("extraction_state") == "unavailable"
            else "eligible_candidates"
            if p.get("candidates")
            else "no_candidate"
        )
    conn.execute(
        "UPDATE interactions SET assessment_outcome=?, assessment_reason_codes=?, "
        "last_event_id=? WHERE tenant_id=? AND interaction_id=? "
        "AND last_event_id < ?",
        (
            outcome,
            json.dumps(p.get("reason_codes") or [], separators=(",", ":")),
            envelope["event_id"],
            envelope["tenant_id"],
            interaction_id,
            envelope["event_id"],
        ),
    )


def _interaction_for_source(
    conn: sqlcipher3.Connection, *, tenant_id: str, source_event_id: str
) -> str | None:
    direct = f"ix_{source_event_id}"
    row = conn.execute(
        "SELECT interaction_id FROM interactions WHERE tenant_id=? AND interaction_id=?",
        (tenant_id, direct),
    ).fetchone()
    if row is not None:
        return str(row[0])
    for candidate in conn.execute(
        "SELECT interaction_id, raw_event_ids FROM interactions WHERE tenant_id=?",
        (tenant_id,),
    ).fetchall():
        try:
            source_ids = json.loads(candidate[1] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if source_event_id in source_ids:
            return str(candidate[0])
    return None


def _handle_semantic_features(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    interaction_id = _interaction_for_source(
        conn, tenant_id=envelope["tenant_id"], source_event_id=p["source_event_id"]
    )
    if interaction_id is None:
        return
    features = {
        key: p.get(key)
        for key in (
            "reply_signal",
            "automated",
            "transactional_due",
            "security_code",
            "person_to_person",
            "deadline",
            "reasons",
        )
    }
    conn.execute(
        "UPDATE interactions SET semantic_features_json=?, semantic_confidence=?, "
        "semantic_skill_name=?, semantic_skill_version=?, last_event_id=? "
        "WHERE tenant_id=? AND interaction_id=? AND last_event_id < ?",
        (
            json.dumps(features, sort_keys=True, separators=(",", ":")),
            p["confidence"],
            p["skill_name"],
            p["skill_version"],
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
        "UPDATE interactions SET command_state='dispatched', last_command_id=?, last_event_id=? "
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
    outcome = str(result.get("outcome") or "completed")
    command_state = (
        "suppressed"
        if p.get("suppressed")
        else "ambiguous"
        if outcome == "ambiguous"
        else "result_accepted"
        if outcome == "accepted"
        else "result_sent"
        if outcome == "sent"
        else "completed"
    )
    values: list[Any] = [
        command_state,
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


def _handle_member_command_approved(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    nested = p.get("command_payload") or {}
    if p.get("provider") not in _COMMS_PROVIDERS or not isinstance(nested, dict):
        return
    interaction_id = nested.get("interaction_id")
    if not interaction_id:
        return
    # Do not advance last_event_id: the immutable source revision remains the
    # CAS target until the independently approved external command exists.
    conn.execute(
        "UPDATE interactions SET command_state='awaiting_openclaw_approval', "
        "last_command_id=? WHERE tenant_id=? AND interaction_id=? "
        "AND last_event_id=?",
        (
            p["action_id"],
            envelope["tenant_id"],
            interaction_id,
            nested.get("expected_last_event_id"),
        ),
    )


def _handle_reply_lifecycle(
    envelope: dict[str, Any], conn: sqlcipher3.Connection, state: str
) -> None:
    p = envelope["payload"]
    conn.execute(
        "UPDATE interactions SET command_state=?, last_command_id=?, last_event_id=? "
        "WHERE tenant_id=? AND interaction_id=? AND last_command_id=?",
        (
            state,
            p["command_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["interaction_id"],
            p["command_id"],
        ),
    )
