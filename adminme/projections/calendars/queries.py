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


def _configured_routes(
    conn: sqlcipher3.Connection, tenant_id: str, member_id: str
) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        """
        SELECT provider, provider_account_id, calendar_external_id
          FROM calendar_member_rules
         WHERE tenant_id=? AND subject_member_id=? AND enabled=1
        """,
        (tenant_id, member_id),
    ).fetchall()
    return {
        (
            str(row["provider"]),
            str(row["provider_account_id"]),
            str(row["calendar_external_id"]),
        )
        for row in rows
    }


def _matches_member(
    row: dict[str, Any], member_id: str, routes: set[tuple[str, str, str]]
) -> bool:
    route = (
        str(row.get("calendar_source") or ""),
        str(row.get("provider_account_id") or ""),
        str(row.get("calendar_external_id") or ""),
    )
    return bool(
        row.get("member_id") == member_id
        or row.get("owner_party") == member_id
        or _party_in_attendees(row.get("attendees_json") or "[]", member_id)
        or route in routes
    )


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
    routes = _configured_routes(conn, session.tenant_id, member_party_id)
    matched: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if _matches_member(row, member_party_id, routes):
            matched.append(row)
    return filter_rows(session, matched)


def week(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    start_date_iso: str,
    tz_name: str = "UTC",
) -> list[dict[str, Any]]:
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {tz_name}") from exc
    local_date = datetime.fromisoformat(start_date_iso[:10]).date()
    local_start = datetime.combine(local_date, time.min, tzinfo=zone)
    local_end = local_start + timedelta(days=7)
    start_iso = local_start.astimezone(timezone.utc).isoformat()
    end_iso = local_end.astimezone(timezone.utc).isoformat()
    rows = conn.execute(
        """
        SELECT * FROM calendar_events
         WHERE tenant_id = ?
           AND datetime(start_at) < datetime(?)
           AND datetime(end_at)   > datetime(?)
         ORDER BY datetime(start_at) ASC
        """,
        (session.tenant_id, end_iso, start_iso),
    ).fetchall()
    routes = _configured_routes(conn, session.tenant_id, member_party_id)
    matched: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if _matches_member(row, member_party_id, routes):
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
           AND datetime(start_at) < datetime(?)
           AND datetime(end_at)   > datetime(?)
           AND status != 'cancelled'
           AND availability IN ('busy', 'unavailable', 'tentative')
        """,
        (session.tenant_id, range_end_iso, range_start_iso),
    ).fetchall()
    routes = _configured_routes(conn, session.tenant_id, member_party_id)
    matched: list[dict[str, Any]] = []
    for r in cal_rows:
        row = dict(r)
        if _matches_member(row, member_party_id, routes):
            matched.append(row)
    visible = filter_rows(session, matched)
    rule_rows = conn.execute(
        """
        SELECT * FROM calendar_member_rules
         WHERE tenant_id=? AND subject_member_id=? AND enabled=1
        """,
        (session.tenant_id, member_party_id),
    ).fetchall()
    rules = {
        (
            row["provider"],
            row["provider_account_id"],
            row["calendar_external_id"],
        ): dict(row)
        for row in rule_rows
    }
    slots: list[dict[str, Any]] = []
    for row in visible:
        rule = rules.get(
            (
                row.get("calendar_source"),
                row.get("provider_account_id"),
                row.get("calendar_external_id"),
            )
        )
        effect = str((rule or {}).get("effect") or "hard_block")
        if row.get("status") == "tentative":
            effect = str((rule or {}).get("tentative_effect") or "soft_hold")
        if effect != "hard_block":
            continue
        if row.get("start_at") and row.get("end_at"):
            slots.append(
                {
                    "start_at": row["start_at"],
                    "end_at": row["end_at"],
                    "effect": effect,
                }
            )
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


def list_sources(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [session.tenant_id]
    where = "tenant_id=?"
    if member_id is not None:
        where += " AND member_id=?"
        params.append(member_id)
    rows = conn.execute(
        f"SELECT * FROM calendar_sources WHERE {where} "
        "ORDER BY is_primary DESC, calendar_name COLLATE NOCASE ASC",
        params,
    ).fetchall()
    return filter_rows(session, [dict(row) for row in rows])


def rules_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, s.calendar_name, s.access_role, s.write_events,
               s.read_details, s.read_free_busy, s.observed_at
          FROM calendar_member_rules r
          LEFT JOIN calendar_sources s
            ON s.tenant_id=r.tenant_id
           AND s.provider=r.provider
           AND s.provider_account_id=r.provider_account_id
           AND s.calendar_external_id=r.calendar_external_id
         WHERE r.tenant_id=? AND r.subject_member_id=?
         ORDER BY r.enabled DESC, s.calendar_name COLLATE NOCASE ASC
        """,
        (session.tenant_id, member_id),
    ).fetchall()
    return filter_rows(session, [dict(row) for row in rows])


def schedule_policy_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM calendar_schedule_policies WHERE tenant_id=? AND member_id=?",
        (session.tenant_id, member_id),
    ).fetchone()
    visible = filter_one(session, dict(row) if row is not None else None)
    if visible is None:
        return None
    result = dict(visible)
    for source, target, default in (
        ("windows_json", "windows", []),
        ("buffer_minutes_json", "buffer_minutes", {}),
    ):
        try:
            result[target] = json.loads(result.get(source) or json.dumps(default))
        except (TypeError, json.JSONDecodeError):
            result[target] = default
    return result


def events_in_range(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    range_start_iso: str,
    range_end_iso: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM calendar_events
         WHERE tenant_id=?
           AND datetime(start_at) < datetime(?)
           AND datetime(end_at) > datetime(?)
           AND status != 'cancelled'
         ORDER BY datetime(start_at), datetime(end_at), calendar_event_id
        """,
        (session.tenant_id, range_end_iso, range_start_iso),
    ).fetchall()
    routes = _configured_routes(conn, session.tenant_id, member_party_id)
    matched: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if _matches_member(row, member_party_id, routes):
            matched.append(row)
    return filter_rows(session, matched)


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
