"""Apple service settings through retained enrollment and encrypted application."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from tests.acceptance.test_cos_member_bridge_acquisition import (
    CAPABILITIES,
    MEMBER_A,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    _configuration_settings,
    _enable,
    _manual_policies,
    _observation,
    _prepare_helper,
    _bridge_headers,
)
from tests.acceptance.test_forwarded_channel_intake import _events


PATH = "/api/member/v1/settings/apple-services"


def prepare_apple_sources(
    client: TestClient, tmp_path: Path
) -> tuple[Any, dict[str, Any]]:
    """Real enrolled source lifecycle, synthetic native permission boundaries."""
    runtime = client.app.state.runtime
    _manual_policies(client)
    helper, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
    for capability in CAPABILITIES:
        _enable(helper, capability)
    helper.finish_permission_phase()
    headers = _bridge_headers(transfer)
    # The selected calendar is deliberately empty: native authentication, not a
    # pre-existing calendar event, is the prerequisite for its first write.
    for capability in ("apple_reminders", "apple_notes"):
        envelope = _observation(runtime, capability, f"write-fixture-{capability}")
        if capability == "apple_notes":
            envelope["payload"].update(
                account_external_id="fixture-notes-account",
                folder_external_id="fixture-notes-folder",
                folder_path="Notes",
                provider_revision="2031-01-01T12:00:00+00:00",
            )
        response = client.post("/api/bridge/ingest", headers=headers, json=envelope)
        assert response.status_code == 202, response.text
        client.portal.call(
            runtime.wait_for_projection,
            "member_knowledge" if capability == "apple_notes" else "lists",
            response.json()["event_id"],
        )
    folder = _observation(runtime, "apple_notes", "fixture-write-folder")
    folder.update(type="member_knowledge.folder_created", schema_version=1)
    folder["payload"] = {
        "member_id": MEMBER_A,
        "source_kind": "apple_notes",
        "account_external_id": "fixture-notes-account",
        "folder_external_id": "fixture-notes-folder",
        "folder_path": "Notes",
        "changed_at": "2031-01-01T12:00:00+00:00",
    }
    response = client.post("/api/bridge/ingest", headers=headers, json=folder)
    assert response.status_code == 202, response.text
    client.portal.call(
        runtime.wait_for_projection, "member_knowledge", response.json()["event_id"]
    )
    return helper, transfer


def grant_services(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get(PATH, headers=MEMBER_HEADERS)
    assert response.status_code == 200, response.text
    commands = {}
    for item in response.json()["items"]:
        assert item["state"] == "disabled", item
        assert item["read_access"] and not item["write_access"]
        command = {
            "request_id": f"enable-{item['provider']}-fixture",
            "expected_revision": 0,
            "enabled": True,
            "scope_ids": [scope["id"] for scope in item["scopes"]],
        }
        enabled = client.post(
            f"{PATH}/{item['provider']}", headers=MEMBER_HEADERS, json=command
        )
        assert enabled.status_code == 202, enabled.text
        assert enabled.json()["provider_effects"] == 0
        commands[item["provider"]] = command
    return commands


def test_private_service_permissions_enable_reconcile_revoke_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    settings = _configuration_settings(tmp_path)
    app = create_application(settings)
    with TestClient(app, base_url="https://central-fixture.ts.net") as client:
        runtime = app.state.runtime
        prepare_apple_sources(client, tmp_path)
        original = copy.deepcopy(runtime.settings.bridge_enrollments)
        commands = grant_services(client)
        calendar = client.get(
            "/api/member/v1/surfaces/calendar", headers=MEMBER_HEADERS
        )
        assert calendar.status_code == 200, calendar.text
        assert calendar.json()["cockpit"]["write_destinations"]
        assert calendar.json()["cockpit"]["sources"] == []
        assert calendar.json()["events"] == []
        reminders = client.get(
            "/api/member/v1/surfaces/reminders", headers=MEMBER_HEADERS
        )
        assert reminders.status_code == 200, reminders.text
        from adminme.products.member.surfaces import reminder_accounts
        from adminme.lib.session import Session
        from adminme.projections.lists.queries import items_for_member, lists_for_member

        session = Session(
            tenant_id=runtime.settings.instance.tenant_id,
            auth_member_id=MEMBER_A,
            auth_role="principal",
            view_member_id=MEMBER_A,
            view_role="principal",
            dm_scope="per_account_channel_peer",
            source="member_portal",
        )
        conn = runtime.projections.connection("lists")
        accounts = reminder_accounts(
            runtime,
            MEMBER_A,
            projected_items=items_for_member(conn, session, member_id=MEMBER_A),
            projected_lists=lists_for_member(conn, session, member_id=MEMBER_A),
        )
        assert (
            "create_reminder"
            in next(row for row in accounts if row["provider"] == "apple_reminders")[
                "operations"
            ]
        )
        after = client.get(PATH, headers=MEMBER_HEADERS).json()
        assert all(
            item["write_access"] and item["approvals_required"]
            for item in after["items"]
        )
        for provider, command in commands.items():
            duplicate = client.post(
                f"{PATH}/{provider}", headers=MEMBER_HEADERS, json=command
            )
            assert duplicate.status_code == 202 and duplicate.json()["idempotent"]
            confirmation = client.get(
                PATH,
                headers=MEMBER_HEADERS,
                params={"request_id": command["request_id"], "provider": provider},
            ).json()["request_confirmation"]
            assert confirmation["state"] == "recorded"
            assert all(
                confirmation[key] == command[key]
                for key in ("request_id", "expected_revision", "enabled", "scope_ids")
            )
            stale = client.post(
                f"{PATH}/{provider}",
                headers=MEMBER_HEADERS,
                json={**command, "request_id": f"stale-{provider}"},
            )
            assert stale.status_code == 409
            conflict = client.post(
                f"{PATH}/{provider}",
                headers=MEMBER_HEADERS,
                json={**command, "enabled": False, "scope_ids": []},
            )
            assert conflict.status_code == 409
        others = client.get(PATH, headers=OTHER_HEADERS).json()["items"]
        assert all(not item["write_access"] and not item["scopes"] for item in others)
        denied = client.post(
            f"{PATH}/apple_notes", headers=OTHER_HEADERS, json=commands["apple_notes"]
        )
        assert denied.status_code == 409
        viewed = client.get(
            PATH,
            headers={
                **MEMBER_HEADERS,
                "X-AdminMe-View-Member": next(
                    v
                    for v in settings.member_roles
                    if v != MEMBER_A and settings.member_roles[v] == "principal"
                ),
            },
        )
        assert viewed.status_code == 403
        revoked_providers: set[str] = set()
        for provider in commands:
            revoke = client.post(
                f"{PATH}/{provider}",
                headers=MEMBER_HEADERS,
                json={
                    "request_id": f"revoke-{provider}-fixture",
                    "expected_revision": 1,
                    "enabled": False,
                    "scope_ids": [],
                },
            )
            assert revoke.status_code == 202, revoke.text
            revoked_providers.add(provider)
            current_permissions = client.get(PATH, headers=MEMBER_HEADERS).json()[
                "items"
            ]
            assert all(item["read_access"] for item in current_permissions)
            assert all(
                item["write_access"] is (item["provider"] not in revoked_providers)
                for item in current_permissions
            )
        assert all(
            item["state"] == "revoked" and not item["write_access"]
            for item in client.get(PATH, headers=MEMBER_HEADERS).json()["items"]
        )
        assert runtime.settings.bridge_enrollments == original
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_completed") == []
        )

        async def replay() -> None:
            await runtime.settings_control.replay_apple_service_permission_events(
                runtime.event_log, tenant_id=runtime.settings.instance.tenant_id
            )

        client.portal.call(replay)
        saved = runtime.settings_control.read()["apple_service_permissions"][MEMBER_A]
        assert all(
            value["revision"] == 2 and value["enabled"] is False
            for value in saved.values()
        )
        receipts = client.portal.call(
            _events, runtime, "settings.apple_service_permission_controlled"
        )
        assert len(receipts) == 6
        assert all(
            event["owner_scope"] == f"private:{MEMBER_A}"
            and event["visibility_scope"] == f"private:{MEMBER_A}"
            for event in receipts
        )

        # Foreign tenant receipts cannot change this instance's permission cache.
        from adminme.events.envelope import EventEnvelope

        foreign = {
            key: copy.deepcopy(value)
            for key, value in receipts[0].items()
            if key in EventEnvelope.model_fields
        }
        foreign.update(
            event_id="",
            recorded_at="",
            tenant_id="foreign-permission-tenant",
            idempotency_key="foreign-permission-receipt",
        )
        client.portal.call(
            runtime.event_log.append, EventEnvelope.model_validate(foreign)
        )
        after_foreign = client.get(PATH, headers=MEMBER_HEADERS)
        assert after_foreign.status_code == 200, after_foreign.text
        assert all(
            item["state"] == "revoked" and item["revision"] == 2
            for item in after_foreign.json()["items"]
        )
