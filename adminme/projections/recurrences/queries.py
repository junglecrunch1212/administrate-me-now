"""
Recurrences projection read queries.

Per ADMINISTRATEME_BUILD.md §3.6 and SYSTEM_INVARIANTS.md §4, §6. Every
public query takes a ``session: Session`` per [§6.1] and runs through
``scope.filter_rows`` before return.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import sqlcipher3
from dateutil.rrule import rrulestr

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session
from adminme.projections.recurrences.handlers import _parse_iso


def get_recurrence(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    recurrence_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM recurrences WHERE tenant_id = ? AND recurrence_id = ?",
        (session.tenant_id, recurrence_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def due_within(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    days: int,
    as_of_iso: str,
) -> list[dict[str, Any]]:
    """Return recurrences firing within ``days`` days of ``as_of_iso``,
    ordered by next_occurrence ascending."""
    cutoff_dt = _parse_iso(as_of_iso) + timedelta(days=days)
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT * FROM recurrences
         WHERE tenant_id = ?
           AND next_occurrence <= ?
         ORDER BY next_occurrence ASC
        """,
        (session.tenant_id, cutoff_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
) -> list[dict[str, Any]]:
    """Recurrences linked directly to ``member_party_id`` or to the
    household (linked_kind='household')."""
    rows = conn.execute(
        """
        SELECT * FROM recurrences
         WHERE tenant_id = ?
           AND ((linked_kind = 'party' AND linked_id = ?)
                OR linked_kind = 'household')
         ORDER BY next_occurrence ASC
        """,
        (session.tenant_id, member_party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def all_active(
    conn: sqlcipher3.Connection,
    session: Session,
) -> list[dict[str, Any]]:
    """No status field on recurrences — all rows are active. Ordered by
    next_occurrence ascending."""
    rows = conn.execute(
        "SELECT * FROM recurrences WHERE tenant_id = ? ORDER BY next_occurrence ASC",
        (session.tenant_id,),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def _local_time(value: str) -> time:
    return time.fromisoformat(value)


def _time_label(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _routine_occurs_on(row: dict[str, Any], occurrence_date: date) -> bool:
    zone = ZoneInfo(row["timezone"])
    starts_on = date.fromisoformat(row["starts_on"])
    dtstart = datetime.combine(
        starts_on, _local_time(row["window_start_local"]), tzinfo=zone
    )
    day_start = datetime.combine(occurrence_date, time.min, tzinfo=zone)
    day_end = day_start + timedelta(days=1)
    rule = rrulestr(row["rrule"], dtstart=dtstart)
    candidate = rule.after(day_start, inc=True)
    return candidate is not None and candidate < day_end


def routine_occurrences_at(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    as_of_iso: str,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    """Return ordered routine occurrences active on each routine's local day.

    The occurrence is derived from the immutable routine definition and an
    explicit clock. Only member-recorded step outcomes are persisted.
    """

    as_of = _parse_iso(as_of_iso).astimezone(timezone.utc)
    rows = conn.execute(
        "SELECT * FROM routines WHERE tenant_id = ? AND member_id = ? "
        "ORDER BY routine_id ASC",
        (session.tenant_id, member_party_id),
    ).fetchall()
    visible = filter_rows(session, [dict(row) for row in rows])
    occurrences: list[dict[str, Any]] = []
    for routine in visible:
        zone = ZoneInfo(routine["timezone"])
        occurrence_date = as_of.astimezone(zone).date()
        if not _routine_occurs_on(routine, occurrence_date):
            continue
        start = datetime.combine(
            occurrence_date,
            _local_time(routine["window_start_local"]),
            tzinfo=zone,
        )
        end = datetime.combine(
            occurrence_date,
            _local_time(routine["window_end_local"]),
            tzinfo=zone,
        )
        if end <= start:
            end += timedelta(days=1)
        occurrence_key = occurrence_date.isoformat()
        step_rows = conn.execute(
            "SELECT * FROM routine_steps "
            "WHERE tenant_id = ? AND routine_id = ? ORDER BY position ASC",
            (session.tenant_id, routine["routine_id"]),
        ).fetchall()
        outcome_rows = conn.execute(
            "SELECT * FROM routine_step_outcomes "
            "WHERE tenant_id = ? AND routine_id = ? AND occurrence_date = ?",
            (session.tenant_id, routine["routine_id"], occurrence_key),
        ).fetchall()
        outcomes = {row["step_id"]: dict(row) for row in outcome_rows}
        steps: list[dict[str, Any]] = []
        for step_row in step_rows:
            step = dict(step_row)
            outcome = outcomes.get(step["step_id"])
            steps.append(
                {
                    "step_id": step["step_id"],
                    "position": step["position"],
                    "title": step["title"],
                    "micro_step": step["micro_step"],
                    "linked_task_id": step["linked_task_id"],
                    "outcome": outcome["outcome"] if outcome else None,
                    "outcome_event_id": outcome["last_event_id"] if outcome else None,
                }
            )
        occurrence_row = conn.execute(
            "SELECT * FROM routine_occurrences "
            "WHERE tenant_id = ? AND routine_id = ? AND occurrence_date = ?",
            (session.tenant_id, routine["routine_id"], occurrence_key),
        ).fetchone()
        persisted = dict(occurrence_row) if occurrence_row is not None else None
        closed = bool(
            persisted and persisted["status"] in {"completed", "completed_with_skips"}
        )
        if closed and not include_closed:
            continue
        current = next((step for step in steps if step["outcome"] is None), None)
        if closed and persisted is not None:
            status = persisted["status"]
        elif persisted:
            status = "in_progress"
        elif as_of < start.astimezone(timezone.utc):
            status = "upcoming"
        elif as_of >= end.astimezone(timezone.utc):
            status = "overdue"
        else:
            status = "in_progress"
        completed = sum(step["outcome"] == "completed" for step in steps)
        skipped = sum(step["outcome"] == "skipped" for step in steps)
        last_event_id = (
            persisted["last_event_id"] if persisted else routine["last_event_id"]
        )
        occurrences.append(
            {
                "id": routine["routine_id"],
                "routine_id": routine["routine_id"],
                "kind": "routine",
                "title": routine["title"],
                "member_id": routine["member_id"],
                "occurrence_date": occurrence_key,
                "window_start_at": start.astimezone(timezone.utc).isoformat(),
                "window_end_at": end.astimezone(timezone.utc).isoformat(),
                "time_label": f"{_time_label(start)}–{_time_label(end)}",
                "status": status,
                "steps": steps,
                "current_step": current,
                "completed_steps": completed,
                "skipped_steps": skipped,
                "total_steps": len(steps),
                "last_event_id": last_event_id,
                "evidence_event_id": last_event_id,
                "owner_scope": routine["owner_scope"],
                "visibility_scope": routine["visibility_scope"],
                "sensitivity": routine["sensitivity"],
            }
        )
    occurrences.sort(key=lambda row: (row["window_start_at"], row["routine_id"]))
    return occurrences


def get_routine_occurrence(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    routine_id: str,
    occurrence_date: str,
    as_of_iso: str,
) -> dict[str, Any] | None:
    for item in routine_occurrences_at(
        conn,
        session,
        member_party_id=member_party_id,
        as_of_iso=as_of_iso,
        include_closed=True,
    ):
        if (
            item["routine_id"] == routine_id
            and item["occurrence_date"] == occurrence_date
        ):
            return item
    return None
