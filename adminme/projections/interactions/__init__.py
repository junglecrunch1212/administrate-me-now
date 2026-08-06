"""
Interactions projection — deduplicated touchpoints.

Per ADMINISTRATEME_BUILD.md §3.2 and SYSTEM_INVARIANTS.md §3 invariant 5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.interactions import handlers


class InteractionsProjection(Projection):
    name = "interactions"
    version = 4
    subscribes_to = [
        "messaging.received",
        "messaging.sent",
        "telephony.sms_received",
        "interaction.participant_resolved",
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "communication.state_changed",
        "external_sync.write_requested",
        "external_sync.write_completed",
        "external_sync.write_failed",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(interactions)")
        }
        additions = {
            "native_message_id": "TEXT",
            "classification": "TEXT",
            "classification_confidence": "REAL",
            "body_text": "TEXT",
            "body_preview": "TEXT",
            "member_id": "TEXT",
            "provider": "TEXT",
            "provider_account_id": "TEXT",
            "workspace": "TEXT NOT NULL DEFAULT 'personal'",
            "unread": "INTEGER NOT NULL DEFAULT 0",
            "archived": "INTEGER NOT NULL DEFAULT 0",
            "starred": "INTEGER NOT NULL DEFAULT 0",
            "important": "INTEGER NOT NULL DEFAULT 0",
            "snoozed_until": "TEXT",
            "quieted": "INTEGER NOT NULL DEFAULT 0",
            "rfc_message_id": "TEXT",
            "reply_to_identifier": "TEXT",
            "provider_labels": "TEXT NOT NULL DEFAULT '[]'",
            "attachment_metadata": "TEXT NOT NULL DEFAULT '[]'",
            "command_state": "TEXT",
            "last_command_id": "TEXT",
            "delivery_status": "TEXT",
            "semantic_features_json": "TEXT NOT NULL DEFAULT '{}'",
            "semantic_confidence": "REAL",
            "semantic_skill_name": "TEXT",
            "semantic_skill_version": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE interactions ADD COLUMN {name} {sql_type}")
                columns.add(name)
        # v2 originally keyed this index only by tenant/channel. That made a
        # repeated provider observation crash the projection before the
        # handler could reconcile it, and it conflated distinct member/account
        # namespaces. Serialized projection code performs the member/provider/
        # account-scoped fold; this non-unique index accelerates that lookup.
        conn.execute("DROP INDEX IF EXISTS idx_interactions_tenant_native_message")
        conn.execute(
            "CREATE INDEX idx_interactions_tenant_native_message "
            "ON interactions(tenant_id, member_id, provider, "
            "provider_account_id, native_message_id) "
            "WHERE native_message_id IS NOT NULL"
        )
        if {"tenant_id", "member_id", "occurred_at"} <= columns:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_interactions_member_occurred "
                "ON interactions(tenant_id, member_id, occurred_at)"
            )
        if {"tenant_id", "provider_account_id", "thread_id"} <= columns:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_interactions_account_thread "
                "ON interactions(tenant_id, provider_account_id, thread_id)"
            )


__all__ = ["InteractionsProjection"]
