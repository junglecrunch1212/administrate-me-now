"""Deterministic, idempotent handlers for owner-scoped member knowledge."""

from __future__ import annotations

import json
from typing import Any


NOTE_EVENTS = {
    "member_knowledge.note_added",
    "member_knowledge.note_updated",
    "member_knowledge.note_corrected",
}
FOLDER_EVENTS = {
    "member_knowledge.folder_created",
    "member_knowledge.folder_updated",
    "member_knowledge.folder_deleted",
}
EVENT_TYPES = NOTE_EVENTS | FOLDER_EVENTS | {"member_knowledge.note_deleted"}
EVENT_TYPES |= {
    "member_knowledge.access_granted",
    "member_knowledge.access_revoked",
}


def _private(envelope: dict[str, Any], member_id: str) -> None:
    expected = f"private:{member_id}"
    if envelope["owner_scope"] != expected or envelope["visibility_scope"] != expected:
        raise ValueError(
            "owner_scope and visibility_scope must match the payload member_id"
        )


def apply_event(envelope: dict[str, Any], conn: Any) -> None:
    event_type = envelope.get("type")
    if event_type not in EVENT_TYPES:
        return
    payload = envelope["payload"]
    member_id = str(payload.get("member_id") or payload.get("owner_member_id") or "")
    if not member_id:
        raise ValueError("member knowledge events require an owning member")
    _private(envelope, member_id)
    if event_type in NOTE_EVENTS:
        apply_note(envelope, conn)
    elif event_type == "member_knowledge.note_deleted":
        invalidate_grants(
            envelope,
            conn,
            knowledge_id=str(payload["knowledge_id"]),
            owner_member_id=member_id,
            revoked_by="system:source-deleted",
        )
        conn.execute(
            "DELETE FROM member_knowledge WHERE tenant_id=? AND knowledge_id=? AND member_id=?",
            (envelope["tenant_id"], payload["knowledge_id"], member_id),
        )
        conn.execute(
            "DELETE FROM member_knowledge_fts WHERE tenant_id=? AND knowledge_id=?",
            (envelope["tenant_id"], payload["knowledge_id"]),
        )
    elif event_type == "member_knowledge.access_granted":
        apply_grant(envelope, conn)
    elif event_type == "member_knowledge.access_revoked":
        conn.execute(
            """
            UPDATE member_knowledge_grants
               SET revoked_at=?, revoked_by_member_id=?, last_event_id=?
             WHERE tenant_id=? AND grant_id=? AND owner_member_id=?
               AND last_event_id <= ?
            """,
            (
                str(payload["revoked_at"]),
                payload["revoked_by_member_id"],
                envelope["event_id"],
                envelope["tenant_id"],
                payload["grant_id"],
                member_id,
                envelope["event_id"],
            ),
        )
    elif event_type == "member_knowledge.folder_deleted":
        conn.execute(
            "DELETE FROM member_knowledge_folders WHERE tenant_id=? AND member_id=? AND source_kind=? AND folder_external_id=?",
            (
                envelope["tenant_id"],
                member_id,
                payload["source_kind"],
                payload["folder_external_id"],
            ),
        )
    else:
        apply_folder(envelope, conn)


