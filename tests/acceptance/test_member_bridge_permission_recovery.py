"""Real retained enrollment/central composition with synthetic native UI only.

These tests belong in hosted CI or an authorized disposable lab, never on a
member acceptance device. Existing CoS fixture provider credentials are inert.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.adapters.cos_channel_acquisition import policy_from_config as cos_policy
from adminme.adapters.loader import load_builtin_adapters
from adminme.bridge_enrollment import CAPABILITIES as DESCRIPTORS
from adminme.bridge_enrollment import (
    PROGRESS_CONTRACT,
    canonical_bytes,
    progress_signature,
)
from adminme.runtime.application import create_application
from adminme_bridge.config import load_config
from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper
from adminme_bridge.enrollment_ui import LocalSourceChoice
from bootstrap.member_bridge_permission_controller import (
    compatible_helper_class,
    http_progress_report,
    run_controller,
)
from bootstrap.bridge_enrollment import BridgeEnrollmentError, MemberBridgeInstaller
from bootstrap.member_bridge_launch import compatible_installer_class
from tests.acceptance.test_cos_member_bridge_acquisition import (
    CAPABILITIES,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    PACKS,
    REQUEST_ID,
    ROOT,
    _assert_history,
    _bridge_headers,
    _configuration_settings,
    _control,
    _enable,
    _history,
    _manual_policies,
    _observation,
    _prepare_helper,
    _settled_source_assurance,
)
from tests.acceptance.test_forwarded_channel_intake import TEXT, _events
from tests.acceptance.test_member_google_connections import (
    PERSONAL_ACCOUNT,
    SCOPES,
    WRITE_SCOPES,
    ReadOnlyGoogle,
    google_configuration,
)


class NativeFixture:
    def __init__(self, *, retry: bool = True, deny: str | None = None) -> None:
        self.retry = retry
        self.deny = deny
        self.calls: list[tuple[str, str]] = []

    def confirm_retry(self, labels: list[str]) -> bool:
        assert labels
        self.calls.append(("retry", "selected"))
        return self.retry

    def confirm_permission(
        self, capability: str, _label: str, _requirement: str
    ) -> bool:
        self.calls.append(("permission", capability))
        return capability != self.deny

    def choose_source(self, capability: str, _label: str) -> LocalSourceChoice:
        self.calls.append(("source", capability))
        return LocalSourceChoice(
            f"fixture-{capability}-container", f"fixture-{capability}-account"
        )

    def content_free_probe(self, capability: str, _choice: Any) -> dict[str, Any]:
        self.calls.append(("probe", capability))
        return {"status": "passed", "content_exposed": False}


def _block_three(helper: Any) -> None:
    helper.record_prerequisite_missing(
        "apple_calendar", reason="local_prerequisite_unavailable"
    )
    helper.record_permission("apple_reminders", granted=False)
    helper.record_permission(
        "apple_notes", granted=True, pack_config=helper.pack_config_for("apple_notes")
    )
    helper.record_capability_degraded(
        "apple_notes", reason="local_capability_test_failed"
    )


def _composed_reporter(client: TestClient, responses: list[Any] | None = None) -> Any:
    def post(url: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers")
        assert "Tailscale-User-Login" not in headers
        assert len(headers["X-AdministrateMe-Bridge-State-SHA256"]) == 64
        # Only this isolated TestClient supplies synthetic authenticated identity.
        response = client.post(url, headers={**MEMBER_HEADERS, **headers}, **kwargs)
        if responses is not None and response.status_code == 409:
            responses.append(response.json())
        return response

    def reporter(
        bound: Any,
        payload: Any,
        signature: str,
        *,
        state_sha256: str,
        initial_start_recovery_sha256: str | None = None,
    ) -> dict[str, Any]:
        return http_progress_report(
            bound,
            payload,
            signature,
            state_sha256=state_sha256,
            initial_start_recovery_sha256=initial_start_recovery_sha256,
            post=post,
        )

    return reporter


def _resume(
    client: TestClient,
    helper: Any,
    *,
    reporter: Any = None,
    compatible_launch: bool = False,
) -> Any:
    installer = helper.installer
    if compatible_launch:
        installer = compatible_installer_class(MemberBridgeInstaller)(
            helper.paths,
            root=ROOT,
            test_mode=True,
            submitter=helper.installer.submitter,
        )
        installer._tailscale_identity = helper.installer._tailscale_identity
    return compatible_helper_class(MemberBridgeEnrollmentHelper).resume(
        helper.paths,
        repository_root=ROOT,
        test_mode=True,
        reporter=reporter or _composed_reporter(client),
        evidence_submitter=helper._evidence_submitter,
        installer=installer,
    )


def _run(
    client: TestClient,
    helper: Any,
    interaction: NativeFixture,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    def status(bound: Any) -> dict[str, Any]:
        response = client.get(
            "/api/bridge/enrollment/status",
            headers={
                **(headers or MEMBER_HEADERS),
                "X-AdministrateMe-Bridge-Enrollment": bound.transfer["enrollment_id"],
            },
            params={"request_id": bound.transfer["request_id"]},
        )
        if response.status_code != 200:
            from bootstrap.member_bridge_permission_controller import PermissionFailure

            raise PermissionFailure("identity_mismatch")
        return response.json()

    return run_controller(
        helper,
        interaction,
        operation="continue",
        expected_commit=helper.transfer["release_commit"],
        expected_central_commit=helper.transfer["release_commit"],
        progress_snapshot=status,
        descriptors=DESCRIPTORS,
        snapshot=lambda _helper: {"control": _control(client, headers)},
    )


def _block_at_initial_start(
    helper: Any, monkeypatch: pytest.MonkeyPatch, capability: str = "apple_notes"
) -> None:
    """Exercise retained enable_pack's actual rollback after central start ACK."""
    for selected in helper.capability_states:
        if selected != capability:
            helper.record_prerequisite_missing(
                selected, reason="local_prerequisite_unavailable"
            )
    helper.record_permission(
        capability,
        granted=True,
        pack_config=helper.pack_config_for(
            capability,
            source_coordinate=f"fixture-{capability}-container",
            account_coordinate=f"fixture-{capability}-account",
        ),
    )
    before_config = helper.paths.config_path.read_bytes()

    def failed_start() -> None:
        raise BridgeEnrollmentError("fixture launchd bootstrap failed")

    with monkeypatch.context() as patch:
        patch.setattr(helper.installer, "_start", failed_start)
        with pytest.raises(BridgeEnrollmentError, match="fixture launchd"):
            helper.prove_and_start(capability)
    assert helper.capability_states[capability] == "observation_starting"
    assert helper.paths.config_path.read_bytes() == before_config
    assert load_config(helper.paths.config_path).enabled_packs == []
    assert helper.installer.status()["service"] == "stopped"
    helper.record_capability_degraded(capability, reason="local_capability_test_failed")


