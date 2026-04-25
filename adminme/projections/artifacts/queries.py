"""
Artifacts projection read queries.

Per ADMINISTRATEME_BUILD.md §3.3 and SYSTEM_INVARIANTS.md §3, §6. Every
public query takes a ``session: Session`` per [§6.1] and runs through
``scope.filter_rows`` before return.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_artifact(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    artifact_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id = ? AND artifact_id = ?",
        (session.tenant_id, artifact_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def search_by_sha256(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    sha256: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id = ? AND sha256 = ?",
        (session.tenant_id, sha256),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def list_recent(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id = ? "
        "ORDER BY captured_at DESC LIMIT ?",
        (session.tenant_id, limit),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])
