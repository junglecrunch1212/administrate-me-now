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
    version = 2
    subscribes_to = [
        "messaging.received",
        "messaging.sent",
        "telephony.sms_received",
        "interaction.participant_resolved",
        "messaging.classified",
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
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE interactions ADD COLUMN {name} {sql_type}")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_interactions_tenant_native_message "
            "ON interactions(tenant_id, channel_specific, native_message_id) "
            "WHERE native_message_id IS NOT NULL"
        )


__all__ = ["InteractionsProjection"]
