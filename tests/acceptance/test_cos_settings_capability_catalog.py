"""G08 process proof for CoS Settings and the capability catalog."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.openclaw_approvals import sign_approval_receipt
from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.wizard import BootstrapWizard


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"s" * 32
SECRET = "g08-loopback-secret-at-least-32-bytes"
A = "member-g08-a"  # fixture:tenant_data:ok
B = "member-g08-b"  # fixture:tenant_data:ok
A_HEADERS = {"Tailscale-User-Login": "g08-a.ts.net"}
B_HEADERS = {"Tailscale-User-Login": "g08-b.ts.net"}
OPENCLAW_HEADERS = {"Authorization": f"Bearer {SECRET}"}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"g08-a.ts.net": A, "g08-b.ts.net": B},
        member_roles={A: "principal", B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "G08 fixture household",  # fixture:tenant_data:ok
            "members": [
                {"member_id": A, "label": "Fixture adult A", "role": "principal"},
                {"member_id": B, "label": "Fixture adult B", "role": "principal"},
            ],
            "memberships": [
                {
                    "membership_id": "g08-membership-a",
                    "party_id": A,
                    "role": "principal",
                },
                {
                    "membership_id": "g08-membership-b",
                    "party_id": B,
                    "role": "principal",
                },
            ],
            "relationships": [],
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=A,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "fixture|g08-account-a|g08-actor-a": A,
            "fixture|g08-account-b|g08-actor-b": B,
        },
    )


def _provenance(member: str, correlation_id: str) -> dict[str, Any]:
    suffix = "a" if member == A else "b"
    return {
        "actor_id": f"g08-actor-{suffix}",
        "agent_id": f"g08-agent-{suffix}",
        "session_key": f"g08-session-key-{suffix}",
        "session_id": f"g08-session-{suffix}",
        "channel_id": "fixture",
        "account_id": f"g08-account-{suffix}",
        "message_id": f"g08-message-{correlation_id}",
        "thread_id": f"g08-thread-{suffix}",
        "correlation_id": correlation_id,
        "call_kind": "tool",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _grant(client: TestClient, member: str, request_id: str) -> str:
    response = client.post(
        "/api/member/v1/cos/delegations",
        headers=A_HEADERS if member == A else B_HEADERS,
        json={
            "request_id": request_id,
            "context_scope": "member_private",
            "permissions": ["read_context"],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["grant_id"])


def _allow_once(
    client: TestClient,
    *,
    receipt_id: str,
    tool_name: str,
    params: dict[str, Any],
) -> str:
    fingerprint = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    issued_at_ms = int(time.time() * 1000)
    expires_at_ms = issued_at_ms + 120_000
    signature = sign_approval_receipt(
        SECRET,
        receipt_id=receipt_id,
        tool_name=tool_name,
        request_fingerprint=fingerprint,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        openclaw_version=OPENCLAW_VERSION,
        openclaw_commit=OPENCLAW_COMMIT,
    )
    response = client.post(
        "/api/openclaw/v1/approvals/resolve",
        headers=OPENCLAW_HEADERS,
        json={
            "receipt_id": receipt_id,
            "tool_name": tool_name,
            "request_fingerprint": fingerprint,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            "decision": "allow-once",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "signature": signature,
        },
    )
    assert response.status_code == 200, response.text
    return receipt_id


async def _provider_effect_count(runtime: Any) -> int:
    types = [
        "external_sync.write_requested",
        "external_sync.write_sent",
        "external_sync.write_completed",
        "provider.command.requested",
    ]
    return sum([1 async for _event in runtime.event_log.read_since(types=types)])


def test_g08_catalog_settings_openclaw_isolation_and_replay(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)

    with TestClient(app) as client:
        runtime = app.state.runtime
        _grant(client, A, "g08-grant-a")
        _grant(client, B, "g08-grant-b")

        asset = client.get("/member-assets/member.js")
        assert asset.status_code == 200
        for section in (
            "Overview",
            "My identity",
            "CoS behavior",
            "Source intake & context",
            "How my CoS works",
            "Capabilities",
            "Connections",
            "Devices & bridges",
            "Learning & trust",
            "Household & privacy",
            "Safety & system",
            "Admin requests",
        ):
            assert section in asset.text

        a_surface = client.get("/api/member/v1/surfaces/settings", headers=A_HEADERS)
        assert a_surface.status_code == 200, a_surface.text
        control = a_surface.json()["control"]
        catalog = control["capabilities"]
        assert catalog["tenant_neutral"] is True
        assert catalog["catalog_presence_grants_nothing"] is True
        assert catalog["provider_effect_count"] == 0
        assert catalog["summary"]["known"] == len(catalog["items"])
        item_by_id = {item["capability_id"]: item for item in catalog["items"]}
        assert item_by_id["planned.apple_messages_observation"]["implementation"] == (
            "superseded_by_bridge.messaging-bluebubbles"
        )
        assert item_by_id["planned.apple_messages_reply"]["implementation"] == (
            "superseded_by_bridge.messaging-bluebubbles"
        )
        assert item_by_id["planned.apple_messages_reply"]["effective"] == {
            "read_available": False,
            "execute_available": False,
            "paused": False,
            "requested": False,
            "view_as_read_only": False,
        }
        bridge_ids = {
            item["capability_id"]
            for item in catalog["items"]
            if item["source_kind"] == "bridge_manifest"
        }
        assert bridge_ids == {
            "bridge.calendaring-apple-calendar-bridge",
            "bridge.contacts-apple-contacts-bridge",
            "bridge.messaging-bluebubbles",
            "bridge.knowledge-apple-notes",
            "bridge.knowledge-apple-voice-memos",
            "bridge.knowledge-obsidian",
            "bridge.reminders-apple-eventkit-bridge",
        }

        member_request = client.post(
            "/api/member/v1/settings/capabilities/planned.apple_messages_observation",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "g08-member-capability-request",
                "action": "request",
                "requested_scope": "member_private",
            },
        )
        assert member_request.status_code == 202, member_request.text
        assert member_request.json()["operational_effects"] is False

        b_surface = client.get(
            "/api/member/v1/surfaces/settings", headers=B_HEADERS
        ).json()["control"]
        assert b_surface["capability_requests"] == []
        assert b_surface["admin_requests"]["items"] == []
        a_after_request = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]
        assert (
            a_after_request["admin_requests"]["items"][0]["requesting_member_id"] == A
        )
        assert (
            a_after_request["admin_requests"]["items"][0][
                "admin_may_complete_private_consent"
            ]
            is False
        )
        assert a_after_request["capability_request_revision"] == 1
        assert a_after_request["capability_requests"][0]["revision"] == 1
        assert (
            a_after_request["cos_identity"]["service_principal_id"]
            == b_surface["cos_identity"]["service_principal_id"]
        )
        assert (
            a_after_request["cos_identity"]["openclaw_agent_id"]
            != b_surface["cos_identity"]["openclaw_agent_id"]
        )
        viewed = client.get(
            "/api/member/v1/surfaces/settings",
            headers={**A_HEADERS, "X-AdminMe-View-Member": B},
        )
        assert viewed.status_code == 200, viewed.text
        viewed_control = viewed.json()["control"]
        assert viewed_control["identity"]["can_mutate"] is False
        assert viewed_control["connections"] == []
        assert viewed_control["bridges"] == []
        assert viewed_control["capability_requests"] == []
        assert viewed_control["how_my_cos_works"]["compiled_context"] is None
        assert all(
            item["request"] is None for item in viewed_control["capabilities"]["items"]
        )
        denied_view_as_write = client.post(
            "/api/member/v1/settings/capabilities/planned.apple_messages_reply",
            headers={**A_HEADERS, "X-AdminMe-View-Member": B},
            json={
                "expected_revision": 0,
                "request_id": "g08-view-as-write-denied",
                "action": "request",
                "requested_scope": "member_private",
            },
        )
        assert denied_view_as_write.status_code == 403

        read_catalog = client.post(
            "/api/openclaw/v1/tools/capabilities",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "g08-catalog-read"),
                "capability_id": "planned.apple_messages_reply",
            },
        )
        assert read_catalog.status_code == 200, read_catalog.text
        assert read_catalog.json()["item"]["effective"]["execute_available"] is False

        context = client.post(
            "/api/openclaw/v1/tools/settings-context",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(A, "g08-context-read")},
        )
        assert context.status_code == 200, context.text
        compiled = context.json()
        assert compiled["identity"]["active_member_id"] == A
        assert compiled["identity"]["isolated_member_context"] is True
        assert "credentials" in compiled["excluded"]
        assert "raw_private_evidence" in compiled["excluded"]
        assert "generic_household_memory" in compiled["excluded"]
        assert "conversation_transcripts" in compiled["excluded"]
        assert "keychain://" not in json.dumps(compiled)

        params = {
            "request_id": "g08-openclaw-capability-request",
            "expected_revision": 1,
            "capability_id": "planned.apple_messages_reply",
            "action": "request",
            "requested_scope": "member_private",
        }
        receipt_id = _allow_once(
            client,
            receipt_id="g08-capability-receipt",
            tool_name="administrateme_capability_request",
            params=params,
        )
        requested = client.post(
            "/api/openclaw/v1/tools/capability-request",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "g08-openclaw-capability-request"),
                "openclaw_approval_receipt_id": receipt_id,
                **params,
            },
        )
        assert requested.status_code == 202, requested.text
        assert requested.json()["operational_effects"] is False

        policies = client.post(
            "/api/openclaw/v1/tools/policy-proposal",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "g08-policy-inspect"),
                "action": "inspect",
            },
        )
        assert policies.status_code == 200, policies.text
        assert policies.json()["provider_effect_count"] == 0

        cancelled = client.post(
            "/api/member/v1/settings/capabilities/planned.apple_messages_reply",
            headers=A_HEADERS,
            json={
                "expected_revision": 2,
                "request_id": "g08-member-capability-cancel",
                "action": "cancel",
            },
        )
        assert cancelled.status_code == 202, cancelled.text
        assert cancelled.json()["request"]["state"] == "cancelled"
        assert client.portal.call(_provider_effect_count, runtime) == 0

    restarted = create_application(settings)
    with TestClient(restarted) as client:
        replayed = client.get("/api/member/v1/surfaces/settings", headers=A_HEADERS)
        assert replayed.status_code == 200, replayed.text
        requests = {
            item["capability_id"]: item
            for item in replayed.json()["control"]["capability_requests"]
        }
        assert requests["planned.apple_messages_observation"]["state"] == "requested"
        assert requests["planned.apple_messages_reply"]["state"] == "cancelled"
        assert replayed.json()["control"]["capability_request_revision"] == 3


def test_g08_bridge_permission_pause_and_authority_fail_closed(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    pack = "calendaring-apple-calendar-bridge"
    settings = replace(
        _settings(instance),
        bridge_enrollments={
            "g08-a-bridge.ts.net": {
                "member_id": A,
                "enrollment_id": "g08-bridge-enrollment",
                "status": "active",
                "allowed_packs": [pack],
                "configured_packs": [pack],
                "enabled_packs": [],
                "authorized_packs": [],
                "adapters_active": [],
                "provider_capability_state": "not_attested",
                "pack_capability_states": {pack: "not_attested"},
                "capability_attestations": {},
                "pack_config": {
                    pack: {
                        "member_id": A,
                        "provider_account_id": "provider-account:" + "a" * 32,
                        "deployment_target": "bridge",
                        "workspace": "personal",
                        "permission_scope": ["eventkit.calendar.read"],
                        "binding_provenance": "member-bridge-enrollment",
                        "ownership_attested": True,
                        "calendar_identifier": "fixture-calendar",
                        "allowed_container_ids": ["fixture-calendar"],
                        "shared_container_ids": [],
                    }
                },
            }
        },
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        runtime.settings.adapter_config["adapters"] = [
            {
                "id": "g08-calendar-a",
                "kind": "calendaring-google-calendar",
                "config": {
                    "member_id": A,
                    "workspace": "personal",
                    "permission_scope": ["calendar.read"],
                },
            }
        ]
        assert runtime.adapters is not None
        runtime.adapters.inventory = lambda: {  # type: ignore[method-assign]
            "g08-calendar-a": {
                "adapter_name": "calendaring-google-calendar",
                "state": "running",
                "last_success_at": "2035-01-01T00:00:00Z",
                "extra": {},
            }
        }
        surface = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]
        items = {
            item["capability_id"]: item for item in surface["capabilities"]["items"]
        }
        bridge = items["bridge.calendaring-apple-calendar-bridge"]
        assert bridge["bridge_state"]["device_pairing"] == "paired"
        assert bridge["bridge_state"]["local_permission"] == "required"
        assert bridge["bridge_state"]["degraded"] is True
        assert bridge["bridge_state"]["effective_readiness"] == "permission_required"
        assert bridge["effective"]["read_available"] is False

        bridge_record = runtime.settings.bridge_enrollments["g08-a-bridge.ts.net"]
        bridge_record["authorized_packs"] = [pack]
        bridge_record["adapters_active"] = [pack]
        bridge_record["provider_capability_state"] = "attested"
        bridge_record["pack_capability_states"] = {pack: "attested"}
        bridge_record["activated_at"] = "2035-01-01T00:00:00Z"
        ready = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["capabilities"]
        ready_bridge = next(
            item
            for item in ready["items"]
            if item["capability_id"] == "bridge.calendaring-apple-calendar-bridge"
        )
        assert ready_bridge["bridge_state"]["effective_readiness"] == "healthy"
        assert ready_bridge["effective"]["read_available"] is True

        adapter = items["adapter.calendaring-google-calendar"]
        assert adapter["runtime_health"] == "healthy"
        assert adapter["freshness"] == "current"
        assert adapter["provider_permission"] == "runtime_read_verified"
        assert adapter["effective"]["read_available"] is True
        assert adapter["administrateme_authority"]["state"] == "owner_scope_bound"
        assert adapter["effective"]["execute_available"] is False
        assert adapter["openclaw"]["effective_tools"] == []

        bridge_record["status"] = "paused"
        paused = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["capabilities"]
        paused_bridge = next(
            item
            for item in paused["items"]
            if item["capability_id"] == "bridge.calendaring-apple-calendar-bridge"
        )
        assert paused_bridge["bridge_state"]["paused"] is True
        assert paused_bridge["effective"]["read_available"] is False

        grant_id = _grant(client, A, "g08-revocable-grant")
        granted = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["capabilities"]
        granted_cos = next(
            item
            for item in granted["items"]
            if item["capability_id"] == "domain.household_state"
        )
        assert granted_cos["openclaw"]["effective_tools"] == ["administrateme_state"]
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant_id}",
            headers=A_HEADERS,
            json={"request_id": "g08-revoke-grant", "reason": "fixture"},
        )
        assert revoked.status_code == 200, revoked.text
        after_revoke = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["capabilities"]
        revoked_cos = next(
            item
            for item in after_revoke["items"]
            if item["capability_id"] == "domain.household_state"
        )
        assert revoked_cos["administrateme_authority"]["state"] == (
            "exact_grant_required"
        )
        assert revoked_cos["openclaw"]["effective_tools"] == []
        assert client.portal.call(_provider_effect_count, runtime) == 0


def test_g08_encrypted_backup_restore_preserves_capability_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    event_key = secret_store(paths, test_mode=True).get(references["event_log"])
    mapping = json.loads(
        (paths.instance_dir / "config" / "member-mapping.json").read_text("utf-8")
    )
    member_id = next(
        str(member["member_id"])
        for member in mapping["members"]
        if member["role"] == "principal"
    )
    login = "g08-backup.ts.net"
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=event_key,
        repository_root=REPO_ROOT,
        member_identities={login: member_id},
        member_roles={
            str(member["member_id"]): str(member["role"])
            for member in mapping["members"]
        },
        household_mapping=mapping,
    )
    with TestClient(create_application(settings)) as client:
        requested = client.post(
            "/api/member/v1/settings/capabilities/planned.apple_messages_observation",
            headers={"Tailscale-User-Login": login},
            json={
                "expected_revision": 0,
                "request_id": "g08-backup-capability-request",
                "action": "request",
                "requested_scope": "member_private",
            },
        )
        assert requested.status_code == 202, requested.text
        assert client.portal.call(_provider_effect_count, client.app.state.runtime) == 0

    ServiceManager(paths, test_mode=True).start()
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    restored = manager.restore(Path(backup["path"]))
    assert restored["projections_rebuilt"] == 15

    with TestClient(create_application(settings)) as client:
        surface = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": login},
        )
        assert surface.status_code == 200, surface.text
        requests = surface.json()["control"]["capability_requests"]
        saved = next(
            item
            for item in requests
            if item["capability_id"] == "planned.apple_messages_observation"
        )
        assert saved["state"] == "requested"
        assert saved["catalog_version"] == "1.2.0"
        assert client.portal.call(_provider_effect_count, client.app.state.runtime) == 0
