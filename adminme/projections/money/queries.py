"""
money projection read queries.

Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2, §6, §12.
Every public query takes a ``session: Session`` per [§6.1] and runs
through ``scope.filter_rows`` before return. Coach-role sessions strip
``financial_*`` columns inside ``filter_rows`` per [§13] / DIAGRAMS §5.

Per DECISIONS.md §D4: the CRM/ops spine (which includes money) is a
shared L3 concern. Queries exclude soft-deleted rows (``deleted_at IS
NOT NULL``) unless a caller explicitly wants them.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_money_flow(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    flow_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM money_flows WHERE tenant_id = ? AND flow_id = ?",
        (session.tenant_id, flow_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def flows_in_range(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    start_iso: str,
    end_iso: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM money_flows
         WHERE tenant_id = ?
           AND occurred_at BETWEEN ? AND ?
           AND deleted_at IS NULL
         ORDER BY occurred_at DESC
        """,
        (session.tenant_id, start_iso, end_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def flows_by_category(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    category: str,
    since_iso: str | None = None,
) -> list[dict[str, Any]]:
    if since_iso is None:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND category = ?
               AND deleted_at IS NULL
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, category),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND category = ?
               AND occurred_at >= ?
               AND deleted_at IS NULL
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, category, since_iso),
        ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def flows_by_account(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    account_id: str,
    since_iso: str | None = None,
) -> list[dict[str, Any]]:
    if since_iso is None:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND linked_account = ?
               AND deleted_at IS NULL
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, account_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND linked_account = ?
               AND occurred_at >= ?
               AND deleted_at IS NULL
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, account_id, since_iso),
        ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def category_totals(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    since_iso: str,
) -> dict[str, int]:
    """Sum ``amount_minor`` by category, restricted to rows the session
    may read (privileged-other rows are excluded from the total)."""
    rows = conn.execute(
        """
        SELECT * FROM money_flows
         WHERE tenant_id = ?
           AND occurred_at >= ?
           AND deleted_at IS NULL
           AND category IS NOT NULL
        """,
        (session.tenant_id, since_iso),
    ).fetchall()
    visible = filter_rows(session, [dict(r) for r in rows])
    totals: dict[str, int] = {}
    for r in visible:
        cat = r.get("category")
        if cat is None:
            continue
        totals[cat] = totals.get(cat, 0) + int(r.get("amount_minor") or 0)
    return totals


def manual_flows(
    conn: sqlcipher3.Connection,
    session: Session,
) -> list[dict[str, Any]]:
    """Manually-added, not-yet-deleted flows. Used by prompt 07b to avoid
    double-counting when it regenerates the Raw Data sheet."""
    rows = conn.execute(
        """
        SELECT * FROM money_flows
         WHERE tenant_id = ?
           AND is_manual = 1
           AND deleted_at IS NULL
         ORDER BY occurred_at DESC
        """,
        (session.tenant_id,),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])
