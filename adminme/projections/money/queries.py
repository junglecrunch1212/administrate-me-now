"""
money projection read queries.

Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2, §12. Plain
query functions; prompt 08 wraps them with Session / scope enforcement.
Every function takes ``tenant_id`` as an explicit required keyword — §12
invariant 1, no global tenant context.

Per DECISIONS.md §D4: the CRM/ops spine (which includes money) is a
shared L3 concern. Queries exclude soft-deleted rows (``deleted_at IS
NOT NULL``) unless a caller explicitly wants them.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3


# TODO(prompt-08): wrap with Session scope check
def get_money_flow(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    flow_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM money_flows WHERE tenant_id = ? AND flow_id = ?",
        (tenant_id, flow_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def flows_in_range(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
        (tenant_id, start_iso, end_iso),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def flows_by_category(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
            (tenant_id, category),
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
            (tenant_id, category, since_iso),
        ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def flows_by_account(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
            (tenant_id, account_id),
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
            (tenant_id, account_id, since_iso),
        ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def category_totals(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    since_iso: str,
) -> dict[str, int]:
    """Sum ``amount_minor`` by category, excluding deleted rows and
    rows with NULL category."""
    rows = conn.execute(
        """
        SELECT category, SUM(amount_minor) AS total_minor
          FROM money_flows
         WHERE tenant_id = ?
           AND occurred_at >= ?
           AND deleted_at IS NULL
           AND category IS NOT NULL
         GROUP BY category
        """,
        (tenant_id, since_iso),
    ).fetchall()
    return {r["category"]: int(r["total_minor"]) for r in rows}


# TODO(prompt-08): wrap with Session scope check
def manual_flows(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]
