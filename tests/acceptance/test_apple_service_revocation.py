"""Durable revocation wins even when its Settings cache write was interrupted."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.apple_service_permissions import digest
from adminme.runtime.application import create_application
from adminme_bridge.config import load_config
from adminme_bridge.main import BridgeSupervisor
from tests.acceptance.test_apple_messages_governed_reply import SECRET, _execute
from tests.acceptance.test_apple_service_permissions import (
    PATH,
    grant_services,
    prepare_apple_sources,
)
from tests.acceptance.test_cos_member_bridge_acquisition import (
    MEMBER_A,
    MEMBER_HEADERS,
    _bridge_headers,
    _configuration_settings,
)


def test_durable_revocation_denies_fresh_proof_and_accepts_original_terminal(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    base = _configuration_settings(tmp_path)
    settings = replace(
        base,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            **base.openclaw_actor_identities,
            "fixture|g11-account-a|g11-actor-a": MEMBER_A,
        },
    )
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as client:
        runtime = client.app.state.runtime
        helper, transfer = prepare_apple_sources(client, tmp_path)
        delegation = client.post(
            "/api/member/v1/cos/delegations",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "revocation-fixture-delegation",
                "context_scope": "member_private",
                "context_member_id": MEMBER_A,
                "permissions": ["read_context", "prepare_external_actions"],
                "expires_at": None,
            },
        )
        assert delegation.status_code == 201, delegation.text
        grant_services(client)
        notes = next(
            item
            for item in client.get(PATH, headers=MEMBER_HEADERS).json()["items"]
            if item["provider"] == "apple_notes"
        )
        scope = notes["scopes"][0]
        approved = client.post(
            "/api/member/v1/notes/commands",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "revocation-fixture-create",
                "operation": "create_note",
                "provider_account_id": scope["provider_account_id"],
                "account_external_id": scope["account_id"],
                "folder_external_id": scope["container_id"],
                "body": "Fixture only",
            },
        )
        assert approved.status_code == 202, approved.text
        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:revocation",
            "Credential-free permission regression",
        )
        executed = _execute(
            client,
            approved.json()["domain_approval_event_id"],
            "revocation-fixture-native",
            receipt=True,
        )
        assert executed.status_code == 200, executed.text
        headers = _bridge_headers(transfer)
        command = client.get(
            "/api/bridge/commands", headers=headers, params={"providers": "apple_notes"}
        ).json()["commands"][0]
        config = load_config(helper.paths.config_path).model_copy(
            update={"cursor_db_path": str(tmp_path / "terminal-ledger.db")}
        )
        proof_url = f"/api/bridge/commands/{command['command_id']}/apple-authorization"
        proof_body = {
            "event_id": command["event_id"],
            "configuration_sha256": digest(config.pack_config),
        }
        # The persisted observation toggle is authoritative in both directions;
        # the startup summary cache may lag behind an operator's change.
        client.portal.call(
            runtime.observation.enable,
            "operator:fixture:revocation",
            "Credential-free observation boundary regression",
        )
        runtime._observation_active = False
        suppressed = client.post(proof_url, headers=headers, json=proof_body)
        assert suppressed.status_code == 409, suppressed.text
        assert suppressed.json()["detail"] == "Observation mode suppresses Apple writes"
        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:revocation",
            "Credential-free observation boundary regression",
        )
        runtime._observation_active = True
        permitted = client.post(proof_url, headers=headers, json=proof_body)
        assert permitted.status_code == 200, permitted.text

        def interrupted(*_args, **_kwargs):
            raise RuntimeError("fixture cache interruption")

        monkeypatch.setattr(
            runtime.settings_control,
            "apply_apple_service_permission_event",
            interrupted,
        )
        with pytest.raises(RuntimeError, match="fixture cache interruption"):
            client.post(
                f"{PATH}/apple_notes",
                headers=MEMBER_HEADERS,
                json={
                    "request_id": "revocation-fixture-revoke",
                    "expected_revision": 1,
                    "enabled": False,
                    "scope_ids": [],
                },
            )
        assert (
            runtime.settings_control.read()["apple_service_permissions"][MEMBER_A][
                "apple_notes"
            ]["enabled"]
            is True
        )
        proof = client.post(proof_url, headers=headers, json=proof_body)
        assert proof.status_code == 409, proof.text
        assert proof.json()["detail"] != "Observation mode suppresses Apple writes"
        assert (
            runtime.settings_control.read()["apple_service_permissions"][MEMBER_A][
                "apple_notes"
            ]["enabled"]
            is False
        )
        denied = client.get(
            "/api/bridge/commands", headers=headers, params={"providers": "apple_notes"}
        )
        assert denied.status_code == 200, denied.text
        assert denied.json()["commands"][0]["apple_authorization_denied"] is True
        supervisor = BridgeSupervisor(
            config=config, ingest_client=object(), adapters=[]
        )
        try:
            envelope = supervisor._result_envelope(
                command,
                result={
                    "outcome": "ambiguous",
                    "applied": False,
                    "automatic_retry": False,
                },
                provider_called=True,
            )
            terminal = client.post("/api/bridge/ingest", headers=headers, json=envelope)
            assert terminal.status_code == 202, terminal.text
            assert (
                client.get(
                    "/api/bridge/commands",
                    headers=headers,
                    params={"providers": "apple_notes"},
                ).json()["commands"]
                == []
            )
        finally:
            supervisor._command_ledger.close()
