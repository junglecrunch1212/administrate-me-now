"""
Lists projection handlers — UPSERT list_item.added@v1 into list_items.

Per ADMINISTRATEME_BUILD.md §L3 and SYSTEM_INVARIANTS.md §2 invariant 4
(apply MUST be idempotent: re-applying the same envelope produces the
same row state). Mirrors the ``apply_task_created`` pattern in
``adminme/projections/tasks/handlers.py``.

Subscribed event types:
- ``list_item.added`` → INSERT row from payload + envelope, ON CONFLICT
  UPDATE every column.

Other ``list_item.*`` types (updated/completed/deleted) are explicitly
no-ops at this projection version (1) because no first-party producer
emits them yet [D18]. Subscribing without a handler would break rebuild
correctness; a future 11f-β extends both.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlcipher3


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "list_item.added": apply_list_item_added,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def _iso(value: Any) -> Any:
    """ListItemAddedV1 datetimes arrive as ``datetime`` (pydantic v2);
    SQLite stores TEXT. Coerce only datetimes so str/None pass through."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def apply_list_item_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    # Idempotent UPSERT keyed by composite PK — replay-safe per §2 invariant 4.
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO list_items (
            tenant_id, list_external_id, item_external_id, member_id,
            list_name, title, notes, due_at, priority, completed,
            last_modified_at, sync_cycle_id,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, list_external_id, item_external_id) DO UPDATE SET
            member_id        = excluded.member_id,
            list_name        = excluded.list_name,
            title            = excluded.title,
            notes            = excluded.notes,
            due_at           = excluded.due_at,
            priority         = excluded.priority,
            completed        = excluded.completed,
            last_modified_at = excluded.last_modified_at,
            sync_cycle_id    = excluded.sync_cycle_id,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["list_external_id"],
            p["item_external_id"],
            p["member_id"],
            p["list_name"],
            p["title"],
            p.get("notes"),
            _iso(p.get("due_at")),
            p["priority"],
            1 if p.get("completed") else 0,
            _iso(p["last_modified_at"]),
            p["sync_cycle_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
