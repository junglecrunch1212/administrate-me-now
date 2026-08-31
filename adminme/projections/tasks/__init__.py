"""
Tasks projection — household work (AdministrateMe-specific, not Hearth).

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.tasks import handlers


class TasksProjection(Projection):
    name = "tasks"
    version = 5
    replay_retained_events_on_version_change = True
    subscribes_to = [
        "task.created",
        "task.completed",
        "task.deferred",
        "task.snoozed",
        "task.blocked",
        "task.unblocked",
        "task.cancelled",
        "task.reopened",
        "task.updated",
        "task.deleted",
        "list_item.completed",
        "list_item.uncompleted",
        "calendar.event_outcome_recorded",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "available_from" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN available_from TEXT")
        if "lifecycle_action" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN lifecycle_action TEXT")
        if "lifecycle_at" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN lifecycle_at TEXT")
        if "lifecycle_reason" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN lifecycle_reason TEXT")
        if "work_kind" not in columns:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN work_kind "
                "TEXT NOT NULL DEFAULT 'household_work'"
            )
        if "owner_member_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_member_id TEXT")
        if "owner_party_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_party_id TEXT")
        if "responsible_party_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN responsible_party_id TEXT")
        if "beneficiary_party_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN beneficiary_party_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_tenant_available "
            "ON tasks(tenant_id, available_from)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_tenant_owner_party "
            "ON tasks(tenant_id, owner_party_id, work_kind)"
        )


__all__ = ["TasksProjection"]
