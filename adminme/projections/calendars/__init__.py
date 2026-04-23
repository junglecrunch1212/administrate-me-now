"""
Calendars projection — external calendar events + availability blocks.

Per ADMINISTRATEME_BUILD.md §3.7 and SYSTEM_INVARIANTS.md §5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.calendars import handlers


class CalendarsProjection(Projection):
    name = "calendars"
    version = 1
    subscribes_to = [
        "calendar.event_added",
        "calendar.event_updated",
        "calendar.event_deleted",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["CalendarsProjection"]
