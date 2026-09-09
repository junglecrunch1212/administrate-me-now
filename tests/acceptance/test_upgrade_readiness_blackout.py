"""A transient Doctor blackout recovers through the selected-release proof."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bootstrap import doctor as doctor_module
from bootstrap import service_settlement, upgrade
from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.services import ServiceManager
from bootstrap.upgrade import UpgradeManager
from bootstrap.wizard import BootstrapWizard
from tests.unit.bootstrap.test_settlement_readiness_blackout import (
    fresh_readiness,
    present_report,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _actual_blackout_report(paths, release, monkeypatch):
    """Exercise Doctor's production absence handling, with fixture host tools."""
    versions = load_versions(release)
    doctor = doctor_module.Doctor(paths, root=release, test_mode=True)
    # Keep the actual disposable encrypted secret store. Host command and
    # provider boundaries below retain their existing fixture implementations;
    # Doctor itself must not take its test-only deployment-readiness exemption.
    doctor.test_mode = False
    boundary = ServiceManager(paths, test_mode=False)
    fixture_services = ServiceManager(paths, test_mode=True)
    assert fixture_services.status().status == "healthy"

    def commands(command):
        return {
            ("node", "-p", "process.versions.node"): versions["node"]["preferred"],
            ("npm", "--version"): "11.17.0",
            ("brew", "--version"): "Homebrew 6.0.0",
            ("openclaw", "--version"): versions["openclaw"]["version"],
        }[tuple(command)]

    with monkeypatch.context() as fixture:
        fixture.setattr(
            doctor_module,
            "platform",
            SimpleNamespace(
                system=lambda: "Darwin",
                machine=lambda: "arm64",
                python_version=lambda: versions["python"]["preferred"],
            ),
        )
        fixture.setattr(doctor_module, "_command_output", commands)
        for name in (
            "reviewed_source_status",
            "administrateme_plugin_status",
            "required_llm_tool_status",
            "probe_required_llm_tool",
            "OpenClawCronReconciler",
        ):
            original = getattr(doctor_module, name)

            def fixture_tool(*args, _original=original, **kwargs):
                kwargs["test_mode"] = True
                return _original(*args, **kwargs)

            fixture.setattr(doctor_module, name, fixture_tool)
        fixture.setattr(
            boundary,
            "_launchctl_loaded",
            lambda label: fixture_services._read_test_state().get(label) == "running",
        )
        fixture.setattr(boundary, "_managed_openclaw_liveness", lambda **_: True)
        fixture.setattr(boundary, "runtime_diagnostics", lambda: None)
        fixture.setattr(
            doctor_module, "ServiceManager", lambda *_args, **_kwargs: boundary
        )
        result = doctor.run(include_synthetic_proof=False)

    failed = {check["name"] for check in result["checks"] if check["ok"] is False}
    assert failed == {
        "launchd_and_process_state",
        "source_assurance_terminal_dispositions",
        "deployment_topology_and_recovery_readiness",
    }
    assert service_settlement._doctor_has_absent_readiness(result)
    assert fixture_services.status().status == "healthy"
    return result


def test_initial_and_retry_blackout_require_complete_selected_release_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-blackout")
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
    # This fixture explicitly declines optional bridge enrollment; all required
    # checks can therefore run through Doctor's production branch unchanged.
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
    pointer = paths.app_dir / "current-release.json"
    selected_bytes = pointer.read_bytes()
    release = Path(json.loads(selected_bytes)["path"])
    services = ServiceManager(paths, test_mode=True)
    services.start()
    blackout = _actual_blackout_report(paths, release, monkeypatch)
    manager = UpgradeManager(paths, root=REPO_ROOT, test_mode=True)
    clock = SimpleNamespace(now=1000.0)

    def sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(service_settlement, "deployment_clock", lambda: clock.now)
    monkeypatch.setattr(upgrade, "deployment_clock", lambda: clock.now)
    monkeypatch.setattr(service_settlement, "time", SimpleNamespace(sleep=sleep))
    manager._begin_deployment_budget()
    manager._begin_recovery_settlement()
    original_deadline = manager._settlement_deadline
    original_wait = service_settlement.wait_for_settled_doctor

    def production_wait(**kwargs):
        # External boundaries use the encrypted disposable deployment fixture;
        # the actual manager and shared settlement loop enforce all proof gates.
        kwargs["test_mode"] = False
        kwargs["validate_selection"] = validate_selection
        return original_wait(**kwargs)

    def validate_selection():
        assert pointer.read_bytes() == selected_bytes

    monkeypatch.setattr(upgrade, "wait_for_settled_doctor", production_wait)
    original_doctor = manager._run_release_doctor
    reports = [blackout, present_report(pending=True), blackout]
    calls = []
    actual = []

    def doctor(selected, *, include_synthetic_proof, **kwargs):
        assert selected == release
        calls.append(include_synthetic_proof)
        if reports:
            assert include_synthetic_proof is False
            return reports.pop(0)
        result = original_doctor(
            selected, include_synthetic_proof=include_synthetic_proof, **kwargs
        )
        actual.append(result)
        return result

    observations = iter([fresh_readiness(), None, fresh_readiness()])
    monkeypatch.setattr(manager, "_run_release_doctor", doctor)
    monkeypatch.setattr(
        manager.services, "runtime_diagnostics", lambda: next(observations)
    )
    monkeypatch.setattr(manager.services, "runtime_is_live", lambda: True)
    result = manager._post_switch_doctor(release)

    assert result["status"] == "healthy"
    assert calls == [False, False, False, False, True]
    assert len(actual) == 2
    canonical = next(
        check
        for check in result["checks"]
        if check["name"] == "canonical_synthetic_round_trip"
    )
    assert canonical["ok"] is True
    assert canonical["evidence"]["external_side_effects"] == 0
    assert canonical["evidence"]["review_suppressed_by_observation"] is True
    assert manager._settlement_deadline == original_deadline
    assert clock.now < original_deadline
    assert pointer.read_bytes() == selected_bytes
    assert services.status().status == "healthy"
