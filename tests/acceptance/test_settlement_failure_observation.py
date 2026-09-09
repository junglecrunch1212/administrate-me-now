"""Actual source failure is retained separately from an older Doctor snapshot."""

from __future__ import annotations

import asyncio
import json
import stat
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import OpenClawUnreachable
from adminme.openclaw_topology import binding_fingerprints, member_agent_id
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.simulations.source_pipeline_stress import (
    _wait_for_composition_quiescence,
)
from bootstrap import doctor as doctor_module
from bootstrap import service_settlement, upgrade
from bootstrap.contracts import DeploymentPaths
from bootstrap.services import ServiceManager
from bootstrap.upgrade import UpgradeManager
from bootstrap.wizard import BootstrapWizard
from tests.unit.bootstrap.test_activation_transaction import _journal_value


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-settlement-owner"  # fixture:tenant_data:ok
PRIVATE = "private-settlement-source-and-exception-fixture"
HEALTH_SECRET = "fixture-settlement-health-secret-at-least-24-characters"


def _task(settings: RuntimeSettings, key: str) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=settings.instance.tenant_id,
        type="task.created",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="fixture-settlement",
        source_account_id="fixture-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        idempotency_key=f"fixture-settlement:{key}",
        payload={"task_id": key, "title": PRIVATE, "owner_member_id": MEMBER},
    )


def _current_readiness(client: TestClient, *, failed: int) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = client.get("/api/system/ready").json()
        source = result["capabilities"]["policy:source-assurance"]["details"]
        if (
            source["freshness"]["revision_current"]
            and not source["freshness"]["refresh_in_progress"]
            and source["counts"]["failed"] == failed
            and source["counts"]["pending"] == 0
        ):
            return result
        time.sleep(0.01)
    raise AssertionError("fixture source assurance did not settle")


