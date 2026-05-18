"""
Lists projection read queries.

Per SYSTEM_INVARIANTS.md §6.1 every public query takes a ``session:
Session`` and routes results through ``filter_rows`` / ``filter_one``
[§6.4]. Child sessions also drop rows whose ``tags`` overlap
``CHILD_FORBIDDEN_TAGS`` — handled inside ``filter_rows``.

Four queries:
- ``get_item``: single row by composite key (minus tenant — derived from
  the session).
- ``items_for_list``: all items in one source-list.
- ``items_for_member``: all items owned by a member across lists.
- ``open_items_for_member``: subset where ``completed = 0``.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_item(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    list_external_id: str,
    item_external_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM list_items
         WHERE tenant_id = ?
           AND list_external_id = ?
           AND item_external_id = ?
        """,
        (session.tenant_id, list_external_id, item_external_id),
    ).fetchone()
    # filter_one short-circuits None and applies scope discipline per [§6.1].
    return filter_one(session, dict(row) if row is not None else None)


def items_for_list(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    list_external_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM list_items
         WHERE tenant_id = ? AND list_external_id = ?
         ORDER BY due_at IS NULL, due_at ASC, last_modified_at ASC
        """,
        (session.tenant_id, list_external_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def items_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM list_items
         WHERE tenant_id = ? AND member_id = ?
         ORDER BY due_at IS NULL, due_at ASC, last_modified_at ASC
        """,
        (session.tenant_id, member_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def open_items_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM list_items
         WHERE tenant_id = ? AND member_id = ? AND completed = 0
         ORDER BY due_at IS NULL, due_at ASC, last_modified_at ASC
        """,
        (session.tenant_id, member_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


__all__ = [
    "get_item",
    "items_for_list",
    "items_for_member",
    "open_items_for_member",
]
