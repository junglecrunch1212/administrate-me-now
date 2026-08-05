"""
Commitments projection — the obligation tracker.

Per ADMINISTRATEME_BUILD.md §3.4 and SYSTEM_INVARIANTS.md §4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.commitments import handlers


class CommitmentsProjection(Projection):
    name = "commitments"
    version = 3
    subscribes_to = [
        "commitment.proposed",
        "commitment.confirmed",
        "commitment.completed",
        "commitment.dismissed",
        "commitment.edited",
        "commitment.snoozed",
        "commitment.deferred",
        "commitment.blocked",
        "commitment.unblocked",
        "commitment.cancelled",
        "commitment.delegated",
        "commitment.expired",
        "list_item.completed",
        "list_item.uncompleted",
        "calendar.event_outcome_recorded",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    @staticmethod
    def _add_columns(conn: Any) -> None:
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(commitments)").fetchall()
        }
        if not existing:
            return
        for column, sql_type in {
            "blocked_reason": "TEXT",
            "deferred_until": "TEXT",
            "lifecycle_at": "TEXT",
            "lifecycle_actor": "TEXT",
        }.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE commitments ADD COLUMN {column} {sql_type}")

    def on_connection_opened(self, conn: Any) -> None:
        self._add_columns(conn)

    def migrate_schema(self, conn: Any) -> None:
        self._add_columns(conn)

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["CommitmentsProjection"]
