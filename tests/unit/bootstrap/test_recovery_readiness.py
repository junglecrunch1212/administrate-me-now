"""An unchanged blocked baseline is recovery evidence, never deployment approval."""

from __future__ import annotations

import copy

import pytest

from bootstrap.recovery_readiness import (
    BASELINE_CONTROLS,
    READINESS_CHECK,
    capture_baseline,
    full_doctor_matches_baseline,
    runtime_matches_baseline,
    validate_baseline,
)
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
