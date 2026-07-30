"""Deterministic, idempotent handlers for owner-scoped member knowledge."""

from __future__ import annotations

from typing import Any


EVENT_TYPES = {
    "member_knowledge.note_added",
    "member_knowledge.note_updated",
    "member_knowledge.note_corrected",
}


def apply_event(envelope: dict[str, Any], conn: Any) -> None:
    if envelope.get("type") not in EVENT_TYPES:
        return
    payload = envelope["payload"]
    member_id = str(payload["member_id"])
    expected_owner = f"private:{member_id}"
    if envelope["owner_scope"] != expected_owner:
        raise ValueError("member knowledge owner_scope must match payload member_id")
    conn.execute(
        """
        INSERT INTO member_knowledge (
            tenant_id, knowledge_id, member_id, owner_scope, visibility_scope,
            sensitivity, title, body, source_kind, source_external_id,
            content_sha256, captured_at, supersedes_event_id, source_adapter,
            source_account_id, correlation_id, causation_id, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, knowledge_id) DO UPDATE SET
            member_id=excluded.member_id,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity,
            title=excluded.title,
            body=excluded.body,
            source_kind=excluded.source_kind,
            source_external_id=excluded.source_external_id,
            content_sha256=excluded.content_sha256,
            captured_at=excluded.captured_at,
            supersedes_event_id=excluded.supersedes_event_id,
            source_adapter=excluded.source_adapter,
            source_account_id=excluded.source_account_id,
            correlation_id=excluded.correlation_id,
            causation_id=excluded.causation_id,
            last_event_id=excluded.last_event_id
        WHERE excluded.last_event_id >= member_knowledge.last_event_id
        """,
        (
            envelope["tenant_id"],
            payload["knowledge_id"],
            member_id,
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope.get("sensitivity", "normal"),
            payload.get("title"),
            payload["body"],
            payload["source_kind"],
            payload["source_external_id"],
            payload["content_sha256"],
            str(payload["captured_at"]),
            payload.get("supersedes_event_id"),
            envelope["source_adapter"],
            envelope["source_account_id"],
            envelope.get("correlation_id"),
            envelope.get("causation_id"),
            envelope["event_id"],
        ),
    )


__all__ = ["EVENT_TYPES", "apply_event"]
