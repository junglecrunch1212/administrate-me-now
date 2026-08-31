"""
Tasks projection handlers — household work state transitions.

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13.
Handlers are idempotent: re-applying the same event produces the same
row state (§2 invariant 4). Keyed by ``(tenant_id, task_id)``.

Subscribed event types:
- ``task.created``    → INSERT row from payload
- ``task.completed``  → status='done', completed_at/completed_by
- typed lifecycle events preserve defer/snooze/block/cancel/reopen intent
- ``task.updated``    → apply field_updates (and new_status if given)
- ``task.deleted``    → soft-delete: status='dismissed' (row stays for
  rebuild correctness)

Per [§5.3]: ``due_date`` does not create calendar events. Per [§4.6]:
task completion does not auto-complete commitments.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)

_UPDATABLE_COLUMNS = frozenset(
    {
        "title",
        "assignee_party",
        "domain",
        "energy",
        "effort",
        "item_type",
        "due_date",
        "available_from",
        "micro_script",
        "linked_item_id",
        "linked_item_kind",
        "recurring_id",
        "depends_on_json",
        "goal_ref",
        "life_event",
        "auto_research",
        "waiting_on",
        "waiting_since",
        "notes",
    }
)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "task.created": apply_task_created,
        "task.completed": apply_task_completed,
        "task.deferred": apply_task_deferred,
        "task.snoozed": apply_task_snoozed,
        "task.blocked": apply_task_blocked,
        "task.unblocked": apply_task_unblocked,
        "task.cancelled": apply_task_cancelled,
        "task.reopened": apply_task_reopened,
        "task.updated": apply_task_updated,
        "task.deleted": apply_task_deleted,
        "list_item.completed": apply_external_completion,
        "list_item.uncompleted": apply_external_completion,
        "calendar.event_outcome_recorded": apply_calendar_completion,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def apply_calendar_completion(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    policy = p.get("completion_policy", "manual")
    qualifies = (
        policy == "on_elapsed" and p.get("outcome") in {"elapsed", "attended"}
    ) or (policy == "on_attended" and p.get("outcome") == "attended")
    if (
        p.get("linked_item_kind") != "task"
        or not p.get("linked_item_id")
        or not qualifies
    ):
        return
    conn.execute(
        """UPDATE tasks SET status='done', completed_at=?, completed_by=?,
                lifecycle_action='complete_from_calendar', lifecycle_at=?,
                lifecycle_reason='linked calendar outcome', last_event_id=?
             WHERE tenant_id=? AND task_id=? AND last_event_id < ?""",
        (
            p["recorded_at"],
            p["member_id"],
            p["recorded_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["linked_item_id"],
            envelope["event_id"],
        ),
    )


def apply_external_completion(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    """Mirror only reminders explicitly linked to a task by AdministrateMe."""

    p = envelope["payload"]
    if (
        p.get("linked_item_kind") != "task"
        or not p.get("linked_item_id")
        or p.get("completion_policy") != "mirror"
    ):
        return
    completed = envelope["type"] == "list_item.completed"
    conn.execute(
        """UPDATE tasks SET status=?, completed_at=?, completed_by=?,
                lifecycle_action=?, lifecycle_at=?, lifecycle_reason=?, last_event_id=?
             WHERE tenant_id=? AND task_id=? AND last_event_id < ?""",
        (
            "done" if completed else "next",
            str(p.get("completion_at") or p.get("last_modified_at"))
            if completed
            else None,
            p.get("member_id") if completed else None,
            "complete_from_provider" if completed else "reopen_from_provider",
            str(p.get("completion_at") or p.get("last_modified_at")),
            "linked provider item outcome",
            envelope["event_id"],
            envelope["tenant_id"],
            p["linked_item_id"],
            envelope["event_id"],
        ),
    )


def apply_task_created(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    # TaskCreatedV1's payload schema is minimal; fall back to sensible
    # defaults for the richer projection columns so row inserts succeed
    # without requiring every caller to fill every field.
    conn.execute(
        """
        INSERT INTO tasks (
            task_id, tenant_id, title, status, assignee_party, work_kind,
            owner_member_id, owner_party_id, responsible_party_id,
            beneficiary_party_id, domain,
            energy, effort, item_type, due_date, available_from, micro_script,
            linked_item_id, linked_item_kind, recurring_id,
            depends_on_json, goal_ref, life_event, auto_research,
            waiting_on, waiting_since, created_at, created_by,
            completed_at, completed_by, source_system, notes,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, task_id) DO UPDATE SET
            title            = excluded.title,
            status           = excluded.status,
            assignee_party   = excluded.assignee_party,
            work_kind        = excluded.work_kind,
            owner_member_id  = excluded.owner_member_id,
            owner_party_id   = excluded.owner_party_id,
            responsible_party_id = excluded.responsible_party_id,
            beneficiary_party_id = excluded.beneficiary_party_id,
            domain           = excluded.domain,
            energy           = excluded.energy,
            effort           = excluded.effort,
            item_type        = excluded.item_type,
            due_date         = excluded.due_date,
            available_from   = excluded.available_from,
            micro_script     = excluded.micro_script,
            linked_item_id   = excluded.linked_item_id,
            linked_item_kind = excluded.linked_item_kind,
            recurring_id     = excluded.recurring_id,
            depends_on_json  = excluded.depends_on_json,
            goal_ref         = excluded.goal_ref,
            life_event       = excluded.life_event,
            auto_research    = excluded.auto_research,
            waiting_on       = excluded.waiting_on,
            waiting_since    = excluded.waiting_since,
            created_at       = excluded.created_at,
            created_by       = excluded.created_by,
            source_system    = excluded.source_system,
            notes            = excluded.notes,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            p["task_id"],
            envelope["tenant_id"],
            p["title"],
            p.get("status", "inbox"),
            p.get("assignee_party_id")
            or p.get("assignee_party")
            or p.get("owner_member_id"),
            p.get("work_kind")
            or ("member_obligation" if p.get("owner_member_id") else "household_work"),
            p.get("owner_member_id"),
            p.get("owner_party_id") or p.get("owner_member_id"),
            p.get("responsible_party_id"),
            p.get("beneficiary_party_id"),
            p.get("domain") or "tasks",
            p.get("energy"),
            p.get("effort"),
            p.get("item_type") or "task",
            p.get("due_date") or p.get("due"),
            p.get("available_from"),
            p.get("micro_script"),
            p.get("linked_item_id"),
            p.get("linked_item_kind"),
            p.get("recurring_id"),
            p.get("depends_on_json", "[]"),
            p.get("goal_ref"),
            p.get("life_event"),
            1 if p.get("auto_research") else 0,
            p.get("waiting_on"),
            p.get("waiting_since"),
            p.get("created_at") or envelope["occurred_at"],
            p.get("created_by"),
            p.get("source_system"),
            p.get("notes") or p.get("description"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_task_completed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE tasks
           SET status        = 'done',
               completed_at  = ?,
               completed_by  = ?,
               lifecycle_action = 'complete',
               lifecycle_at = ?,
               lifecycle_reason = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND task_id = ?
        """,
        (
            p["completed_at"],
            p["completed_by_member_id"],
            p["completed_at"],
            p.get("note"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["task_id"],
        ),
    )


def _apply_typed_lifecycle(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    *,
    status: str,
    action: str,
    at_field: str,
    reason_field: str | None = None,
    available_field: str | None = None,
    waiting_field: str | None = None,
    clear_completion: bool = False,
    clear_available: bool = False,
    clear_waiting: bool = False,
) -> None:
    p = envelope["payload"]
    reason = p.get(reason_field) if reason_field else None
    assignments = [
        "status = ?",
        "lifecycle_action = ?",
        "lifecycle_at = ?",
        "lifecycle_reason = ?",
        "last_event_id = ?",
    ]
    params: list[Any] = [
        status,
        action,
        p[at_field],
        reason,
        envelope["event_id"],
    ]
    if available_field:
        assignments.append("available_from = ?")
        params.append(p.get(available_field))
    elif clear_available:
        assignments.append("available_from = NULL")
    if waiting_field:
        assignments.extend(["waiting_on = ?", "waiting_since = ?"])
        params.extend([p.get(waiting_field), p[at_field]])
    elif clear_waiting:
        assignments.extend(["waiting_on = NULL", "waiting_since = NULL"])
    if clear_completion:
        assignments.extend(["completed_at = NULL", "completed_by = NULL"])
    params.extend([envelope["tenant_id"], p["task_id"]])
    conn.execute(
        f"UPDATE tasks SET {', '.join(assignments)} "
        "WHERE tenant_id = ? AND task_id = ?",
        params,
    )


def apply_task_deferred(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="deferred",
        action="defer",
        at_field="deferred_at",
        reason_field="reason",
        available_field="deferred_until",
        clear_waiting=True,
    )


def apply_task_snoozed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="deferred",
        action="snooze",
        at_field="snoozed_at",
        available_field="snoozed_until",
        clear_waiting=True,
    )


def apply_task_blocked(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="waiting_on",
        action="block",
        at_field="blocked_at",
        reason_field="reason",
        waiting_field="reason",
    )


def apply_task_unblocked(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="next",
        action="unblock",
        at_field="unblocked_at",
        reason_field="note",
        clear_waiting=True,
    )


def apply_task_cancelled(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="cancelled",
        action="cancel",
        at_field="cancelled_at",
        reason_field="reason",
        clear_waiting=True,
    )


def apply_task_reopened(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="next",
        action="reopen",
        at_field="reopened_at",
        clear_completion=True,
        clear_available=True,
        clear_waiting=True,
    )


def apply_task_updated(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = dict(p.get("field_updates") or {})
    # new_status in the envelope is a top-level hint — promote to status.
    new_status = p.get("new_status")
    if new_status:
        updates["status"] = new_status

    columns: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key == "status" or key in _UPDATABLE_COLUMNS:
            columns.append(key)
            params.append(value)
    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns)
    assignments += (
        ", lifecycle_action = 'edit', lifecycle_at = ?, "
        "lifecycle_reason = NULL, last_event_id = ?"
    )
    params.extend([envelope["occurred_at"], envelope["event_id"]])
    params.extend([envelope["tenant_id"], p["task_id"]])
    conn.execute(
        f"UPDATE tasks SET {assignments} WHERE tenant_id = ? AND task_id = ?",
        params,
    )


def apply_task_deleted(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    # Soft-delete: row stays (rebuild correctness depends on it).
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE tasks
           SET status        = 'dismissed',
               lifecycle_action = 'delete',
               lifecycle_at = ?,
               lifecycle_reason = 'deleted from reviewed workbook',
               last_event_id = ?
         WHERE tenant_id = ? AND task_id = ?
        """,
        (
            p["deleted_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["task_id"],
        ),
    )