def _initial_start_status(client: TestClient, helper: Any) -> dict[str, Any]:
    response = client.get(
        "/api/bridge/enrollment/status",
        headers=_bridge_headers(helper.transfer),
        params={"request_id": helper.transfer["request_id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _initial_start_post(
    client: TestClient,
    helper: Any,
    capability: str,
    proof: str | None,
) -> Any:
    transfer = helper.transfer
    payload = {
        "contract": PROGRESS_CONTRACT,
        **{
            key: transfer[key]
            for key in (
                "request_id",
                "enrollment_id",
                "member_id",
                "instance_id",
                "release_commit",
                "capability_set_sha256",
            )
        },
        "previous_state": helper.state,
        "state": helper.state,
        "nonce": "fixture-initial-start-recovery-nonce",
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "device_node_id": helper.device_node_id,
        "enrollment_event_id": None,
        "capability_id": capability,
        "capability_state": "observation_starting",
        "reason": None,
        "last_successful_observation": None,
        "pack_config": None,
        "capability_event_id": None,
    }
    headers = {
        **_bridge_headers(transfer),
        "X-AdministrateMe-Bridge-State-SHA256": hashlib.sha256(
            canonical_bytes(
                {
                    "state": helper.state,
                    "capability_states": helper.capability_states,
                }
            )
        ).hexdigest(),
    }
    if proof is not None:
        headers["X-AdministrateMe-Bridge-Initial-Start-Recovery"] = proof
    return client.post(
        "/api/bridge/enrollment/progress",
        headers=headers,
        json={**payload, "signature": progress_signature(helper._proof_key(), payload)},
    )


def _selected_google_boundary(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, list[str]]]:
    """Keep production adapters; replace only credential/provider I/O in CI."""
    calls: list[tuple[str, list[str]]] = []
    for adapter in load_builtin_adapters(config, repository_root=ROOT):
        module = sys.modules[type(adapter).__module__]
        monkeypatch.setattr(module, "_default_credentials_resolver", lambda _ref: {})

        def service(
            _credentials: Any, scopes: list[str], subject: str
        ) -> ReadOnlyGoogle:
            assert len(scopes) == 1 and scopes[0] in {
                *SCOPES.values(),
                *WRITE_SCOPES.values(),
            }
            calls.append((subject, scopes))
            # No provider mutation methods exist on this fixture boundary.
            return ReadOnlyGoogle(subject)

        monkeypatch.setattr(module, "_default_service_factory", service)
        monkeypatch.setattr(type(adapter), "POLL_INTERVAL_S", 3600)
    return calls


async def _wait_selected_google(runtime: Any) -> None:
    assert set(runtime.adapters.inventory()) == {
        "fixture-personal-gmail",
        "fixture-personal-calendar",
        "fixture-personal-tasks",
        "fixture-cos-gmail",
    }
    for _attempt in range(1000):
        if all(
            row.adapter._ctx is not None for row in runtime.adapters._managed.values()
        ):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("combined Google and CoS adapters did not start")


async def _emit_selected_google(runtime: Any) -> None:
    """Representative provider reads travel through the actual loaded adapters."""
    managed = runtime.adapters._managed
    gmail = managed["fixture-personal-gmail"].adapter
    await gmail._emit_messaging_received(
        ctx=gmail._ctx,
        message={
            "id": "initial-start-personal-mail",
            "threadId": "initial-start-personal-thread",
            "internalDate": "1925035200000",
            "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "sender@example.invalid"},
                    {"name": "To", "value": PERSONAL_ACCOUNT},
                    {"name": "Subject", "value": "Please submit the form"},
                ],
                "body": {"data": base64.urlsafe_b64encode(TEXT.encode()).decode()},
            },
        },
    )
    calendar = managed["fixture-personal-calendar"].adapter
    await calendar._emit_calendar_snapshot(
        ctx=calendar._ctx,
        event_type="calendar.event_added",
        calendar_id="primary",
        calendar_name="Fixture Google calendar",
        event={
            "id": "initial-start-personal-calendar",
            "summary": TEXT,
            "status": "confirmed",
            "start": {"dateTime": "2031-01-02T12:00:00Z"},
            "end": {"dateTime": "2031-01-02T13:00:00Z"},
            "updated": "2030-12-01T12:00:00Z",
            "etag": "fixture-1",
        },
    )
    tasks = managed["fixture-personal-tasks"].adapter
    await tasks._emit_item(
        ctx=tasks._ctx,
        event_type="list_item.added",
        tasklist={"id": "@default", "title": "Fixture Google list"},
        task={
            "id": "initial-start-personal-task",
            "title": TEXT,
            "status": "needsAction",
            "updated": "2030-12-01T12:00:00Z",
            "etag": "fixture-1",
        },
    )


