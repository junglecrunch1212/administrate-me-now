"""Notes approval, exact bridge dispatch and readback through the real runtime."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from adminme.apple_service_permissions import digest
from adminme.apple_service_history import apple_command_owner_matches
from adminme_bridge.config import load_config
from adminme_bridge.connector_packs.apple_notes import AppleNotesAdapter
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
    OTHER_HEADERS,
    _bridge_headers,
    _configuration_settings,
    _observation,
    _history,
    _settled_source_assurance,
)
from tests.acceptance.test_forwarded_channel_intake import _events


class _NotesBoundary:
    def __init__(self) -> None:
        self.effects: list[dict[str, Any]] = []

    async def apply_write(
        self, operation: str, payload: dict[str, Any], target: str | None
    ) -> dict[str, Any]:
        self.effects.append(
            {"operation": operation, "payload": payload, "target": target}
        )
        return {
            "applied": True,
            "suppressed": False,
            "external_id": "fixture-created-note",
        }


def test_notes_requires_both_approvals_exact_scope_then_reconciles_without_resend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
                "request_id": "notes-fixture-delegation",
                "context_scope": "member_private",
                "permissions": ["read_context", "prepare_external_actions"],
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
        body = {
            "request_id": "notes-fixture-create",
            "operation": "create_note",
            "provider_account_id": scope["provider_account_id"],
            "account_external_id": scope["account_id"],
            "folder_external_id": scope["container_id"],
            "body": "An ordinary test note",
            "title": "Notes test",
        }
        other = client.post(
            "/api/member/v1/notes/commands", headers=OTHER_HEADERS, json=body
        )
        assert other.status_code in {403, 409}
        escape = client.post(
            "/api/member/v1/notes/commands",
            headers=MEMBER_HEADERS,
            json={**body, "folder_external_id": "unselected-folder"},
        )
        assert escape.status_code == 403
        approved = client.post(
            "/api/member/v1/notes/commands", headers=MEMBER_HEADERS, json=body
        )
        assert approved.status_code == 202, approved.text
        approval_id = approved.json()["domain_approval_event_id"]
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        missing = _execute(client, approval_id, "notes-no-native", receipt=False)
        assert missing.status_code == 403
        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:notes",
            "Credential-free Notes acceptance",
        )
        executed = _execute(client, approval_id, "notes-native", receipt=True)
        assert executed.status_code == 200, executed.text
        headers = _bridge_headers(transfer)
        pending = client.get(
            "/api/bridge/commands", headers=headers, params={"providers": "apple_notes"}
        )
        assert pending.status_code == 200, pending.text
        assert pending.json()["count"] == 1
        command = pending.json()["commands"][0]
        record = next(
            value
            for value in runtime.settings.bridge_enrollments.values()
            if value.get("member_id") == MEMBER_A and value.get("status") == "active"
        )
        config = load_config(helper.paths.config_path).model_copy(
            update={"cursor_db_path": str(tmp_path / "notes-command-ledger.sqlite3")}
        )
        proof = client.post(
            f"/api/bridge/commands/{command['command_id']}/apple-authorization",
            headers=headers,
            json={
                "event_id": command["event_id"],
                "configuration_sha256": digest(config.pack_config),
            },
        )
        assert proof.status_code == 200, proof.text
        command["apple_service_authorization"] = proof.json()["authorization"]
        boundary = _NotesBoundary()
        adapter = AppleNotesAdapter(
            config=config.pack_config["knowledge-apple-notes"], provider=boundary
        )
        supervisor = BridgeSupervisor(
            config=config,
            ingest_client=object(),
            adapters=[adapter],
            device_identity_sha256=record["device_identity_sha256"],
        )  # type: ignore[arg-type]
        try:
            for path, value in (
                (["payload", "folder_external_id"], "unselected-folder"),
                (["payload", "body"], "changed text"),
                (["provider_account_id"], "other-account"),
            ):
                forged = copy.deepcopy(command)
                target = forged
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                with pytest.raises(ValueError):
                    supervisor._authorized_command_adapter(
                        forged, {"apple_notes": adapter}
                    )
            assert boundary.effects == []
            selected = supervisor._authorized_command_adapter(
                command, {"apple_notes": adapter}
            )
            provider_payload = {
                **command["payload"],
                "apple_service_allowed_container_ids": command[
                    "apple_service_authorization"
                ]["grant"]["allowed_container_ids"],
                "apple_service_authorization_expires_at": command[
                    "apple_service_authorization"
                ]["expires_at"],
            }
            result = asyncio.run(
                selected.apply_write(
                    command["operation"],
                    provider_payload,
                    command.get("target_external_id"),
                )
            )
            assert len(boundary.effects) == 1
            envelope = supervisor._result_envelope(
                command, result=result, provider_called=True
            )
            uploaded = client.post("/api/bridge/ingest", headers=headers, json=envelope)
            assert uploaded.status_code == 202, uploaded.text
            assert (
                client.get(
                    "/api/bridge/commands",
                    headers=headers,
                    params={"providers": "apple_notes"},
                ).json()["commands"]
                == []
            )
            observed = _observation(
                runtime, "apple_notes", "notes-independent-readback"
            )
            html = command["payload"]["body"]
            observed["payload"].update(
                source_external_id="fixture-created-note",
                body=html,
                body_format="html",
                content_sha256=hashlib.sha256(html.encode()).hexdigest(),
                title="Notes test",
                account_external_id=scope["account_id"],
                folder_external_id=scope["container_id"],
                provider_revision="2031-01-02T00:00:00.000Z",
            )
            accepted = client.post("/api/bridge/ingest", headers=headers, json=observed)
            assert accepted.status_code == 202, accepted.text
            client.portal.call(
                runtime.wait_for_projection,
                "member_knowledge",
                accepted.json()["event_id"],
            )
            rows = client.get("/api/member/v1/notes", headers=MEMBER_HEADERS).json()[
                "items"
            ]
            assert any(
                row["source_external_id"] == "fixture-created-note"
                and row["body"] == html
                for row in rows
            )
            status = client.get(
                "/api/member/v1/notes/commands/status",
                headers=MEMBER_HEADERS,
                params={"request_id": body["request_id"]},
            )
            assert status.status_code == 200, status.text
            assert status.json()["state"] == "completed"
            assert status.json()["request"]["body"] == body["body"]
            assert status.json()["domain_approval_event_id"] == approval_id
            hidden = client.get(
                "/api/member/v1/notes/commands/status",
                headers=OTHER_HEADERS,
                params={"request_id": body["request_id"]},
            )
            assert hidden.json()["state"] == "not_recorded"
            duplicate = client.post(
                "/api/member/v1/notes/commands", headers=MEMBER_HEADERS, json=body
            )
            assert duplicate.status_code == 202 and duplicate.json()["duplicate"]
            assert len(boundary.effects) == 1
            revoked = client.post(
                f"{PATH}/apple_notes",
                headers=MEMBER_HEADERS,
                json={
                    "request_id": "notes-fixture-revoke",
                    "expected_revision": 1,
                    "enabled": False,
                    "scope_ids": [],
                },
            )
            assert revoked.status_code == 202, revoked.text
            refused = client.post(
                "/api/member/v1/notes/commands",
                headers=MEMBER_HEADERS,
                json={**body, "request_id": "notes-after-revoke"},
            )
            assert refused.status_code == 409
            # Immutable authorization attributes the old effect even after the
            # current permission is revoked; this is not a new provider binding.
            history = client.portal.call(_history, runtime.event_log)
            assert history["state"] == "healthy", history
            assert any(
                row["binding_state"]
                == "internal_apple_service_member_source_reconciled"
                for row in history["groups"]
            )
            assurance = client.portal.call(
                _settled_source_assurance, runtime, accepted.json()["event_id"]
            )
            assert assurance["provider_effects"]["state"] == "healthy", assurance
            assert assurance["provider_effects"]["counts"]["provider_called_true"] == 1
            source_command = client.portal.call(
                runtime.event_log.get, command["event_id"]
            )

            async def historical(candidate: dict[str, Any]) -> bool:
                return await apple_command_owner_matches(
                    runtime.event_log, candidate, through=history["watermark_event_id"]
                )

            assert client.portal.call(historical, source_command)
            for field, value in (
                ("provider_account_id", "unknown-account"),
                ("domain_approval_event_id", "missing-approval"),
                ("member_id", "different-member"),
            ):
                forged = copy.deepcopy(source_command)
                forged["payload"][field] = value
                assert not client.portal.call(historical, forged)
        finally:
            supervisor._command_ledger.close()
