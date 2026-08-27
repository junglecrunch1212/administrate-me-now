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


def _envelope_json(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, default=_iso, separators=(",", ":"), sort_keys=True)


def _record_list_observation(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    conn.execute(
        """
        INSERT INTO list_observations (
            tenant_id, provider, provider_account_id, member_id,
            list_external_id, owner_scope, visibility_scope, last_event_id,
            envelope_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(
            tenant_id, provider, provider_account_id, member_id, list_external_id
        ) DO UPDATE SET owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            last_event_id=excluded.last_event_id,
            envelope_json=excluded.envelope_json
        """,
        (
            envelope["tenant_id"],
            payload["provider"],
            payload["provider_account_id"],
            payload["member_id"],
            payload["list_external_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["event_id"],
            _envelope_json(envelope),
        ),
    )


def _record_list_item_observation(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    provider = _provider(envelope, payload)
    account = payload.get("provider_account_id")
    if not isinstance(account, str) or not account:
        # v1 facts predate route-complete provider coordinates.  They remain
        # canonical facts but cannot be presented as exact bridge observations.
        return
    conn.execute(
        """
        INSERT INTO list_item_observations (
            tenant_id, provider, provider_account_id, member_id,
            list_external_id, item_external_id, owner_scope, visibility_scope,
            last_event_id, envelope_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(
            tenant_id, provider, provider_account_id, member_id,
            list_external_id, item_external_id
        ) DO UPDATE SET owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            last_event_id=excluded.last_event_id,
            envelope_json=excluded.envelope_json
        """,
        (
            envelope["tenant_id"],
            provider,
            account,
            payload["member_id"],
            payload["list_external_id"],
            payload["item_external_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["event_id"],
            _envelope_json(envelope),
        ),
    )


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    if event_type in {"list.created", "list.updated"}:
        apply_list_changed(envelope, conn)
    elif event_type == "list.deleted":
        apply_list_deleted(envelope, conn)
    elif event_type in {
        "list_item.added",
        "list_item.updated",
        "list_item.completed",
        "list_item.uncompleted",
    }:
        apply_list_item_snapshot(envelope, conn)
    elif event_type == "list_item.deleted":
        apply_list_item_deleted(envelope, conn)
    elif event_type == "context.signal.observed":
        apply_context_signal(envelope, conn)
    elif event_type == "reminder.cue.configured":
        apply_reminder_cue(envelope, conn)
    elif event_type == "reminder.cue.cleared":
        clear_reminder_cue(envelope, conn)


def apply_list_changed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    _record_list_observation(envelope, conn)
    canonical_account = (
        "shared:household"
        if envelope["owner_scope"] == "shared:household"
        else p["provider_account_id"]
    )
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
            envelope["tenant_id"],
            p["member_id"],
            p["provider"],
            canonical_account,
            p["list_external_id"],
            p["list_name"],
            p.get("color"),
            1 if p.get("is_default") else 0,
            p.get("provider_revision"),
            _iso(p["changed_at"]),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_list_deleted(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        DELETE FROM list_observations
         WHERE tenant_id=? AND provider=? AND provider_account_id=?
           AND member_id=? AND list_external_id=?
        """,
        (
            envelope["tenant_id"],
            p["provider"],
            p["provider_account_id"],
            p["member_id"],
            p["list_external_id"],
        ),
    )
    shared_observations = conn.execute(
        """
        SELECT count(*) FROM list_observations
         WHERE tenant_id=? AND provider=? AND list_external_id=?
           AND owner_scope='shared:household'
        """,
        (envelope["tenant_id"], p["provider"], p["list_external_id"]),
    ).fetchone()[0]
    canonical_account = (
        "shared:household"
        if envelope["owner_scope"] == "shared:household"
        else p["provider_account_id"]
    )
    if envelope["owner_scope"] == "shared:household" and shared_observations:
        return
    conn.execute(
        "DELETE FROM lists WHERE tenant_id=? AND provider=? AND provider_account_id=? AND list_external_id=?",
        (
            envelope["tenant_id"],
            p["provider"],
            canonical_account,
            p["list_external_id"],
        ),
    )
    conn.execute(
        "DELETE FROM list_items WHERE tenant_id=? AND list_external_id=? AND member_id=?",
        (envelope["tenant_id"], p["list_external_id"], p["member_id"]),
    )


def apply_list_item_snapshot(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    _record_list_item_observation(envelope, conn)
    v2 = int(envelope.get("schema_version", 1)) >= 2
    due = p.get("due") if v2 else None
    due_at = p.get("due_at")
    if due_at is None and isinstance(due, dict):
        year, month, day = due.get("year"), due.get("month"), due.get("day")
        if year is not None and month is not None and day is not None:
            due_at = (
                f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                f"T{int(due.get('hour') or 0):02d}:{int(due.get('minute') or 0):02d}:00"
            )
    conn.execute(
        """
        INSERT INTO list_items (
            tenant_id, list_external_id, item_external_id, member_id,
            provider, provider_account_id, list_name, list_color, title, notes,
            url, due_at, priority, completed, completion_at, start_json, due_json,
            alarms_json, recurrence_json, flagged, tags_json, parent_external_id,
            sort_order, provider_position, source_links_json, provider_data_json,
            read_only_reason, linked_item_kind, linked_item_id, completion_policy,
            provider_revision, last_modified_at, sync_cycle_id,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            sort_order=excluded.sort_order,
            provider_position=excluded.provider_position,
            source_links_json=excluded.source_links_json,
            provider_data_json=excluded.provider_data_json,
            read_only_reason=excluded.read_only_reason,
            linked_item_kind=excluded.linked_item_kind,
            linked_item_id=excluded.linked_item_id,
            completion_policy=excluded.completion_policy,
            provider_revision=excluded.provider_revision,
            last_modified_at=excluded.last_modified_at,
            sync_cycle_id=excluded.sync_cycle_id, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["list_external_id"],
            p["item_external_id"],
            p["member_id"],
            _provider(envelope, p),
            p.get("provider_account_id"),
            p["list_name"],
            p.get("list_color"),
            p["title"],
            p.get("notes"),
            p.get("url"),
            _iso(due_at),
            int(p.get("priority") or 0),
            1 if p.get("completed") else 0,
            _iso(p.get("completion_at")),
            _json(p.get("start"), None),
            _json(due, None),
            _json(p.get("alarms"), []),
            _json(p.get("recurrence_rules"), []),
            None if p.get("flagged") is None else (1 if p.get("flagged") else 0),
            None if p.get("tags") is None else _json(p.get("tags"), []),
            p.get("parent_external_id"),
            p.get("sort_order"),
            p.get("provider_position"),
            _json(p.get("source_links"), []),
            _json(p.get("provider_data"), {}),
            p.get("read_only_reason"),
            p.get("linked_item_kind"),
            p.get("linked_item_id"),
            p.get("completion_policy", "manual"),
            p.get("provider_revision"),
            _iso(p["last_modified_at"]),
            p["sync_cycle_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


apply_list_item_added = apply_list_item_snapshot


def apply_list_item_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    provider = _provider(envelope, p)
    account = p.get("provider_account_id")
    if isinstance(account, str) and account:
        conn.execute(
            """
            DELETE FROM list_item_observations
             WHERE tenant_id=? AND provider=? AND provider_account_id=?
               AND member_id=? AND list_external_id=? AND item_external_id=?
            """,
            (
                envelope["tenant_id"],
                provider,
                account,
                p["member_id"],
                p["list_external_id"],
                p["item_external_id"],
            ),
        )
    if envelope["owner_scope"] == "shared:household":
        remaining = conn.execute(
            """
            SELECT count(*) FROM list_item_observations
             WHERE tenant_id=? AND provider=? AND list_external_id=?
               AND item_external_id=? AND owner_scope='shared:household'
            """,
            (
                envelope["tenant_id"],
                provider,
                p["list_external_id"],
                p["item_external_id"],
            ),
        ).fetchone()[0]
        if remaining:
            return
    conn.execute(
        "DELETE FROM list_items WHERE tenant_id=? AND list_external_id=? AND item_external_id=?",
        (envelope["tenant_id"], p["list_external_id"], p["item_external_id"]),
    )


def apply_context_signal(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO context_signals (
            tenant_id, member_id, signal_key, signal_id, source, kind, state,
            numeric_value, unit, latitude, longitude, accuracy_meters,
            observed_at, expires_at, attributes_json, owner_scope,
            visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, member_id, signal_key) DO UPDATE SET
            signal_id=excluded.signal_id, source=excluded.source,
            kind=excluded.kind, state=excluded.state,
            numeric_value=excluded.numeric_value, unit=excluded.unit,
            latitude=excluded.latitude, longitude=excluded.longitude,
            accuracy_meters=excluded.accuracy_meters,
            observed_at=excluded.observed_at, expires_at=excluded.expires_at,
            attributes_json=excluded.attributes_json,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        WHERE excluded.observed_at >= context_signals.observed_at
        """,
        (
            envelope["tenant_id"],
            p["member_id"],
            p["signal_key"],
            p["signal_id"],
            p["source"],
            p["kind"],
            p["state"],
            p.get("numeric_value"),
            p.get("unit"),
            p.get("latitude"),
            p.get("longitude"),
            p.get("accuracy_meters"),
            _iso(p["observed_at"]),
            _iso(p.get("expires_at")),
            _json(p.get("attributes"), {}),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_reminder_cue(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO reminder_cues (
            tenant_id, cue_id, member_id, provider, provider_account_id,
            list_external_id, item_external_id, signal_key, source, kind,
            operator, expected_state, expected_number, enabled, configured_at,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, cue_id) DO UPDATE SET
            member_id=excluded.member_id, provider=excluded.provider,
            provider_account_id=excluded.provider_account_id,
            list_external_id=excluded.list_external_id,
            item_external_id=excluded.item_external_id,
            signal_key=excluded.signal_key, source=excluded.source,
            kind=excluded.kind, operator=excluded.operator,
            expected_state=excluded.expected_state,
            expected_number=excluded.expected_number, enabled=excluded.enabled,
            configured_at=excluded.configured_at, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["cue_id"],
            p["member_id"],
            p["provider"],
            p["provider_account_id"],
            p["list_external_id"],
            p["item_external_id"],
            p["signal_key"],
            p["source"],
            p["kind"],
            p["operator"],
            p.get("expected_state"),
            p.get("expected_number"),
            1 if p.get("enabled", True) else 0,
            _iso(p["configured_at"]),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def clear_reminder_cue(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        "DELETE FROM reminder_cues WHERE tenant_id=? AND cue_id=? AND member_id=?",
        (envelope["tenant_id"], p["cue_id"], p["member_id"]),
    )
