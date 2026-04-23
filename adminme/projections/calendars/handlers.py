"""
Calendars projection handlers — external calendar events.

Per ADMINISTRATEME_BUILD.md §3.7 and SYSTEM_INVARIANTS.md §5.

Subscribed event types:
- ``calendar.event_added``    → INSERT row. Upsert via
  ``(calendar_source, external_uid)`` so adapter polling is idempotent
  (prompt 11).
- ``calendar.event_updated``  → UPDATE only fields in field_updates.
  Matched via the UNIQUE index on (calendar_source, external_uid).
- ``calendar.event_deleted``  → DELETE the row. Unlike tasks, calendar
  events have an external source of truth — an event gone externally
  should be gone internally.

Rebuild correctness note: because add→update→delete results in DELETE,
replaying the whole log produces the same final state as live — the row
simply never exists in both live and post-rebuild.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)

_UPDATABLE_COLUMNS = frozenset({
    "owner_party",
    "summary",
    "description",
    "location",
    "start_at",
    "end_at",
    "all_day",
    "attendees_json",
    "privacy",
    "title_redacted",
})


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    if event_type == "calendar.event_added":
        apply_calendar_event_added(envelope, conn)
    elif event_type == "calendar.event_updated":
        apply_calendar_event_updated(envelope, conn)
    elif event_type == "calendar.event_deleted":
        apply_calendar_event_deleted(envelope, conn)


def apply_calendar_event_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    # The v1 ingest payload from prompt 04 uses source/external_event_id/
    # calendar_id + start/end. The projection's event-added shape accepts
    # both that legacy form and the richer post-prompt-04 form.
    calendar_source = p.get("calendar_source") or p.get("source")
    external_uid = p.get("external_uid") or p.get("external_event_id")
    calendar_event_id = p.get("calendar_event_id") or (
        f"cal_{envelope['event_id']}"
    )
    start_at = p.get("start_at") or p.get("start")
    end_at = p.get("end_at") or p.get("end") or start_at
    summary = p.get("summary")
    attendees = p.get("attendees") or []
    attendees_json = json.dumps(attendees, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO calendar_events (
            calendar_event_id, tenant_id, calendar_source, external_uid,
            owner_party, summary, description, location, start_at, end_at,
            all_day, attendees_json, privacy, title_redacted,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, calendar_source, external_uid) DO UPDATE SET
            owner_party      = excluded.owner_party,
            summary          = excluded.summary,
            description      = excluded.description,
            location         = excluded.location,
            start_at         = excluded.start_at,
            end_at           = excluded.end_at,
            all_day          = excluded.all_day,
            attendees_json   = excluded.attendees_json,
            privacy          = excluded.privacy,
            title_redacted   = excluded.title_redacted,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            calendar_event_id,
            envelope["tenant_id"],
            calendar_source,
            external_uid,
            p.get("owner_party"),
            summary,
            p.get("description") or p.get("body"),
            p.get("location"),
            start_at,
            end_at,
            1 if p.get("all_day") else 0,
            attendees_json,
            p.get("privacy", "open"),
            p.get("title_redacted"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_calendar_event_updated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = dict(p.get("field_updates") or {})
    if not updates:
        return
    columns: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key not in _UPDATABLE_COLUMNS:
            continue
        if key == "all_day":
            params.append(1 if value else 0)
        elif key == "attendees_json" and not isinstance(value, str):
            params.append(json.dumps(value, separators=(",", ":")))
        else:
            params.append(value)
        columns.append(key)
    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns) + ", last_event_id = ?"
    params.append(envelope["event_id"])
    params.extend(
        [envelope["tenant_id"], p["calendar_source"], p["external_uid"]]
    )
    conn.execute(
        f"UPDATE calendar_events SET {assignments} "
        "WHERE tenant_id = ? AND calendar_source = ? AND external_uid = ?",
        params,
    )


def apply_calendar_event_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        "DELETE FROM calendar_events "
        "WHERE tenant_id = ? AND calendar_source = ? AND external_uid = ?",
        (envelope["tenant_id"], p["calendar_source"], p["external_uid"]),
    )
