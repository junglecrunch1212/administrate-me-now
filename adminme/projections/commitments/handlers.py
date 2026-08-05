"""Idempotent commitment projection state transitions."""

from __future__ import annotations

from typing import Any

import sqlcipher3

_EDITABLE_COLUMNS = frozenset(
    {
        "kind",
        "description",
        "due_at",
        "owed_by_party",
        "owed_to_party",
        "confidence",
        "source_interaction_id",
        "source_skill",
    }
)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    handler = {
        "commitment.proposed": apply_commitment_proposed,
        "commitment.confirmed": apply_commitment_confirmed,
        "commitment.completed": apply_commitment_completed,
        "commitment.dismissed": apply_commitment_dismissed,
        "commitment.edited": apply_commitment_edited,
        "commitment.snoozed": apply_commitment_snoozed,
        "commitment.deferred": apply_commitment_deferred,
        "commitment.blocked": apply_commitment_blocked,
        "commitment.unblocked": apply_commitment_unblocked,
        "commitment.cancelled": apply_commitment_cancelled,
        "commitment.delegated": apply_commitment_delegated,
        "commitment.expired": apply_commitment_expired,
        "list_item.completed": apply_external_completion,
        "list_item.uncompleted": apply_external_completion,
        "calendar.event_outcome_recorded": apply_calendar_completion,
    }.get(envelope["type"])
    if handler is not None:
        handler(envelope, conn)


def apply_calendar_completion(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    policy = p.get("completion_policy", "manual")
    qualifies = (
        policy == "on_elapsed" and p.get("outcome") in {"elapsed", "attended"}
    ) or (policy == "on_attended" and p.get("outcome") == "attended")
    if (
        p.get("linked_item_kind") != "commitment"
        or not p.get("linked_item_id")
        or not qualifies
    ):
        return
    conn.execute(
        """UPDATE commitments SET status='done', completed_at=?, lifecycle_at=?,
                  lifecycle_actor=?, last_event_id=?
             WHERE tenant_id=? AND commitment_id=? AND last_event_id < ?""",
        (
            p["recorded_at"],
            p["recorded_at"],
            p["member_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["linked_item_id"],
            envelope["event_id"],
        ),
    )


def apply_external_completion(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if (
        p.get("linked_item_kind") != "commitment"
        or not p.get("linked_item_id")
        or p.get("completion_policy") != "mirror"
    ):
        return
    completed = envelope["type"] == "list_item.completed"
    at = str(p.get("completion_at") or p.get("last_modified_at"))
    conn.execute(
        """UPDATE commitments SET status=?, completed_at=?, lifecycle_at=?,
                  lifecycle_actor=?, last_event_id=?
             WHERE tenant_id=? AND commitment_id=? AND last_event_id < ?""",
        (
            "done" if completed else "pending",
            at if completed else None,
            at,
            p.get("member_id"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["linked_item_id"],
            envelope["event_id"],
        ),
    )


def _where_newer(envelope: dict[str, Any], commitment_id: str) -> tuple[str, str, str]:
    return envelope["tenant_id"], commitment_id, envelope["event_id"]


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
        ON CONFLICT(tenant_id, commitment_id) DO NOTHING
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
            p.get("source_skill") or envelope["source_adapter"],
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
        """UPDATE commitments SET confirmed_at = ?, confirmed_by = ?,
                  lifecycle_at = ?, lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            p["confirmed_at"],
            p["confirmed_by_member_id"],
            p["confirmed_at"],
            p["confirmed_by_member_id"],
            envelope["event_id"],
            *_where_newer(envelope, p["commitment_id"]),
        ),
    )


def apply_commitment_completed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE commitments SET status = 'done', completed_at = ?,
                  blocked_reason = NULL, deferred_until = NULL,
                  lifecycle_at = ?, lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            p["completed_at"],
            p["completed_at"],
            p["completed_by_party_id"],
            envelope["event_id"],
            *_where_newer(envelope, p["commitment_id"]),
        ),
    )


def apply_commitment_dismissed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    _terminal(
        envelope, conn, "cancelled", p["dismissed_at"], p["dismissed_by_party_id"]
    )


def apply_commitment_edited(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates = p.get("field_updates") or {}
    columns = [key for key in updates if key in _EDITABLE_COLUMNS]
    if not columns:
        return
    assignments = ", ".join(f"{column} = ?" for column in columns)
    conn.execute(
        f"""UPDATE commitments SET {assignments}, lifecycle_at = ?,
                  lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            *(updates[column] for column in columns),
            p["edited_at"],
            p["edited_by_party_id"],
            envelope["event_id"],
            *_where_newer(envelope, p["commitment_id"]),
        ),
    )


def apply_commitment_snoozed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    _timed_state(
        envelope,
        conn,
        "snoozed",
        p["snoozed_until"],
        p["snoozed_at"],
        p["snoozed_by_party_id"],
        "due_at",
    )


def apply_commitment_deferred(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    _timed_state(
        envelope,
        conn,
        "deferred",
        p["deferred_until"],
        p["deferred_at"],
        p["deferred_by_party_id"],
        "deferred_until",
    )


def _timed_state(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    status: str,
    until: str,
    at: str,
    actor: str,
    column: str,
) -> None:
    commitment_id = envelope["payload"]["commitment_id"]
    conn.execute(
        f"""UPDATE commitments SET status = ?, {column} = ?, lifecycle_at = ?,
                  lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            status,
            until,
            at,
            actor,
            envelope["event_id"],
            *_where_newer(envelope, commitment_id),
        ),
    )


def apply_commitment_blocked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE commitments SET status = 'blocked', blocked_reason = ?,
                  lifecycle_at = ?, lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            p["reason"],
            p["blocked_at"],
            p["blocked_by_party_id"],
            envelope["event_id"],
            *_where_newer(envelope, p["commitment_id"]),
        ),
    )


def apply_commitment_unblocked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE commitments SET status = 'pending', blocked_reason = NULL,
                  lifecycle_at = ?, lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND status = 'blocked'
               AND last_event_id < ?""",
        (
            p["unblocked_at"],
            p["unblocked_by_party_id"],
            envelope["event_id"],
            *_where_newer(envelope, p["commitment_id"]),
        ),
    )


def apply_commitment_cancelled(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    _terminal(
        envelope, conn, "cancelled", p["cancelled_at"], p["cancelled_by_party_id"]
    )


def _terminal(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    status: str,
    at: str,
    actor: str,
) -> None:
    commitment_id = envelope["payload"]["commitment_id"]
    conn.execute(
        """UPDATE commitments SET status = ?, completed_at = ?, lifecycle_at = ?,
                  lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            status,
            at,
            at,
            actor,
            envelope["event_id"],
            *_where_newer(envelope, commitment_id),
        ),
    )


def apply_commitment_delegated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE commitments SET status = 'delegated', owed_by_party = ?,
                  lifecycle_at = ?, lifecycle_actor = ?, last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            p["delegated_to_party_id"],
            p["delegated_at"],
            p["delegated_by_party_id"],
            envelope["event_id"],
            *_where_newer(envelope, p["commitment_id"]),
        ),
    )


def apply_commitment_expired(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE commitments SET status = 'cancelled', lifecycle_at = ?,
                  lifecycle_actor = 'system:expiry', last_event_id = ?
             WHERE tenant_id = ? AND commitment_id = ? AND last_event_id < ?""",
        (
            p["expired_at"],
            envelope["event_id"],
            *_where_newer(envelope, p["commitment_id"]),
        ),
    )
