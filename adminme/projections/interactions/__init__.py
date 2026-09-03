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
    version = 7
    subscribes_to = [
        "messaging.received",
        "messaging.sent",
        "telephony.sms_received",
        "interaction.participant_resolved",
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "triage.intent.features_extracted",
        "communication.state_changed",
        "external_sync.write_requested",
        "external_sync.write_completed",
        "external_sync.write_failed",
        "member.provider_action.confirmed",
        "messaging.reply.canceled",
        "messaging.reply.observed",
        "messaging.reply.reconciled",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS service_interactions (
                tenant_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                service_principal_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                native_message_id TEXT,
                subject TEXT,
                body_text TEXT,
                occurred_at TEXT NOT NULL,
                trusted_route_key TEXT,
                routing_scope TEXT NOT NULL DEFAULT 'unresolved',
                routing_target_id TEXT,
                owner_scope TEXT NOT NULL,
                visibility_scope TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, interaction_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_service_interactions_native
                ON service_interactions(
                    tenant_id, service_principal_id, provider,
                    provider_account_id, native_message_id
                ) WHERE native_message_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS interaction_source_participants (
                tenant_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                role TEXT NOT NULL,
                is_self INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, interaction_id, participant_id, role)
            );
            CREATE INDEX IF NOT EXISTS idx_isparts_tenant_interaction
                ON interaction_source_participants(tenant_id, interaction_id);
            """
        )
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(interactions)")
        }
        additions = {
            "native_message_id": "TEXT",
            "native_chat_id": "TEXT",
            "native_revision": "TEXT",
            "native_revision_ms": "INTEGER NOT NULL DEFAULT 0",
            "reply_to_native_message_id": "TEXT",
            "transport_class": "TEXT",
            "is_group": "INTEGER NOT NULL DEFAULT 0",
            "message_state": "TEXT NOT NULL DEFAULT 'active'",
            "bridge_enrollment_id": "TEXT",
            "device_identity_sha256": "TEXT",
            "connector_id": "TEXT",
            "protected_source_id": "TEXT",
            "transport_version": "TEXT",
            "transport_commit": "TEXT",
            "correlation_id": "TEXT",
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
            "assessment_outcome": "TEXT",
            "assessment_reason_codes": "TEXT NOT NULL DEFAULT '[]'",
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
