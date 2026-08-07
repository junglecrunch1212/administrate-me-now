"""Scope-explicit read helpers for the member knowledge projection."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
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
    if session.auth_role in {"ambient", "device", "child"}:
        return None
    row = get_for_owner(
        conn,
        tenant_id=session.tenant_id,
        member_id=session.auth_member_id,
        knowledge_id=knowledge_id,
    )
    owned = filter_one(session, row)
    if owned is not None:
        owned["access_mode"] = "owner"
        return owned
    shared = conn.execute(
        """
        SELECT k.*, g.grant_id, g.permissions_json AS grant_permissions_json
          FROM member_knowledge k
          JOIN member_knowledge_grants g
            ON g.tenant_id=k.tenant_id AND g.knowledge_id=k.knowledge_id
         WHERE k.tenant_id=? AND k.knowledge_id=?
           AND g.grantee_member_id=? AND g.revoked_at IS NULL
           AND (g.expires_at IS NULL OR g.expires_at > ?)
           AND g.permissions_json LIKE '%"read"%'
           AND k.sensitivity <> 'privileged'
         ORDER BY g.granted_at DESC LIMIT 1
        """,
        (
            session.tenant_id,
            knowledge_id,
            session.auth_member_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    ).fetchone()
    if shared is None:
        return None
    result = dict(shared)
    result["access_mode"] = "shared_reference"
    result["grant_permissions"] = json.loads(
        result.pop("grant_permissions_json") or "[]"
    )
    return result


def list_visible(
    conn: Any,
    session: Session,
    *,
    include_privileged: bool = True,
) -> list[dict[str, Any]]:
    """Return the authenticated member's private knowledge and nothing else."""

    if session.auth_member_id != session.view_member_id:
        return []
    if session.auth_role in {"ambient", "device", "child"}:
        return []
    rows = list_for_owner(
        conn,
        tenant_id=session.tenant_id,
        member_id=session.auth_member_id,
        include_privileged=include_privileged,
    )
    owned = filter_rows(session, rows)
    for row in owned:
        row["access_mode"] = "owner"
    shared_rows = conn.execute(
        """
        SELECT k.*, g.grant_id, g.permissions_json AS grant_permissions_json
          FROM member_knowledge k
          JOIN member_knowledge_grants g
            ON g.tenant_id=k.tenant_id AND g.knowledge_id=k.knowledge_id
         WHERE k.tenant_id=? AND g.grantee_member_id=?
           AND g.revoked_at IS NULL
           AND (g.expires_at IS NULL OR g.expires_at > ?)
           AND g.permissions_json LIKE '%"read"%'
           AND k.sensitivity <> 'privileged'
         ORDER BY k.captured_at DESC, k.knowledge_id ASC
        """,
        (
            session.tenant_id,
            session.auth_member_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    ).fetchall()
    seen = {str(row["knowledge_id"]) for row in owned}
    for raw in shared_rows:
        row = dict(raw)
        if str(row["knowledge_id"]) in seen:
            continue
        row["access_mode"] = "shared_reference"
        row["grant_permissions"] = json.loads(row.pop("grant_permissions_json") or "[]")
        owned.append(row)
    return owned


def grants_for_note(
    conn: Any, session: Session, *, knowledge_id: str
) -> list[dict[str, Any]]:
    """List active and revoked grants only for the authenticated owner."""

    if session.auth_member_id != session.view_member_id:
        return []
    if session.auth_role in {"ambient", "device", "child"}:
        return []
    rows = conn.execute(
        """
        SELECT * FROM member_knowledge_grants
         WHERE tenant_id=? AND knowledge_id=? AND owner_member_id=?
         ORDER BY granted_at DESC, grant_id ASC
        """,
        (session.tenant_id, knowledge_id, session.auth_member_id),
    ).fetchall()
    values: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        row["permissions"] = json.loads(row.pop("permissions_json") or "[]")
        values.append(row)
    return values


_SEARCH_TOKEN = re.compile(r"[\w'-]+", re.UNICODE)


def _fts_query(value: str) -> str:
    tokens = [token.replace('"', '""') for token in _SEARCH_TOKEN.findall(value)]
    return " AND ".join(f'"{token}"' for token in tokens[:24])


def search_visible(
    conn: Any,
    session: Session,
    *,
    query: str,
    limit: int = 25,
    source_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministic full-text search over owned and explicitly shared notes."""

    if session.auth_member_id != session.view_member_id:
        return []
    if session.auth_role in {"ambient", "device", "child"}:
        return []
    expression = _fts_query(query)
    if not expression:
        return []
    source_clause = " AND k.source_kind=?" if source_kind else ""
    params: list[Any] = [
        session.auth_member_id,
        session.auth_member_id,
        session.auth_member_id,
        datetime.now(timezone.utc).isoformat(),
        session.tenant_id,
        expression,
    ]
    if source_kind:
        params.append(source_kind)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT k.*, bm25(member_knowledge_fts) AS search_rank,
               CASE WHEN k.member_id=? THEN 'owner' ELSE 'shared_reference' END
                    AS access_mode
          FROM member_knowledge_fts
          JOIN member_knowledge k
            ON k.tenant_id=member_knowledge_fts.tenant_id
           AND k.knowledge_id=member_knowledge_fts.knowledge_id
         WHERE (
             k.member_id=? OR EXISTS (
                 SELECT 1 FROM member_knowledge_grants g
                  WHERE g.tenant_id=k.tenant_id
                    AND g.knowledge_id=k.knowledge_id
                    AND g.grantee_member_id=?
                    AND g.revoked_at IS NULL
                    AND (g.expires_at IS NULL OR g.expires_at > ?)
                    AND g.permissions_json LIKE '%"search"%'
             )
         )
           AND k.tenant_id=?
           AND k.sensitivity <> 'privileged'
           AND member_knowledge_fts MATCH ?
           {source_clause}
         ORDER BY search_rank, k.provider_modified_at DESC, k.captured_at DESC
         LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "get_for_owner",
    "get_visible",
    "grants_for_note",
    "list_for_owner",
    "list_visible",
    "search_visible",
]
