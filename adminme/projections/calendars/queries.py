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
from datetime import timedelta
from typing import Any

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
        "SELECT * FROM calendar_events "
        "WHERE tenant_id = ? AND calendar_event_id = ?",
        (session.tenant_id, calendar_event_id),
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
    """Events overlapping the day containing ``today_iso`` where the
    member is owner or attendee. ``tz_name`` is accepted for API
    symmetry with later prompts; for v1 the day window is derived from
    the date portion of ``today_iso`` in UTC."""
    del tz_name  # later prompts apply tz-aware day boundaries
    start_day = today_iso[:10] + "T00:00:00Z"
    end_day = today_iso[:10] + "T23:59:59Z"
    rows = conn.execute(
        """
        SELECT * FROM calendar_events
         WHERE tenant_id = ?
           AND start_at <= ?
           AND end_at   >= ?
         ORDER BY start_at ASC
        """,
        (session.tenant_id, end_day, start_day),
    ).fetchall()
    matched: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if row["owner_party"] == member_party_id or _party_in_attendees(
            row["attendees_json"], member_party_id
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
        if row["owner_party"] == member_party_id or _party_in_attendees(
            row["attendees_json"], member_party_id
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
        if row["owner_party"] == member_party_id or _party_in_attendees(
            row["attendees_json"], member_party_id
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
