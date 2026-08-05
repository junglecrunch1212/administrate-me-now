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
    version = 2
    subscribes_to = [
        "task.created",
        "task.completed",
        "task.updated",
        "task.deleted",
        "list_item.completed",
        "list_item.uncompleted",
        "calendar.event_outcome_recorded",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["TasksProjection"]
