"""Scope-explicit read helpers for the member knowledge projection."""

from __future__ import annotations

from typing import Any


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


__all__ = ["get_for_owner", "list_for_owner"]
