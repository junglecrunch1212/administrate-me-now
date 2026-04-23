"""
vector_search projection read queries.

Per ADMINISTRATEME_BUILD.md §3.10 and SYSTEM_INVARIANTS.md §6.3, §8, §12,
§13.8. Plain query functions; prompt 08 wraps them with Session / scope
enforcement. Every function takes ``tenant_id`` as an explicit required
keyword — §12 invariant 1.

``nearest`` filters privileged rows at read time as belt-and-braces
alongside the handler's write-time filter. Prompt 08 extends this with
full scope enforcement (``visibility_scope`` in the session's allowed
scopes, ``owner_scope`` checks).
"""

from __future__ import annotations

from typing import Any

import sqlcipher3
import sqlite_vec  # type: ignore[import-untyped]


# TODO(prompt-08): wrap with Session scope check
def get_embedding_meta(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    embedding_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM embeddings_meta WHERE tenant_id = ? AND embedding_id = ?",
        (tenant_id, embedding_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def nearest(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    query_vector: list[float],
    k: int = 10,
    exclude_sensitivity: str = "privileged",
) -> list[dict[str, Any]]:
    """k-nearest neighbors for ``query_vector``. Joins with ``embeddings_meta``
    to surface linked_kind/linked_id. Excludes rows where
    ``sensitivity = exclude_sensitivity`` per [§13.8]."""
    serialized = sqlite_vec.serialize_float32(query_vector)
    rows = conn.execute(
        """
        SELECT
            vi.embedding_id    AS embedding_id,
            em.linked_kind     AS linked_kind,
            em.linked_id       AS linked_id,
            vi.distance        AS distance
          FROM vector_index AS vi
          JOIN embeddings_meta AS em
            ON em.embedding_id = vi.embedding_id
           AND em.tenant_id    = vi.tenant_id
         WHERE vi.tenant_id  = ?
           AND vi.sensitivity != ?
           AND vi.embedding MATCH ?
           AND k = ?
         ORDER BY vi.distance
        """,
        (tenant_id, exclude_sensitivity, serialized, k),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def embeddings_for_link(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    linked_kind: str,
    linked_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM embeddings_meta
         WHERE tenant_id   = ?
           AND linked_kind = ?
           AND linked_id   = ?
        """,
        (tenant_id, linked_kind, linked_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def count_embeddings(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
) -> int:
    row = conn.execute(
        "SELECT count(*) FROM embeddings_meta WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    return int(row[0])
