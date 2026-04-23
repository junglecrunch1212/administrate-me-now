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
    version = 1
    subscribes_to = [
        "recurrence.added",
        "recurrence.completed",
        "recurrence.updated",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["RecurrencesProjection"]