def _assert_selected_google(client: TestClient, runtime: Any, access: str) -> None:
    for event_type, adapter in (
        ("messaging.received", "messaging-gmail-api"),
        ("calendar.event_added", "calendaring-google-calendar"),
        ("list_item.added", "tasks-google-tasks"),
    ):
        observed = [
            event
            for event in client.portal.call(_events, runtime, event_type)
            if event["source_adapter"] == adapter
            and event["source_account_id"] == PERSONAL_ACCOUNT
        ]
        assert len(observed) == 1
        client.portal.call(
            runtime.wait_for_subscriber,
            "source-policy-v2:shadow",
            observed[0]["event_id"],
        )
        if event_type == "messaging.received":
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:noise_filtering",
                observed[0]["event_id"],
            )
        audits = client.portal.call(
            _events, runtime, "source.policy_v2.shadow_evaluated"
        )
        matching = [
            event
            for event in audits
            if event["payload"]["source_event_id"] == observed[0]["event_id"]
        ]
        assert matching and all(
            event["payload"]["policy"]["triage_mode"] == "manual_only"
            for event in matching
        )
    control = _control(client)
    selected = [
        row
        for row in control["connections"]
        if row["id"].startswith("fixture-personal-")
    ]
    assert len(selected) == 3
    for row in selected:
        writable = access == "read_write" and row["id"] in {
            "fixture-personal-calendar",
            "fixture-personal-tasks",
        }
        assert bool(row["permissions"]["write"]) is writable
    sources = [
        row
        for row in control["source_policy_v2"]["sources"]
        if str(row.get("adapter_instance_id") or "").startswith("fixture-personal-")
    ]
    assert len(sources) >= 3
    assert all(
        row["acquisition"]["coverage"] == "selected_google_sources" for row in sources
    )
    assert all(row["policy"]["triage_mode"] == "manual_only" for row in sources)
    for row in sources:
        writable = access == "read_write" and row["adapter_instance_id"] in {
            "fixture-personal-calendar",
            "fixture-personal-tasks",
        }
        assert row["acquisition"]["provider_writes"] is writable
        assert row["acquisition"]["writes_require_existing_governance"]
    for endpoint, identifier in (
        (
            "/api/member/v1/surfaces/calendar?date=2031-01-02",
            "initial-start-personal-calendar",
        ),
        ("/api/member/v1/surfaces/reminders", "initial-start-personal-task"),
    ):
        mine = client.get(endpoint, headers=MEMBER_HEADERS)
        other = client.get(endpoint, headers=OTHER_HEADERS)
        assert mine.status_code == other.status_code == 200
        assert identifier in mine.text and identifier not in other.text
    for kind in (
        "triage.item.proposed",
        "triage.intent.submitted",
        "external_sync.write_requested",
        "external.sent",
    ):
        assert client.portal.call(_events, runtime, kind) == []


def _assert_initial_start_history(
    client: TestClient, runtime: Any, google_access: str | None
) -> None:
    if google_access is None:
        _assert_history(client.portal.call(_history, runtime.event_log))
        return
    # The original bridge-only helper intentionally omits central bindings.
    # Use production's current, historical-capable binding validation for the
    # combined source set, preserving exact immutable bridge attribution too.
    response = client.get("/api/system/historical-attribution", headers=MEMBER_HEADERS)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["state"] == "healthy", report
    assert report["source_event_count"] == report["attributed_event_count"] >= 6
    assert report["unattributed_event_count"] == 0
    assert (
        report["content_exposed"] is False
        and report["raw_identifiers_exposed"] is False
    )
    assert (
        sum(
            row["event_count"]
            for row in report["groups"]
            if row["binding_state"] == "member_bridge_source_reconciled"
        )
        == 3
    )
    assert all(
        row["binding_state"] in {"member_bridge_source_reconciled", "matched"}
        for row in report["groups"]
    )


