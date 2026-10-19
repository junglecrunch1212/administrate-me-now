"""Journaled recovery restarts production composition without refreshing old proof.

The OS selectors, host identity and full host Doctor transport are fixture
boundaries. Runtime startup/shutdown, encrypted truth, source/effect diagnostics,
readiness evaluation and the recovery controller are real.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from bootstrap.activation_transaction import ActivationJournal
from bootstrap.contracts import atomic_write
from bootstrap.secrets import secret_store
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.integration.test_runtime_application import (
    REPO_ROOT,
    _report_strict_openclaw,
    _strict_settings,
)
from tests.unit.bootstrap.test_activation_transaction import _paths, _journal_value
from tests.unit.bootstrap.test_recovery_readiness import _doctor


@pytest.mark.parametrize("new_doctor_failure", [False, True])
def test_interrupted_recovery_preserves_expired_evidence_and_restarts_real_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new_doctor_failure: bool
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = _paths(tmp_path)
    settings = replace(
        _strict_settings(paths.root),
        openclaw_model_configured=True,
        deterministic_member_chat_fixture=True,
    )
    paths = replace(paths, instance_dir=settings.instance.instance_dir)
    stale = (datetime.now(timezone.utc) - timedelta(days=32)).isoformat()
    origin = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    evidence = settings.deployment_evidence
    evidence["provider_effect_window"]["started_at"] = origin
    for key in ("identity_acceptance", "recovery", "backup_rollback"):
        evidence[key]["tested_at"] = stale
    # The old readiness binding predates this host/instance observation.
    settings.host_topology["deployment_binding"] = {
        **settings.host_topology["deployment_binding"],
        "host_fingerprint": "e" * 64,
        "instance_fingerprint": "f" * 64,
    }
    config = paths.instance_dir / "config"
    config.mkdir(mode=0o700, exist_ok=True)
    reference = secret_store(paths, test_mode=True).put(
        "event-log", settings.encryption_key
    )
    atomic_write(
        config / "instance.yaml",
        f"tenant_id: {settings.instance.tenant_id}\ntimezone: Etc/UTC\n".encode(),
    )
    for name, value in (
        ("adapters.json", settings.adapter_config),
        ("settings-control.json", {"revision": 0, "schema_version": 2}),
        ("deployment-readiness.json", evidence),
        ("secret-references.json", {"references": {"event_log": reference}}),
    ):
        atomic_write(config / name, json.dumps(value).encode())
    original_readiness = (config / "deployment-readiness.json").read_bytes()
    journal_input = _journal_value(paths)
    journal_input.update(
        {
            "initial_readiness": json.loads(original_readiness),
            "initial_readiness_bytes": base64.b64encode(original_readiness).decode(),
            "initial_readiness_sha256": hashlib.sha256(original_readiness).hexdigest(),
        }
    )
    pointer = paths.app_dir / "current-release.json"
    atomic_write(pointer, base64.b64decode(journal_input["initial_pointer_bytes"]))

    class Services:
        client = None
        starts = 0
        stops = 0

        def start(self):
            self.client = TestClient(create_application(settings))
            self.client.__enter__()
            self.starts += 1
            _report_strict_openclaw(self.client)
            for _ in range(200):
                diagnostics = self.runtime_diagnostics()
                freshness = diagnostics["capabilities"]["policy:source-assurance"][
                    "details"
                ]["freshness"]
                if (
                    freshness["revision_current"]
                    and not freshness["refresh_in_progress"]
                ):
                    break
                self.client.portal.call(asyncio.sleep, 0.01)
            assert freshness["revision_current"]
            return self.status()

        def stop(self):
            if self.client is not None:
                self.client.__exit__(None, None, None)
                self.client = None
                self.stops += 1

        def status(self):
            return SimpleNamespace(
                status="degraded" if self.client else "stopped",
                runtime="degraded",
                openclaw="running",
            )

        def runtime_diagnostics(self):
            assert self.client is not None
            return self.client.get("/api/system/ready").json()

    services = Services()
    services.start()
    settings_payload = (config / "settings-control.json").read_bytes()
    journal_input.update(
        {
            "initial_settings_control": json.loads(settings_payload),
            "initial_settings_control_bytes": base64.b64encode(
                settings_payload
            ).decode(),
            "initial_settings_control_sha256": hashlib.sha256(
                settings_payload
            ).hexdigest(),
        }
    )
    manager = UpgradeManager(paths, root=REPO_ROOT, test_mode=True)
    manager.services = services
    monkeypatch.setattr(
        "bootstrap.upgrade.capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(
            host_fingerprint="e" * 64, instance_fingerprint="f" * 64
        ),
    )
    try:
        diagnostics = services.runtime_diagnostics()
        assert diagnostics["ready"] is False
        assert diagnostics["service_ready"] is True
        baseline = manager._capture_recovery_readiness_baseline(original_readiness)
        assert baseline is not None
        journal_input["initial_readiness_baseline"] = baseline
        runtime = services.client.app.state.runtime
        count_before = services.client.portal.call(runtime.event_log.count)
        owner = ActivationJournal(paths)
        owner.create(journal_input)
        # Simulate interruption after candidate control selection. The durable
        # journal, not live mutable candidate controls, owns recovery authority.
        atomic_write(config / "deployment-readiness.json", b'{"candidate":true}')
        monkeypatch.setattr(
            manager, "_install_reviewed_openclaw", lambda _release: None
        )
        monkeypatch.setattr(
            "bootstrap.upgrade.reconcile_administrateme_plugin",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "bootstrap.upgrade.install_launch_agents", lambda *_args, **_kwargs: None
        )

        def refuse_refresh(*_args, **_kwargs):
            pytest.fail("recovery must not refresh or re-attest expired owner evidence")

        monkeypatch.setattr(manager, "_write_refreshed_runtime_binding", refuse_refresh)
        doctor = _doctor()
        monkeypatch.setattr(
            manager, "_post_switch_doctor", lambda _release: copy.deepcopy(doctor)
        )
        full_calls = []

        def full_doctor(_release, *, include_synthetic_proof, timeout_seconds):
            assert include_synthetic_proof is True
            assert timeout_seconds > 0
            full_calls.append(True)
            result = copy.deepcopy(doctor)
            if new_doctor_failure:
                result["checks"].append({"name": "encrypted_storage", "ok": False})
            return result

        monkeypatch.setattr(manager, "_run_release_doctor", full_doctor)
        if new_doctor_failure:
            with pytest.raises(UpgradeError, match="did not become healthy"):
                manager._recover_interrupted_transaction()
            assert owner.load()["phase"] == "recovering"
        else:
            result = manager._recover_interrupted_transaction()
            assert result["status"] == "recovered"
            assert result["restored_readiness"] == "preexisting_attestation_gaps"
            assert not owner.path.exists()
            saved = json.loads(Path(result["history"]).read_text())
            assert saved["restored_readiness"] == "preexisting_attestation_gaps"
        assert full_calls == [True]
        assert services.starts == 2 and services.stops == 1
        assert (config / "deployment-readiness.json").read_bytes() == original_readiness
        final = services.runtime_diagnostics()
        assert final["ready"] is False
        assert final["safe_reads"] is True
        assert final["write_admission"] == "blocked"
        assert set(final["deployment_readiness"]["blocking_dimensions"]) == set(
            baseline["blocking_dimensions"]
        )
        runtime = services.client.app.state.runtime
        assert services.client.portal.call(runtime.event_log.count) == count_before
        window = final["capabilities"]["policy:source-assurance"]["details"][
            "provider_effects"
        ]["observation_window"]
        assert window["started_at"] == origin
        assert window["counts"]["provider_called_true"] == 0
    finally:
        services.stop()