def apply_note(envelope: dict[str, Any], conn: Any) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO member_knowledge (
            tenant_id, knowledge_id, member_id, owner_scope, visibility_scope,
            sensitivity, title, body, body_format, source_type, source_kind,
            source_external_id, source_url,
            account_external_id, account_name, folder_external_id, folder_path,
            content_sha256, captured_at, provider_created_at, provider_modified_at,
            provider_revision, locked, shared, pinned, tags_json, attachments_json,
            interaction_id, metadata_json, capability_warnings_json, supersedes_event_id,
            source_adapter, source_account_id, correlation_id, causation_id,
            last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, knowledge_id) DO UPDATE SET
            member_id=excluded.member_id, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope, sensitivity=excluded.sensitivity,
            title=excluded.title, body=excluded.body, body_format=excluded.body_format,
            source_type=excluded.source_type,
            source_kind=excluded.source_kind, source_external_id=excluded.source_external_id,
            source_url=excluded.source_url,
            account_external_id=excluded.account_external_id, account_name=excluded.account_name,
            folder_external_id=excluded.folder_external_id, folder_path=excluded.folder_path,
            content_sha256=excluded.content_sha256, captured_at=excluded.captured_at,
            provider_created_at=excluded.provider_created_at,
            provider_modified_at=excluded.provider_modified_at,
            provider_revision=excluded.provider_revision, locked=excluded.locked,
            shared=excluded.shared, pinned=excluded.pinned, tags_json=excluded.tags_json,
            attachments_json=excluded.attachments_json, metadata_json=excluded.metadata_json,
            interaction_id=excluded.interaction_id,
            capability_warnings_json=excluded.capability_warnings_json,
            supersedes_event_id=excluded.supersedes_event_id,
            source_adapter=excluded.source_adapter,
            source_account_id=excluded.source_account_id,
            correlation_id=excluded.correlation_id, causation_id=excluded.causation_id,
            last_event_id=excluded.last_event_id
        WHERE excluded.last_event_id >= member_knowledge.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["knowledge_id"],
            p["member_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope.get("sensitivity", "normal"),
            p.get("title"),
            p["body"],
            p.get("body_format", "plain"),
            p.get("source_type", "note"),
            p["source_kind"],
            p["source_external_id"],
            p.get("source_url"),
            p.get("account_external_id"),
            p.get("account_name"),
            p.get("folder_external_id"),
            p.get("folder_path"),
            p["content_sha256"],
            str(p["captured_at"]),
            str(p.get("created_at") or "") or None,
            str(p.get("provider_modified_at") or "") or None,
            p.get("provider_revision"),
            1 if p.get("locked") else 0,
            1 if p.get("shared") else 0,
            None if p.get("pinned") is None else (1 if p.get("pinned") else 0),
            None
            if p.get("tags") is None
            else json.dumps(p["tags"], separators=(",", ":")),
            json.dumps(p.get("attachments") or [], separators=(",", ":")),
            p.get("interaction_id"),
            json.dumps(p.get("metadata") or {}, separators=(",", ":")),
            json.dumps(p.get("capability_warnings") or [], separators=(",", ":")),
            p.get("supersedes_event_id"),
            envelope["source_adapter"],
            envelope["source_account_id"],
            envelope.get("correlation_id"),
            envelope.get("causation_id"),
            envelope["event_id"],
        ),
    )
    conn.execute(
        "DELETE FROM member_knowledge_fts WHERE tenant_id=? AND knowledge_id=?",
        (envelope["tenant_id"], p["knowledge_id"]),
    )
    if envelope.get("sensitivity", "normal") != "privileged":
        conn.execute(
            """
            INSERT INTO member_knowledge_fts (
                tenant_id, knowledge_id, member_id, title, body, folder_path, tags
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                envelope["tenant_id"],
                p["knowledge_id"],
                p["member_id"],
                p.get("title") or "",
                p["body"],
                p.get("folder_path") or "",
                " ".join(str(value) for value in p.get("tags") or []),
            ),
        )
    else:
        invalidate_grants(
            envelope,
            conn,
            knowledge_id=str(p["knowledge_id"]),
            owner_member_id=str(p["member_id"]),
            revoked_by="system:privileged-reclassification",
        )


def invalidate_grants(
    envelope: dict[str, Any],
    conn: Any,
    *,
    knowledge_id: str,
    owner_member_id: str,
    revoked_by: str,
) -> None:
    """Make source-loss privacy revocation durable in the rebuilt projection."""

    conn.execute(
        """
        UPDATE member_knowledge_grants
           SET revoked_at=?, revoked_by_member_id=?, last_event_id=?
         WHERE tenant_id=? AND knowledge_id=? AND owner_member_id=?
           AND revoked_at IS NULL AND last_event_id <= ?
        """,
        (
            envelope["occurred_at"],
            revoked_by,
            envelope["event_id"],
            envelope["tenant_id"],
            knowledge_id,
            owner_member_id,
            envelope["event_id"],
        ),
    )


def apply_grant(envelope: dict[str, Any], conn: Any) -> None:
    p = envelope["payload"]
    if p["owner_member_id"] == p["grantee_member_id"]:
        raise ValueError("a member cannot grant their own note back to themselves")
    note = conn.execute(
        """
        SELECT sensitivity FROM member_knowledge
         WHERE tenant_id=? AND knowledge_id=? AND member_id=?
        """,
        (envelope["tenant_id"], p["knowledge_id"], p["owner_member_id"]),
    ).fetchone()
    if note is None:
        raise ValueError("a grant requires an existing owner-scoped note")
    if str(note["sensitivity"]) == "privileged":
        raise ValueError("privileged notes cannot be shared")
    permissions = sorted(set(str(value) for value in p["permissions"]))
    conn.execute(
        """
        INSERT INTO member_knowledge_grants (
            tenant_id, grant_id, knowledge_id, owner_member_id,
            grantee_member_id, permissions_json, granted_at, expires_at,
            owner_scope, last_event_id, revoked_at, revoked_by_member_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)
        ON CONFLICT(tenant_id, grant_id) DO UPDATE SET
            knowledge_id=excluded.knowledge_id,
            owner_member_id=excluded.owner_member_id,
            grantee_member_id=excluded.grantee_member_id,
            permissions_json=excluded.permissions_json,
            granted_at=excluded.granted_at,
            expires_at=excluded.expires_at,
            owner_scope=excluded.owner_scope,
            last_event_id=excluded.last_event_id,
            revoked_at=NULL,
            revoked_by_member_id=NULL
        WHERE excluded.last_event_id >= member_knowledge_grants.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["grant_id"],
            p["knowledge_id"],
            p["owner_member_id"],
            p["grantee_member_id"],
            json.dumps(permissions, separators=(",", ":")),
            str(p["granted_at"]),
            str(p.get("expires_at") or "") or None,
            envelope["owner_scope"],
            envelope["event_id"],
        ),
    )


def apply_folder(envelope: dict[str, Any], conn: Any) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO member_knowledge_folders (
            tenant_id, member_id, source_kind, account_external_id,
            folder_external_id, folder_path, parent_folder_external_id,
            changed_at, owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, member_id, source_kind, folder_external_id) DO UPDATE SET
            account_external_id=excluded.account_external_id,
            folder_path=excluded.folder_path,
            parent_folder_external_id=excluded.parent_folder_external_id,
            changed_at=excluded.changed_at, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["member_id"],
            p["source_kind"],
            p.get("account_external_id"),
            p["folder_external_id"],
            p["folder_path"],
            p.get("parent_folder_external_id"),
            str(p["changed_at"]),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


__all__ = ["EVENT_TYPES", "apply_event"]