@pytest.mark.parametrize(
    "google_access",
    [None, "read_only", "read_write"],
    ids=["cos_only", "cos_google_reads", "cos_google_read_write"],
)
def test_initial_start_rollback_recovers_all_sources_without_repeating_notes_consent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    google_access: str | None,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    settings = _configuration_settings(tmp_path)
    google_calls: list[tuple[str, list[str]]] = []
    if google_access is not None:
        config = google_configuration(
            tmp_path / "instance", read_write=google_access == "read_write"
        )
        google_calls = _selected_google_boundary(config, monkeypatch)
        settings = replace(settings, adapter_config=config)
    retained_cos_policy = cos_policy(settings.adapter_config).digest()
    with TestClient(
        create_application(settings),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        if google_access is not None:
            client.portal.call(_wait_selected_google, runtime)
            client.portal.call(_emit_selected_google, runtime)
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_at_initial_start(original, monkeypatch)
        snapshot = _initial_start_status(client, original)
        assert snapshot["permission_retry_capabilities"] == [
            "apple_calendar",
            "apple_reminders",
        ]
        assert set(snapshot["initial_start_recovery"]) == {"apple_notes"}
        proof = snapshot["initial_start_recovery"]["apple_notes"]
        assert set(proof) == {"proof_sha256", "configured_source_sha256"}
        assert all(len(value) == 64 for value in proof.values())
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        record = runtime.settings.bridge_enrollments[
            MEMBER_HEADERS["Tailscale-User-Login"]
        ]
        original_notes = copy.deepcopy(record["pack_config"][PACKS["apple_notes"]])
        helper = _resume(client, original, compatible_launch=True)
        interaction = NativeFixture()
        result = _run(client, helper, interaction)
        assert result["outcome"] == "ready", result
        assert result["provider_effects"] is False
        assert not any(
            capability == "apple_notes" for _action, capability in interaction.calls
        )
        assert record["pack_config"][PACKS["apple_notes"]] == original_notes
        progress = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        assert progress[: len(before)] == before
        notes_tail = [
            event["payload"]["capability_state"]
            for event in progress[len(before) :]
            if event["payload"].get("capability_id") == "apple_notes"
        ]
        assert notes_tail == ["observation_starting", "observing"]
        assert not (helper.paths.root / "permission-controller-pending.json").exists()
        source_ids = {}
        for capability in CAPABILITIES:
            accepted = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, capability, f"initial-start-recovered-{capability}"
                ),
            )
            assert accepted.status_code == 202, accepted.text
            source_ids[capability] = accepted.json()["event_id"]
        assert (
            _control(client)["bridge_wizard"]["current"]["state"]
            == "active_observation"
        )
        for capability, projection in (
            ("apple_calendar", "calendars"),
            ("apple_reminders", "lists"),
            ("apple_notes", "member_knowledge"),
        ):
            client.portal.call(
                runtime.wait_for_projection, projection, source_ids[capability]
            )
        calendar = client.get(
            "/api/member/v1/surfaces/calendar?date=2031-01-02&view=day",
            headers=MEMBER_HEADERS,
        )
        assert calendar.status_code == 200, calendar.text
        assert any(
            row["external_uid"] == "initial-start-recovered-apple_calendar"
            for row in calendar.json()["events"]
        )
        reminders = client.get(
            "/api/member/v1/surfaces/reminders", headers=MEMBER_HEADERS
        )
        assert reminders.status_code == 200, reminders.text
        assert "initial-start-recovered-apple_reminders" in json.dumps(
            reminders.json()["cockpit"]["items"]
        )
        notes = client.get("/api/member/v1/surfaces/notes", headers=MEMBER_HEADERS)
        assert notes.status_code == 200, notes.text
        assert any(
            row["knowledge_id"] == "initial-start-recovered-apple_notes"
            for row in notes.json()["notes"]
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:member_knowledge_indexing",
            source_ids["apple_notes"],
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "source-policy-v2:shadow",
            source_ids["apple_notes"],
        )
        assurance = client.portal.call(
            _settled_source_assurance, runtime, source_ids["apple_notes"]
        )
        assert assurance["state"] == "healthy", assurance
        _assert_initial_start_history(client, runtime, google_access)
        assert (
            len(
                client.get("/api/capture/v1/knowledge", headers=MEMBER_HEADERS).json()[
                    "items"
                ]
            )
            == 1
        )
        assert (
            client.get("/api/capture/v1/knowledge", headers=OTHER_HEADERS).json()[
                "items"
            ]
            == []
        )
        connections = [
            item
            for item in _control(client)["connections"]
            if item.get("deployment") == "member bridge"
        ]
        assert len(connections) == 3
        assert all(
            item["setup"] == "connected" and not item["safety"]["writes_execute"]
            for item in connections
        )
        apple_sources = [
            row
            for row in _control(client)["candidate_identification"]["sources"]
            if row["id"] in {f"adapter:{capability}" for capability in CAPABILITIES}
        ]
        assert len(apple_sources) == 3
        assert all(
            row["policy"]["mode"] == "manual"
            and row["policy"]["language_enabled"] is False
            for row in apple_sources
        )
        for kind in (
            "external.sent",
            "external_sync.write_requested",
            "external_sync.write_completed",
            "triage.item.proposed",
        ):
            assert client.portal.call(_events, runtime, kind) == []
        assert (
            cos_policy(runtime.settings.adapter_config).digest() == retained_cos_policy
        )
        if google_access is not None:
            _assert_selected_google(client, runtime, google_access)
        retained_progress = client.portal.call(
            _events, runtime, "bridge.enrollment_progressed"
        )
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as restarted:
        runtime = restarted.app.state.runtime
        if google_access is not None:
            restarted.portal.call(_wait_selected_google, runtime)
            _assert_selected_google(restarted, runtime, google_access)
        assert (
            cos_policy(runtime.settings.adapter_config).digest() == retained_cos_policy
        )
        assert (
            _control(restarted)["bridge_wizard"]["current"]["state"]
            == "active_observation"
        )
        restarted_progress = restarted.portal.call(
            _events, runtime, "bridge.enrollment_progressed"
        )
        assert restarted_progress == retained_progress
        _assert_initial_start_history(restarted, runtime, google_access)
        assert _initial_start_status(restarted, helper)["initial_start_recovery"] == {}
        assert restarted.portal.call(_events, runtime, "external.sent") == []

    if google_access is not None:
        assert len(google_calls) == 8
        assert {
            scope
            for account, scopes in google_calls
            if account == PERSONAL_ACCOUNT
            for scope in scopes
        } == {
            SCOPES["messaging-gmail-api"],
            (WRITE_SCOPES if google_access == "read_write" else SCOPES)[
                "calendaring-google-calendar"
            ],
            (WRITE_SCOPES if google_access == "read_write" else SCOPES)[
                "tasks-google-tasks"
            ],
        }


