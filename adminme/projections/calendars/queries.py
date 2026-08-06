"""
Calendars projection read queries.

Per ADMINISTRATEME_BUILD.md §3.7 and SYSTEM_INVARIANTS.md §5, §6.
[§5.5] privacy filtering is applied at read time, not at ingest. Every
public query takes a ``session: Session`` per [§6.1] and runs through
``scope.filter_rows``, which applies ``privacy_filter`` so non-owner reads
of privileged calendar events come back as ``[busy]`` blocks
[CONSOLE_PATTERNS.md §6].

``busy_slots`` is a special case: it returns time-only tuples even for
events the session would otherwise see in full, by design.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session
from adminme.projections.recurrences.handlers import _parse_iso


def get_calendar_event(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    calendar_event_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM calendar_events WHERE tenant_id = ? AND calendar_event_id = ?",
        (session.tenant_id, calendar_event_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def get_calendar_outcome(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    calendar_source: str,
    external_uid: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM calendar_event_outcomes
         WHERE tenant_id = ? AND member_id = ?
           AND calendar_source = ? AND external_uid = ?
        """,
        (session.tenant_id, member_id, calendar_source, external_uid),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def _party_in_attendees(attendees_json: str, party_id: str) -> bool:
    try:
        attendees = json.loads(attendees_json)
    except (json.JSONDecodeError, TypeError):
        return False
    for a in attendees:
        if isinstance(a, str) and a == party_id:
            return True
        if isinstance(a, dict) and a.get("party_id") == party_id:
            return True
    return False


def today(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    today_iso: str,
    tz_name: str,
) -> list[dict[str, Any]]:
    """Events overlapping the member's local day.

    The date component is interpreted in the configured IANA timezone and
    converted to a half-open UTC window. This keeps late-evening and DST-day
    events on the member's actual Day Sheet instead of a server-UTC day.
    """
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {tz_name}") from exc
    local_date = datetime.fromisoformat(today_iso[:10]).date()
    local_start = datetime.combine(local_date, time.min, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    start_day = local_start.astimezone(timezone.utc).isoformat()
    end_day = local_end.astimezone(timezone.utc).isoformat()
    rows = conn.execute(
        """
        SELECT e.*, o.outcome AS recorded_outcome,
               o.attendance_status AS recorded_attendance_status,
               o.last_event_id AS outcome_event_id
          FROM calendar_events e
          LEFT JOIN calendar_event_outcomes o
            ON o.tenant_id = e.tenant_id
           AND o.member_id = ?
           AND o.calendar_source = e.calendar_source
           AND o.external_uid = e.external_uid
         WHERE e.tenant_id = ?
           AND datetime(e.start_at) < datetime(?)
           AND datetime(e.end_at)   > datetime(?)
         ORDER BY datetime(e.start_at) ASC
        """,
        (member_party_id, session.tenant_id, end_day, start_day),
    ).fetchall()
    matched: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if (
            row.get("member_id") == member_party_id
            or row["owner_party"] == member_party_id
            or _party_in_attendees(row["attendees_json"], member_party_id)
        ):
            matched.append(row)
    return filter_rows(session, matched)


def week(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    start_date_iso: str,
) -> list[dict[str, Any]]:
    start = _parse_iso(start_date_iso[:10] + "T00:00:00Z")
    end = start + timedelta(days=7)
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT * FROM calendar_events
         WHERE tenant_id = ?
           AND start_at < ?
           AND end_at   >= ?
         ORDER BY start_at ASC
        """,
        (session.tenant_id, end_iso, start_iso),
    ).fetchall()
    matched: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if (
            row.get("member_id") == member_party_id
            or row["owner_party"] == member_party_id
            or _party_in_attendees(row["attendees_json"], member_party_id)
        ):
            matched.append(row)
    return filter_rows(session, matched)


def busy_slots(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    range_start_iso: str,
    range_end_iso: str,
) -> list[dict[str, Any]]:
    """Return (start_at, end_at) pairs from BOTH calendar_events (where
    party is owner or attendee) AND availability_blocks, overlapping the
    range. Returns no event content — busy-only for privacy [§5.6].

    Even though the rows are scope-filtered, the projection of just
    (start_at, end_at) means a denied event still does not leak content;
    privileged-redacted rows similarly contribute only their time block.
    """
    cal_rows = conn.execute(
        """
        SELECT * FROM calendar_events
         WHERE tenant_id = ?
           AND start_at <= ?
           AND end_at   >= ?
        """,
        (session.tenant_id, range_end_iso, range_start_iso),
    ).fetchall()
    matched: list[dict[str, Any]] = []
    for r in cal_rows:
        row = dict(r)
        if (
            row.get("member_id") == member_party_id
            or row["owner_party"] == member_party_id
            or _party_in_attendees(row["attendees_json"], member_party_id)
        ):
            matched.append(row)
    visible = filter_rows(session, matched)
    slots: list[dict[str, Any]] = [
        {"start_at": r["start_at"], "end_at": r["end_at"]}
        for r in visible
        if r.get("start_at") and r.get("end_at")
    ]
    avail_rows = conn.execute(
        """
        SELECT start_at, end_at FROM availability_blocks
         WHERE tenant_id = ?
           AND party_id = ?
           AND start_at <= ?
           AND end_at   >= ?
        """,
        (session.tenant_id, member_party_id, range_end_iso, range_start_iso),
    ).fetchall()
    for r in avail_rows:
        slots.append({"start_at": r["start_at"], "end_at": r["end_at"]})
    slots.sort(key=lambda s: s["start_at"])
    return slots


def by_source(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    calendar_source: str,
    external_uid: str,
) -> dict[str, Any] | None:
    """For adapter deduplication in prompt 11."""
    row = conn.execute(
        "SELECT * FROM calendar_events "
        "WHERE tenant_id = ? AND calendar_source = ? AND external_uid = ?",
        (session.tenant_id, calendar_source, external_uid),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)
