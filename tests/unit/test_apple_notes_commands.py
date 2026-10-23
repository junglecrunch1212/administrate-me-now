"""Exact owner, source and rich-content boundaries for Notes confirmations."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from adminme.apple_notes_writes import assert_editable_note, note_body_html
from adminme.events.registry import registry
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.products.member.apple_notes_commands import (
    AppleNoteCommandRequest,
    prepare_note_payload,
    router,
)


def _permission() -> dict[str, Any]:
    return {
        "provider_account_id": "provider-account:" + "a" * 32,
        "account_external_id": "account-a",
        "allowed_container_ids": ["folder-a"],
        "operations": ["create_note", "update_note", "append_note", "delete_note"],
    }


def _note() -> dict[str, Any]:
    return {
        "knowledge_id": "note-a",
        "member_id": "member-a",
        "access_mode": "owner",
        "source_kind": "apple_notes",
        "source_external_id": "native-a",
        "account_external_id": "account-a",
        "folder_external_id": "folder-a",
        "last_event_id": "event-a",
        "provider_revision": "2026-01-01T00:00:00.000Z",
        "body": "<div>Existing ordinary text</div>",
        "body_format": "html",
        "locked": False,
        "shared": False,
        "attachments_json": "[]",
    }


def _body(**changes: Any) -> AppleNoteCommandRequest:
    value = {
        "request_id": "request-a",
        "operation": "create_note",
        "provider_account_id": _permission()["provider_account_id"],
        "account_external_id": "account-a",
        "folder_external_id": "folder-a",
        "body": "A note with <literal> & text",
        "title": "A title",
        **changes,
    }
    return AppleNoteCommandRequest.model_validate(value)


def test_text_is_escaped_and_preserves_line_breaks() -> None:
    assert note_body_html(body="one\n<two> & three", title="Title") == (
        "<div><b>Title</b></div><div>one</div><div>&lt;two&gt; &amp; three</div>"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_account_id": "other-account"},
        {"account_external_id": "other-native-account"},
        {"folder_external_id": "other-folder"},
    ],
)
def test_create_cannot_escape_selected_coordinates(changes: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as caught:
        prepare_note_payload(
            _body(**changes), permission=_permission(), note=None, member_id="member-a"
        )
    assert caught.value.status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"locked": True},
        {"shared": True},
        {"attachments_json": '[{"attachment_id":"a"}]'},
        {"body": '<div><img src="cid:image"></div>'},
        {
            "body": '<div style="-webkit-text-decorations-in-effect: underline">rich</div>'
        },
        {"body": "<table><tr><td>table</td></tr></table>"},
        {"provider_revision": None},
    ],
)
def test_unsupported_existing_notes_are_never_flattened(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        assert_editable_note({**_note(), **changes})


@pytest.mark.parametrize("operation", ["update_note", "append_note", "delete_note"])
def test_ordinary_existing_note_actions_bind_exact_revision(operation: str) -> None:
    body = _body(
        operation=operation,
        knowledge_id="note-a",
        expected_last_event_id="event-a",
        expected_provider_revision=_note()["provider_revision"],
        title=None,
        body=None if operation == "delete_note" else "Revised text",
    )
    payload, target = prepare_note_payload(
        body, permission=_permission(), note=_note(), member_id="member-a"
    )
    assert target == "native-a"
    assert payload["account_external_id"] == "account-a"
    assert payload["expected_provider_revision"] == _note()["provider_revision"]


@pytest.mark.parametrize(
    "changes",
    [
        {"member_id": "member-b"},
        {"access_mode": "shared_reference"},
        {"account_external_id": "account-b"},
        {"folder_external_id": "folder-b"},
        {"last_event_id": "new-event"},
        {"provider_revision": "new-revision"},
    ],
)
def test_existing_note_ownership_and_concurrency_are_required(
    changes: dict[str, Any],
) -> None:
    body = _body(
        operation="update_note",
        knowledge_id="note-a",
        expected_last_event_id="event-a",
        expected_provider_revision=_note()["provider_revision"],
    )
    with pytest.raises(HTTPException):
        prepare_note_payload(
            body,
            permission=_permission(),
            note={**_note(), **changes},
            member_id="member-a",
        )


@pytest.mark.asyncio
async def test_http_confirmation_creates_only_domain_approval_and_retries_immutably(
    monkeypatch: Any,
) -> None:
    from adminme import apple_service_permissions

    recorded: list[Any] = []

    async def get_prior(tenant: str, key: str) -> dict[str, Any] | None:
        for envelope in recorded:
            if envelope.tenant_id == tenant and envelope.idempotency_key == key:
                return {**envelope.model_dump(mode="json"), "event_id": "approval-a"}
        return None

    async def write(envelope: Any) -> str:
        registry.validate(envelope.type, envelope.schema_version, envelope.payload)
        recorded.append(envelope)
        return "approval-a"

    async def guard(*args: Any) -> Any:
        return SimpleNamespace(pass_=True)

    def bind(runtime: Any, member: str, command: dict[str, Any]) -> None:
        command["payload"]["apple_service_permission"] = {
            "event_id": "grant-a",
            "revision": 1,
            "coordinates_sha256": "b" * 64,
        }

    monkeypatch.setattr(
        apple_service_permissions, "service_permission", lambda *args: _permission()
    )
    monkeypatch.setattr(apple_service_permissions, "bind_command_permission", bind)
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = SimpleNamespace(
        projections=SimpleNamespace(),
        events=SimpleNamespace(write=write),
        event_log=SimpleNamespace(get_by_idempotency=get_prior),
        guarded_write=SimpleNamespace(check=guard),
        _observation_active=False,
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
    app.dependency_overrides[session_from_request] = lambda: session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = _body().model_dump(mode="json")
        response = await client.post("/api/member/v1/notes/commands", json=body)
        assert response.status_code == 202, response.text
        assert response.json()["state"] == "awaiting_openclaw_approval"
        assert response.json()["domain_approval_event_id"] == "approval-a"
        duplicate = await client.post("/api/member/v1/notes/commands", json=body)
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        changed = deepcopy(body)
        changed["body"] = "Different content"
        conflict = await client.post("/api/member/v1/notes/commands", json=changed)
        assert conflict.status_code == 409
    assert len(recorded) == 1
    assert recorded[0].type == "member.provider_action.confirmed"
    assert recorded[0].payload["provider"] == "apple_notes"
    assert (
        recorded[0].payload["command_payload"]["apple_service_permission"]["event_id"]
        == "grant-a"
    )
