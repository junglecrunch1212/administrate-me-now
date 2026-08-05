"""
Lists projection — 13th projection per [D18].

Per ADMINISTRATEME_BUILD.md §L3 and SYSTEM_INVARIANTS.md §2 (invariants
1-4) + §6 (scope discipline on reads).

Subscribes ONLY to ``list_item.added@v1``. The other three list_item
event types (updated/completed/deleted) have no first-party producer on
main yet — declaring subscriptions without producers + handlers would
fail rebuild correctness per UT-27-style asymmetric-subscription
discipline. Follow-up sub-prompts (11b-i-β / 11b-iv-β) ship the producers
and a paired 11f-β extends subscriptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.lists import handlers


class ListsProjection(Projection):
    name = "lists"
    version = 2
    # Asymmetric on purpose — other list_item.* types ship in 11f-β [D18, UT-27].
    subscribes_to = [
        "list.created",
        "list.updated",
        "list.deleted",
        "list_item.added",
        "list_item.updated",
        "list_item.completed",
        "list_item.uncompleted",
        "list_item.deleted",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(list_items)")}
        additions = {
            "provider": "TEXT",
            "provider_account_id": "TEXT",
            "list_color": "TEXT",
            "url": "TEXT",
            "completion_at": "TEXT",
            "start_json": "TEXT",
            "due_json": "TEXT",
            "alarms_json": "TEXT NOT NULL DEFAULT '[]'",
            "recurrence_json": "TEXT NOT NULL DEFAULT '[]'",
            "flagged": "INTEGER",
            "tags_json": "TEXT",
            "parent_external_id": "TEXT",
            "sort_order": "INTEGER",
            "linked_item_kind": "TEXT",
            "linked_item_id": "TEXT",
            "completion_policy": "TEXT NOT NULL DEFAULT 'manual'",
            "provider_revision": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE list_items ADD COLUMN {name} {declaration}")


__all__ = ["ListsProjection"]
