"""
vector_search projection handler — semantic index over non-privileged content.

Per ADMINISTRATEME_BUILD.md §3.10 and SYSTEM_INVARIANTS.md §6.3, §8, §13.8.
Handler is idempotent: re-applying the same event produces the same row
state (§2 invariant 4). Keyed by ``(tenant_id, embedding_id)``.

Subscribed event types:
- ``embedding.generated`` → INSERT/REPLACE into vec0 + sidecar meta
- ``embedding.revoked`` → remove the exact derived vector and sidecar row

[§13.8] privileged content is filtered at write time. Either envelope-
or payload-level ``sensitivity == 'privileged'`` drops the event with an
INFO log before any row lands. Queries require a ``Session`` and reapply
owner/visibility authorization for defense in depth.

[§8] AdministrateMe does not import embedding SDKs. The vector is already
supplied by an authorized domain indexing pipeline with source scope and
provenance. The credential-free fixture uses a deterministic local hash vector.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import sqlcipher3
import sqlite_vec  # type: ignore[import-untyped]

_log = logging.getLogger(__name__)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    if event_type == "embedding.generated":
        apply_embedding_generated(envelope, conn)
    elif event_type == "embedding.revoked":
        apply_embedding_revoked(envelope, conn)


def apply_embedding_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    existing = conn.execute(
        """
        SELECT 1 FROM embeddings_meta
         WHERE tenant_id=? AND embedding_id=?
           AND linked_kind=? AND linked_id=?
        """,
        (
            envelope["tenant_id"],
            p["embedding_id"],
            p["linked_kind"],
            p["linked_id"],
        ),
    ).fetchone()
    if existing is None:
        return
    conn.execute(
        "DELETE FROM vector_index WHERE embedding_id=?",
        (p["embedding_id"],),
    )
    conn.execute(
        "DELETE FROM embeddings_meta WHERE tenant_id=? AND embedding_id=?",
        (envelope["tenant_id"], p["embedding_id"]),
    )


def apply_embedding_generated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]

    # [§13.8] write-time privileged filter. Either envelope OR payload
    # sensitivity being 'privileged' blocks the write.
    envelope_sensitivity = envelope.get("sensitivity", "normal")
    payload_sensitivity = p.get("sensitivity", "normal")
    if envelope_sensitivity == "privileged" or payload_sensitivity == "privileged":
        _log.info(
            "vector_search: privileged embedding skipped per [§13.8] "
            "(embedding_id=%s, linked=%s/%s)",
            p.get("embedding_id"),
            p.get("linked_kind"),
            p.get("linked_id"),
        )
        return

    embedding = p.get("embedding") or []
    dims = int(p.get("embedding_dimensions", 0))
    if len(embedding) != dims:
        _log.warning(
            "vector_search: embedding length %d does not match dims %d; "
            "dropping (embedding_id=%s)",
            len(embedding),
            dims,
            p.get("embedding_id"),
        )
        return

    serialized = sqlite_vec.serialize_float32(embedding)

    # vec0 tables don't support ON CONFLICT — INSERT OR REPLACE is the
    # idempotency idiom. Delete then insert to keep semantics explicit.
    conn.execute(
        "DELETE FROM vector_index WHERE embedding_id = ?",
        (p["embedding_id"],),
    )
    conn.execute(
        """
        INSERT INTO vector_index (
            embedding_id, embedding, linked_kind, linked_id, sensitivity,
            owner_scope, tenant_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["embedding_id"],
            serialized,
            p["linked_kind"],
            p["linked_id"],
            payload_sensitivity,
            envelope["owner_scope"],
            envelope["tenant_id"],
        ),
    )

    conn.execute(
        """
        INSERT INTO embeddings_meta (
            tenant_id, embedding_id, linked_kind, linked_id,
            embedding_dimensions, model_name, sensitivity,
            source_text_sha256, owner_scope, visibility_scope,
            source_event_id, source_adapter, source_account_id,
            correlation_id, causation_id, created_at_ms, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, embedding_id) DO UPDATE SET
            linked_kind          = excluded.linked_kind,
            linked_id            = excluded.linked_id,
            embedding_dimensions = excluded.embedding_dimensions,
            model_name           = excluded.model_name,
            sensitivity          = excluded.sensitivity,
            source_text_sha256   = excluded.source_text_sha256,
            owner_scope          = excluded.owner_scope,
            visibility_scope     = excluded.visibility_scope,
            source_event_id      = excluded.source_event_id,
            source_adapter       = excluded.source_adapter,
            source_account_id    = excluded.source_account_id,
            correlation_id       = excluded.correlation_id,
            causation_id         = excluded.causation_id,
            created_at_ms        = excluded.created_at_ms,
            last_event_id        = excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["embedding_id"],
            p["linked_kind"],
            p["linked_id"],
            dims,
            p["model_name"],
            payload_sensitivity,
            p["source_text_sha256"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope.get("causation_id") or envelope["event_id"],
            envelope["source_adapter"],
            envelope["source_account_id"],
            envelope.get("correlation_id"),
            envelope.get("causation_id"),
            envelope.get("event_at_ms", int(time.time() * 1000)),
            envelope["event_id"],
        ),
    )