@pytest.mark.parametrize("capability", CAPABILITIES)
@pytest.mark.parametrize(
    "case", ["missing", "malformed", "changed", "observed_race", "config_drift"]
)
def test_initial_start_proof_refusals_precede_nonce_consumption_and_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
    case: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_at_initial_start(original, monkeypatch, capability)
        proof = _initial_start_status(client, original)["initial_start_recovery"][
            capability
        ]["proof_sha256"]
        record = runtime.settings.bridge_enrollments[
            MEMBER_HEADERS["Tailscale-User-Login"]
        ]
        if case == "observed_race":
            accepted = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, capability, "initial-start-observation-race"
                ),
            )
            assert accepted.status_code == 202, accepted.text
            assert (
                _control(client)["bridge_wizard"]["current"]["capability_states"][
                    capability
                ]["state"]
                == "degraded"
            )
        if case == "config_drift":
            changed_config = copy.deepcopy(record["pack_config"])
            changed_config[PACKS[capability]]["ownership_attested"] = False
            monkeypatch.setitem(record, "pack_config", changed_config)
        before_record = copy.deepcopy(record)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        before_receipt = original.paths.receipt_path.read_bytes()
        before_config = original.paths.config_path.read_bytes()
        provided = (
            None
            if case == "missing"
            else "not-a-proof"
            if case == "malformed"
            else "0" * 64
            if case == "changed"
            else proof
        )
        denied = _initial_start_post(client, original, capability, provided)
        assert denied.status_code == 409, denied.text
        assert (
            denied.json()["detail"]["error"] == "bridge_progress_stale_before_mutation"
        )
        assert denied.json()["detail"]["effects"] == "none"
        assert record == before_record
        assert (
            client.portal.call(_events, runtime, "bridge.enrollment_progressed")
            == before
        )
        assert original.paths.receipt_path.read_bytes() == before_receipt
        assert original.paths.config_path.read_bytes() == before_config
        if case in {"missing", "malformed", "changed"}:
            # Same nonce remains usable after the proven pre-mutation refusal.
            accepted = _initial_start_post(client, original, capability, proof)
            assert accepted.status_code == 202, accepted.text
        assert client.portal.call(_events, runtime, "external.sent") == []


@pytest.mark.parametrize("case", ["revoked", "previously_observing", "unknown"])
def test_initial_start_does_not_resume_revoked_active_or_unknown_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        if case == "previously_observing":
            _enable(original, "apple_notes")
            original.record_capability_degraded(
                "apple_notes", reason="local_capability_test_failed"
            )
        else:
            _block_at_initial_start(original, monkeypatch)
        if case == "revoked":
            response = client.post(
                f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
                headers=MEMBER_HEADERS,
                json={"command_id": "initial-start-revoke", "action": "revoke"},
            )
            assert response.status_code == 200, response.text
        if case == "unknown":
            pending = original.paths.root / "permission-controller-pending.json"
            pending.write_text('{"outcome":"unknown"}')
            pending.chmod(0o600)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        config = original.paths.config_path.read_bytes()
        receipt = original.paths.receipt_path.read_bytes()
        interaction = NativeFixture()
        result = _run(client, _resume(client, original), interaction)
        assert result["outcome"] == ("unknown" if case == "unknown" else "blocked"), (
            result
        )
        assert interaction.calls == []
        assert original.paths.receipt_path.read_bytes() == receipt
        assert original.paths.config_path.read_bytes() == config
        assert (
            client.portal.call(_events, runtime, "bridge.enrollment_progressed")
            == before
        )
        assert client.portal.call(_events, runtime, "external.sent") == []


