"""
Tasks projection read queries.

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §6, §13.
Every public query takes a ``session: Session`` per [§6.1] and runs
through ``scope.filter_rows`` before return. Child sessions also drop
rows whose ``tags`` overlap ``CHILD_FORBIDDEN_TAGS``
[CONSOLE_PATTERNS.md §6/§7] — handled inside ``filter_rows``.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_task(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    task_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM tasks WHERE tenant_id = ? AND task_id = ?",
        (session.tenant_id, task_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def today_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
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
        (session.tenant_id, member_party_id, today_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def open_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
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
        (session.tenant_id, member_party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def by_context(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    domain: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND domain = ?
         ORDER BY due_date ASC, created_at DESC
        """,
        (session.tenant_id, domain),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def in_status(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    status: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND status = ?
         ORDER BY due_date ASC, created_at DESC
        """,
        (session.tenant_id, status),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


# TODO(prompt-10c): whatnow pipeline uses this for goal-DAG ranking
def sub_tasks_of(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    goal_ref_task_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND goal_ref = ?
         ORDER BY created_at ASC
        """,
        (session.tenant_id, goal_ref_task_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])
