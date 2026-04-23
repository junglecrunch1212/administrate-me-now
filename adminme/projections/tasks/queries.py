"""
Tasks projection read queries.

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13. Plain
query functions; prompt 08 wraps them with Session / scope enforcement.
Every function takes ``tenant_id`` as an explicit required keyword —
§12 invariant 1, no global tenant context.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3


# TODO(prompt-08): wrap with Session scope check
def get_task(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM tasks WHERE tenant_id = ? AND task_id = ?",
        (tenant_id, task_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def today_for_member(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    member_party_id: str,
    today_iso: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ?
           AND (assignee_party = ? OR assignee_party IS NULL)
           AND status NOT IN ('done', 'dismissed', 'deferred')
           AND ((due_date IS NOT NULL AND due_date <= ?)
                OR status = 'in_progress')
         ORDER BY due_date ASC, created_at ASC
        """,
        (tenant_id, member_party_id, today_iso),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def open_for_member(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    member_party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ?
           AND (assignee_party = ? OR assignee_party IS NULL)
           AND status IN ('inbox', 'next', 'in_progress', 'waiting_on')
         ORDER BY due_date ASC, created_at ASC
        """,
        (tenant_id, member_party_id),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def by_context(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    domain: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND domain = ?
         ORDER BY due_date ASC, created_at DESC
        """,
        (tenant_id, domain),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def in_status(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    status: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND status = ?
         ORDER BY due_date ASC, created_at DESC
        """,
        (tenant_id, status),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
# TODO(prompt-10c): whatnow pipeline uses this for goal-DAG ranking
def sub_tasks_of(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    goal_ref_task_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND goal_ref = ?
         ORDER BY created_at ASC
        """,
        (tenant_id, goal_ref_task_id),
    ).fetchall()
    return [dict(r) for r in rows]
