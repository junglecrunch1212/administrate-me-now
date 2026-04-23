"""
Interactions projection read queries.

Per ADMINISTRATEME_BUILD.md §3.2. Plain query functions; prompt 08 wraps
them with Session / scope enforcement.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3


# TODO(prompt-08): wrap with Session scope check
def recent_with(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
        (tenant_id, party_id, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def thread(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    thread_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM interactions "
        "WHERE tenant_id = ? AND thread_id = ? "
        "ORDER BY occurred_at ASC",
        (tenant_id, thread_id),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def closeness_signals(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    party_id: str,
    since_iso: str,
) -> dict[str, Any]:
    """Stub signature. Real computation lives in prompt 05 or 06; the
    parties projection can link against this shape today."""
    inbound = conn.execute(
        """
        SELECT count(*) FROM interactions i
          JOIN interaction_participants p
            ON p.tenant_id = i.tenant_id
           AND p.interaction_id = i.interaction_id
         WHERE i.tenant_id = ?
           AND p.party_id = ?
           AND p.role = 'from'
           AND i.occurred_at >= ?
        """,
        (tenant_id, party_id, since_iso),
    ).fetchone()[0]
    outbound = conn.execute(
        """
        SELECT count(*) FROM interactions i
          JOIN interaction_participants p
            ON p.tenant_id = i.tenant_id
           AND p.interaction_id = i.interaction_id
         WHERE i.tenant_id = ?
           AND p.party_id = ?
           AND p.role = 'to'
           AND i.occurred_at >= ?
        """,
        (tenant_id, party_id, since_iso),
    ).fetchone()[0]
    last = conn.execute(
        """
        SELECT max(occurred_at) FROM interactions i
          JOIN interaction_participants p
            ON p.tenant_id = i.tenant_id
           AND p.interaction_id = i.interaction_id
         WHERE i.tenant_id = ? AND p.party_id = ?
        """,
        (tenant_id, party_id),
    ).fetchone()[0]
    return {
        "inbound_count": int(inbound or 0),
        "outbound_count": int(outbound or 0),
        "last_contact_iso": last,
    }
