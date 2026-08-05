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
    version = 2
    subscribes_to = [
        "calendar.event_added",
        "calendar.event_updated",
        "calendar.event_deleted",
        "calendar.event_outcome_recorded",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(calendar_events)")}
        additions = {
            "provider_account_id": "TEXT",
            "member_id": "TEXT",
            "calendar_external_id": "TEXT",
            "calendar_name": "TEXT",
            "ical_uid": "TEXT",
            "provider_revision": "TEXT",
            "structured_location_json": "TEXT",
            "start_time_zone": "TEXT",
            "end_time_zone": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'confirmed'",
            "event_type": "TEXT NOT NULL DEFAULT 'default'",
            "availability": "TEXT NOT NULL DEFAULT 'busy'",
            "provider_visibility": "TEXT NOT NULL DEFAULT 'default'",
            "organizer_json": "TEXT",
            "alarms_json": "TEXT NOT NULL DEFAULT '[]'",
            "use_default_alarms": "INTEGER NOT NULL DEFAULT 0",
            "recurrence_json": "TEXT NOT NULL DEFAULT '{}'",
            "sequence": "INTEGER NOT NULL DEFAULT 0",
            "conference_json": "TEXT",
            "attachments_json": "TEXT NOT NULL DEFAULT '[]'",
            "color_id": "TEXT",
            "source_url": "TEXT",
            "html_link": "TEXT",
            "linked_item_kind": "TEXT",
            "linked_item_id": "TEXT",
            "completion_policy": "TEXT NOT NULL DEFAULT 'manual'",
            "provider_created_at": "TEXT",
            "provider_updated_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE calendar_events ADD COLUMN {name} {declaration}"
                )


__all__ = ["CalendarsProjection"]