def test_three_blocked_apple_paths_recover_with_original_signed_history_and_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        helper = _resume(client, original)
        interaction = NativeFixture()
        result = _run(client, helper, interaction)
        assert result["outcome"] == "ready", result
        assert result["provider_effects"] is False
        assert interaction.calls[0] == ("retry", "selected")
        assert {
            capability for action, capability in interaction.calls if action == "probe"
        } == set(CAPABILITIES)
        record = runtime.settings.bridge_enrollments[
            MEMBER_HEADERS["Tailscale-User-Login"]
        ]
        assert set(record["authorized_packs"]) == set(PACKS.values())
        assert set(record["enabled_packs"]) == set(PACKS.values())
        assert helper.state == "settling"
        assert not (helper.paths.root / "permission-controller-pending.json").exists()
        after = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        assert after[: len(before)] == before
        for capability in CAPABILITIES:
            rows = [
                event["payload"]
                for event in after[len(before) :]
                if event["payload"].get("capability_id") == capability
            ]
            assert rows[0]["capability_state"] == "permission_required"
            assert rows[1]["capability_state"] == "permission_verified"
            assert rows[-1]["capability_state"] == "observing"
            assert all(row["signature_verified"] is True for row in rows)
        source_ids = {}
        for capability in CAPABILITIES:
            accepted = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, capability, f"permission-recovered-{capability}"
                ),
            )
            assert accepted.status_code == 202, accepted.text
            source_ids[capability] = accepted.json()["event_id"]
        assert (
            _control(client)["bridge_wizard"]["current"]["state"]
            == "active_observation"
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:member_knowledge_indexing",
            source_ids["apple_notes"],
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "source-policy-v2:shadow",
            source_ids["apple_notes"],
        )
        assurance = client.portal.call(
            _settled_source_assurance, runtime, source_ids["apple_notes"]
        )
        assert assurance["state"] == "healthy", assurance
        _assert_history(client.portal.call(_history, runtime.event_log))
        assert _control(client, OTHER_HEADERS)["bridge_wizard"]["current"] is None
        for source in _control(client)["candidate_identification"]["sources"]:
            if source["id"] in {f"adapter:{capability}" for capability in CAPABILITIES}:
                assert source["policy"]["mode"] == "manual"
                assert source["policy"]["language_enabled"] is False
        for event_type in (
            "external.sent",
            "external_sync.write_requested",
            "external_sync.write_completed",
        ):
            assert client.portal.call(_events, runtime, event_type) == []


@pytest.mark.parametrize(
    "case",
    [
        "cancel",
        "revoke",
        "repair_drift",
        "cross_owner",
        "unknown",
        "malformed_ack",
        "deny_notes",
    ],
)
def test_recovery_refusals_preserve_history_and_never_retry_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        if case in {"revoke", "repair_drift"}:
            response = client.post(
                f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
                headers=MEMBER_HEADERS,
                json={
                    "command_id": f"permission-{case}",
                    "action": "revoke" if case == "revoke" else "repair",
                },
            )
            assert response.status_code == 200, response.text
        before_receipt = original.paths.receipt_path.read_bytes()
        before_config = original.paths.config_path.read_bytes()
        before_events = client.portal.call(
            _events, runtime, "bridge.enrollment_progressed"
        )
        calls = []

        def uncertain_reporter(
            bound: Any, payload: Any, signature: str, *, state_sha256: str
        ) -> dict[str, Any]:
            calls.append(payload)
            _composed_reporter(client)(
                bound, payload, signature, state_sha256=state_sha256
            )
            if case == "malformed_ack":
                return {}
            raise TimeoutError("fixture acknowledgement lost after commit")

        helper = _resume(
            client,
            original,
            reporter=uncertain_reporter
            if case in {"unknown", "malformed_ack"}
            else None,
        )
        interaction = NativeFixture(
            retry=case != "cancel", deny="apple_notes" if case == "deny_notes" else None
        )
        result = _run(
            client,
            helper,
            interaction,
            headers=OTHER_HEADERS if case == "cross_owner" else None,
        )
        assert result["outcome"] == (
            "cancelled"
            if case == "cancel"
            else "unknown"
            if case in {"unknown", "malformed_ack"}
            else "blocked"
        ), result
        if case == "deny_notes":
            assert ("probe", "apple_notes") not in interaction.calls
            assert helper.capability_states["apple_notes"] == "permission_denied"
            assert result["outcome"] == "blocked"
        else:
            assert original.paths.receipt_path.read_bytes() == before_receipt
            assert original.paths.config_path.read_bytes() == before_config
            after = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
            if case in {"unknown", "malformed_ack"}:
                assert len(calls) == 1
                assert len(after) == len(before_events) + 1
                journal = original.paths.root / "permission-controller-pending.json"
                assert journal.is_file()
                assert "fixture acknowledgement" not in journal.read_text()
                restarted = _resume(client, original, reporter=uncertain_reporter)
                repeated = _run(client, restarted, NativeFixture())
                assert repeated["outcome"] == "unknown"
                assert len(calls) == 1
                assert (
                    client.portal.call(_events, runtime, "bridge.enrollment_progressed")
                    == after
                )
            else:
                assert after == before_events
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert "fixture acknowledgement" not in json.dumps(result)


def test_mixed_settling_preserves_working_source_and_retries_blocked_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        original._report(
            original.state,
            capability_id="apple_calendar",
            capability_state="permission_required",
        )
        _enable(original, "apple_calendar")
        helper = _resume(client, original)
        helper.finish_permission_phase()
        assert helper.state == "settling"
        interaction = NativeFixture()
        result = _run(client, _resume(client, helper), interaction)
        assert result["outcome"] == "ready", result
        assert not any(
            capability == "apple_calendar" for _action, capability in interaction.calls
        )
        assert result["capabilities"][0]["status"] == "preserved"


