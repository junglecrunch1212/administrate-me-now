"""
Recurrences projection read queries.

Per ADMINISTRATEME_BUILD.md §3.6. Plain query functions; prompt 08 wraps
them with Session / scope enforcement. Every function takes ``tenant_id``
as an explicit required keyword — §12 invariant 1.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import sqlcipher3

from adminme.projections.recurrences.handlers import _parse_iso


# TODO(prompt-08): wrap with Session scope check
def get_recurrence(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    recurrence_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM recurrences WHERE tenant_id = ? AND recurrence_id = ?",
        (tenant_id, recurrence_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def due_within(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
        (tenant_id, cutoff_iso),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def for_member(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
        (tenant_id, member_party_id),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def all_active(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """No status field on recurrences — all rows are active. Ordered by
    next_occurrence ascending."""
    rows = conn.execute(
        "SELECT * FROM recurrences WHERE tenant_id = ? "
        "ORDER BY next_occurrence ASC",
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]
