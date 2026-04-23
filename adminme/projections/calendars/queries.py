"""
Calendars projection read queries.

Per ADMINISTRATEME_BUILD.md §3.7 and SYSTEM_INVARIANTS.md §5. Plain
query functions; prompt 08 wraps them with Session / scope enforcement.

[§5.5] privacy filtering is applied at read time, not at ingest. For
prompt 06, these queries return rows unfiltered — prompt 08 wraps with
``Session`` and applies the redactToBusy allowlist shape.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import sqlcipher3

from adminme.projections.recurrences.handlers import _parse_iso


# TODO(prompt-08): wrap with Session scope check
def get_calendar_event(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    calendar_event_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM calendar_events "
        "WHERE tenant_id = ? AND calendar_event_id = ?",
        (tenant_id, calendar_event_id),
    ).fetchone()
    return dict(row) if row is not None else None


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


# TODO(prompt-08): wrap with Session scope check
def today(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    member_party_id: str,
    today_iso: str,
    tz_name: str,
) -> list[dict[str, Any]]:
    """Events overlapping the day containing ``today_iso`` where the
    member is owner or attendee. ``tz_name`` is accepted for API
    symmetry with later prompts; for v1 the day window is derived from
    the date portion of ``today_iso`` in UTC."""
    del tz_name  # prompt-08 applies tz-aware day boundaries
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
        (tenant_id, end_day, start_day),
    ).fetchall()
    filtered: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if row["owner_party"] == member_party_id or _party_in_attendees(
            row["attendees_json"], member_party_id
        ):
            filtered.append(row)
    return filtered


# TODO(prompt-08): wrap with Session scope check
def week(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
        (tenant_id, end_iso, start_iso),
    ).fetchall()
    filtered: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if row["owner_party"] == member_party_id or _party_in_attendees(
            row["attendees_json"], member_party_id
        ):
            filtered.append(row)
    return filtered


# TODO(prompt-08): wrap with Session scope check
def busy_slots(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    member_party_id: str,
    range_start_iso: str,
    range_end_iso: str,
) -> list[dict[str, Any]]:
    """Return (start_at, end_at) pairs from BOTH calendar_events (where
    party is owner or attendee) AND availability_blocks, overlapping the
    range. Returns no event content — busy-only for privacy. [§5.6]"""
    cal_rows = conn.execute(
        """
        SELECT start_at, end_at, owner_party, attendees_json
          FROM calendar_events
         WHERE tenant_id = ?
           AND start_at <= ?
           AND end_at   >= ?
        """,
        (tenant_id, range_end_iso, range_start_iso),
    ).fetchall()
    slots: list[dict[str, Any]] = []
    for r in cal_rows:
        if r["owner_party"] == member_party_id or _party_in_attendees(
            r["attendees_json"], member_party_id
        ):
            slots.append({"start_at": r["start_at"], "end_at": r["end_at"]})
    avail_rows = conn.execute(
        """
        SELECT start_at, end_at FROM availability_blocks
         WHERE tenant_id = ?
           AND party_id = ?
           AND start_at <= ?
           AND end_at   >= ?
        """,
        (tenant_id, member_party_id, range_end_iso, range_start_iso),
    ).fetchall()
    for r in avail_rows:
        slots.append({"start_at": r["start_at"], "end_at": r["end_at"]})
    slots.sort(key=lambda s: s["start_at"])
    return slots


# TODO(prompt-08): wrap with Session scope check
def by_source(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    calendar_source: str,
    external_uid: str,
) -> dict[str, Any] | None:
    """For adapter deduplication in prompt 11."""
    row = conn.execute(
        "SELECT * FROM calendar_events "
        "WHERE tenant_id = ? AND calendar_source = ? AND external_uid = ?",
        (tenant_id, calendar_source, external_uid),
    ).fetchone()
    return dict(row) if row is not None else None
