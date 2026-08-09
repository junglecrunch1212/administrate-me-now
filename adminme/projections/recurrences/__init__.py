"""
Recurrences projection — RFC 5545 RRULE templates.

Per ADMINISTRATEME_BUILD.md §3.6 and SYSTEM_INVARIANTS.md §4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.recurrences import handlers


class RecurrencesProjection(Projection):
    name = "recurrences"
    version = 3
    subscribes_to = [
        "recurrence.added",
        "recurrence.completed",
        "recurrence.updated",
        "recurrence.paused",
        "recurrence.resumed",
        "recurrence.ended",
        "routine.defined",
        "routine.step_outcome_recorded",
        "routine.paused",
        "routine.resumed",
        "routine.ended",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        for table in ("recurrences", "routines"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "status" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
                )
            if "lifecycle_at" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN lifecycle_at TEXT")
            if "lifecycle_reason" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN lifecycle_reason TEXT")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recurrence_occurrences (
                tenant_id TEXT NOT NULL,
                recurrence_id TEXT NOT NULL,
                occurrence_date TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('completed','skipped')),
                recorded_at TEXT NOT NULL,
                recorded_by_member_id TEXT,
                last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, recurrence_id, occurrence_date)
            );
            CREATE INDEX IF NOT EXISTS idx_recurrence_outcomes_occurrence
                ON recurrence_occurrences(tenant_id, recurrence_id, occurrence_date);
            """
        )


__all__ = ["RecurrencesProjection"]
