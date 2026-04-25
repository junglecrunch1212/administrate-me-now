"""
Interactions projection read queries.

Per ADMINISTRATEME_BUILD.md §3.2 and SYSTEM_INVARIANTS.md §3, §6. Every
public query takes a ``session: Session`` per [§6.1] and runs through
``scope.filter_rows`` before return.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_rows
from adminme.lib.session import Session


def recent_with(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Return interactions involving ``party_id`` with occurred_at within
    the last ``days`` days. ``occurred_at`` is an ISO 8601 string; we use
    lexicographic comparison, which is correct for ISO-8601 UTC with a
    stable suffix (e.g. ``Z`` or ``+00:00``)."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    rows = conn.execute(
        """
        SELECT DISTINCT i.* FROM interactions i
          JOIN interaction_participants p
            ON p.tenant_id = i.tenant_id
           AND p.interaction_id = i.interaction_id
         WHERE i.tenant_id = ?
           AND p.party_id = ?
           AND i.occurred_at >= ?
         ORDER BY i.occurred_at DESC
        """,
        (session.tenant_id, party_id, cutoff),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def thread(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    thread_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM interactions "
        "WHERE tenant_id = ? AND thread_id = ? "
        "ORDER BY occurred_at ASC",
        (session.tenant_id, thread_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def closeness_signals(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
    since_iso: str,
) -> dict[str, Any]:
    """Aggregate counts of inbound / outbound interactions involving
    ``party_id`` since ``since_iso``. The aggregation is restricted to
    rows the session may read [§6.4]; rows the session cannot see are
    excluded from the counts."""
    rows = conn.execute(
        """
        SELECT i.*, p.role AS _participant_role
          FROM interactions i
          JOIN interaction_participants p
            ON p.tenant_id = i.tenant_id
           AND p.interaction_id = i.interaction_id
         WHERE i.tenant_id = ?
           AND p.party_id = ?
           AND i.occurred_at >= ?
        """,
        (session.tenant_id, party_id, since_iso),
    ).fetchall()
    visible = filter_rows(session, [dict(r) for r in rows])
    inbound = sum(1 for r in visible if r.get("_participant_role") == "from")
    outbound = sum(1 for r in visible if r.get("_participant_role") == "to")
    last_iso = max(
        (r["occurred_at"] for r in visible if r.get("occurred_at")),
        default=None,
    )
    return {
        "inbound_count": inbound,
        "outbound_count": outbound,
        "last_contact_iso": last_iso,
    }
