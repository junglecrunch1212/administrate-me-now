"""
Commitments projection handlers — obligation-tracker state transitions.

Per ADMINISTRATEME_BUILD.md §3.4 and SYSTEM_INVARIANTS.md §4. Handlers are
idempotent: re-applying the same event produces the same row state
(§2 invariant 4). Keyed by ``(tenant_id, commitment_id)``.

Subscribed event types:
- ``commitment.proposed``   → INSERT with status='pending'
- ``commitment.confirmed``  → set confirmed_at / confirmed_by
- ``commitment.completed``  → status='done', completed_at
- ``commitment.dismissed``  → status='cancelled'
- ``commitment.edited``     → apply field_updates
- ``commitment.snoozed``    → status='snoozed', due_at=snoozed_until
- ``commitment.cancelled``  → status='cancelled', completed_at
- ``commitment.delegated``  → status='delegated', owed_by_party=delegated_to
- ``commitment.expired``    → status='cancelled' (no completed_by)

Per [§2.3], handlers do not validate cross-projection references — if an
event carries a party_id that does not exist in ``parties``, the row still
lands. Integrity is preserved by upstream pipelines.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)

# Columns accepted by commitment.edited's field_updates. Anything outside
# this allowlist is ignored to keep handlers deterministic across evolving
# payloads.
_EDITABLE_COLUMNS = frozenset({
    "kind",
    "description",
    "due_at",
    "owed_by_party",
    "owed_to_party",
    "confidence",
    "source_interaction_id",
    "source_skill",
})


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "commitment.proposed": apply_commitment_proposed,
        "commitment.confirmed": apply_commitment_confirmed,
        "commitment.completed": apply_commitment_completed,
        "commitment.dismissed": apply_commitment_dismissed,
        "commitment.edited": apply_commitment_edited,
        "commitment.snoozed": apply_commitment_snoozed,
        "commitment.cancelled": apply_commitment_cancelled,
        "commitment.delegated": apply_commitment_delegated,
        "commitment.expired": apply_commitment_expired,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def apply_commitment_proposed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO commitments (
            commitment_id, tenant_id, owed_by_party, owed_to_party, kind,
            description, due_at, status, confidence, source_interaction_id,
            source_skill, proposed_at, confirmed_at, confirmed_by,
            completed_at, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, NULL, NULL,
                  NULL, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, commitment_id) DO UPDATE SET
            owed_by_party         = excluded.owed_by_party,
            owed_to_party         = excluded.owed_to_party,
            kind                  = excluded.kind,
            description           = excluded.description,
            due_at                = excluded.due_at,
            status                = 'pending',
            confidence            = excluded.confidence,
            source_interaction_id = excluded.source_interaction_id,
            source_skill          = excluded.source_skill,
            proposed_at           = excluded.proposed_at,
            owner_scope           = excluded.owner_scope,
            visibility_scope      = excluded.visibility_scope,
            sensitivity           = excluded.sensitivity,
            last_event_id         = excluded.last_event_id
        """,
        (
            p["commitment_id"],
            envelope["tenant_id"],
            p["owed_by_member_id"],
            p["owed_to_party_id"],
            p["kind"],
            p["text_summary"],
            p.get("suggested_due"),
            p.get("confidence"),
            p.get("source_interaction_id"),
            p.get("source_skill"),
            envelope["occurred_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_commitment_confirmed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE commitments
           SET confirmed_at  = ?,
               confirmed_by  = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND commitment_id = ?
        """,
        (
            p["confirmed_at"],
            p["confirmed_by_member_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["commitment_id"],
        ),
    )


def apply_commitment_completed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE commitments
           SET status        = 'done',
               completed_at  = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND commitment_id = ?
        """,
        (
            p["completed_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["commitment_id"],
        ),
    )


def apply_commitment_dismissed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE commitments
           SET status        = 'cancelled',
               completed_at  = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND commitment_id = ?
        """,
        (
            p["dismissed_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["commitment_id"],
        ),
    )


def apply_commitment_edited(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = p.get("field_updates") or {}
    if not updates:
        return
    columns = [k for k in updates.keys() if k in _EDITABLE_COLUMNS]
    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns) + ", last_event_id = ?"
    params: list[Any] = [updates[c] for c in columns]
    params.append(envelope["event_id"])
    params.extend([envelope["tenant_id"], p["commitment_id"]])
    conn.execute(
        f"UPDATE commitments SET {assignments} "
        "WHERE tenant_id = ? AND commitment_id = ?",
        params,
    )


def apply_commitment_snoozed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE commitments
           SET status        = 'snoozed',
               due_at        = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND commitment_id = ?
        """,
        (
            p["snoozed_until"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["commitment_id"],
        ),
    )


def apply_commitment_cancelled(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE commitments
           SET status        = 'cancelled',
               completed_at  = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND commitment_id = ?
        """,
        (
            p["cancelled_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["commitment_id"],
        ),
    )


def apply_commitment_delegated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE commitments
           SET status         = 'delegated',
               owed_by_party  = ?,
               last_event_id  = ?
         WHERE tenant_id = ? AND commitment_id = ?
        """,
        (
            p["delegated_to_party_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["commitment_id"],
        ),
    )


def apply_commitment_expired(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    # Distinct from dismissed: expiry = nobody acted. No completed_by.
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE commitments
           SET status        = 'cancelled',
               last_event_id = ?
         WHERE tenant_id = ? AND commitment_id = ?
        """,
        (
            envelope["event_id"],
            envelope["tenant_id"],
            p["commitment_id"],
        ),
    )
