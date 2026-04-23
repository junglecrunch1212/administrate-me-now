"""
Recurrences projection handlers — RRULE template tracking.

Per ADMINISTRATEME_BUILD.md §3.6 and SYSTEM_INVARIANTS.md §4.

Subscribed event types:
- ``recurrence.added``      → INSERT row from payload
- ``recurrence.completed``  → advance next_occurrence via RRULE
- ``recurrence.updated``    → apply field_updates; if rrule changed,
  recompute next_occurrence from now

Per [§4.5]: firing a recurrence does NOT auto-materialize a task. The
handler advances ``next_occurrence`` only; task creation is a prompt-10c
pipeline concern (``reminder_dispatch``). Handlers never emit events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import sqlcipher3
from dateutil.rrule import rrulestr

_log = logging.getLogger(__name__)

_UPDATABLE_COLUMNS = frozenset({
    "linked_kind",
    "linked_id",
    "kind",
    "rrule",
    "next_occurrence",
    "lead_time_days",
    "trackable",
    "notes",
})


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    if event_type == "recurrence.added":
        apply_recurrence_added(envelope, conn)
    elif event_type == "recurrence.completed":
        apply_recurrence_completed(envelope, conn)
    elif event_type == "recurrence.updated":
        apply_recurrence_updated(envelope, conn)


def apply_recurrence_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO recurrences (
            recurrence_id, tenant_id, linked_kind, linked_id, kind, rrule,
            next_occurrence, lead_time_days, trackable, notes,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, recurrence_id) DO UPDATE SET
            linked_kind      = excluded.linked_kind,
            linked_id        = excluded.linked_id,
            kind             = excluded.kind,
            rrule            = excluded.rrule,
            next_occurrence  = excluded.next_occurrence,
            lead_time_days   = excluded.lead_time_days,
            trackable        = excluded.trackable,
            notes            = excluded.notes,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            p["recurrence_id"],
            envelope["tenant_id"],
            p["linked_kind"],
            p["linked_id"],
            p["kind"],
            p["rrule"],
            p["next_occurrence"],
            int(p.get("lead_time_days", 0)),
            1 if p.get("trackable") else 0,
            p.get("notes"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_recurrence_completed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    row = conn.execute(
        "SELECT rrule, next_occurrence FROM recurrences "
        "WHERE tenant_id = ? AND recurrence_id = ?",
        (envelope["tenant_id"], p["recurrence_id"]),
    ).fetchone()
    if row is None:
        # Defensive — completion without prior .added lands nothing.
        # Upstream pipelines should not emit in this order.
        _log.info(
            "recurrence.completed for unknown recurrence_id=%s (tenant=%s)",
            p["recurrence_id"],
            envelope["tenant_id"],
        )
        return
    rrule_str = row["rrule"]
    dtstart = _parse_iso(row["next_occurrence"])
    after = _parse_iso(p["completed_at"])
    next_occ = _advance_rrule(rrule_str, dtstart, after)
    conn.execute(
        """
        UPDATE recurrences
           SET next_occurrence = ?,
               last_event_id   = ?
         WHERE tenant_id = ? AND recurrence_id = ?
        """,
        (
            next_occ,
            envelope["event_id"],
            envelope["tenant_id"],
            p["recurrence_id"],
        ),
    )


def apply_recurrence_updated(
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
        if key == "trackable":
            params.append(1 if value else 0)
        elif key == "lead_time_days":
            params.append(int(value))
        else:
            params.append(value)
        columns.append(key)

    # If rrule changed but next_occurrence was not explicitly set in this
    # update, recompute next_occurrence from now using the new rrule.
    if "rrule" in updates and "next_occurrence" not in updates:
        new_rrule = updates["rrule"]
        now = datetime.now(timezone.utc)
        columns.append("next_occurrence")
        params.append(_advance_rrule(new_rrule, now, now))

    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns) + ", last_event_id = ?"
    params.append(envelope["event_id"])
    params.extend([envelope["tenant_id"], p["recurrence_id"]])
    conn.execute(
        f"UPDATE recurrences SET {assignments} "
        "WHERE tenant_id = ? AND recurrence_id = ?",
        params,
    )


def _parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string, returning a timezone-aware datetime in
    UTC. Accepts both ``Z`` suffix and explicit ``+00:00`` offsets."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _advance_rrule(rrule_str: str, dtstart: datetime, after: datetime) -> str:
    """Compute the next firing after ``after`` given ``rrule_str`` with
    ``dtstart``. Returns an ISO 8601 string (UTC, Z suffix)."""
    rule = rrulestr(rrule_str, dtstart=dtstart)
    next_dt = rule.after(after, inc=False)
    if next_dt is None:
        # RRULE exhausted; leave next_occurrence pointing at the prior
        # value. Production usage covers only open-ended rules.
        return dtstart.strftime("%Y-%m-%dT%H:%M:%SZ")
    if next_dt.tzinfo is None:
        next_dt = next_dt.replace(tzinfo=timezone.utc)
    return next_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
