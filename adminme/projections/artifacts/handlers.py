"""
Artifacts projection handlers.

Per ADMINISTRATEME_BUILD.md §3.3 and SYSTEM_INVARIANTS.md §3 invariant 6.

v1 subscribes only to ``artifact.received``; later adapter prompts add more
event types. ``artifact_links`` is created empty and wired up in prompt 06.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    if envelope["type"] != "artifact.received":
        return
    _on_artifact_received(envelope, conn)


def _artifact_id_for(envelope: dict[str, Any]) -> str:
    return f"art_{envelope['event_id']}"


def _on_artifact_received(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
) -> None:
    p = envelope["payload"]
    artifact_id = _artifact_id_for(envelope)
    conn.execute(
        """
        INSERT INTO artifacts (
            artifact_id, tenant_id, mime_type, byte_size, sha256,
            source_adapter, storage_ref, title,
            extracted_text, extracted_structured_json, extracted_structured_kind,
            captured_at, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, artifact_id) DO UPDATE SET
            mime_type        = excluded.mime_type,
            byte_size        = excluded.byte_size,
            sha256           = excluded.sha256,
            source_adapter   = excluded.source_adapter,
            storage_ref      = excluded.storage_ref,
            title            = excluded.title,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            artifact_id,
            envelope["tenant_id"],
            p["mime_type"],
            p["size_bytes"],
            p["sha256"],
            envelope["source_adapter"],
            p["artifact_ref"],
            p.get("filename"),
            None,
            None,
            None,
            p.get("received_at") or envelope["occurred_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
