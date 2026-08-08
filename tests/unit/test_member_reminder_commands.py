"""Fail-closed member/list boundaries for provider reminder commands."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from adminme.lib.session import Session
from adminme.products.member.main import (
    ReminderCommandRequest,
    _provider_reminder_payload,
    _validate_reminder_command_target,
)


def _rig() -> tuple[SimpleNamespace, Session]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE lists (
            tenant_id TEXT, member_id TEXT, provider TEXT,
            provider_account_id TEXT, list_external_id TEXT, list_name TEXT,
            provider_revision TEXT, last_event_id TEXT,
            owner_scope TEXT, visibility_scope TEXT, sensitivity TEXT
        );
        CREATE TABLE list_items (
            tenant_id TEXT, member_id TEXT, provider TEXT,
            provider_account_id TEXT, list_external_id TEXT,
            item_external_id TEXT, read_only_reason TEXT,
            provider_revision TEXT, last_event_id TEXT,
            owner_scope TEXT, visibility_scope TEXT, sensitivity TEXT
        );
        """
    )
    rows = [
        ("member-a", "list-a", "Own list", "list-event-a"),
        ("member-b", "list-b", "Other list", "list-event-b"),
    ]
    for member_id, list_id, name, event_id in rows:
        conn.execute(
            """
            INSERT INTO lists VALUES (
                'tenant-a', ?, 'google_tasks', 'google-a', ?, ?,
                'etag-list', ?, ?, ?, 'normal'
            )
            """,
            (
                member_id,
                list_id,
                name,
                event_id,
                f"private:{member_id}",
                f"private:{member_id}",
            ),
        )
    conn.execute(
        """
        INSERT INTO list_items VALUES (
            'tenant-a', 'member-a', 'google_tasks', 'google-a', 'list-a',
            'item-a', NULL, 'etag-item', 'item-event-a',
            'private:member-a', 'private:member-a', 'normal'
        )
        """
    )
    runtime = SimpleNamespace(
        projections=SimpleNamespace(connection=lambda _name: conn)
    )
    session = Session(
        tenant_id="tenant-a",
        auth_member_id="member-a",
        auth_role="principal",
        view_member_id="member-a",
        view_role="principal",
        dm_scope="per_channel_peer",
        source="member_portal",
    )
    return runtime, session


def _command(**updates: object) -> ReminderCommandRequest:
    raw: dict[str, object] = {
        "provider": "google_tasks",
        "provider_account_id": "google-a",
        "operation": "create_task",
        "list_external_id": "list-a",
        "payload": {"title": "Pick up milk"},
        "request_id": "request-reminders-2",
    }
    raw.update(updates)
    return ReminderCommandRequest.model_validate(raw)


def test_create_fails_closed_for_another_members_list() -> None:
    runtime, session = _rig()
    body = _command(list_external_id="list-b")
    payload, _ = _provider_reminder_payload(body)

    with pytest.raises(HTTPException, match="destination reminder list not found"):
        _validate_reminder_command_target(runtime, session, body, payload)


def test_move_fails_closed_for_another_members_destination() -> None:
    runtime, session = _rig()
    body = _command(
        operation="move_task",
        item_external_id="item-a",
        expected_last_event_id="item-event-a",
        payload={"destination_list_external_id": "list-b"},
    )
    payload, _ = _provider_reminder_payload(body)

    with pytest.raises(HTTPException, match="destination reminder list not found"):
        _validate_reminder_command_target(runtime, session, body, payload)


def test_list_mutation_requires_projection_optimistic_concurrency() -> None:
    runtime, session = _rig()
    body = _command(
        operation="update_list",
        payload={"title": "Renamed"},
        expected_last_event_id="stale-list-event",
    )
    payload, _ = _provider_reminder_payload(body)

    with pytest.raises(HTTPException, match="list changed"):
        _validate_reminder_command_target(runtime, session, body, payload)
