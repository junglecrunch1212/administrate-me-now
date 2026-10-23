"""Lists projection — provider list/item mirrors plus reminder cue facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.lists import handlers


class ListsProjection(Projection):
    name = "lists"
    version = 5
    # v5 adds private service source pointers; v4 added member observations.
    # Replay is required so retained events populate that provenance rather
    # than limiting it to observations received after upgrade.
    replay_retained_events_on_version_change = True
    subscribes_to = [
        "list.created",
        "list.updated",
        "list.deleted",
        "list_item.added",
        "list_item.updated",
        "list_item.completed",
        "list_item.uncompleted",
        "list_item.deleted",
        "context.signal.observed",
        "reminder.cue.configured",
        "reminder.cue.cleared",
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
            "provider_position": "TEXT",
            "source_links_json": "TEXT NOT NULL DEFAULT '[]'",
            "provider_data_json": "TEXT NOT NULL DEFAULT '{}'",
            "read_only_reason": "TEXT",
            "linked_item_kind": "TEXT",
            "linked_item_id": "TEXT",
            "completion_policy": "TEXT NOT NULL DEFAULT 'manual'",
            "provider_revision": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE list_items ADD COLUMN {name} {declaration}")


__all__ = ["ListsProjection"]