def test_actual_failed_assessment_persists_trigger_and_retains_prior_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "controller")
    wizard = BootstrapWizard(
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
    )
    wizard.answers["bridges"]["enabled_members"] = []
    wizard.run()
    monkeypatch.setattr(
        UpgradeManager, "_attest_controller_provenance", lambda *_args: None
    )
    monkeypatch.setattr(
        upgrade,
        "reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: {"ok": True},
    )
    UpgradeManager(paths, root=REPO_ROOT, test_mode=True).activate(REPO_ROOT)
    release = Path(
        json.loads((paths.app_dir / "current-release.json").read_text("utf-8"))["path"]
    )
    fixture_services = ServiceManager(paths, test_mode=True)
    fixture_services.start()
    settings = RuntimeSettings.credential_free(
        tmp_path / "runtime",
        encryption_key=b"s" * 32,
        repository_root=REPO_ROOT,
        member_identities={"settlement-owner.ts.net": MEMBER},  # fixture:tenant_data:ok
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        openclaw_enabled=True,
        openclaw_shared_secret=HEALTH_SECRET,
        openclaw_actor_identities={
            "portal|fixture-settlement-account|fixture-settlement-actor": MEMBER,
        },
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert client.portal is not None
        assert runtime.events is not None
        assert runtime.pipelines is not None
        # An intentionally absent Gateway is a separate structural failure.
        # Prove the synthetic integration through its real authenticated route
        # so only the selected source assessment fails during settlement.
        reported = client.post(
            "/api/openclaw/v1/health/report",
            headers={"Authorization": f"Bearer {HEALTH_SECRET}"},
            json={
                "state": "healthy",
                "reason": "fixture integration heartbeat",
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
                "dm_scope": "per_account_channel_peer",
                "member_agent_ids": [member_agent_id(MEMBER)],
                "binding_fingerprints": list(
                    binding_fingerprints(
                        settings.openclaw_actor_identities,
                        settings.member_chat_enabled_members,
                    )
                ),
                "unique_agents": True,
                "unique_accounts": True,
            },
        )
        assert reported.status_code == 200, reported.text
        client.portal.call(runtime.events.write, _task(settings, "initial"))
        client.portal.call(_wait_for_composition_quiescence, runtime, 30)
        _current_readiness(client, failed=0)
        release_refresh = asyncio.Event()
        original_refresh = runtime._run_source_assurance_refresh

        async def held_refresh(*args: Any, **kwargs: Any) -> None:
            await release_refresh.wait()
            await original_refresh(*args, **kwargs)

        monkeypatch.setattr(runtime, "_run_source_assurance_refresh", held_refresh)
        client.portal.call(runtime.events.write, _task(settings, "changed-revision"))
        client.portal.call(_wait_for_composition_quiescence, runtime, 30)
        prior_readiness = client.get("/api/system/ready").json()
        prior_source = prior_readiness["capabilities"]["policy:source-assurance"][
            "details"
        ]
        assert prior_source["counts"]["failed"] == 0
        assert prior_source["freshness"]["revision_current"] is False
        assert prior_source["freshness"]["refresh_in_progress"] is True
        assert not service_settlement.has_new_structural_degradation(prior_readiness)

        # Doctor executes its real source-assurance predicate. Only its host
        # services use the disposable fixture; readiness comes from the actual
        # application, including its asynchronous fixed-watermark audit.
        with monkeypatch.context() as doctor_boundary:
            doctor_boundary.setattr(
                fixture_services, "runtime_diagnostics", lambda: prior_readiness
            )
            doctor_boundary.setattr(
                doctor_module,
                "ServiceManager",
                lambda *_args, **_kwargs: fixture_services,
            )
            prior_doctor = doctor_module.Doctor(
                paths, root=release, test_mode=True
            ).run(include_synthetic_proof=False)
        assert service_settlement.failed_check_names(prior_doctor) == {
            "source_assurance_terminal_dispositions"
        }
        prior_bytes = json.dumps(prior_doctor, sort_keys=True)
        manager = UpgradeManager(paths, root=REPO_ROOT, test_mode=True)
        journal_value = _journal_value(paths)
        for name in ("candidate_commit", "target_commit"):
            journal_value[name] = release.name
        for name in ("candidate_path", "target_path"):
            journal_value[name] = str(release)
        journal = manager.activation_journal.create(journal_value)
        journal_bytes = manager.activation_journal.path.read_bytes()
        original_wait = service_settlement.wait_for_settled_doctor

        def production_wait(**kwargs: Any) -> dict[str, Any]:
            kwargs["test_mode"] = False
            return original_wait(**kwargs)

        monkeypatch.setattr(upgrade, "wait_for_settled_doctor", production_wait)
        doctor_calls = []

        def retained_doctor(selected: Path, *, include_synthetic_proof: bool):
            assert selected == release
            doctor_calls.append(include_synthetic_proof)
            return prior_doctor

        monkeypatch.setattr(manager, "_run_release_doctor", retained_doctor)
        attempts = 0
        source_ids = []

        async def failed_skill(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal attempts
            attempts += 1
            raise OpenClawUnreachable(PRIVATE)

        monkeypatch.setattr(runtime.pipelines, "_run_skill_fn", failed_skill)

        async def fail_source() -> str:
            now = EventEnvelope.now_utc_iso()
            return await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=settings.instance.tenant_id,
                    type="messaging.received",
                    schema_version=3,
                    occurred_at=now,
                    source_adapter="fixture-settlement",
                    source_account_id="fixture-account",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    idempotency_key="fixture-settlement:failed-message",
                    payload={
                        "member_id": MEMBER,
                        "provider": "gmail",
                        "provider_account_id": "fixture-account",
                        "source_channel": "gmail",
                        "native_message_id": "fixture-failed-message",
                        "thread_id": "fixture-failed-thread",
                        "from_identifier": "sender@example.invalid",
                        "to_identifier": "member@example.invalid",
                        "workspace": "personal",
                        "subject": "Fixture status",
                        "body_text": PRIVATE + ". No action required.",
                        "received_at": now,
                        "attachments": [],
                    },
                )
            )

        def actual_failed_diagnostics() -> dict[str, Any]:
            assert not source_ids
            source_ids.append(client.portal.call(fail_source))
            client.portal.call(_wait_for_composition_quiescence, runtime, 30)
            client.portal.call(release_refresh.set)
            diagnostics = _current_readiness(client, failed=1)
            assert not service_settlement.has_new_structural_degradation(diagnostics)
            return diagnostics

        monkeypatch.setattr(
            manager.services, "runtime_diagnostics", actual_failed_diagnostics
        )
        try:
            result = manager._post_switch_doctor(release)
        finally:
            client.portal.call(release_refresh.set)
        assert result["status"] == "failed"
        assert doctor_calls == [False]
        assert attempts == 3
        assert json.dumps(prior_doctor, sort_keys=True) == prior_bytes
        assert manager.activation_journal.path.read_bytes() == journal_bytes
        artifact = (
            manager.activation_journal.directory
            / "failures"
            / (f"{journal['transaction_id']}-candidate_doctor.json")
        )
        document = json.loads(artifact.read_text("utf-8"))
        failure = document["failure"]
        assert failure["readiness_origin"] == "retained_doctor"
        assert failure["readiness"]["source_counts"]["failed"] == 0
        [bounded] = failure["bounded"]
        assert bounded["reason"] == "source_assurance_failed"
        trigger = bounded["triggering_readiness"]
        assert trigger["origin"] == "runtime_diagnostics"
        assert trigger["source_counts"]["failed"] == 1
        assert trigger["source_counts"]["pending"] == 0
        assert trigger["source_freshness"]["revision_current"] is True
        assert PRIVATE not in artifact.read_text("utf-8")
        assert source_ids[0] not in artifact.read_text("utf-8")
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
        assert "trigger_source_failed=1" in manager._settlement_failure_summary(result)
        assert runtime.fixture_adapter.delivered == []
