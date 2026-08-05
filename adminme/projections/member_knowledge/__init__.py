"""Owner-scoped member knowledge projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.member_knowledge.handlers import EVENT_TYPES, apply_event


class MemberKnowledgeProjection(Projection):
    name = "member_knowledge"
    version = 2
    subscribes_to = sorted(EVENT_TYPES)
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(member_knowledge)")}
        additions = {
            "body_format": "TEXT NOT NULL DEFAULT 'plain'",
            "account_external_id": "TEXT", "account_name": "TEXT",
            "folder_external_id": "TEXT", "folder_path": "TEXT",
            "provider_created_at": "TEXT", "provider_modified_at": "TEXT",
            "provider_revision": "TEXT", "locked": "INTEGER NOT NULL DEFAULT 0",
            "shared": "INTEGER NOT NULL DEFAULT 0", "pinned": "INTEGER",
            "tags_json": "TEXT", "attachments_json": "TEXT NOT NULL DEFAULT '[]'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "capability_warnings_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE member_knowledge ADD COLUMN {name} {declaration}")


__all__ = ["MemberKnowledgeProjection"]
