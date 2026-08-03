"""Scope-explicit read helpers for the member knowledge projection."""

from __future__ import annotations

from typing import Any

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_for_owner(
    conn: Any, *, tenant_id: str, member_id: str, knowledge_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM member_knowledge "
        "WHERE tenant_id=? AND member_id=? AND knowledge_id=?",
        (tenant_id, member_id, knowledge_id),
    ).fetchone()
    return dict(row) if row is not None else None


def list_for_owner(
    conn: Any,
    *,
    tenant_id: str,
    member_id: str,
    include_privileged: bool = False,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM member_knowledge WHERE tenant_id=? AND member_id=?"
    params: list[Any] = [tenant_id, member_id]
    if not include_privileged:
        sql += " AND sensitivity <> 'privileged'"
    sql += " ORDER BY captured_at DESC, knowledge_id ASC"
    return [dict(row) for row in conn.execute(sql, params)]


def get_visible(
    conn: Any,
    session: Session,
    *,
    knowledge_id: str,
) -> dict[str, Any] | None:
    """Read one record only for the authenticated owning member.

    Private member knowledge deliberately does not inherit the general
    principal ``view-as`` behavior. A principal may view another member's
    household surfaces, but that never grants access to the member's private
    Notes, Voice Memos, or Obsidian material.
    """

    if session.auth_member_id != session.view_member_id:
        return None
    if session.auth_role in {"ambient", "device"}:
        return None
    row = get_for_owner(
        conn,
        tenant_id=session.tenant_id,
        member_id=session.auth_member_id,
        knowledge_id=knowledge_id,
    )
    return filter_one(session, row)


def list_visible(
    conn: Any,
    session: Session,
    *,
    include_privileged: bool = True,
) -> list[dict[str, Any]]:
    """Return the authenticated member's private knowledge and nothing else."""

    if session.auth_member_id != session.view_member_id:
        return []
    if session.auth_role in {"ambient", "device"}:
        return []
    rows = list_for_owner(
        conn,
        tenant_id=session.tenant_id,
        member_id=session.auth_member_id,
        include_privileged=include_privileged,
    )
    return filter_rows(session, rows)


__all__ = [
    "get_for_owner",
    "get_visible",
    "list_for_owner",
    "list_visible",
]
