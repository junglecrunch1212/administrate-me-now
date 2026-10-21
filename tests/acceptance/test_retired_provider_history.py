"""Retired ownership survives CoS-only acquisition without operational authority.

The production runtime, encrypted source log, intake/extraction, historical audit,
HTR-10 exporter and reducer are real. Provider/Gateway transports, source checkout
coordinates and host diagnostics use credential-free disposable boundaries.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import shutil
import time

import pytest

from adminme.historical_attribution import audit_historical_attribution
from adminme.provider_bindings import (
    validate_historical_provider_bindings,
    validate_provider_bindings,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap import htr10_export, submission_repair
from bootstrap.candidate_readiness import CandidateReadinessError
from bootstrap.contracts import atomic_write
from bootstrap.secrets import secret_store
from tests.acceptance import test_cos_live_maintenance as maintenance
from tests.acceptance.test_forwarded_channel_intake import MEMBER_A, MEMBER_B, ROOT
from tests.acceptance.test_forwarded_submission_recovery import repair_manifest
from tests.acceptance.test_forwarded_submission_repair_operations import _gateway
from tests.acceptance.test_service_owned_cos_sources_direct_intake import (
    ROUTE_A,
    _member_message,
    _route_result,
    _service_message,
)
from tests.acceptance.test_triage_vertical_slice import _events
from tests.unit.bootstrap.test_submission_repair_readiness import _fixture, _review


PERSONAL_ACCOUNT = "personal-fixture@example.invalid"


def _retained_personal_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    original = maintenance._live_controller

    def controller(*args, **kwargs):
        value, activate, pause = original(*args, **kwargs)
        path = value.config_dir / "adapters.json"
        config = json.loads(path.read_bytes())
        personal = config["adapters"][0]
        assert personal["id"] == "fixture-personal-gmail"
        assert personal["enabled"] is False
        personal["kind"] = "messaging-gmail-api"
        personal["config"].update(
            user_email=PERSONAL_ACCOUNT,
            workspace="personal",
            permission_scope=["https://www.googleapis.com/auth/gmail.readonly"],
            binding_provenance="oauth-subject",
            ownership_attested=True,
            oauth_credentials_ref="keychain://fixture-personal/never-resolved",
            read_only=True,
            enable_writes=False,
        )
        config["adapters"].append(
            {
                "id": "optional-unenrolled",
                "kind": "tasks-google-tasks",
                "enabled": False,
                "config": {},
            }
        )
        atomic_write(path, json.dumps(config).encode())
        return value, activate, pause

    # This extends only the existing disposable OS/configuration fixture. The
    # runtime's real binding validator and adapter loader remain untouched.
    monkeypatch.setattr(maintenance, "_live_controller", controller)


def _capture_history_and_failed_forward(tmp_path: Path, monkeypatch):
    _retained_personal_setup(monkeypatch)
    gateway = _gateway(monkeypatch)
    with maintenance.maintenance_runtime(
        tmp_path, monkeypatch, allow_degraded=True
    ) as values:
        _controller, _activate, _pause, services, settings, gmail, config_dir = values
        client = services.client
        assert client is not None
        runtime = client.app.state.runtime
        config = json.loads((config_dir / "adapters.json").read_bytes())
        assert set(runtime.adapters.inventory()) == {"fixture-cos-gmail"}
        assert {row.adapter_id for row in runtime.settings.provider_bindings} == {
            "fixture-cos-gmail"
        }
        # A retained, complete source fact models the predecessor's personal
        # inbox history. The retired credential must never be resolved: the
        # existing provider fixture asserts that only the CoS reference is used.
        historical = _member_message(runtime)
        historical.source_adapter = "messaging-gmail-api:fixture-personal-gmail"
        historical.source_account_id = PERSONAL_ACCOUNT
        historical.owner_scope = historical.visibility_scope = f"private:{MEMBER_A}"
        historical.payload.update(
            member_id=MEMBER_A,
            routing_target_id=MEMBER_A,
            source_channel="gmail",
            provider="gmail",
            provider_account_id=PERSONAL_ACCOUNT,
            to_identifier=PERSONAL_ACCOUNT,
            to_identifiers=[PERSONAL_ACCOUNT],
        )
        historical_id = client.portal.call(runtime.events.write, historical)
        client.portal.call(
            runtime.wait_for_subscriber, "direct-intake:routing", historical_id
        )
        services.settle()
        assert gateway["calls"] == 0
        assert client.portal.call(_events, runtime, "triage.item.proposed") == []

        delegated = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "maintenance.example.test"},
            json={
                "request_id": "retired-history-forward",
                "context_scope": "member_private",
                "permissions": ["read_context", "create_internal_work"],
            },
        )
        assert delegated.status_code == 201, delegated.text
        selected = _service_message(
            runtime,
            key="retired-history-forward",
            trusted_route_key=ROUTE_A,
            from_identifier="member-a@example.invalid",
            subject="Selected forward",
            body="Preserved selected background.\nCall the school office.",
            event_at_ms=int(time.time() * 1000),
        )
        selected.source_adapter = "messaging-gmail-api:fixture-cos-gmail"
        source_id = client.portal.call(runtime.events.write, selected)
        cue_id = _route_result(client, source_id)["payload"]["cue_event_id"]
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:interpretation", cue_id
        )
        source = client.portal.call(runtime.event_log.get, cue_id)
        failed = next(
            event
            for event in client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            if event["payload"]["source_event_id"] == cue_id
        )
        assert failed["payload"]["assessment_outcome"] == "failed"
        assert gateway["calls"] == 1
        headers = {"Tailscale-User-Login": "maintenance.example.test"}
        audit_response = client.get(
            "/api/system/historical-attribution", headers=headers
        )
        assert audit_response.status_code == 200, audit_response.text
        assert audit_response.json()["state"] == "healthy"
        assert audit_response.json()["unattributed_event_count"] == 0
        assert PERSONAL_ACCOUNT not in audit_response.text
        retained_config = runtime.settings.adapter_config["adapters"][0]["config"]
        workspace = retained_config.pop("workspace")
        try:
            refused = client.get("/api/system/historical-attribution", headers=headers)
            assert refused.status_code == 503
            assert refused.json() == {
                "detail": "historical ownership evidence is invalid"
            }
        finally:
            retained_config["workspace"] = workspace
        assert {row.adapter_id for row in runtime.settings.provider_bindings} == {
            "fixture-cos-gmail"
        }
        # Enter observation through the real controller object owned by the
        # application; this test exports history and performs no activation.
        client.portal.call(
            runtime.observation.enable, "operator:fixture", "history audit"
        )
        diagnostics = services.settle()
        assert diagnostics["observation_active"] is True
        assurance = diagnostics["capabilities"]["policy:source-assurance"]["details"]
        assert assurance["counts"]["failed"] == 1
        assert assurance["counts"]["pending"] == assurance["counts"]["missing"] == 0
        assert gmail.calls == 0
        assert set(runtime.adapters.inventory()) == {"fixture-cos-gmail"}
        assert (
            client.portal.call(runtime.event_log.get, historical_id)["payload"]
            == historical.payload
        )
    return settings, config, diagnostics, source, failed, audit_response.json()


@pytest.mark.parametrize(
    "defect", ["none", "malformed", "cross_owner", "ambiguous", "missing_optional"]
)
def test_retired_member_history_has_attribution_but_never_reactivates_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    live = tmp_path / "production-composition"
    live.mkdir()
    settings, config, diagnostics, source, failed, endpoint_audit = (
        _capture_history_and_failed_forward(live, monkeypatch)
    )
    fixture = _fixture(tmp_path / "export", monkeypatch)
    paths = fixture["paths"]
    (paths.instance_dir / "events").mkdir(mode=0o700)
    shutil.copy2(
        settings.instance.event_log_path, paths.instance_dir / "events/events.db"
    )
    reference = secret_store(paths, test_mode=True).put(
        "event-log", settings.encryption_key
    )
    mapping = {
        "members": [
            {"member_id": member, "role": "principal"}
            for member in (MEMBER_A, MEMBER_B)
        ]
    }
    personal = config["adapters"][0]
    if defect == "malformed":
        personal["config"].pop("workspace")
    elif defect == "cross_owner":
        personal["config"]["member_id"] = MEMBER_B
    elif defect == "ambiguous":
        conflict = copy.deepcopy(personal)
        conflict["id"] = "retired-conflicting-owner"
        conflict["config"]["member_id"] = MEMBER_B
        config["adapters"].append(conflict)
    elif defect == "missing_optional":
        personal["config"] = {}
    original_config = copy.deepcopy(config)
    binding_args = {
        "member_roles": {MEMBER_A: "principal", MEMBER_B: "principal"},
        "household_mapping": mapping,
        "service_principal_ids": {COS_SERVICE_PRINCIPAL_ID},
    }
    active = validate_provider_bindings(config, **binding_args)
    assert {row.adapter_id for row in active} == {"fixture-cos-gmail"}
    assert config == original_config
    manifest = repair_manifest(source, failed).model_dump(mode="json")
    manifest.update(
        candidate_commit=fixture["commit"],
        owner_member_id=MEMBER_A,
        minimum_source_facts=diagnostics["capabilities"]["policy:source-assurance"][
            "details"
        ]["counts"]["source_facts"],
    )
    manifest["targets"][0]["member_id"] = MEMBER_A
    atomic_write(fixture["manifest"], json.dumps(manifest).encode())
    for name, value in (
        ("adapters.json", config),
        ("member-mapping.json", mapping),
        ("secret-references.json", {"references": {"event_log": reference}}),
    ):
        atomic_write(paths.instance_dir / "config" / name, json.dumps(value).encode())
    original_identity = submission_repair.reviewed_candidate_commit
    monkeypatch.setattr(
        submission_repair,
        "reviewed_candidate_commit",
        lambda path: fixture["commit"] if path == ROOT else original_identity(path),
    )
    monkeypatch.setattr(
        htr10_export.ServiceManager, "runtime_diagnostics", lambda _self: diagnostics
    )
    historical_output = paths.backup_dir / "retired-historical.json"
    source_output = paths.backup_dir / "retired-source.json"

    async def export():
        return await htr10_export.export_htr10_audits(
            paths,
            repository_root=ROOT,
            historical_output=historical_output,
            source_output=source_output,
            ownership_reviewed=True,
            submission_repair_manifest=fixture["manifest"],
            test_mode=True,
        )

    if defect in {"malformed", "ambiguous"}:
        with pytest.raises(ValueError):
            validate_historical_provider_bindings(config, **binding_args)
        with pytest.raises(ValueError):
            asyncio.run(export())
        assert not historical_output.exists()
        return

    bindings = validate_historical_provider_bindings(config, **binding_args)
    assert "optional-unenrolled" not in {row.adapter_id for row in bindings}
    assert all(
        not hasattr(row, "b03_dispatch_coordinate")
        and not hasattr(row, "permission_scope")
        for row in bindings
    )
    from adminme.events.log import EventLog

    async def historical_audit():
        log = EventLog(
            paths.instance_dir / "events/events.db",
            settings.encryption_key,
            read_only=True,
        )
        try:
            return await audit_historical_attribution(
                log,
                repository_root=ROOT,
                provider_bindings=bindings,
                hash_key=b"retired-history-private-fixture" * 2,
            )
        finally:
            await log.close()

    audited = asyncio.run(historical_audit())
    asyncio.run(export())
    assert config == original_config
    assert (
        json.loads((paths.instance_dir / "config/adapters.json").read_bytes())
        == original_config
    )
    historical = json.loads(historical_output.read_bytes())
    assert historical["state"] == audited["state"]
    count_fields = (
        "source_event_count",
        "attributed_event_count",
        "unattributed_event_count",
    )
    assert {key: historical[key] for key in count_fields} == {
        key: audited[key] for key in count_fields
    }
    assert historical["source_event_count"] == endpoint_audit["source_event_count"]
    fixture.update(historical=historical_output, source=source_output)
    if defect in {"cross_owner", "missing_optional"}:
        assert historical["state"] == "degraded"
        assert historical["unattributed_event_count"] == 1
        with pytest.raises(CandidateReadinessError):
            _review(fixture)
        return
    assert historical["state"] == "healthy"
    assert historical["unattributed_event_count"] == 0
    assert {key: historical[key] for key in count_fields} == {
        key: endpoint_audit[key] for key in count_fields
    }
    assert any(
        row["binding_state"] == "matched" and row["workspace"] == "personal"
        for row in historical["groups"]
    )
    assert _review(fixture)["status"] == "repair_pending"
    exported = json.loads(source_output.read_bytes())
    assert exported["counts"]["failed"] == 1
    assert exported["provider_effects"]["counts"]["provider_called_true"] == 0
    assert PERSONAL_ACCOUNT not in historical_output.read_text()
    assert "Call the school office" not in source_output.read_text()
