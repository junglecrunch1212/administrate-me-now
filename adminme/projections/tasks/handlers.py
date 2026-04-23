"""
Tasks projection handlers — household work state transitions.

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13.
Handlers are idempotent: re-applying the same event produces the same
row state (§2 invariant 4). Keyed by ``(tenant_id, task_id)``.

Subscribed event types:
- ``task.created``    → INSERT row from payload
- ``task.completed``  → status='done', completed_at/completed_by
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

_UPDATABLE_COLUMNS = frozenset({
    "title",
    "assignee_party",
    "domain",
    "energy",
    "effort",
    "item_type",
    "due_date",
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
})


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "task.created": apply_task_created,
        "task.completed": apply_task_completed,
        "task.updated": apply_task_updated,
        "task.deleted": apply_task_deleted,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def apply_task_created(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    # TaskCreatedV1's payload schema is minimal; fall back to sensible
    # defaults for the richer projection columns so row inserts succeed
    # without requiring every caller to fill every field.
    conn.execute(
        """
        INSERT INTO tasks (
            task_id, tenant_id, title, status, assignee_party, domain,
            energy, effort, item_type, due_date, micro_script,
            linked_item_id, linked_item_kind, recurring_id,
            depends_on_json, goal_ref, life_event, auto_research,
            waiting_on, waiting_since, created_at, created_by,
            completed_at, completed_by, source_system, notes,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, task_id) DO UPDATE SET
            title            = excluded.title,
            status           = excluded.status,
            assignee_party   = excluded.assignee_party,
            domain           = excluded.domain,
            energy           = excluded.energy,
            effort           = excluded.effort,
            item_type        = excluded.item_type,
            due_date         = excluded.due_date,
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
            p.get("assignee_party") or p.get("owner_member_id"),
            p.get("domain", "tasks"),
            p.get("energy"),
            p.get("effort"),
            p.get("item_type", "task"),
            p.get("due_date") or p.get("due"),
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


def apply_task_completed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE tasks
           SET status        = 'done',
               completed_at  = ?,
               completed_by  = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND task_id = ?
        """,
        (
            p["completed_at"],
            p["completed_by_member_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["task_id"],
        ),
    )


def apply_task_updated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
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
    assignments = ", ".join(f"{c} = ?" for c in columns) + ", last_event_id = ?"
    params.append(envelope["event_id"])
    params.extend([envelope["tenant_id"], p["task_id"]])
    conn.execute(
        f"UPDATE tasks SET {assignments} WHERE tenant_id = ? AND task_id = ?",
        params,
    )


def apply_task_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    # Soft-delete: row stays (rebuild correctness depends on it).
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE tasks
           SET status        = 'dismissed',
               last_event_id = ?
         WHERE tenant_id = ? AND task_id = ?
        """,
        (
            envelope["event_id"],
            envelope["tenant_id"],
            p["task_id"],
        ),
    )
