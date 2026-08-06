"""
Calendars projection — external calendar events + availability blocks.

Per ADMINISTRATEME_BUILD.md §3.7 and SYSTEM_INVARIANTS.md §5.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.calendars import handlers


class CalendarsProjection(Projection):
    name = "calendars"
    version = 3
    subscribes_to = [
        "calendar.event_added",
        "calendar.event_updated",
        "calendar.event_deleted",
        "calendar.event_outcome_recorded",
        "calendar.source_observed",
        "calendar.rule.configured",
        "calendar.schedule_policy.configured",
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
        # v2 keyed only by provider kind + event id. Google event ids are
        # scoped to a calendar copy, so that index cannot represent one
        # account with several calendars. Projection databases are derived;
        # replace it additively and let replay retain every provider copy.
        conn.execute("DROP INDEX IF EXISTS idx_calendar_events_tenant_source_uid")
        # Existing v2 rows used ``cal_<provider>_<external id>`` and would
        # collide with the new route uniqueness constraint on their next
        # provider observation. Rekey the derived rows in place to the exact
        # deterministic v3 identity before live delivery resumes.
        routed_rows = conn.execute(
            """
            SELECT tenant_id, calendar_event_id, calendar_source,
                   provider_account_id, calendar_external_id, external_uid
              FROM calendar_events
             WHERE provider_account_id IS NOT NULL
               AND calendar_external_id IS NOT NULL
            """
        ).fetchall()
        for row in routed_rows:
            route = "\x1f".join(
                str(row[name] or "")
                for name in (
                    "calendar_source",
                    "provider_account_id",
                    "calendar_external_id",
                    "external_uid",
                )
            )
            event_id = "cal_" + hashlib.sha256(route.encode("utf-8")).hexdigest()[:32]
            if row["calendar_event_id"] != event_id:
                conn.execute(
                    """
                    UPDATE calendar_events SET calendar_event_id=?
                     WHERE tenant_id=? AND calendar_event_id=?
                    """,
                    (event_id, row["tenant_id"], row["calendar_event_id"]),
                )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_events_tenant_route_uid
                ON calendar_events(
                    tenant_id, calendar_source,
                    ifnull(provider_account_id, ''),
                    ifnull(calendar_external_id, ''), external_uid
                )
            """
        )
        rule_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(calendar_member_rules)")
        }
        for name, declaration in {
            "caregiving_summary_values_json": "TEXT NOT NULL DEFAULT '[]'",
            "buffer_kind": "TEXT NOT NULL DEFAULT 'auto'",
        }.items():
            if name not in rule_columns:
                conn.execute(
                    f"ALTER TABLE calendar_member_rules ADD COLUMN {name} {declaration}"
                )


__all__ = ["CalendarsProjection"]
