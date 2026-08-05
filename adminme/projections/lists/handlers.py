"""Deterministic reducers for list metadata and rich reminder snapshots."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import sqlcipher3


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _json(value: Any, default: Any) -> str:
    return json.dumps(default if value is None else value, separators=(",", ":"))


def _provider(envelope: dict[str, Any], payload: dict[str, Any]) -> str:
    if payload.get("provider"):
        return str(payload["provider"])
    source = str(envelope.get("source_adapter") or "")
    return "google_tasks" if "google-tasks" in source else "apple_reminders"


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    if event_type in {"list.created", "list.updated"}:
        apply_list_changed(envelope, conn)
    elif event_type == "list.deleted":
        apply_list_deleted(envelope, conn)
    elif event_type in {
        "list_item.added", "list_item.updated", "list_item.completed",
        "list_item.uncompleted",
    }:
        apply_list_item_snapshot(envelope, conn)
    elif event_type == "list_item.deleted":
        apply_list_item_deleted(envelope, conn)


def apply_list_changed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO lists (
            tenant_id, member_id, provider, provider_account_id,
            list_external_id, list_name, color, is_default, provider_revision,
            changed_at, owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, provider, provider_account_id, list_external_id)
        DO UPDATE SET member_id=excluded.member_id, list_name=excluded.list_name,
            color=excluded.color, is_default=excluded.is_default,
            provider_revision=excluded.provider_revision,
            changed_at=excluded.changed_at, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"], p["member_id"], p["provider"],
            p["provider_account_id"], p["list_external_id"], p["list_name"],
            p.get("color"), 1 if p.get("is_default") else 0,
            p.get("provider_revision"), _iso(p["changed_at"]),
            envelope["owner_scope"], envelope["visibility_scope"],
            envelope["sensitivity"], envelope["event_id"],
        ),
    )


def apply_list_deleted(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        "DELETE FROM lists WHERE tenant_id=? AND provider=? AND provider_account_id=? AND list_external_id=?",
        (envelope["tenant_id"], p["provider"], p["provider_account_id"], p["list_external_id"]),
    )
    conn.execute(
        "DELETE FROM list_items WHERE tenant_id=? AND list_external_id=? AND member_id=?",
        (envelope["tenant_id"], p["list_external_id"], p["member_id"]),
    )


def apply_list_item_snapshot(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    v2 = int(envelope.get("schema_version", 1)) >= 2
    due = p.get("due") if v2 else None
    due_at = p.get("due_at")
    if due_at is None and isinstance(due, dict):
        parts = [due.get("year"), due.get("month"), due.get("day")]
        if all(part is not None for part in parts):
            due_at = (
                f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                f"T{int(due.get('hour') or 0):02d}:{int(due.get('minute') or 0):02d}:00"
            )
    conn.execute(
        """
        INSERT INTO list_items (
            tenant_id, list_external_id, item_external_id, member_id,
            provider, provider_account_id, list_name, list_color, title, notes,
            url, due_at, priority, completed, completion_at, start_json, due_json,
            alarms_json, recurrence_json, flagged, tags_json, parent_external_id,
            sort_order, linked_item_kind, linked_item_id, completion_policy,
            provider_revision, last_modified_at, sync_cycle_id,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, list_external_id, item_external_id) DO UPDATE SET
            member_id=excluded.member_id, provider=excluded.provider,
            provider_account_id=excluded.provider_account_id,
            list_name=excluded.list_name, list_color=excluded.list_color,
            title=excluded.title, notes=excluded.notes, url=excluded.url,
            due_at=excluded.due_at, priority=excluded.priority,
            completed=excluded.completed, completion_at=excluded.completion_at,
            start_json=excluded.start_json, due_json=excluded.due_json,
            alarms_json=excluded.alarms_json, recurrence_json=excluded.recurrence_json,
            flagged=excluded.flagged, tags_json=excluded.tags_json,
            parent_external_id=excluded.parent_external_id,
            sort_order=excluded.sort_order, linked_item_kind=excluded.linked_item_kind,
            linked_item_id=excluded.linked_item_id,
            completion_policy=excluded.completion_policy,
            provider_revision=excluded.provider_revision,
            last_modified_at=excluded.last_modified_at,
            sync_cycle_id=excluded.sync_cycle_id, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"], p["list_external_id"], p["item_external_id"],
            p["member_id"], _provider(envelope, p), p.get("provider_account_id"),
            p["list_name"], p.get("list_color"), p["title"], p.get("notes"),
            p.get("url"), _iso(due_at), int(p.get("priority") or 0),
            1 if p.get("completed") else 0, _iso(p.get("completion_at")),
            _json(p.get("start"), None), _json(due, None),
            _json(p.get("alarms"), []), _json(p.get("recurrence_rules"), []),
            None if p.get("flagged") is None else (1 if p.get("flagged") else 0),
            None if p.get("tags") is None else _json(p.get("tags"), []),
            p.get("parent_external_id"), p.get("sort_order"),
            p.get("linked_item_kind"), p.get("linked_item_id"),
            p.get("completion_policy", "manual"), p.get("provider_revision"),
            _iso(p["last_modified_at"]), p["sync_cycle_id"],
            envelope["owner_scope"], envelope["visibility_scope"],
            envelope["sensitivity"], envelope["event_id"],
        ),
    )


apply_list_item_added = apply_list_item_snapshot


def apply_list_item_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        "DELETE FROM list_items WHERE tenant_id=? AND list_external_id=? AND item_external_id=?",
        (envelope["tenant_id"], p["list_external_id"], p["item_external_id"]),
    )
