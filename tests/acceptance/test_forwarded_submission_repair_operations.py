"""Hosted maintenance/upgrade composition for an exact failed forwarded receipt.

Provider, Gateway HTTP, immutable release coordinates and OS Doctor transports
are disposable boundaries. The app, encrypted log, extraction, source assurance,
CLI containment, journaled updater, replay and rollback are production code.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import hashlib
import json
import os
import sys
import time
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from adminme.lib.skill_runner.fixture import _response, build_fixture_runtime
from bootstrap import cos_channel_activation as operation
from bootstrap import upgrade
from bootstrap.contracts import atomic_write
from bootstrap.secrets import secret_store
from bootstrap.submission_repair import (
    REPAIR_DOCTOR_CHECKS,
    SubmissionRepairError,
    SubmissionRepairManifest,
    offline_submission_repair,
    repair_binding,
    validate_submission_repair_diagnostics,
)
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.acceptance.test_cos_live_maintenance import maintenance_runtime
from tests.acceptance.test_forwarded_channel_intake import MEMBER_A, ROOT
from tests.acceptance.test_service_owned_cos_sources_direct_intake import (
    _service_message,
    _route_result,
    ROUTE_A,
)
from tests.acceptance.test_triage_vertical_slice import _events
from tests.unit.bootstrap.test_cos_channel_activation import _audit


def _gateway(monkeypatch):
    state = {"fail": True, "calls": 0, "release": None, "pending_seen": False}

    class Gateway:
        async def post(self, url, *, json, headers):
            request = httpx.Request("POST", url, json=json, headers=headers)
            if "candidates" not in ((json.get("args") or {}).get("schema") or {}).get(
                "properties", {}
            ):
                return _response(request)
            state["calls"] += 1
            if state["release"] is not None:

                async def released():
                    while not state["release"].is_set():
                        await asyncio.sleep(0.001)

                await asyncio.wait_for(released(), timeout=60)
            if state["fail"]:
                raise httpx.ReadTimeout(
                    "synthetic forwarded extraction timeout", request=request
                )
            quote = "Call the school office."
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "details": {
                            "json": {
                                "candidates": [
                                    {
                                        "target_kind": "task",
                                        "title": quote,
                                        "candidate": {},
                                        "evidence_quote": quote,
                                        "confidence": 0.9,
                                        "reasons": ["selected obligation"],
                                    }
                                ],
                                "reasons": [],
                            },
                            "provider": "fixture",
                            "model": "operator-repair-fixture",
                        },
                        "invocation_id": "fixture-repair-call",
                    },
                },
            )

    monkeypatch.setattr(
        "adminme.runtime.application.build_fixture_runtime",
        lambda **kwargs: replace(
            build_fixture_runtime(**kwargs), httpx_client_factory=Gateway
        ),
    )
    return state


def _doctor(services):
    diagnostics = services.settle()
    source = diagnostics["capabilities"]["policy:source-assurance"]["details"]
    checks = [
        {"name": name, "ok": True}
        for name in sorted(REPAIR_DOCTOR_CHECKS | {"canonical_synthetic_round_trip"})
    ]
    for check in checks:
        if check["name"] == "source_assurance_terminal_dispositions":
            check["ok"] = source["state"] == "healthy"
        elif check["name"] == "deployment_topology_and_recovery_readiness":
            check["ok"] = diagnostics["deployment_readiness"]["ready"]

    return {
        "status": "healthy" if all(row["ok"] for row in checks) else "degraded",
        "checks": checks,
    }


def _prepare_upgrade(
    controller, services, settings, manifest, manifest_path, monkeypatch
):
    paths = controller.paths
    candidate = paths.app_dir / "releases" / manifest.candidate_commit
    predecessor = paths.app_dir / "releases" / manifest.predecessor_commit
    for release in (candidate, predecessor):
        (release / "bootstrap").mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(
            release / "bootstrap/versions.json",
            (ROOT / "bootstrap/versions.json").read_bytes(),
        )
    pointer_path = paths.app_dir / "current-release.json"
    atomic_write(
        pointer_path,
        json.dumps(
            {
                "path": str(predecessor),
                "commit": manifest.predecessor_commit,
                "previous": None,
            }
        ).encode(),
    )
    config = paths.instance_dir / "config"
    initial = json.loads((config / "deployment-readiness.json").read_bytes())
    pending_id = "3" * 32
    manager = UpgradeManager(
        paths,
        root=ROOT,
        test_mode=True,
        pending_attestation_id=pending_id,
        submission_repair_manifest=manifest_path,
    )
    manager.services = services
    original_start, original_stop = services.start, services.stop

    def start():
        selected = json.loads(pointer_path.read_bytes())["commit"]
        settings.host_topology["deployment_binding"]["release_commit"] = selected
        original_start()
        return SimpleNamespace(status="degraded", runtime="running", openclaw="running")

    monkeypatch.setattr(services, "start", start)
    monkeypatch.setattr(services, "stop", lambda **_kwargs: original_stop())
    monkeypatch.setattr(
        services,
        "status",
        lambda: SimpleNamespace(
            status="degraded" if services.client else "stopped",
            runtime="running",
            openclaw="running",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        services, "runtime_is_live", lambda: services.client is not None, raising=False
    )
    monkeypatch.setattr(
        manager, "_candidate_commit", lambda _path: manifest.candidate_commit
    )
    monkeypatch.setattr(manager, "_attest_controller_provenance", lambda *_a: None)
    monkeypatch.setattr(
        manager, "_validate_instance_candidate", lambda *_a, **_kw: {"compatible": True}
    )
    monkeypatch.setattr(manager, "_preflight_keychain_access", lambda *_a: None)
    monkeypatch.setattr(manager, "_publish_release", lambda *_a: candidate)
    monkeypatch.setattr(
        manager,
        "_validate_retained_runtime",
        lambda *_a, **_kw: {
            "source_identity_method": "disposable-source-fixture",
            "tracked_source_tree_matches": True,
        },
    )
    binding = copy.deepcopy(initial["deployment_binding"])

    def snapshot(_paths):
        selected = json.loads(pointer_path.read_bytes())["commit"]
        return SimpleNamespace(
            predecessor_commit=selected,
            instance_fingerprint=binding["instance_fingerprint"],
            host_fingerprint=binding["host_fingerprint"],
            configuration_fingerprint=binding["configuration_fingerprint"],
            public=lambda: {"fixture": "unchanged-snapshot"},
        )

    monkeypatch.setattr(upgrade, "capture_deployment_snapshot", snapshot)
    pending_snapshot = snapshot(paths)
    pending = {
        "attestation_id": pending_id,
        "deployment_binding": {
            "submission_repair": repair_binding(manifest),
            "provider_binding_transition": None,
        },
    }

    def load_pending(*_args, **kwargs):
        assert kwargs["submission_repair_manifest"] == manifest_path
        return pending, "4" * 64, pending_snapshot

    monkeypatch.setattr(
        upgrade, "load_pending_attestation_for_activation", load_pending
    )

    def promoted(*_args, **_kwargs):
        evidence = copy.deepcopy(initial)
        evidence["deployment_binding"]["release_commit"] = manifest.candidate_commit
        evidence["activation_evidence"]["submission_repair"] = repair_binding(manifest)
        return evidence

    monkeypatch.setattr(upgrade, "build_active_attestation_from_pending", promoted)
    monkeypatch.setattr(
        manager,
        "_prove_predecessor_rollback_eligibility",
        lambda **_kwargs: {
            "contract": "administrateme-predecessor-rollback-eligibility-v1",
            "content_exposed": False,
        },
    )

    class Backup:
        def __init__(self, *_args, **_kwargs):
            pass

        def create(self, target):
            assert not target.exists()
            atomic_write(target, b"authenticated-encrypted-fixture-boundary")
            return {"path": str(target), "encrypted": True, "manifest_sha256": "6" * 64}

        def rehearse(self, backup, *, candidate, receipt):
            atomic_write(receipt, b'{"fixture":"authenticated-restore"}')
            return {
                "status": "healthy",
                "candidate": {"commit": manifest.candidate_commit},
                "artifact": {"sha256": hashlib.sha256(backup.read_bytes()).hexdigest()},
                "deployment_binding": pending_snapshot.public(),
                "receipt_sha256": "7" * 64,
                "tested_at": "2026-10-20T12:00:00Z",
            }

    monkeypatch.setattr(upgrade, "BackupManager", Backup)
    activity = paths.app_dir / "fixture-preparation-lock"
    monkeypatch.setattr(
        manager,
        "_consume_preparation_state",
        lambda *_a: os.open(activity, os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda *_a: None)
    monkeypatch.setattr(
        upgrade, "reconcile_administrateme_plugin", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(upgrade, "configure_required_llm_tool", lambda *_a, **_kw: None)
    monkeypatch.setattr(upgrade, "install_launch_agents", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        manager, "_run_release_doctor", lambda *_a, **_kw: _doctor(services)
    )
    return manager, candidate, pointer_path


@pytest.mark.parametrize(
    "outcome", ["success", "failed_retry", "success_then_later_failure"]
)
def test_forwarded_failure_containment_guarded_upgrade_and_retained_rollback(
    tmp_path, monkeypatch, capsys, outcome
):
    gateway = _gateway(monkeypatch)
    with maintenance_runtime(tmp_path, monkeypatch, allow_degraded=True) as fixture:
        controller, _activate, pause, services, settings, gmail, config = fixture
        client = services.client
        assert client is not None
        granted = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "maintenance.example.test"},
            json={
                "request_id": "operator-repair-context",
                "context_scope": "member_private",
                "permissions": ["read_context", "create_internal_work"],
            },
        )
        assert granted.status_code == 201, granted.text
        channel = next(
            row
            for row in settings.adapter_config["service_identities"]["channels"]
            if row["provider"] == "gmail"
        )
        assert channel["routes"] == [
            {"route_key": ROUTE_A, "scope": "member_private", "target_id": MEMBER_A}
        ]
        runtime = client.app.state.runtime
        selected = _service_message(
            runtime,
            key="operator-selected-forward",
            trusted_route_key=ROUTE_A,
            from_identifier="member-a@example.invalid",
            subject="Forward for repair",
            body="Preserved selected background.\nCall the school office.",
            event_at_ms=int(time.time() * 1000),
        )
        selected.source_adapter = "messaging-gmail-api:fixture-cos-gmail"
        source_id = client.portal.call(runtime.events.write, selected)
        route = _route_result(client, source_id)
        cue_id = route["payload"]["cue_event_id"]
        assert cue_id
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:interpretation", cue_id
        )
        source = client.portal.call(runtime.event_log.get, cue_id)
        assert source is not None
        features = client.portal.call(
            _events, client.app.state.runtime, "triage.intent.features_extracted"
        )
        failed = next(
            event
            for event in features
            if event["payload"]["source_event_id"] == source["event_id"]
        )
        assert failed["payload"]["assessment_outcome"] == "failed"
        assert gateway["calls"] == 1
        declared = SubmissionRepairManifest.model_validate(
            {
                "contract": "forwarded-submission-repair-v1",
                "predecessor_commit": "a" * 40,
                "candidate_commit": "b" * 40,
                "owner_member_id": MEMBER_A,
                "operator_principal_id": pause.operator_principal_id,
                "owner_authorization_sha256": pause.owner_authorization_sha256,
                "minimum_source_facts": 1,
                "targets": [
                    {
                        "source_event_id": source["event_id"],
                        "failed_assessment_event_id": failed["event_id"],
                        "member_id": MEMBER_A,
                        "expected_failure_reason": "language_seam_OpenClawTimeout",
                    }
                ],
            }
        )
        manifest_path = tmp_path / "private-repair.json"
        request_path = tmp_path / "private-containment.json"
        atomic_write(manifest_path, declared.model_dump_json().encode())
        atomic_write(request_path, pause.model_dump_json().encode())
        reference = secret_store(controller.paths, test_mode=True).put(
            "event-log", settings.encryption_key
        )
        atomic_write(
            config / "secret-references.json",
            json.dumps({"references": {"event_log": reference}}).encode(),
        )
        monkeypatch.setattr(
            "bootstrap.submission_repair.reviewed_candidate_commit",
            lambda _path: declared.candidate_commit,
        )
        monkeypatch.setattr(
            operation, "run_release_doctor", lambda *_a, **_kw: _doctor(services)
        )
        before = services.settle()
        try:
            validate_submission_repair_diagnostics(
                declared, before, observation_active=False
            )
        except SubmissionRepairError as exc:
            readiness = before["deployment_readiness"]
            exc.add_note(
                json.dumps(
                    {
                        "blocking_dimensions": readiness["blocking_dimensions"],
                        "dimension_states": {
                            name: {key: row.get(key) for key in ("state", "required")}
                            for name, row in readiness["dimensions"].items()
                        },
                        "capability_ids": sorted(before["capabilities"]),
                    },
                    sort_keys=True,
                )
            )
            raise
        policy_before = asyncio.run(_audit(controller))[0]
        enrollment_before = (config / "adapters.json").read_bytes()
        monkeypatch.setattr(
            operation, "CoSChannelActivator", lambda *_a, **_kw: controller
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cos-channel-activation",
                "--instance-root",
                str(controller.paths.root),
                "--request",
                str(request_path),
                "--enter-observation",
                "--submission-repair-manifest",
                str(manifest_path),
            ],
        )
        operation.main()
        assert json.loads(capsys.readouterr().out)["status"] == "contained_degraded"
        contained = services.settle()
        assert contained["observation_active"] is True and contained["ready"] is False
        assert (
            contained["capabilities"]["policy:source-assurance"]["details"]["counts"][
                "failed"
            ]
            == 1
        )
        assert (config / "adapters.json").read_bytes() == enrollment_before
        assert asyncio.run(_audit(controller))[0] == policy_before
        manager, candidate, pointer = _prepare_upgrade(
            controller, services, settings, declared, manifest_path, monkeypatch
        )
        # Real ServiceManager.start may return degraded while the selected
        # command is still executing; the updater owns bounded settlement.
        services.settle_on_start = False
        gateway["fail"] = outcome == "failed_retry"
        gateway["release"] = Event()
        real_offline = upgrade.offline_submission_repair

        def observe_pending(*args, **kwargs):
            proof = real_offline(*args, **kwargs)
            if (
                kwargs.get("allow_recovery_lineage")
                and proof["recovery_pending_count"] > 0
            ):
                gateway["pending_seen"] = True
                gateway["release"].set()
            return proof

        monkeypatch.setattr(upgrade, "offline_submission_repair", observe_pending)
        original_doctor = manager._post_switch_doctor

        def candidate_doctor(release, **kwargs):
            runtime = services.client.app.state.runtime
            commands = services.client.portal.call(
                _events, runtime, "triage.intent.recovery_requested"
            )
            assert len(commands) == 1
            services.client.portal.call(
                runtime.wait_for_subscriber,
                "member-submissions:recovery",
                commands[0]["event_id"],
            )
            if outcome != "success":
                return {
                    "status": "degraded",
                    "checks": [
                        {"name": "fixture_later_candidate_failure", "ok": False}
                    ],
                }
            return original_doctor(release, **kwargs)

        monkeypatch.setattr(manager, "_post_switch_doctor", candidate_doctor)
        if outcome == "success":
            result = manager.activate(candidate)
            assert result["status"] == "upgraded"
            assert services.settle()["ready"] is True
            assert (
                json.loads(pointer.read_bytes())["commit"] == declared.candidate_commit
            )
        else:
            with pytest.raises(UpgradeError, match="predecessor was recovered"):
                manager.activate(candidate)
            assert (
                json.loads(pointer.read_bytes())["commit"]
                == declared.predecessor_commit
            )
            histories = [
                json.loads(path.read_bytes())
                for path in manager.activation_journal.history.glob("*.json")
            ]
            assert histories[-1]["restored_readiness"] == "contained_degraded"
            proof = offline_submission_repair(
                controller.paths,
                declared,
                repository_root=ROOT,
                candidate_commit=declared.candidate_commit,
                allow_recovery_lineage=True,
                test_mode=True,
            )
            assert proof["status"] == "contained_degraded"
        assert gateway["calls"] == 2
        assert gateway["pending_seen"] is True
        runtime = services.client.app.state.runtime
        all_features = services.client.portal.call(
            _events, runtime, "triage.intent.features_extracted"
        )
        assert failed in all_features and len(all_features) == 2
        assert all_features[-1]["schema_version"] == 6
        assert all_features[-1]["payload"]["assessment_outcome"] == (
            "failed" if outcome == "failed_retry" else "eligible_candidates"
        )
        assert gmail.calls == 0
        assert asyncio.run(_audit(controller))[0] == policy_before
        assert services.runtime_diagnostics()["observation_active"] is True
        assert not manager.activation_journal.path.exists()