def test_local_configuration_failure_after_ack_retains_unknown_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adminme_bridge import enrollment_helper as retained

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        helper = _resume(client, original)
        original_writer = retained.atomic_write

        def failing_config(path: Path, payload: bytes, **kwargs: Any) -> Any:
            if path == helper.paths.config_path:
                raise OSError("fixture disk write failure")
            return original_writer(path, payload, **kwargs)

        monkeypatch.setattr(retained, "atomic_write", failing_config)
        result = _run(client, helper, NativeFixture())
        assert result["outcome"] == "unknown", result
        assert result["capabilities"][0]["code"] == "local_configuration_unknown"
        assert (helper.paths.root / "permission-controller-pending.json").is_file()
        restarted = _resume(client, original)
        repeated = _run(client, restarted, NativeFixture())
        assert repeated["outcome"] == "unknown"


@pytest.mark.parametrize(
    "timing",
    [
        "before_start_returns",
        "during_next_permission",
        "before_next_progress",
        "late_observing_progress",
        "unchanged_aggregate",
    ],
)
def test_real_observation_interleavings_do_not_replay_native_setup_or_lose_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing: str
) -> None:

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        observed: set[str] = set()
        conflicts: list[dict[str, Any]] = []
        starts: list[str] = []
        admitted: list[tuple[str, str, int]] = []
        unchanged_boundary: list[tuple[str, str, str, str]] = []

        def observe(capability: str) -> None:
            if capability in observed:
                return
            before = _control(client)["bridge_wizard"]["current"]
            before_source = before["capability_states"][capability]["state"]
            response = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, capability, f"permission-race-{timing}-{capability}"
                ),
            )
            admitted.append((capability, before_source, response.status_code))
            assert before_source in {"observation_starting", "observing"}, before
            assert response.status_code == 202, response.text
            observed.add(capability)
            if timing == "unchanged_aggregate" and capability == second:
                after = _control(client)["bridge_wizard"]["current"]
                unchanged_boundary.append(
                    (
                        before["state"],
                        after["state"],
                        before_source,
                        after["capability_states"][capability]["state"],
                    )
                )
                assert (
                    before["capability_states"][last]["state"] == "permission_required"
                )
                assert (
                    after["capability_states"][last]["state"] == "permission_required"
                )

        def reporter(
            bound: Any, payload: Any, signature: str, *, state_sha256: str
        ) -> dict[str, Any]:
            if (
                timing == "before_next_progress"
                and payload.get("capability_id") == second
                and payload.get("capability_state") == "permission_verified"
            ):
                observe(first)
            if (
                timing == "late_observing_progress"
                and payload.get("capability_id") == second
                and payload.get("capability_state") == "observing"
            ):
                observe(second)
            if (
                timing == "unchanged_aggregate"
                and payload.get("capability_id") == last
                and payload.get("capability_state") == "permission_verified"
            ):
                observe(second)
            return _composed_reporter(client, conflicts)(
                bound, payload, signature, state_sha256=state_sha256
            )

        helper = _resume(client, original, reporter=reporter)
        # Installed receipts use canonical JSON key order. Exercise that real
        # resume order rather than assuming the selection-list order survives.
        setup_order = tuple(helper.capability_states)
        assert set(setup_order) == set(CAPABILITIES) and len(setup_order) == 3
        first, second, last = setup_order
        original_enable = helper.installer.enable_pack

        def enable(pack: str) -> dict[str, Any]:
            result = original_enable(pack)
            starts.append(pack)
            if timing == "before_start_returns":
                observe(
                    next(
                        capability
                        for capability, value in PACKS.items()
                        if value == pack
                    )
                )
            return result

        monkeypatch.setattr(helper.installer, "enable_pack", enable)

        class InterleavedNative(NativeFixture):
            def confirm_permission(
                self, capability: str, label: str, requirement: str
            ) -> bool:
                if capability == second and timing in {
                    "during_next_permission",
                    "late_observing_progress",
                    "unchanged_aggregate",
                }:
                    observe(first)
                return super().confirm_permission(capability, label, requirement)

        native = InterleavedNative()
        result = _run(client, helper, native)
        assert admitted and all(
            status == 202 for _capability, _prior, status in admitted
        ), admitted
        assert result["outcome"] == "ready", json.dumps(result, sort_keys=True)
        assert starts == [PACKS[capability] for capability in setup_order]
        assert [cap for action, cap in native.calls if action == "probe"] == list(
            setup_order
        )
        assert not (helper.paths.root / "permission-controller-pending.json").exists()
        for capability in CAPABILITIES:
            observe(capability)
        # A restart is a pure proof refresh: no native re-consent/probe/start.
        restarted = _resume(client, helper, reporter=reporter)
        restart_native = NativeFixture()
        repeated = _run(client, restarted, restart_native)
        assert repeated["outcome"] == "ready", repeated
        assert restart_native.calls == []
        assert restarted.state == "active_observation"
        assert set(restarted.capability_states.values()) == {"settled"}
        assert (
            _control(client)["bridge_wizard"]["current"]["state"]
            == "active_observation"
        )
        if timing in {
            "before_next_progress",
            "unchanged_aggregate",
        }:
            assert conflicts
            assert all(row["detail"]["effects"] == "none" for row in conflicts)
        if timing == "unchanged_aggregate":
            assert unchanged_boundary == [
                ("settling", "settling", "observing", "settled")
            ]
            assert len(conflicts) == 1
            assert conflicts[0]["detail"]["reason"] == "state_snapshot_mismatch"
        progress = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        if timing == "late_observing_progress":
            # The source arrived before its genuine helper observing fact.
            # Catch-up follows that fact; it is not a stale POST or a fake start.
            assert (second, "observation_starting", 202) in admitted
            second_progress = [
                row for row in progress if row["payload"].get("capability_id") == second
            ]
            tail = second_progress[-3:]
            assert [row["payload"]["capability_state"] for row in tail] == [
                "observing",
                "settling",
                "settled",
            ]
            assert [row["payload"]["reported_by"] for row in tail] == [
                "target_helper",
                "central_runtime",
                "central_runtime",
            ]
            assert conflicts == []
        for capability in CAPABILITIES:
            settled = [
                row
                for row in progress
                if row["payload"].get("capability_id") == capability
                and row["payload"].get("capability_state") == "settled"
            ]
            assert len(settled) == 1
        _assert_history(client.portal.call(_history, runtime.event_log))
        assert client.portal.call(_events, runtime, "external.sent") == []
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


