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
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adminme.events.bus import EventBus
from adminme.runtime.application import AdministrateMeRuntime, create_application
from adminme.settings_control import SettingsControlStore
from bootstrap.activation_transaction import ActivationJournal
from bootstrap.contracts import atomic_write
from bootstrap.secrets import secret_store
from bootstrap.release_history import retained_predecessor
from bootstrap.recovery_readiness import recovery_consumers_settled
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.integration.test_runtime_application import (
    REPO_ROOT,
    _report_strict_openclaw,
    _strict_settings,
)
from tests.unit.bootstrap.test_activation_transaction import _paths, _journal_value
from tests.unit.bootstrap.test_recovery_readiness import _doctor


@pytest.mark.parametrize("new_doctor_failure", [False, True])
@pytest.mark.parametrize("pointer_depth", [1, 64])
def test_interrupted_recovery_preserves_expired_evidence_and_restarts_real_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    new_doctor_failure: bool,
    pointer_depth: int,
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
        (
            "settings-control.json",
            SettingsControlStore(config / "settings-control.json").read(),
        ),
        ("deployment-readiness.json", evidence),
        ("secret-references.json", {"references": {"event_log": reference}}),
    ):
        atomic_write(config / name, json.dumps(value).encode())
    original_readiness = (config / "deployment-readiness.json").read_bytes()
    journal_input = _journal_value(paths)
    tail = None
    for index in range(pointer_depth - 1):
        commit = f"{index + 1:040x}"
        release = paths.app_dir / "releases" / commit
        release.mkdir(mode=0o700)
        tail = {"commit": commit, "path": str(release), "previous": tail}
    if tail is not None:
        journal_input["initial_pointer"]["previous"] = tail
    original_pointer = json.dumps(journal_input["initial_pointer"], indent=2).encode()
    journal_input["initial_pointer_bytes"] = base64.b64encode(original_pointer).decode()
    journal_input["initial_pointer_sha256"] = hashlib.sha256(
        original_pointer
    ).hexdigest()
    journal_input.update(
        {
            "initial_readiness": json.loads(original_readiness),
            "initial_readiness_bytes": base64.b64encode(original_readiness).decode(),
            "initial_readiness_sha256": hashlib.sha256(original_readiness).hexdigest(),
        }
    )
    pointer = paths.app_dir / "current-release.json"
    atomic_write(pointer, base64.b64decode(journal_input["initial_pointer_bytes"]))

    # Hold an actual all-events projection after the restart fact is appended.
    # Its earlier checkpoint can have a current zero-effect audit despite lag.
    # Releasing the durable consumer must invalidate that audit before recovery
    # can accept the final checkpoint and its independently refreshed proof.
    hold_recovery_consumer = pointer_depth == 64 and not new_doctor_failure
    hold_requested = threading.Event()
    consumer_waiting = threading.Event()
    release_consumer = threading.Event()
    audit_waiting = threading.Event()
    release_audit = threading.Event()
    barrier_expired = threading.Event()
    original_deliver = EventBus._deliver_batch
    original_refresh = AdministrateMeRuntime._run_source_assurance_refresh

    async def wait_for_barrier(release):
        async def wait():
            while not release.is_set():
                await asyncio.sleep(0.001)

        try:
            await asyncio.wait_for(wait(), timeout=10)
        except TimeoutError:
            # Never leave the production runtime stuck if an assertion fails.
            barrier_expired.set()

    async def delayed_delivery(bus, state, events):
        if (
            hold_requested.is_set()
            and state.subscriber_id == "projection:context_fabric"
            and any(event["type"] == "runtime.started" for event in events)
        ):
            consumer_waiting.set()
            await wait_for_barrier(release_consumer)
        return await original_deliver(bus, state, events)

    async def delayed_refresh(runtime, capabilities, revision, *, initial=False):
        if (
            not initial
            and hold_requested.is_set()
            and release_consumer.is_set()
            and capabilities["projection:context_fabric"]["details"]["lag_count"] == 0
        ):
            audit_waiting.set()
            await wait_for_barrier(release_audit)
        return await original_refresh(runtime, capabilities, revision, initial=initial)

    monkeypatch.setattr(EventBus, "_deliver_batch", delayed_delivery)
    monkeypatch.setattr(
        AdministrateMeRuntime, "_run_source_assurance_refresh", delayed_refresh
    )

    class Services:
        client = None
        starts = 0
        stops = 0
        starting = False
        recovery_observations = None

        def start(self):
            self.starting = True
            if self.starts == 1 and hold_recovery_consumer:
                hold_requested.set()
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
                    and (
                        hold_requested.is_set()
                        or recovery_consumers_settled(diagnostics)
                    )
                ):
                    break
                self.client.portal.call(asyncio.sleep, 0.01)
            assert freshness["revision_current"]
            if hold_requested.is_set():
                assert consumer_waiting.is_set()
                assert not recovery_consumers_settled(diagnostics)
                self.recovery_observations = []
            else:
                assert recovery_consumers_settled(diagnostics)
            self.starting = False
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
            diagnostics = self.client.get("/api/system/ready").json()
            if not self.starting and self.recovery_observations is not None:
                self.recovery_observations.append(diagnostics)
                freshness = diagnostics["capabilities"]["policy:source-assurance"][
                    "details"
                ]["freshness"]
                if not release_consumer.is_set():
                    assert not recovery_consumers_settled(diagnostics)
                    assert freshness["revision_current"]
                    release_consumer.set()
                elif (
                    recovery_consumers_settled(diagnostics)
                    and not freshness["revision_current"]
                ):
                    # The actual refresh cannot complete before this invalidated
                    # audit has been observed, independent of worker scheduling.
                    release_audit.set()
            return diagnostics

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

        async def read_events(log):
            return [event async for event in log.read_since()]

        events_before = services.client.portal.call(read_events, runtime.event_log)
        owner = ActivationJournal(paths)
        owner.create(journal_input)
        # Simulate interruption after candidate control selection. The durable
        # journal, not live mutable candidate controls, owns recovery authority.
        atomic_write(
            pointer,
            json.dumps(
                {
                    "path": journal_input["candidate_path"],
                    "commit": journal_input["candidate_commit"],
                    "previous": retained_predecessor(journal_input["initial_pointer"]),
                }
            ).encode(),
        )
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
            if hold_recovery_consumer:
                assert release_consumer.is_set()
                assert release_audit.is_set()
                assert recovery_consumers_settled(services.recovery_observations[-1])
                assert services.recovery_observations[-1]["capabilities"][
                    "policy:source-assurance"
                ]["details"]["freshness"]["revision_current"]
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
        if hold_recovery_consumer:
            assert audit_waiting.is_set()
            assert not barrier_expired.is_set()
            observations = services.recovery_observations
            assert not recovery_consumers_settled(observations[0])
            assert any(
                not row["capabilities"]["policy:source-assurance"]["details"][
                    "freshness"
                ]["revision_current"]
                and "observation_effects"
                in row["deployment_readiness"]["blocking_dimensions"]
                for row in observations[1:]
            )
        assert services.starts == 2 and services.stops == 1
        assert pointer.read_bytes() == original_pointer
        assert (config / "deployment-readiness.json").read_bytes() == original_readiness
        final = services.runtime_diagnostics()
        assert final["ready"] is False
        assert final["safe_reads"] is True
        assert final["write_admission"] == "blocked"
        assert set(final["deployment_readiness"]["blocking_dimensions"]) == set(
            baseline["blocking_dimensions"]
        )
        runtime = services.client.app.state.runtime
        events_after = services.client.portal.call(read_events, runtime.event_log)
        assert events_after[: len(events_before)] == events_before
        # A real restart records its startup and fresh Gateway health report.
        # Every original fact is preserved and no domain/provider fact is added.
        assert [event["type"] for event in events_after[len(events_before) :]] == [
            "runtime.started",
            "openclaw.integration.state_changed",
        ]
        window = final["capabilities"]["policy:source-assurance"]["details"][
            "provider_effects"
        ]["observation_window"]
        assert window["started_at"] == origin
        assert window["counts"]["provider_called_true"] == 0
    finally:
        release_consumer.set()
        release_audit.set()
        services.stop()
