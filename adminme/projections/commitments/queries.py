"""
Commitments projection read queries.

Per ADMINISTRATEME_BUILD.md §3.4 and SYSTEM_INVARIANTS.md §4, §6. Every
public query takes a ``session: Session`` per [§6.1] and runs through
``scope.filter_rows`` before return.

Per DECISIONS.md §D4: the CRM spine (which includes commitments) is a
shared L3 concern. Any Python product may read these queries via its
local connection.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_commitment(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    commitment_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM commitments WHERE tenant_id = ? AND commitment_id = ?",
        (session.tenant_id, commitment_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def open_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM commitments
         WHERE tenant_id = ?
           AND owed_by_party = ?
           AND status IN ('pending', 'snoozed')
         ORDER BY due_at ASC, proposed_at DESC
        """,
        (session.tenant_id, member_party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def pending_approval(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Commitments awaiting principal approval — status='pending' AND
    not yet confirmed. Once ``confirmed_at`` is populated, a commitment
    still has status='pending' per BUILD.md §3.4's status enum, but it
    has moved past the approval gate."""
    rows = conn.execute(
        """
        SELECT * FROM commitments
         WHERE tenant_id = ?
           AND status = 'pending'
           AND confirmed_at IS NULL
         ORDER BY proposed_at DESC
         LIMIT ?
        """,
        (session.tenant_id, limit),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def by_party(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM commitments
         WHERE tenant_id = ?
           AND (owed_by_party = ? OR owed_to_party = ?)
         ORDER BY proposed_at DESC
        """,
        (session.tenant_id, party_id, party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def by_source_interaction(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    interaction_id: str,
) -> list[dict[str, Any]]:
    """Used by prompt-10b's noise_filtering pipeline to detect proposal
    loops per source interaction."""
    rows = conn.execute(
        """
        SELECT * FROM commitments
         WHERE tenant_id = ? AND source_interaction_id = ?
         ORDER BY proposed_at ASC
        """,
        (session.tenant_id, interaction_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])
