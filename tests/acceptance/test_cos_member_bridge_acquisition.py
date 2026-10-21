"""Self-service read-only Apple bridges coexist with retained CoS acquisition.

These credential-free process tests execute the production composition root,
encrypted event log, signed enrollment helper and member surfaces. Only local
Apple permission/provider boundaries are synthetic; run in hosted CI or a lab.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.cos_channel_acquisition import CONFIG_KEY as COS_KEY
from adminme.adapters.disposable_test_acquisition import CONFIG_KEY as TEST_KEY
from adminme.bridge_enrollment import BridgeEnrollmentError, inspect_transfer_package
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.historical_attribution import audit_historical_attribution
from adminme.provider_bindings import validate_historical_provider_bindings
from adminme.runtime.application import RuntimeSettings, create_application
from adminme_bridge.config import load_config
from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper
from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.acceptance.test_forwarded_channel_intake import (
    MEMBER_A,
    MEMBER_B,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    ROOT,
    _events,
    _settings,
)


CAPABILITIES = ["apple_calendar", "apple_reminders", "apple_notes"]
PACKS = {
    "apple_calendar": "calendaring-apple-calendar-bridge",
    "apple_reminders": "reminders-apple-eventkit-bridge",
    "apple_notes": "knowledge-apple-notes",
}
REQUEST_ID = "cos-member-bridge-fixture"
DEVICE = "cos-member-bridge-fixture-node"
NOW = "2031-01-01T12:00:00+00:00"


async def _history(log: EventLog, watermark: str | None = None) -> dict[str, Any]:
    # Member bridge ownership is proven by immutable enrollment/source receipts,
    # not a central provider binding or today's mutable enrollment dictionary.
    return await audit_historical_attribution(
        log,
        repository_root=ROOT,
        provider_bindings=(),
        hash_key=b"fixture-member-bridge-history",
        through_event_id=watermark,
    )


async def _settled_source_assurance(
    runtime: Any, source_event_id: str, *, timeout_s: float = 10.0
) -> dict[str, Any]:
    """Wait for the public asynchronous audit to cover the accepted source."""

    deadline = time.monotonic() + timeout_s
    assurance: dict[str, Any] = {}
    while time.monotonic() < deadline:
        diagnostics = await runtime.diagnostics()
        assurance = diagnostics["capabilities"]["policy:source-assurance"]["details"]
        freshness = assurance["freshness"]
        assert freshness["state"] != "failed", assurance
        if (
            str(assurance.get("watermark_event_id") or "") >= source_event_id
            and freshness["settled_revision"] is True
        ):
            return assurance
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"source assurance did not settle at the accepted source: {assurance}"
    )


def _assert_history(report: dict[str, Any], *, source_count: int = 3) -> None:
    assert report["state"] == "healthy", report
    assert report["source_event_count"] == source_count
    assert report["attributed_event_count"] == source_count
    assert report["unattributed_event_count"] == 0
    assert {row["binding_state"] for row in report["groups"]} == {
        "member_bridge_source_reconciled"
    }
    assert report["content_exposed"] is False
    assert report["raw_identifiers_exposed"] is False
    encoded = json.dumps(report)
    for private_value in (MEMBER_A, MEMBER_B, DEVICE, "Submit the fixture form"):
        assert private_value not in encoded


def _configuration_settings(tmp_path: Path) -> RuntimeSettings:
    instance = tmp_path / "instance"
    instance.mkdir()
    base = _settings(instance)
    config = _configuration(instance)
    # The overlay-only fixture intentionally contains an unavailable enabled
    # personal pack. This lifecycle fixture also invokes the historical audit,
    # so retain a complete, disabled Google ownership profile like a deployed
    # instance. No central provider or credential may be loaded here.
    personal, service = config["adapters"]
    personal.update(kind="messaging-gmail-api", enabled=False)
    personal["config"].update(
        user_email=config[TEST_KEY]["provider_account_id"],
        workspace="personal",
        permission_scope=["https://www.googleapis.com/auth/gmail.readonly"],
        binding_provenance="oauth-subject",
        ownership_attested=True,
        oauth_credentials_ref="keychain://fixture-personal/never-resolved",
        read_only=True,
        enable_writes=False,
    )
    service["enabled"] = False
    for suffix, kind, scope in (
        ("calendar", "calendaring-google-calendar", "calendar.readonly"),
        ("tasks", "tasks-google-tasks", "tasks.readonly"),
    ):
        retained = copy.deepcopy(personal)
        retained["id"] = f"fixture-personal-{suffix}"
        retained["kind"] = kind
        retained["config"]["permission_scope"] = [
            f"https://www.googleapis.com/auth/{scope}"
        ]
        config["adapters"].append(retained)
    assert {
        binding.adapter_id
        for binding in validate_historical_provider_bindings(
            config,
            member_roles=base.member_roles,
            household_mapping=base.household_mapping,
        )
    } == {
        "fixture-personal-gmail",
        "fixture-personal-calendar",
        "fixture-personal-tasks",
    }
    return replace(base, adapter_config=config)


def _control(
    client: TestClient, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = client.get(
        "/api/member/v1/surfaces/settings", headers=headers or MEMBER_HEADERS
    )
    assert response.status_code == 200, response.text
    return response.json()["control"]


def _manual_policies(client: TestClient) -> None:
    for capability in CAPABILITIES:
        control = _control(client)
        source_id = f"adapter:{capability}"
        source = next(
            row
            for row in control["candidate_identification"]["sources"]
            if row["id"] == source_id
        )
        response = client.post(
            f"/api/member/v1/settings/candidate-policies/{source_id}",
            headers=MEMBER_HEADERS,
            json={
                "request_id": f"cos-bridge-manual-{capability}",
                "expected_revision": control["revision"],
                "mode": "manual",
                "language_enabled": False,
                "candidate_kinds": source["policy"]["candidate_kinds"],
            },
        )
        assert response.status_code == 202, response.text


def _prepare_helper(
    client: TestClient,
    tmp_path: Path,
    capabilities: list[str],
    *,
    owner_id: str = MEMBER_A,
    owner_headers: dict[str, str] | None = None,
    device_id: str = DEVICE,
) -> tuple[MemberBridgeEnrollmentHelper, dict[str, Any]]:
    owner_headers = owner_headers or MEMBER_HEADERS
    request_body = {
        "expected_revision": _control(client, owner_headers)["revision"],
        "request_id": REQUEST_ID,
        "member_id": owner_id,
        "device_class": "this_mac",
        "capabilities": capabilities,
    }
    response = client.post(
        "/api/member/v1/settings/bridges",
        headers=owner_headers,
        json=request_body,
    )
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "package_ready"
    runtime = client.app.state.runtime
    record = runtime.settings.bridge_enrollments[owner_headers["Tailscale-User-Login"]]
    marker = record["acquisition_policy"]
    assert marker == "cos-member-source-observation-v1"
    before_current = copy.deepcopy(
        _control(client, owner_headers)["bridge_wizard"]["current"]
    )
    event_types = ("settings.bridge_enrollment_started", "bridge.enrollment_progressed")
    before_events = {
        kind: client.portal.call(_events, runtime, kind) for kind in event_types
    }
    before_packages = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in runtime.bridge_enrollment.package_dir.glob("*.adminmebridge")
    }
    assert len(before_packages) == 1
    other_id, other_headers = (
        (MEMBER_B, OTHER_HEADERS)
        if owner_id == MEMBER_A
        else (MEMBER_A, MEMBER_HEADERS)
    )
    before_other = copy.deepcopy(_control(client, other_headers))
    protected_files = (
        runtime.bridge_enrollment.index_path,
        runtime.bridge_enrollment.vault.path,
        *runtime.bridge_enrollment.package_dir.glob("*.adminmebridge"),
    )
    before_files = {path: path.read_bytes() for path in protected_files}
    before_index = copy.deepcopy(runtime.settings.bridge_enrollments)
    collision = client.post(
        "/api/member/v1/settings/bridges",
        headers=other_headers,
        json={
            **request_body,
            "expected_revision": before_other["revision"],
            "member_id": other_id,
        },
    )
    assert collision.status_code == 409, collision.text
    assert collision.json()["detail"]["error"] == "request_id_unavailable"
    with pytest.raises(BridgeEnrollmentError) as refused_prepare:
        runtime.bridge_enrollment.prepare(
            request_id=REQUEST_ID,
            member_id=other_id,
            device_class="this_mac",
            capabilities=capabilities,
            central_url="https://central-fixture.ts.net",
        )
    assert refused_prepare.value.code == "request_id_unavailable"
    assert {path: path.read_bytes() for path in protected_files} == before_files
    assert runtime.settings.bridge_enrollments == before_index
    assert (
        _control(client, other_headers)["bridge_wizard"]
        == before_other["bridge_wizard"]
    )
    assert _control(client, owner_headers)["revision"] == before_other["revision"]
    assert {
        kind: client.portal.call(_events, runtime, kind) for kind in event_types
    } == before_events
    retry = client.post(
        "/api/member/v1/settings/bridges", headers=owner_headers, json=request_body
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()["idempotent"] is True
    assert {path: path.read_bytes() for path in protected_files} == before_files
    # An older pending handoff cannot acquire this new authority through the
    # idempotent prepare shortcut or the download endpoint. Both refusals must
    # precede any lifecycle append, secret response or artifact replacement.
    for invalid_marker in (None, "different-fixture-acquisition-contract"):
        if invalid_marker is None:
            record.pop("acquisition_policy")
        else:
            record["acquisition_policy"] = invalid_marker
        try:
            for endpoint, body in (
                ("/api/member/v1/settings/bridges", request_body),
                (f"/api/member/v1/settings/bridges/{REQUEST_ID}/package", None),
            ):
                refused = client.post(endpoint, headers=owner_headers, json=body)
                assert refused.status_code == 409, refused.text
                assert "X-AdministrateMe-Transfer-Code" not in refused.headers
                assert "transfer_code" not in refused.text.casefold()
                assert "transfer_key" not in refused.text.casefold()
            assert (
                _control(client, owner_headers)["bridge_wizard"]["current"]
                == before_current
            )
            assert {
                kind: client.portal.call(_events, runtime, kind) for kind in event_types
            } == before_events
            assert {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in runtime.bridge_enrollment.package_dir.glob(
                    "*.adminmebridge"
                )
            } == before_packages
        finally:
            record["acquisition_policy"] = marker
    handoff = client.post(
        f"/api/member/v1/settings/bridges/{REQUEST_ID}/package",
        headers=owner_headers,
    )
    assert handoff.status_code == 200, handoff.text
    code = handoff.headers["X-AdministrateMe-Transfer-Code"]
    transfer = inspect_transfer_package(handoff.content, code)
    assert transfer["requested_capabilities"] == sorted(capabilities)
    assert transfer["restrictions"]["provider_writes"] == "deny"
    assert transfer["restrictions"]["acquisition_policy"] == marker
    package = tmp_path / "member.adminmebridge"
    package.write_bytes(handoff.content)
    paths = MemberBridgePaths.from_root(tmp_path / "member-mac")

    def headers(bound: dict[str, Any]) -> dict[str, str]:
        return {
            "Tailscale-User-Login": str(bound["tailscale_identity"]),
            "X-AdministrateMe-Bridge-Enrollment": str(bound["enrollment_id"]),
        }

    def submitter(bound: dict[str, Any], envelope: dict[str, Any]) -> str:
        if envelope["type"] == "bridge.enrolled":
            forged = copy.deepcopy(envelope)
            forged["payload"]["bridge_node_id"] = "unapproved-device"
            refused = client.post(
                "/api/bridge/ingest", headers=headers(bound), json=forged
            )
            assert refused.status_code == 403, refused.text
            assert (
                client.portal.call(_events, client.app.state.runtime, "bridge.enrolled")
                == []
            )
        accepted = client.post(
            "/api/bridge/ingest", headers=headers(bound), json=envelope
        )
        assert accepted.status_code == 202, accepted.text
        return str(accepted.json()["event_id"])

    def reporter(
        bound: dict[str, Any], payload: dict[str, Any], signature: str
    ) -> dict[str, Any]:
        response = client.post(
            "/api/bridge/enrollment/progress",
            headers=headers(bound),
            json={**payload, "signature": signature},
        )
        assert response.status_code == 202, response.text
        return response.json()

    installer = MemberBridgeInstaller(
        paths, root=ROOT, test_mode=True, submitter=submitter
    )
    installer._tailscale_identity = lambda: (  # type: ignore[method-assign]
        owner_headers["Tailscale-User-Login"],
        device_id,
    )
    helper = MemberBridgeEnrollmentHelper(
        paths,
        repository_root=ROOT,
        package_path=package,
        transfer_code=code,
        test_mode=True,
        reporter=reporter,
        evidence_submitter=submitter,
        installer=installer,
    )
    assert helper.open_and_verify().provider_writes_authorized is False
    assert helper.accept_locally(member_confirmed=True)["connectors_started"] == []
    installed = load_config(paths.config_path)
    assert installed.acquisition_policy == marker
    assert installed.observation_only is True
    assert installed.provider_writes == "deny"
    return helper, transfer


def _enable(helper: MemberBridgeEnrollmentHelper, capability: str) -> None:
    config = helper.pack_config_for(
        capability,
        source_coordinate=f"fixture-{capability}-container",
        account_coordinate=f"fixture-{capability}-account",
    )
    helper.record_permission(capability, granted=True, pack_config=config)
    helper.run_capability_test(
        capability,
        lambda _capability, request: {
            "status": "passed" if request == {"content_allowed": False} else "failed",
            "content_exposed": False,
        },
    )
    helper.prove_and_start(capability)
    helper.record_observing(capability)


def _observation(runtime: Any, capability: str, key: str) -> dict[str, Any]:
    account = (
        "apple_notes"
        if capability == "apple_notes"
        else "provider-account:"
        + hashlib.sha256(f"fixture-{capability}-account".encode()).hexdigest()[:32]
    )
    body = "[fixture:triage-kind=task] Submit the fixture form by 2031-01-02."
    if capability == "apple_calendar":
        event_type, adapter = "calendar.event_added", "calendaring-apple-calendar"
        payload = {
            "member_id": MEMBER_A,
            "provider": capability,
            "provider_account_id": account,
            "calendar_external_id": f"fixture-{capability}-container",
            "external_event_id": key,
            "summary": body,
            "start": "2031-01-02T12:00:00+00:00",
            "end": "2031-01-02T13:00:00+00:00",
            "updated_at": NOW,
        }
    elif capability == "apple_reminders":
        event_type, adapter = "list_item.added", "reminders-apple-eventkit"
        payload = {
            "member_id": MEMBER_A,
            "provider": capability,
            "provider_account_id": account,
            "list_external_id": f"fixture-{capability}-container",
            "item_external_id": key,
            "list_name": "Fixture member list",
            "title": body,
            "last_modified_at": NOW,
            "sync_cycle_id": "fixture-read-cycle",
        }
    else:
        event_type, adapter = "member_knowledge.note_added", "knowledge-apple-notes"
        payload = {
            "member_id": MEMBER_A,
            "knowledge_id": key,
            "title": "Fixture private note",
            "body": body,
            "body_format": "plain",
            "source_kind": capability,
            "source_external_id": key,
            "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "captured_at": NOW,
        }
    return {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": runtime.settings.instance.tenant_id,
        "type": event_type,
        "schema_version": 2,
        "occurred_at": NOW,
        "source_adapter": adapter,
        "source_account_id": account,
        "owner_scope": f"private:{MEMBER_A}",
        "visibility_scope": f"private:{MEMBER_A}",
        "sensitivity": "normal",
        "idempotency_key": key,
        "payload": payload,
    }


def _bridge_headers(transfer: dict[str, Any]) -> dict[str, str]:
    return {
        **MEMBER_HEADERS,
        "X-AdministrateMe-Bridge-Enrollment": transfer["enrollment_id"],
    }


def _assert_denied_batch_is_atomic(
    client: TestClient, transfer: dict[str, Any]
) -> None:
    runtime = client.app.state.runtime
    headers = _bridge_headers(transfer)
    good = _observation(runtime, "apple_calendar", "fixture-batch-first")
    for field, value in (
        ("owner_scope", f"private:{MEMBER_B}"),
        ("visibility_scope", "shared:household"),
        ("tenant_id", "other-fixture-tenant"),
        ("source_account_id", "provider-account:" + "0" * 32),
        ("source_adapter", "contacts-apple-contacts"),
        ("payload.calendar_external_id", "unselected-container"),
        ("payload.member_id", MEMBER_B),
        ("event_id", "ev_fixture_forged_historical_order"),
        ("recorded_at", NOW),
    ):
        bad = copy.deepcopy(good)
        bad["idempotency_key"] = f"fixture-batch-bad-{field}"
        if field.startswith("payload."):
            bad["payload"][field.removeprefix("payload.")] = value
        else:
            bad[field] = value
        single = client.post("/api/bridge/ingest", headers=headers, json=bad)
        assert single.status_code == 403, single.text
        response = client.post(
            "/api/bridge/ingest/batch",
            headers=headers,
            json={"envelopes": [good, bad]},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["offset"] == 1
        for envelope in (good, bad):
            assert (
                client.portal.call(
                    runtime.event_log.get_by_idempotency,
                    runtime.settings.instance.tenant_id,
                    envelope["idempotency_key"],
                )
                is None
            )
    assert (
        client.post(
            "/api/bridge/ingest",
            headers={
                **OTHER_HEADERS,
                **{
                    key: value for key, value in headers.items() if key.startswith("X-")
                },
            },
            json=good,
        ).status_code
        == 403
    )


async def _queue_calendar_command(runtime: Any) -> str:
    account = (
        "provider-account:"
        + hashlib.sha256(b"fixture-apple_calendar-account").hexdigest()[:32]
    )
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=runtime.settings.instance.tenant_id,
            type="external_sync.write_requested",
            schema_version=2,
            occurred_at=NOW,
            source_adapter="fixture-command-history",
            source_account_id=account,
            owner_scope=f"private:{MEMBER_A}",
            visibility_scope=f"private:{MEMBER_A}",
            sensitivity="normal",
            idempotency_key="fixture-read-only-bridge-command",
            payload={
                "command_id": "fixture-read-only-bridge-command",
                "member_id": MEMBER_A,
                "provider": "apple_calendar",
                "provider_account_id": account,
                "operation": "create_event",
                "payload": {
                    "calendar_external_id": "fixture-apple_calendar-container",
                    "summary": "This retained command must never leave the host",
                },
                "requested_at": NOW,
                "domain_approval_event_id": "fixture-retained-domain-approval",
                "openclaw_approval_event_id": "fixture-retained-native-approval",
                "request_fingerprint": "a" * 64,
                "privacy_guard_version": "fixture-retained-history",
            },
        )
    )


def test_cos_overlay_allows_exact_member_bridge_without_candidate_mining_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    settings = _configuration_settings(tmp_path)
    retained = copy.deepcopy(settings.adapter_config[TEST_KEY])
    app = create_application(settings)
    with TestClient(app, base_url="https://central-fixture.ts.net") as client:
        runtime = app.state.runtime
        descriptors = {
            row["capability_id"]: row
            for row in _control(client)["bridge_wizard"]["capabilities"]
        }
        assert all(descriptors[name]["enrollment_selectable"] for name in CAPABILITIES)
        assert not descriptors["apple_messages_read"]["enrollment_selectable"]
        assert not descriptors["apple_contacts"]["enrollment_selectable"]
        for capability in ("apple_messages_read", "apple_contacts", "obsidian"):
            denied = client.post(
                "/api/member/v1/settings/bridges",
                headers=MEMBER_HEADERS,
                json={
                    "request_id": f"cos-unselected-{capability}",
                    "expected_revision": _control(client)["revision"],
                    "member_id": MEMBER_A,
                    "device_class": "this_mac",
                    "capabilities": [capability],
                },
            )
            assert denied.status_code == 409, denied.text
            assert denied.json()["detail"]["request_recorded"] is False
        assert _control(client)["bridge_wizard"]["current"] is None
        assert runtime.settings.bridge_enrollments == {}
        assert not list(runtime.bridge_enrollment.package_dir.glob("*.adminmebridge"))
        assert (
            client.portal.call(_events, runtime, "settings.bridge_enrollment_started")
            == []
        )

        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        headers = _bridge_headers(transfer)
        for capability in CAPABILITIES:
            _enable(helper, capability)
        helper.finish_permission_phase()
        record = runtime.settings.bridge_enrollments[
            MEMBER_HEADERS["Tailscale-User-Login"]
        ]
        assert set(record["enabled_packs"]) == set(PACKS.values())
        assert _control(client, OTHER_HEADERS)["bridge_wizard"]["current"] is None

        # The first observation settles only its own source, never another
        # selected service that has not yet supplied any observation.
        event_ids: dict[str, str] = {}
        for capability in CAPABILITIES:
            response = client.post(
                "/api/bridge/ingest",
                headers=headers,
                json=_observation(runtime, capability, f"fixture-source-{capability}"),
            )
            assert response.status_code == 202, response.text
            event_ids[capability] = response.json()["event_id"]
            control = _control(client)
            current = control["bridge_wizard"]["current"]
            admitted_sources = {
                row["source_kind"]: row
                for row in control["source_policy_v2"]["sources"]
                if row.get("bridge_binding", {}).get("enrollment_id")
                == transfer["enrollment_id"]
            }
            assert set(admitted_sources) == {f"adapter:{name}" for name in CAPABILITIES}
            for selected in CAPABILITIES:
                expected = "settled" if selected in event_ids else "observing"
                assert current["capability_states"][selected]["state"] == expected
                source = admitted_sources[f"adapter:{selected}"]
                assert source["coordinate_state"] == "ambiguous"
                assert source["coordinate"]["workspace"] == "unknown"
                assert source["configurable"] is False
                assert source["connection_state"] == (
                    "connected" if selected in event_ids else "configured"
                )
                assert source["acquisition"]["state"] == "active"
                assert source["acquisition"]["coverage"] == "partial"
                assert source["acquisition"]["freshness"] == (
                    "observed" if selected in event_ids else "unknown"
                )
                assert source["acquisition"]["provider_writes"] is False
                assert source["bridge_binding"]["member_id"] == MEMBER_A
                assert source["bridge_binding"]["private_only"] is True
                assert source["selected_container_ids"] == (
                    []
                    if selected == "apple_notes"
                    else [f"fixture-{selected}-container"]
                )
        assert current["state"] == "active_observation"
        assert current["writes_authorized"] is False
        assert not any(
            row.get("bridge_binding")
            for row in _control(client, OTHER_HEADERS)["source_policy_v2"]["sources"]
        )
        connections = [
            row
            for row in _control(client)["connections"]
            if row.get("deployment") == "member bridge"
        ]
        assert len(connections) == 3
        for connection in connections:
            assert connection["setup"] == "connected"
            assert connection["permissions"]["write"] == []
            assert connection["safety"]["writes_execute"] is False

        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:member_knowledge_indexing",
            event_ids["apple_notes"],
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "source-policy-v2:shadow",
            event_ids["apple_notes"],
        )
        client.portal.call(
            runtime.wait_for_projection, "member_knowledge", event_ids["apple_notes"]
        )
        historical = client.get(
            "/api/system/historical-attribution", headers=MEMBER_HEADERS
        )
        assert historical.status_code == 200, historical.text
        _assert_history(historical.json())
        history_watermark = historical.json()["watermark_event_id"]
        immutable_history = client.portal.call(
            _history, runtime.event_log, history_watermark
        )
        _assert_history(immutable_history)
        assurance = client.portal.call(
            _settled_source_assurance, runtime, event_ids["apple_notes"]
        )
        assert assurance["state"] == "healthy", assurance
        assert assurance["counts"]["source_facts"] == 3
        assert all(
            assurance["counts"][name] == 0 for name in ("failed", "missing", "pending")
        )
        assert client.portal.call(_events, runtime, "triage.item.proposed") == []
        sources = _control(client)["candidate_identification"]["sources"]
        for capability in CAPABILITIES:
            source = next(
                row for row in sources if row["id"] == f"adapter:{capability}"
            )
            assert source["policy"]["mode"] == "manual"
            assert source["policy"]["language_enabled"] is False
        mine = client.get("/api/capture/v1/knowledge", headers=MEMBER_HEADERS)
        other = client.get("/api/capture/v1/knowledge", headers=OTHER_HEADERS)
        assert mine.status_code == other.status_code == 200
        assert len(mine.json()["items"]) == 1
        assert other.json()["items"] == []
        assert settings.adapter_config[TEST_KEY] == retained
        assert client.portal.call(_events, runtime, "messaging.received") == []
        _assert_denied_batch_is_atomic(client, transfer)

        # A stale device, changed permission claim, or widened pack set cannot
        # reuse the authenticated identity's otherwise valid enrollment.
        for key, value in (
            ("request_id", "unrelated-member-request"),
            ("bridge_node_id", "unapproved-device"),
            ("observation_only", False),
            ("allowed_packs", [*record["allowed_packs"], "messaging-bluebubbles"]),
        ):
            envelope = _observation(runtime, "apple_calendar", f"fixture-forged-{key}")
            with monkeypatch.context() as patch:
                patch.setitem(record, key, value)
                refused = client.post(
                    "/api/bridge/ingest", headers=headers, json=envelope
                )
                assert refused.status_code == 403, refused.text
                assert (
                    client.portal.call(_history, runtime.event_log, history_watermark)
                    == immutable_history
                )
            assert (
                client.portal.call(
                    runtime.event_log.get_by_idempotency,
                    runtime.settings.instance.tenant_id,
                    envelope["idempotency_key"],
                )
                is None
            )

        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:bridge-acquisition",
            "credential-free CoS-live/read-only-member-bridge acceptance",
        )
        command_event_id = client.portal.call(_queue_calendar_command, runtime)
        pending = client.get(
            "/api/bridge/commands?providers=apple_calendar", headers=headers
        )
        assert pending.status_code == 200, pending.text
        assert pending.json()["observation_active"] is False
        assert pending.json()["commands"] == []
        [terminal] = client.portal.call(
            _events, runtime, "external_sync.write_completed"
        )
        assert terminal["causation_id"] == command_event_id
        assert terminal["payload"]["suppressed"] is True
        assert terminal["payload"]["provider_called"] is False
        assert (
            terminal["payload"]["result"]["reason"]
            == "member_bridge_acquisition_read_only"
        )
        assert (
            client.get(
                "/api/bridge/commands?providers=apple_calendar", headers=headers
            ).json()["commands"]
            == []
        )
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_completed"))
            == 1
        )
        for kind in (
            "external.sent",
            "external_sync.write_attempted",
            "external_sync.write_sent",
        ):
            assert client.portal.call(_events, runtime, kind) == []

        paused = client.post(
            f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
            headers=MEMBER_HEADERS,
            json={"command_id": "fixture-pause-bridge", "action": "pause"},
        )
        assert paused.status_code == 200, paused.text
        assert (
            client.portal.call(_history, runtime.event_log, history_watermark)
            == immutable_history
        )
        assert (
            client.post(
                "/api/bridge/ingest",
                headers=headers,
                json=_observation(
                    runtime, "apple_calendar", "fixture-paused-observation"
                ),
            ).status_code
            == 403
        )
        assert not any(
            row.get("bridge_binding")
            for row in _control(client)["source_policy_v2"]["sources"]
        )
        resumed = client.post(
            f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
            headers=MEMBER_HEADERS,
            json={"command_id": "fixture-resume-bridge", "action": "resume"},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "active_observation"

    restarted = create_application(settings)
    with TestClient(restarted) as client:
        runtime = restarted.state.runtime
        current = _control(client)["bridge_wizard"]["current"]
        assert current["state"] == "active_observation"
        assert all(
            row["state"] == "settled" for row in current["capability_states"].values()
        )
        assert _control(client, OTHER_HEADERS)["bridge_wizard"]["current"] is None
        assert (
            client.portal.call(_events, restarted.state.runtime, "triage.item.proposed")
            == []
        )
        pending = client.get(
            "/api/bridge/commands?providers=apple_calendar", headers=headers
        )
        assert pending.status_code == 200, pending.text
        assert pending.json()["commands"] == []
        assert (
            len(
                client.portal.call(
                    _events, restarted.state.runtime, "external_sync.write_completed"
                )
            )
            == 1
        )
        revoked = client.post(
            f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
            headers=MEMBER_HEADERS,
            json={"command_id": "fixture-revoke-bridge", "action": "revoke"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["state"] == "revoked"
        assert (
            client.post(
                "/api/bridge/ingest",
                headers=headers,
                json=_observation(
                    runtime, "apple_notes", "fixture-revoked-observation"
                ),
            ).status_code
            == 403
        )
        assert (
            client.portal.call(_history, runtime.event_log, history_watermark)
            == immutable_history
        )

    async def audit_closed_encrypted_log() -> dict[str, Any]:
        log = EventLog(
            settings.instance.event_log_path,
            settings.encryption_key,
            read_only=True,
        )
        try:
            return await _history(log, history_watermark)
        finally:
            await log.close()

    # The HTR-10 exporter opens this same encrypted log read-only and calls the
    # same audit. No active runtime, current grant or enrollment config is needed.
    assert asyncio.run(audit_closed_encrypted_log()) == immutable_history


def test_retained_test_without_cos_overlay_cannot_start_a_member_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    settings = _configuration_settings(tmp_path)
    config = copy.deepcopy(settings.adapter_config)
    config.pop(COS_KEY)
    # The stand-alone bounded acquisition fixture retains a real personal pack
    # definition, disabled here to keep its provider boundary inert.
    config["adapters"][0]["kind"] = "messaging-gmail-api"
    config["adapters"][0]["enabled"] = False
    config["adapters"][0]["config"]["user_email"] = config[TEST_KEY][
        "provider_account_id"
    ]
    app = create_application(replace(settings, adapter_config=config))
    with TestClient(app) as client:
        before = _control(client)
        response = client.post(
            "/api/member/v1/settings/bridges",
            headers=MEMBER_HEADERS,
            json={
                "request_id": REQUEST_ID,
                "expected_revision": before["revision"],
                "member_id": MEMBER_A,
                "device_class": "this_mac",
                "capabilities": CAPABILITIES,
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["request_recorded"] is False
        assert _control(client)["revision"] == before["revision"]
        assert app.state.runtime.settings.bridge_enrollments == {}
        assert not list(
            app.state.runtime.bridge_enrollment.package_dir.glob("*.adminmebridge")
        )
        for path, body in (
            ("/api/bridge/ingest", {}),
            ("/api/bridge/ingest/batch", {"envelopes": [{}]}),
        ):
            assert (
                client.post(path, headers=MEMBER_HEADERS, json=body).status_code == 403
            )


def test_cos_overlay_does_not_admit_legacy_bridge_without_member_request(
    tmp_path: Path,
) -> None:
    settings = _configuration_settings(tmp_path)
    pack = PACKS["apple_notes"]
    legacy = {
        "member_id": MEMBER_A,
        "enrollment_id": "fixture-retained-legacy-bridge",
        "status": "active",
        "configured_packs": [pack],
        "enabled_packs": [pack],
        "authorized_packs": [pack],
        "adapters_active": [pack],
        "pack_config": {pack: {"member_id": MEMBER_A}},
        "pack_capability_states": {pack: "not_required"},
        "provider_capability_state": "not_required",
        "capability_attestations": {},
    }
    app = create_application(
        replace(
            settings,
            bridge_enrollments={MEMBER_HEADERS["Tailscale-User-Login"]: legacy},
        )
    )
    with TestClient(app) as client:
        runtime = app.state.runtime
        headers = _bridge_headers(legacy)
        envelope = _observation(runtime, "apple_notes", "fixture-legacy-note")
        assert _control(client)["bridge_wizard"]["current"] is None
        for path, body in (
            ("/api/bridge/ingest", envelope),
            ("/api/bridge/ingest/batch", {"envelopes": [envelope]}),
        ):
            refused = client.post(path, headers=headers, json=body)
            assert refused.status_code == 403, refused.text
            assert (
                refused.json()["detail"] == "member_bridge_explicit_enrollment_required"
            )
        assert client.get("/api/bridge/commands", headers=headers).status_code == 403
        assert client.portal.call(_events, runtime, "member_knowledge.note_added") == []
