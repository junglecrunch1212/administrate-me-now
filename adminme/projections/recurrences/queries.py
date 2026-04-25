"""
Recurrences projection read queries.

Per ADMINISTRATEME_BUILD.md §3.6 and SYSTEM_INVARIANTS.md §4, §6. Every
public query takes a ``session: Session`` per [§6.1] and runs through
``scope.filter_rows`` before return.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session
from adminme.projections.recurrences.handlers import _parse_iso


def get_recurrence(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    recurrence_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM recurrences WHERE tenant_id = ? AND recurrence_id = ?",
        (session.tenant_id, recurrence_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def due_within(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    days: int,
    as_of_iso: str,
) -> list[dict[str, Any]]:
    """Return recurrences firing within ``days`` days of ``as_of_iso``,
    ordered by next_occurrence ascending."""
    cutoff_dt = _parse_iso(as_of_iso) + timedelta(days=days)
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT * FROM recurrences
         WHERE tenant_id = ?
           AND next_occurrence <= ?
         ORDER BY next_occurrence ASC
        """,
        (session.tenant_id, cutoff_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
) -> list[dict[str, Any]]:
    """Recurrences linked directly to ``member_party_id`` or to the
    household (linked_kind='household')."""
    rows = conn.execute(
        """
        SELECT * FROM recurrences
         WHERE tenant_id = ?
           AND ((linked_kind = 'party' AND linked_id = ?)
                OR linked_kind = 'household')
         ORDER BY next_occurrence ASC
        """,
        (session.tenant_id, member_party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def all_active(
    conn: sqlcipher3.Connection,
    session: Session,
) -> list[dict[str, Any]]:
    """No status field on recurrences — all rows are active. Ordered by
    next_occurrence ascending."""
    rows = conn.execute(
        "SELECT * FROM recurrences WHERE tenant_id = ? "
        "ORDER BY next_occurrence ASC",
        (session.tenant_id,),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])
