"""
Interactions projection handlers.

Per ADMINISTRATEME_BUILD.md §3.2 and SYSTEM_INVARIANTS.md §3 invariant 5.

Handlers derive the interaction_id deterministically from the source
envelope's event_id so replay produces the same rows. The participants
table is keyed by the from/to identifiers from the payload; prompt 10a
(identity_resolution) will later replace identifier strings with resolved
party_ids. For now we treat the identifier string itself as the party
stand-in — this keeps the projection functional before identity resolution
lands and the only thing prompt 10a changes is the value in
``interaction_participants.party_id``.

# TODO(prompt-10b): noise_filtering pipeline will merge related
# interactions into one row (one interaction may aggregate multiple raw
# events via the raw_event_ids JSON column).
"""

from __future__ import annotations

import json
import logging
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
    else:
        return


def _interaction_id_for(envelope: dict[str, Any]) -> str:
    # Deterministic: one raw event → one interaction at v1.
    return f"ix_{envelope['event_id']}"


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
    body_ref = None
    if p.get("body_text"):
        body_ref = "inline"

    conn.execute(
        """
        INSERT INTO interactions (
            interaction_id, tenant_id, direction, channel_family,
            channel_specific, occurred_at, subject, summary, body_ref,
            thread_id, raw_event_ids, owner_scope, visibility_scope,
            sensitivity, response_urgency, suggested_action, auto_handled,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, interaction_id) DO UPDATE SET
            direction        = excluded.direction,
            channel_family   = excluded.channel_family,
            channel_specific = excluded.channel_specific,
            occurred_at      = excluded.occurred_at,
            subject          = excluded.subject,
            summary          = excluded.summary,
            body_ref         = excluded.body_ref,
            thread_id        = excluded.thread_id,
            raw_event_ids    = excluded.raw_event_ids,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
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
            p.get("thread_id"),
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
    for role_key, payload_key in (("from", "from_identifier"), ("to", "to_identifier")):
        if payload_key not in p:
            continue
        conn.execute(
            "INSERT INTO interaction_participants "
            "(tenant_id, interaction_id, party_id, role) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, interaction_id, party_id, role) DO NOTHING",
            (envelope["tenant_id"], interaction_id, p[payload_key], role_key),
        )


def _handle_sms(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    interaction_id = _interaction_id_for(envelope)
    conn.execute(
        """
        INSERT INTO interactions (
            interaction_id, tenant_id, direction, channel_family,
            channel_specific, occurred_at, subject, summary, body_ref,
            thread_id, raw_event_ids, owner_scope, visibility_scope,
            sensitivity, response_urgency, suggested_action, auto_handled,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, interaction_id) DO UPDATE SET
            direction        = excluded.direction,
            channel_family   = excluded.channel_family,
            channel_specific = excluded.channel_specific,
            occurred_at      = excluded.occurred_at,
            subject          = excluded.subject,
            summary          = excluded.summary,
            body_ref         = excluded.body_ref,
            thread_id        = excluded.thread_id,
            raw_event_ids    = excluded.raw_event_ids,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
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
            None,
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
