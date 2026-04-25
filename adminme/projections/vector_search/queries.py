"""
vector_search projection read queries.

Per ADMINISTRATEME_BUILD.md §3.10 and SYSTEM_INVARIANTS.md §6.9, §8, §12,
§13.8/§13.9. Every public query takes a ``session: Session`` per [§6.1].

Special case [§13.9] (UT-8 carve-out): privileged content NEVER enters
vector_search at all — the handler filters at write time, and ``nearest``
re-asserts the exclusion at read time. If a query asks for
privileged-owned content, ``nearest`` returns empty (does NOT raise) so
that callers downstream of a coach context build cannot inadvertently
trigger a ScopeViolation that leaks "the row exists but you can't see
it" through error semantics.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3
import sqlite_vec  # type: ignore[import-untyped]

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_embedding_meta(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    embedding_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM embeddings_meta WHERE tenant_id = ? AND embedding_id = ?",
        (session.tenant_id, embedding_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def nearest(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    query_vector: list[float],
    k: int = 10,
) -> list[dict[str, Any]]:
    """k-nearest neighbors for ``query_vector``.

    Three layers of privileged exclusion combine here per [§13.9] /
    UT-8 (vector-search is always privileged-free regardless of session
    scope):
      1. The handler refuses to insert privileged rows at write time.
      2. The SQL filter excludes any sensitivity != 'normal' row at
         read time, regardless of session role.
      3. The Python ``filter_rows`` post-filter is a final canary; if
         a privileged row somehow leaked into the index, scope.allowed_read
         drops it.

    If the session has no allowed_scopes (ambient), returns empty.
    """
    # Defense-in-depth: ambient sessions cannot read vector_search [§6.2].
    if not session.allowed_scopes:
        return []

    serialized = sqlite_vec.serialize_float32(query_vector)
    # Hardcoded 'privileged' exclusion at SQL level — this is the
    # belt-and-braces alongside the handler's write-time filter
    # [§6.9, §13.9]. The exclusion is NOT session-controlled — even a
    # principal-as-owner cannot pull privileged content through
    # vector_search; that path goes through the relevant projection
    # directly.
    rows = conn.execute(
        """
        SELECT
            vi.embedding_id    AS embedding_id,
            em.linked_kind     AS linked_kind,
            em.linked_id       AS linked_id,
            vi.distance        AS distance,
            vi.sensitivity     AS sensitivity,
            vi.owner_scope     AS owner_scope,
            'shared:household' AS visibility_scope
          FROM vector_index AS vi
          JOIN embeddings_meta AS em
            ON em.embedding_id = vi.embedding_id
           AND em.tenant_id    = vi.tenant_id
         WHERE vi.tenant_id  = ?
           AND vi.sensitivity != 'privileged'
           AND vi.embedding MATCH ?
           AND k = ?
         ORDER BY vi.distance
        """,
        (session.tenant_id, serialized, k),
    ).fetchall()
    visible = filter_rows(session, [dict(r) for r in rows])
    # Strip the synthesized columns we only added for the scope filter.
    out: list[dict[str, Any]] = []
    for r in visible:
        slim = {k: v for k, v in r.items() if k not in {"visibility_scope"}}
        out.append(slim)
    return out


def embeddings_for_link(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
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
        (session.tenant_id, linked_kind, linked_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def count_embeddings(
    conn: sqlcipher3.Connection,
    session: Session,
) -> int:
    """Count of all (non-privileged) embeddings for the session's tenant.
    The handler never inserts privileged rows so this matches the
    physical row count, but we use a session-scoped query for symmetry
    with the rest of the projection's read surface."""
    row = conn.execute(
        "SELECT count(*) FROM embeddings_meta "
        "WHERE tenant_id = ? AND sensitivity != 'privileged'",
        (session.tenant_id,),
    ).fetchone()
    return int(row[0])
