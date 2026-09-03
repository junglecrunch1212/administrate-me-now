"""G09 process proof for self-service member-bridge enrollment."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.bridge_enrollment import (
    BridgeEnrollmentError,
    capability_set_digest,
    inspect_transfer_package,
    progress_signature,
)
from adminme.runtime.application import RuntimeSettings, create_application
from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper
from adminme_bridge.config import load_config
from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths


REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = b"g" * 32
A = "member-g09-a"  # fixture:tenant_data:ok
B = "member-g09-b"  # fixture:tenant_data:ok
A_LOGIN = "g09-a.ts.net"
B_LOGIN = "g09-b.ts.net"
A_HEADERS = {"Tailscale-User-Login": A_LOGIN}
B_HEADERS = {"Tailscale-User-Login": B_LOGIN}
PACK = "calendaring-apple-calendar-bridge"
ACCOUNT_COORDINATE = "fixture-calendar-account"
ACCOUNT = (
    "provider-account:"
    + hashlib.sha256(ACCOUNT_COORDINATE.encode("utf-8")).hexdigest()[:32]
)
CALENDAR = "g09-calendar"


def _settings(instance: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance,
        encryption_key=KEY,
        repository_root=REPO_ROOT,
        member_identities={A_LOGIN: A, B_LOGIN: B},
        member_roles={A: "principal", B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "G09 fixture household",  # fixture:tenant_data:ok
            "members": [
                {"member_id": A, "label": "Fixture adult A", "role": "principal"},
                {"member_id": B, "label": "Fixture adult B", "role": "principal"},
            ],
            "memberships": [
                {
                    "membership_id": "g09-membership-a",
                    "party_id": A,
                    "role": "principal",
                },
                {
                    "membership_id": "g09-membership-b",
                    "party_id": B,
                    "role": "principal",
                },
            ],
            "relationships": [],
        },
    )


async def _provider_effect_count(runtime: Any) -> int:
    types = [
        "external_sync.write_requested",
        "external_sync.write_sent",
        "external_sync.write_completed",
        "provider.command.requested",
    ]
    return sum([1 async for _event in runtime.event_log.read_since(types=types)])


def _calendar_config() -> dict[str, Any]:
    return {
        "member_id": A,
        "provider_account_id": ACCOUNT,
        "deployment_target": "bridge",
        "workspace": "personal",
        "permission_scope": [
            "eventkit.calendar.read",
            "eventkit.calendar.write",
        ],
        "binding_provenance": "member-bridge-enrollment",
        "ownership_attested": True,
        "calendar_identifier": CALENDAR,
        "allowed_container_ids": [CALENDAR],
        "shared_container_ids": [],
    }


def _bridge_headers(enrollment_id: str) -> dict[str, str]:
    return {
        "Tailscale-User-Login": A_LOGIN,
        "X-AdministrateMe-Bridge-Enrollment": enrollment_id,
    }


def _adapter_evidence(
    *, tenant_id: str, enrollment_id: str, configuration_sha256: str
) -> dict[str, Any]:
    return {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": tenant_id,
        "type": "adapter.authenticated",
        "schema_version": 2,
        "occurred_at": "2026-09-02T20:00:00+00:00",
        "source_adapter": "calendaring-apple-calendar",
        "source_account_id": ACCOUNT,
        "owner_scope": f"private:{A}",
        "visibility_scope": f"private:{A}",
        "sensitivity": "normal",
        "idempotency_key": "g09-calendar-capability-evidence",
        "payload": {
            "adapter_name": "calendaring-apple-calendar",
            "adapter_family": "calendaring",
            "token_storage_ref": "keychain://device-permissions/calendar",
            "authenticated_at": "2026-09-02T20:00:00+00:00",
            "scopes": [
                "eventkit.calendar.read",
                "eventkit.calendar.write",
            ],
            "bridge_enrollment_id": enrollment_id,
            "configuration_sha256": configuration_sha256,
            "provider_account_id": ACCOUNT,
        },
    }


def _calendar_observation(*, tenant_id: str) -> dict[str, Any]:
    return {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": tenant_id,
        "type": "calendar.event_added",
        "schema_version": 2,
        "occurred_at": "2026-09-02T20:01:00+00:00",
        "source_adapter": "calendaring-apple-calendar",
        "source_account_id": ACCOUNT,
        "owner_scope": f"private:{A}",
        "visibility_scope": f"private:{A}",
        "sensitivity": "normal",
        "idempotency_key": "g09-calendar-observation",
        "payload": {
            "member_id": A,
            "provider": "apple_calendar",
            "provider_account_id": ACCOUNT,
            "calendar_external_id": CALENDAR,
            "external_event_id": "g09-event",
            "summary": "Fixture event",
            "start": "2026-09-03T12:00:00+00:00",
            "end": "2026-09-03T13:00:00+00:00",
            "updated_at": "2026-09-02T20:01:00+00:00",
        },
    }


def test_g09_self_service_partial_success_restart_and_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)

    with TestClient(app) as client:
        runtime = app.state.runtime
        initial = client.get("/api/member/v1/surfaces/settings", headers=A_HEADERS)
        assert initial.status_code == 200
        wizard = initial.json()["control"]["bridge_wizard"]
        assert wizard["contract"] == "administrateme-member-bridge-wizard-v1"
        assert wizard["writes_authorized"] is False
        descriptors = {item["capability_id"]: item for item in wizard["capabilities"]}
        assert descriptors["apple_messages_read"]["implementation"] == "available"
        assert descriptors["apple_messages_reply"]["implementation"] == "available"
        assert descriptors["apple_messages_reply"][
            "administrateme_write_ceiling"
        ].startswith("Ask every time")
        assert descriptors["apple_messages_reply"]["enrollment_selectable"] is False
        assert (
            "enable it separately"
            in descriptors["apple_messages_reply"]["enrollment_note"]
        )
        reply_as_read_source = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "g11-reply-is-separate",
                "member_id": A,
                "device_class": "another_mac",
                "capabilities": ["apple_messages_reply"],
            },
        )
        assert reply_as_read_source.status_code == 422

        cross_adult = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "g09-cross-adult",
                "member_id": B,
                "device_class": "another_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert cross_adult.status_code == 403
        view_as = client.post(
            "/api/member/v1/settings/bridges",
            headers={**A_HEADERS, "X-AdminMe-View-Member": B},
            json={
                "expected_revision": 0,
                "request_id": "g09-view-as-denied",
                "member_id": A,
                "device_class": "this_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert view_as.status_code == 403

        request_id = "g09-self-service-enrollment"
        started = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": request_id,
                "member_id": A,
                "device_class": "another_mac",
                "capabilities": ["apple_calendar", "apple_reminders"],
            },
        )
        assert started.status_code == 202, started.text
        assert started.json()["state"] == "package_ready"
        assert started.json()["contains_transfer_secret"] is False
        assert "transfer_key" not in json.dumps(started.json()).casefold()
        assert "transfer_code" not in json.dumps(started.json()).casefold()

        a_surface = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]
        b_surface = client.get(
            "/api/member/v1/surfaces/settings", headers=B_HEADERS
        ).json()["control"]
        assert a_surface["bridge_wizard"]["current"]["state"] == "package_ready"
        assert b_surface["bridge_wizard"]["current"] is None
        rendered = json.dumps(a_surface).casefold()
        assert "transfer_key" not in rendered
        assert "proof_key" not in rendered
        assert "token" not in rendered

        handoff = client.post(
            f"/api/member/v1/settings/bridges/{request_id}/package",
            headers=A_HEADERS,
        )
        assert handoff.status_code == 200, handoff.text
        transfer_code = handoff.headers["X-AdministrateMe-Transfer-Code"]
        assert transfer_code not in handoff.text
        transfer = inspect_transfer_package(handoff.content, transfer_code)
        assert transfer["instance_id"] == runtime.settings.instance.tenant_id
        assert transfer["member_id"] == A
        assert transfer["request_id"] == request_id
        assert len(transfer["release_commit"]) == 40
        assert transfer["capability_set_sha256"] == capability_set_digest(
            ["apple_calendar", "apple_reminders"]
        )
        assert transfer["enabled_packs"] == []
        assert transfer["restrictions"]["provider_writes"] == "deny"

        target_package = tmp_path / "target.adminmebridge"
        target_package.write_bytes(handoff.content)
        member_paths = MemberBridgePaths.from_root(tmp_path / "target-mac")

        def submitter(bound: dict[str, Any], envelope: dict[str, Any]) -> str:
            response = client.post(
                "/api/bridge/ingest",
                headers={
                    "Tailscale-User-Login": bound["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": bound["enrollment_id"],
                },
                json=envelope,
            )
            assert response.status_code == 202, response.text
            return str(response.json()["event_id"])

        installer = MemberBridgeInstaller(
            member_paths,
            root=REPO_ROOT,
            test_mode=True,
            submitter=submitter,
        )
        installer._tailscale_identity = lambda: (A_LOGIN, "g09-target-node")  # type: ignore[method-assign]

        def reporter(
            bound: dict[str, Any], payload: dict[str, Any], signature: str
        ) -> dict[str, Any]:
            response = client.post(
                "/api/bridge/enrollment/progress",
                headers={
                    "Tailscale-User-Login": bound["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": bound["enrollment_id"],
                },
                json={**payload, "signature": signature},
            )
            assert response.status_code == 202, response.text
            return response.json()

        def evidence_submitter(bound: dict[str, Any], envelope: dict[str, Any]) -> str:
            response = client.post(
                "/api/bridge/ingest",
                headers={
                    "Tailscale-User-Login": bound["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": bound["enrollment_id"],
                },
                json=envelope,
            )
            assert response.status_code == 202, response.text
            return str(response.json()["event_id"])

        helper = MemberBridgeEnrollmentHelper(
            member_paths,
            repository_root=REPO_ROOT,
            package_path=target_package,
            transfer_code=transfer_code,
            test_mode=True,
            reporter=reporter,
            evidence_submitter=evidence_submitter,
            installer=installer,
        )
        preview = helper.open_and_verify()
        assert preview.member_id == A
        assert preview.provider_writes_authorized is False

        proof_key = runtime.bridge_enrollment.progress_proof_key_for_test(request_id)
        wrong_member_payload = {
            "contract": "administrateme-member-bridge-progress-v1",
            "request_id": request_id,
            "enrollment_id": transfer["enrollment_id"],
            "member_id": A,
            "instance_id": transfer["instance_id"],
            "release_commit": transfer["release_commit"],
            "capability_set_sha256": transfer["capability_set_sha256"],
            "previous_state": "instance_verified",
            "state": "member_confirmed",
            "nonce": "wrong-member-nonce-0001",
            "reported_at": "2026-09-02T20:00:00+00:00",
            "device_node_id": "g09-target-node",
            "enrollment_event_id": None,
            "capability_id": None,
            "capability_state": None,
            "reason": None,
            "last_successful_observation": None,
            "pack_config": None,
            "capability_event_id": None,
        }
        denied_other_member = client.post(
            "/api/bridge/enrollment/progress",
            headers={
                "Tailscale-User-Login": B_LOGIN,
                "X-AdministrateMe-Bridge-Enrollment": transfer["enrollment_id"],
            },
            json={
                **wrong_member_payload,
                "signature": progress_signature(proof_key, wrong_member_payload),
            },
        )
        assert denied_other_member.status_code == 403

        receipt = helper.accept_locally(member_confirmed=True)
        assert receipt["connectors_started"] == []
        installed_config = load_config(member_paths.config_path)
        assert installed_config.request_id == request_id
        assert installed_config.requested_capabilities == [
            "apple_calendar",
            "apple_reminders",
        ]
        assert installed_config.configured_packs == []
        assert installed_config.enabled_packs == []
        bridge_record = runtime.settings.bridge_enrollments[A_LOGIN]
        assert bridge_record["status"] == "active"
        assert bridge_record["configured_packs"] == []
        assert bridge_record["authorized_packs"] == []

        helper = MemberBridgeEnrollmentHelper.resume(
            member_paths,
            repository_root=REPO_ROOT,
            test_mode=True,
            reporter=reporter,
            evidence_submitter=evidence_submitter,
            installer=installer,
        )
        assert helper.state == "awaiting_permissions"
        assert helper.device_node_id == "g09-target-node"
        assert helper._transfer_code == ""

        calendar_config = helper.pack_config_for(
            "apple_calendar",
            source_coordinate=CALENDAR,
            account_coordinate=ACCOUNT_COORDINATE,
        )
        assert calendar_config == _calendar_config()
        helper.record_permission(
            "apple_calendar", granted=True, pack_config=calendar_config
        )
        helper.run_capability_test(
            "apple_calendar",
            lambda _capability, request: {
                "status": "passed"
                if request == {"content_allowed": False}
                else "failed",
                "content_exposed": False,
            },
        )
        denied = helper.record_permission("apple_reminders", granted=False)
        assert denied["state"] == "degraded"
        assert PACK in bridge_record["configured_packs"]
        assert bridge_record["authorized_packs"] == []

        helper.prove_and_start("apple_calendar")
        assert bridge_record["authorized_packs"] == [PACK]
        assert bridge_record["enabled_packs"] == [PACK]
        helper.record_observing("apple_calendar")

        observation = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(str(transfer["enrollment_id"])),
            json=_calendar_observation(tenant_id=runtime.settings.instance.tenant_id),
        )
        assert observation.status_code == 202, observation.text
        status = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["bridge_wizard"]["current"]
        assert status["state"] == "partially_active"
        assert status["capability_states"]["apple_calendar"]["state"] == "settled"
        assert (
            status["capability_states"]["apple_reminders"]["state"]
            == "permission_denied"
        )

        view_as_control = client.post(
            f"/api/member/v1/settings/bridges/{request_id}/control",
            headers={**B_HEADERS, "X-AdminMe-View-Member": A},
            json={"command_id": "g09-view-as-pause", "action": "pause"},
        )
        assert view_as_control.status_code == 403
        paused = client.post(
            f"/api/member/v1/settings/bridges/{request_id}/control",
            headers=A_HEADERS,
            json={"command_id": "g09-owner-pause", "action": "pause"},
        )
        assert paused.status_code == 200, paused.text
        assert bridge_record["status"] == "paused"
        assert bridge_record["enabled_packs"] == []
        assert bridge_record["paused_enabled_packs"] == [PACK]
        paused_status = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["bridge_wizard"]["current"]
        assert paused_status["state"] == "paused"
        assert paused_status["capability_states"]["apple_calendar"]["state"] == "paused"
        assert (
            paused_status["capability_states"]["apple_reminders"]["state"] == "paused"
        )
        resumed = client.post(
            f"/api/member/v1/settings/bridges/{request_id}/control",
            headers=A_HEADERS,
            json={"command_id": "g09-owner-resume", "action": "resume"},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "partially_active"
        assert bridge_record["status"] == "active"
        assert bridge_record["enabled_packs"] == [PACK]
        resumed_status = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["bridge_wizard"]["current"]
        assert (
            resumed_status["capability_states"]["apple_calendar"]["state"] == "settled"
        )
        assert (
            resumed_status["capability_states"]["apple_reminders"]["state"]
            == "permission_denied"
        )
        assert client.portal.call(_provider_effect_count, runtime) == 0

    restarted = create_application(settings)
    with TestClient(restarted) as client:
        replayed = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["bridge_wizard"]["current"]
        assert replayed["state"] == "partially_active"
        assert replayed["capability_states"]["apple_calendar"]["state"] == "settled"
        revoked = client.post(
            f"/api/member/v1/settings/bridges/{request_id}/control",
            headers=A_HEADERS,
            json={"command_id": "g09-owner-revoke", "action": "revoke"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["state"] == "revoked"
        assert settings.bridge_enrollments[A_LOGIN]["status"] == "revoked"
        blocked = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(str(transfer["enrollment_id"])),
            json={
                **_calendar_observation(
                    tenant_id=restarted.state.runtime.settings.instance.tenant_id
                ),
                "idempotency_key": "g09-after-revocation",
            },
        )
        assert blocked.status_code == 403
        assert client.portal.call(_provider_effect_count, restarted.state.runtime) == 0


def test_g09_tamper_wrong_release_capability_binding_and_replay_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        started = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "g09-abuse-enrollment",
                "member_id": A,
                "device_class": "another_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert started.status_code == 202
        handoff = client.post(
            "/api/member/v1/settings/bridges/g09-abuse-enrollment/package",
            headers=A_HEADERS,
        )
        code = handoff.headers["X-AdministrateMe-Transfer-Code"]
        tampered = bytearray(handoff.content)
        tampered[-8] = tampered[-8] ^ 1
        with pytest.raises(Exception):
            inspect_transfer_package(bytes(tampered), code)

        transfer = inspect_transfer_package(handoff.content, code)
        proof_key = app.state.runtime.bridge_enrollment.progress_proof_key_for_test(
            "g09-abuse-enrollment"
        )

        def payload(nonce: str, **changes: Any) -> dict[str, Any]:
            value = {
                "contract": "administrateme-member-bridge-progress-v1",
                "request_id": transfer["request_id"],
                "enrollment_id": transfer["enrollment_id"],
                "member_id": transfer["member_id"],
                "instance_id": transfer["instance_id"],
                "release_commit": transfer["release_commit"],
                "capability_set_sha256": transfer["capability_set_sha256"],
                "previous_state": "awaiting_transfer",
                "state": "package_opened",
                "nonce": nonce,
                "reported_at": "2026-09-02T21:00:00+00:00",
                "device_node_id": "g09-abuse-node",
                "enrollment_event_id": None,
                "capability_id": None,
                "capability_state": None,
                "reason": None,
                "last_successful_observation": None,
                "pack_config": None,
                "capability_event_id": None,
            }
            value.update(changes)
            return value

        for field, wrong in (
            ("instance_id", "wrong-instance"),
            ("release_commit", "0" * 40),
            ("capability_set_sha256", "0" * 64),
        ):
            wrong_payload = payload(f"wrong-binding-{field}-0001", **{field: wrong})
            response = client.post(
                "/api/bridge/enrollment/progress",
                headers=_bridge_headers(str(transfer["enrollment_id"])),
                json={
                    **wrong_payload,
                    "signature": progress_signature(proof_key, wrong_payload),
                },
            )
            assert response.status_code == 403
            assert response.json()["detail"]["error"] == "progress_binding_mismatch"

        accepted_payload = payload("g09-replay-nonce-0001")
        signed = progress_signature(proof_key, accepted_payload)
        accepted = client.post(
            "/api/bridge/enrollment/progress",
            headers=_bridge_headers(str(transfer["enrollment_id"])),
            json={**accepted_payload, "signature": signed},
        )
        assert accepted.status_code == 202, accepted.text
        wrong_device_payload = payload(
            "g09-wrong-device-nonce-0001",
            previous_state="package_opened",
            state="instance_verified",
            device_node_id="g09-other-target-node",
        )
        wrong_device = client.post(
            "/api/bridge/enrollment/progress",
            headers=_bridge_headers(str(transfer["enrollment_id"])),
            json={
                **wrong_device_payload,
                "signature": progress_signature(proof_key, wrong_device_payload),
            },
        )
        assert wrong_device.status_code == 403
        assert wrong_device.json()["detail"]["error"] == "wrong_device"
        replayed = client.post(
            "/api/bridge/enrollment/progress",
            headers=_bridge_headers(str(transfer["enrollment_id"])),
            json={**accepted_payload, "signature": signed},
        )
        assert replayed.status_code in {403, 409}
        assert client.portal.call(_provider_effect_count, app.state.runtime) == 0


def test_g09_expiry_clean_regeneration_and_duplicate_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    instance = tmp_path / "instance-expiry"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        original_now = runtime.bridge_enrollment.now
        started = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "g09-expiring-enrollment",
                "member_id": A,
                "device_class": "this_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert started.status_code == 202, started.text
        expires_at = datetime.fromisoformat(started.json()["expires_at"])
        runtime.bridge_enrollment.now = lambda: expires_at + timedelta(seconds=1)

        expired = client.post(
            "/api/member/v1/settings/bridges/g09-expiring-enrollment/package",
            headers=A_HEADERS,
        )
        assert expired.status_code == 410, expired.text
        expired_state = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["bridge_wizard"]["current"]
        assert expired_state["state"] == "expired"
        assert runtime.settings.bridge_enrollments[A_LOGIN]["status"] == "revoked"

        repeated_id = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 1,
                "request_id": "g09-expiring-enrollment",
                "member_id": A,
                "device_class": "this_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert repeated_id.status_code == 409

        runtime.bridge_enrollment.now = original_now
        regenerated = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 1,
                "request_id": "g09-regenerated-enrollment",
                "member_id": A,
                "device_class": "this_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert regenerated.status_code == 202, regenerated.text
        assert regenerated.json()["state"] == "package_ready"
        duplicate = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 2,
                "request_id": "g09-duplicate-enrollment",
                "member_id": A,
                "device_class": "another_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert duplicate.status_code == 409
        assert client.portal.call(_provider_effect_count, runtime) == 0


def test_g09_failed_preparation_can_retry_without_operator_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    instance = tmp_path / "instance-retry"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        original_prepare = runtime.bridge_enrollment.prepare

        def unavailable(**_values: Any) -> Any:
            raise BridgeEnrollmentError(
                "temporary_helper_unavailable",
                "reviewed helper is temporarily unavailable",
            )

        runtime.bridge_enrollment.prepare = unavailable
        failed = client.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "g09-retry-enrollment",
                "member_id": A,
                "device_class": "another_mac",
                "capabilities": ["apple_calendar"],
            },
        )
        assert failed.status_code == 409
        current = client.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]["bridge_wizard"]["current"]
        assert current["state"] == "failed"

        runtime.bridge_enrollment.prepare = original_prepare
        retried = client.post(
            "/api/member/v1/settings/bridges/g09-retry-enrollment/control",
            headers=A_HEADERS,
            json={"command_id": "g09-retry-command", "action": "retry"},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["state"] == "package_ready"
        assert client.portal.call(_provider_effect_count, runtime) == 0
