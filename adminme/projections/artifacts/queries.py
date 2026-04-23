"""
Artifacts projection read queries.

Per ADMINISTRATEME_BUILD.md §3.3. Plain query functions; prompt 08 wraps
them with Session / scope enforcement.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3


# TODO(prompt-08): wrap with Session scope check
def get_artifact(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id = ? AND artifact_id = ?",
        (tenant_id, artifact_id),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


# TODO(prompt-08): wrap with Session scope check
def search_by_sha256(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    sha256: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id = ? AND sha256 = ?",
        (tenant_id, sha256),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def list_recent(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id = ? "
        "ORDER BY captured_at DESC LIMIT ?",
        (tenant_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