def test_previously_attested_degraded_source_is_not_a_permission_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _enable(original, "apple_calendar")
        original.record_capability_degraded(
            "apple_calendar", reason="local_capability_test_failed"
        )
        original.record_prerequisite_missing(
            "apple_reminders", reason="local_prerequisite_unavailable"
        )
        original.record_permission("apple_notes", granted=False)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        config = original.paths.config_path.read_bytes()
        receipt = original.paths.receipt_path.read_bytes()
        native = NativeFixture()
        result = _run(client, _resume(client, original), native)
        assert result["outcome"] == "blocked", result
        assert {row["code"] for row in result["capabilities"]} == {
            "unsupported_recovery"
        }
        assert native.calls == []
        assert original.paths.config_path.read_bytes() == config
        assert original.paths.receipt_path.read_bytes() == receipt
        assert (
            client.portal.call(_events, runtime, "bridge.enrollment_progressed")
            == before
        )
        assert client.portal.call(_events, runtime, "external.sent") == []


@pytest.mark.parametrize("stage", ["permission", "source_selection"])
def test_native_cancellation_stops_without_inventing_prerequisite_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from bootstrap.member_bridge_permission_controller import PermissionFailure

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        config = original.paths.config_path.read_bytes()

        class CancelNative(NativeFixture):
            def confirm_permission(
                self, capability: str, label: str, requirement: str
            ) -> bool:
                if stage == "permission":
                    raise PermissionFailure(
                        "native_cancelled", native_error_number=-128
                    )
                return super().confirm_permission(capability, label, requirement)

            def choose_source(self, capability: str, label: str) -> Any:
                raise PermissionFailure("native_cancelled", native_error_number=-128)

        native = CancelNative()
        helper = _resume(client, original)
        result = _run(client, helper, native)
        assert result["outcome"] == "cancelled", result
        assert result["capabilities"][0]["code"] == "native_cancelled"
        assert result["capabilities"][0]["native_error_number"] == -128
        assert not any(
            action == "probe" or capability in {"apple_reminders", "apple_notes"}
            for action, capability in native.calls
        )
        assert original.paths.config_path.read_bytes() == config
        after = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        assert [
            event["payload"]["capability_state"] for event in after[len(before) :]
        ] == ["permission_required"]
        assert helper.capability_states["apple_calendar"] == "permission_required"
        assert not (helper.paths.root / "permission-controller-pending.json").exists()
        assert client.portal.call(_events, runtime, "external.sent") == []


def test_unknown_observing_ack_cannot_be_replaced_by_later_settlement_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        reports: list[str | None] = []

        def reporter(
            bound: Any, payload: Any, signature: str, *, state_sha256: str
        ) -> dict[str, Any]:
            result = _composed_reporter(client)(
                bound, payload, signature, state_sha256=state_sha256
            )
            reports.append(payload.get("capability_state"))
            if payload.get("capability_state") == "observing":
                raise TimeoutError("fixture observing acknowledgement lost")
            return result

        helper = _resume(client, original, reporter=reporter)
        actual_enable = helper.installer.enable_pack

        def early_observation(pack: str) -> dict[str, Any]:
            result = actual_enable(pack)
            response = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, "apple_calendar", "permission-lost-observing-ack"
                ),
            )
            assert response.status_code == 202, response.text
            return result

        monkeypatch.setattr(helper.installer, "enable_pack", early_observation)
        native = NativeFixture()
        result = _run(client, helper, native)
        assert result["outcome"] == "unknown", result
        assert helper.capability_states["apple_calendar"] == "observation_starting"
        assert (
            _control(client)["bridge_wizard"]["current"]["capability_states"][
                "apple_calendar"
            ]["state"]
            == "settled"
        )
        assert (helper.paths.root / "permission-controller-pending.json").exists()
        prior_reports = list(reports)
        receipt = helper.paths.receipt_path.read_bytes()
        restarted = _resume(client, original, reporter=reporter)
        assert _run(client, restarted, NativeFixture())["outcome"] == "unknown"
        assert reports == prior_reports
        assert helper.paths.receipt_path.read_bytes() == receipt
        assert reports.count("observing") == 1
        assert client.portal.call(_events, runtime, "external.sent") == []
