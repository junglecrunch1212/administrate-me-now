"""An unchanged blocked baseline is recovery evidence, never deployment approval."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bootstrap.recovery_readiness import (
    BASELINE_CONTROLS,
    READINESS_CHECK,
    capture_baseline,
    full_doctor_matches_baseline,
    recovery_settlement_pending,
    runtime_matches_baseline,
    validate_baseline,
)
from bootstrap.service_settlement import (
    POST_SWITCH_CONSUMER_PROGRESS_SECONDS,
    wait_for_recovery_baseline,
)
from bootstrap.services import RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS, ServiceManager
from tests.unit.bootstrap.test_htr10_export import _expired_attestation_diagnostics


def _baseline():
    return capture_baseline(
        _expired_attestation_diagnostics(),
        readiness_sha256="a" * 64,
        host_fingerprint="b" * 64,
        instance_fingerprint="c" * 64,
        control_sha256={name: None for name in BASELINE_CONTROLS},
    )


def _doctor():
    return {
        "status": "failed",
        "checks": [
            {"name": READINESS_CHECK, "ok": False},
            {"name": "source_assurance_terminal_dispositions", "ok": True},
            {
                "name": "launchd_and_process_state",
                "ok": False,
                "evidence": {
                    "definitions": {
                        "com.administrateme.runtime": True,
                        "com.administrateme.openclaw": True,
                    },
                    "services": {"runtime": "degraded", "openclaw": "running"},
                },
            },
        ],
    }


def test_baseline_retains_blocked_truth_and_checks_every_other_doctor_result():
    baseline = _baseline()
    assert baseline is not None
    validate_baseline(baseline, "a" * 64)
    diagnostics = _expired_attestation_diagnostics()
    before = copy.deepcopy(diagnostics)
    assert runtime_matches_baseline(baseline, diagnostics)
    assert full_doctor_matches_baseline(_doctor())
    assert diagnostics == before
    assert diagnostics["ready"] is False


@pytest.mark.parametrize("change", ["new_gap", "effects", "unsafe_read", "pipeline"])
def test_recovery_never_accepts_a_new_runtime_degradation(change):
    baseline = _baseline()
    assert baseline is not None
    diagnostics = _expired_attestation_diagnostics()
    if change == "new_gap":
        baseline["blocking_dimensions"].remove("identity_acceptance")
    elif change == "effects":
        diagnostics["capabilities"]["policy:source-assurance"]["details"][
            "provider_effects"
        ]["observation_window"]["counts"]["provider_called_true"] = 1
    elif change == "unsafe_read":
        diagnostics["safe_reads"] = False
    else:
        diagnostics["capabilities"]["pipeline:fixture"]["state"] = "degraded"
    assert not runtime_matches_baseline(baseline, diagnostics)


@pytest.mark.parametrize("change", ["lag", "missing_lag", "refresh"])
def test_current_audit_cannot_accept_unsettled_recovery(change):
    baseline = _baseline()
    diagnostics = _expired_attestation_diagnostics()
    if change == "lag":
        diagnostics["capabilities"]["projection:fixture"]["details"]["lag_count"] = 1
    elif change == "missing_lag":
        del diagnostics["capabilities"]["projection:fixture"]["details"]["lag_count"]
    else:
        diagnostics["capabilities"]["policy:source-assurance"]["details"]["freshness"][
            "refresh_in_progress"
        ] = True
    assert not runtime_matches_baseline(baseline, diagnostics)


@pytest.mark.parametrize(
    "change",
    [
        "missing_consumers",
        "missing_pipeline",
        "kind",
        "details",
        "lag",
        "version",
        "provider_effect",
        "unknown_effect",
        "source_failure",
        "source_details",
        "refresh_failure",
        "new_gap",
    ],
)
def test_recovery_wait_refuses_structural_failure_without_waiting(change):
    baseline = _baseline()
    diagnostics = _expired_attestation_diagnostics()
    capabilities = diagnostics["capabilities"]
    source = capabilities["policy:source-assurance"]["details"]
    consumer = capabilities["projection:fixture"]
    if change == "missing_consumers":
        del capabilities["projection:fixture"]
        del capabilities["pipeline:fixture"]
    elif change == "missing_pipeline":
        del capabilities["pipeline:fixture"]
    elif change == "kind":
        consumer["kind"] = "not_a_consumer"
    elif change == "details":
        consumer["details"] = []
    elif change == "lag":
        consumer["details"]["lag_count"] = True
    elif change == "version":
        del consumer["details"]["version"]
    elif change == "provider_effect":
        source["provider_effects"]["observation_window"]["counts"][
            "provider_called_true"
        ] = 1
    elif change == "unknown_effect":
        source["provider_effects"]["counts"]["provider_called_unknown"] = 1
    elif change == "source_failure":
        source["counts"]["failed"] = 1
    elif change == "source_details":
        capabilities["policy:source-assurance"]["details"] = ["invalid"]
    elif change == "refresh_failure":
        source["freshness"]["refresh_error_type"] = "AuditFailure"
    else:
        diagnostics["deployment_readiness"]["blocking_dimensions"].append("privacy")
    assert not recovery_settlement_pending(baseline, diagnostics)


@pytest.fixture
def recovery_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        clock.now += seconds
        clock.sleeps.append(seconds)

    monkeypatch.setattr(
        "bootstrap.service_settlement.deployment_clock", lambda: clock.now
    )
    monkeypatch.setattr(
        "bootstrap.service_settlement.time", SimpleNamespace(sleep=sleep)
    )
    return clock


def _settlement_observation(*, lag=0, audit_current=True):
    diagnostics = _expired_attestation_diagnostics()
    readiness = diagnostics["deployment_readiness"]
    if audit_current:
        readiness["blocking_dimensions"].remove("observation_effects")
        readiness["dimensions"]["observation_effects"]["state"] = "healthy"
    source = diagnostics["capabilities"]["policy:source-assurance"]["details"]
    source["freshness"]["revision_current"] = audit_current
    source["freshness"]["refresh_in_progress"] = not audit_current
    diagnostics["capabilities"]["projection:fixture"]["details"]["lag_count"] = lag
    return diagnostics


def test_recovery_waits_for_checkpoint_then_fresh_audit_with_original_budget(
    recovery_clock,
):
    baseline = _baseline()
    baseline["blocking_dimensions"].remove("observation_effects")
    original = copy.deepcopy(baseline)
    observations = [
        _settlement_observation(lag=1),
        _settlement_observation(audit_current=False),
        _settlement_observation(),
    ]
    services = Mock(spec=ServiceManager)
    services.runtime_diagnostics.side_effect = observations
    selection = Mock()
    result = wait_for_recovery_baseline(
        services=services,
        baseline=baseline,
        absolute_deadline=1000,
        reserve_seconds=120,
        validate_selection=selection,
    )
    assert result == observations[-1]
    assert services.runtime_diagnostics.call_count == 3
    assert selection.call_count == 6
    assert recovery_clock.sleeps == [2, 2]
    assert baseline == original


@pytest.mark.parametrize("pending", ["consumer", "audit"])
def test_recovery_no_progress_cannot_wait_past_existing_watchdog(
    recovery_clock, pending
):
    services = Mock(spec=ServiceManager)
    services.runtime_diagnostics.return_value = _settlement_observation(
        lag=1 if pending == "consumer" else 0, audit_current=pending == "consumer"
    )
    assert (
        wait_for_recovery_baseline(
            services=services,
            baseline=_baseline(),
            absolute_deadline=2000,
            reserve_seconds=120,
            validate_selection=lambda: None,
        )
        is None
    )
    assert recovery_clock.now == POST_SWITCH_CONSUMER_PROGRESS_SECONDS


def test_recovery_budget_reserves_full_proof_and_rejects_late_success(recovery_clock):
    services = Mock(spec=ServiceManager)
    assert (
        wait_for_recovery_baseline(
            services=services,
            baseline=_baseline(),
            absolute_deadline=RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS + 119,
            reserve_seconds=120,
            validate_selection=lambda: None,
        )
        is None
    )
    services.runtime_diagnostics.assert_not_called()

    def late_observation():
        recovery_clock.now = 1000
        return _settlement_observation()

    services.runtime_diagnostics.side_effect = late_observation
    assert (
        wait_for_recovery_baseline(
            services=services,
            baseline=_baseline(),
            absolute_deadline=1000,
            reserve_seconds=0,
            validate_selection=lambda: None,
        )
        is None
    )
    assert not recovery_clock.sleeps


def test_recovery_wait_rechecks_selection_and_refuses_checkpoint_regression(
    recovery_clock,
):
    observations = [_settlement_observation(lag=1), _settlement_observation()]
    for row, checkpoint in zip(observations, ("event-b", "event-a"), strict=True):
        row["capabilities"]["projection:fixture"]["details"]["checkpoint_event_id"] = (
            checkpoint
        )
    services = Mock(spec=ServiceManager)
    services.runtime_diagnostics.side_effect = observations
    assert (
        wait_for_recovery_baseline(
            services=services,
            baseline=_baseline(),
            absolute_deadline=1000,
            reserve_seconds=0,
            validate_selection=lambda: None,
        )
        is None
    )
    services.runtime_diagnostics.side_effect = None
    services.runtime_diagnostics.return_value = _settlement_observation()
    with pytest.raises(RuntimeError, match="selection changed"):
        wait_for_recovery_baseline(
            services=services,
            baseline=_baseline(),
            absolute_deadline=1000,
            reserve_seconds=0,
            validate_selection=Mock(
                side_effect=[None, RuntimeError("selection changed")]
            ),
        )


@pytest.mark.parametrize(
    "change", ["source", "extra", "definition", "process", "missing", "duplicate"]
)
def test_full_doctor_cannot_hide_other_failures(change):
    doctor = _doctor()
    if change == "source":
        doctor["checks"][1]["ok"] = False
    elif change == "extra":
        doctor["checks"].append({"name": "privacy", "ok": False})
    elif change == "definition":
        doctor["checks"][2]["evidence"]["definitions"]["com.administrateme.runtime"] = (
            False
        )
    elif change == "process":
        doctor["checks"][2]["evidence"]["services"]["openclaw"] = "stopped"
    elif change == "missing":
        doctor["checks"].pop(1)
    else:
        doctor["checks"].append(copy.deepcopy(doctor["checks"][1]))
    assert not full_doctor_matches_baseline(doctor)


@pytest.mark.parametrize(
    "key,value",
    [
        ("readiness_sha256", "d" * 64),
        ("host_fingerprint", "bad"),
        ("blocking_dimensions", ["provider_bindings"]),
        ("blocking_dimensions", ["identity_acceptance", "identity_acceptance"]),
        ("unexpected", True),
    ],
)
def test_journal_baseline_is_exact_and_bound_to_original_readiness(key, value):
    baseline = _baseline()
    assert baseline is not None
    baseline[key] = value
    with pytest.raises(ValueError):
        validate_baseline(baseline, "a" * 64)


def test_explicit_rollback_reports_preserved_readiness_gaps(tmp_path, monkeypatch):
    import base64
    import json
    from types import SimpleNamespace
    from pathlib import Path

    from bootstrap.activation_transaction import ActivationJournal
    from bootstrap.contracts import atomic_write
    from bootstrap.upgrade import UpgradeManager
    from tests.unit.bootstrap.intake_fixture import stub_uninitialized_intake
    from tests.unit.bootstrap.test_activation_transaction import (
        _paths,
        _journal_value,
        _commit_activation,
    )

    paths = _paths(tmp_path)
    stub_uninitialized_intake(monkeypatch, paths)
    value = _journal_value(paths)
    baseline = _baseline()
    baseline["readiness_sha256"] = value["initial_readiness_sha256"]
    manager = UpgradeManager(
        paths, root=Path(__file__).resolve().parents[3], test_mode=True
    )
    baseline["control_sha256"] = manager._recovery_control_fingerprints()
    value["initial_readiness_baseline"] = baseline
    owner = ActivationJournal(paths)
    owner.finish(_commit_activation(owner, owner.create(value)))
    current = {
        "commit": value["candidate_commit"],
        "path": value["candidate_path"],
        "previous": value["initial_pointer"],
    }
    atomic_write(paths.app_dir / "current-release.json", json.dumps(current).encode())
    readiness_path = paths.instance_dir / "config/deployment-readiness.json"
    atomic_write(
        readiness_path, b'{"contract":"deployment-readiness-v1","sentinel":"current"}'
    )
    started = [False]
    manager = UpgradeManager(
        paths, root=Path(__file__).resolve().parents[3], test_mode=True
    )
    manager.services = SimpleNamespace(
        status=lambda: SimpleNamespace(status="healthy"),
        runtime_diagnostics=lambda: (
            _expired_attestation_diagnostics() if started[0] else {"ready": True}
        ),
        stop=lambda: None,
        start=lambda: started.__setitem__(0, True),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(
            host_fingerprint="b" * 64, instance_fingerprint="c" * 64
        ),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.validate_candidate",
        lambda *_args, **_kwargs: {"compatible": True},
    )
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda _release: None)
    for name in (
        "reconcile_administrateme_plugin",
        "install_launch_agents",
        "configure_required_llm_tool",
    ):
        monkeypatch.setattr("bootstrap.upgrade." + name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "_post_switch_doctor", lambda _release: _doctor())
    monkeypatch.setattr(
        manager, "_run_release_doctor", lambda _release, **kwargs: _doctor()
    )

    def refuse_refresh(*_args, **_kwargs):
        pytest.fail("old recovery evidence must not be refreshed")

    monkeypatch.setattr(manager, "_write_refreshed_runtime_binding", refuse_refresh)
    result = manager.rollback()
    assert result["status"] == "rolled_back"
    assert result["restored_readiness"] == "preexisting_attestation_gaps"
    assert readiness_path.read_bytes() == base64.b64decode(
        value["initial_readiness_bytes"]
    )
    assert (
        json.loads((paths.app_dir / "current-release.json").read_bytes())
        == value["initial_pointer"]
    )
    assert not owner.path.exists()
    assert (
        json.loads(Path(result["activation_history"]).read_bytes())[
            "restored_readiness"
        ]
        == "preexisting_attestation_gaps"
    )
