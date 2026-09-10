"""Guarded operation identity, proof completeness and stop-before-work gates."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bootstrap import source_contract_recovery as control
from bootstrap.contracts import atomic_write
from bootstrap.deployment_budget import CONTROLLER_SECONDS, DeploymentBudget
from bootstrap.upgrade import UpgradeManager
from tests.unit.bootstrap.test_activation_transaction import _journal_value, _paths


ROOT = Path(__file__).resolve().parents[2]


def _plan():
    return control.ContractRecoveryPlan(
        contract="guarded-source-contract-recovery-v1",
        transaction_id="5" * 32,
        predecessor_commit="1" * 40,
        required_assessment_contract="personal-obligation-assessment-v1",
        target={
            "source_event_id": "00000000000000000000000001",
            "expected_latest_assessment_event_id": "00000000000000000000000003",
            "failed_assessment_event_id": "00000000000000000000000003",
            "expected_failure_reason": "language_seam_OpenClawTimeout",
        },
    )


def _proof(tmp_path):
    names = control.expected_doctor_checks(ROOT)
    consumers = control.expected_consumer_capabilities(ROOT, tmp_path)
    doctor = {
        "schema_version": 1,
        "mode": "observation",
        "checks": [
            {"name": name, "ok": name not in control._SOURCE_FAILURE_CHECKS}
            for name in sorted(names)
        ],
    }
    counts = {name: 0 for name in control._EFFECT_COUNTS}
    source = {
        "contract": "source-assurance-v1",
        "content_exposed": False,
        "counts": {
            "pending": 0,
            "missing": 0,
            "failed": 1,
            "accounted": 1,
            "obligations": 1,
        },
        "freshness": {
            "state": "fresh",
            "revision_current": True,
            "settled_revision": True,
            "pending_recheck_required": False,
            "refresh_error_type": None,
        },
        "failed": [{"event_id": _plan().target.source_event_id}],
        "provider_effects": {
            "state": "healthy",
            "counts": counts.copy(),
            "observation_window": {"state": "healthy", "counts": counts.copy()},
        },
        "watermark_event_id": "00000000000000000000000003",
    }
    diagnostics = {
        "observation_active": True,
        "safe_reads": True,
        "deployment_readiness": {
            "contract": "deployment-readiness-v1",
            "blocking_dimensions": ["observation_effects"],
        },
        "capabilities": {
            name: {
                "state": "healthy",
                "details": {
                    "lag_count": 0,
                    "degraded": False,
                    "poison_event_id": None,
                    "consecutive_failures": 0,
                },
            }
            for name in consumers
        },
    }
    diagnostics["capabilities"]["policy:source-assurance"] = {
        "state": "degraded",
        "details": source,
    }
    return doctor, diagnostics, names, consumers


def test_preflight_requires_full_doctor_consumer_inventory_and_exact_failure(tmp_path):
    doctor, diagnostics, names, consumers = _proof(tmp_path)
    kwargs = {"doctor_checks": names, "required_consumers": consumers}
    assert (
        control.validate_live_preflight(doctor, diagnostics, _plan(), **kwargs)[
            "content_exposed"
        ]
        is False
    )
    corruptions = [
        lambda d, r: d["checks"].pop(),
        lambda d, r: d["checks"].append(d["checks"][0]),
        lambda d, r: d["checks"].append({"name": "unknown", "ok": True}),
        lambda d, r: r["capabilities"].pop(next(iter(consumers))),
        lambda d, r: r["capabilities"][next(iter(consumers))]["details"].update(
            poison_event_id="poison"
        ),
        lambda d, r: r["capabilities"]["policy:source-assurance"]["details"][
            "counts"
        ].update(missing=1),
        lambda d, r: r["capabilities"]["policy:source-assurance"]["details"][
            "freshness"
        ].update(revision_current=False),
        lambda d, r: r["capabilities"]["policy:source-assurance"]["details"][
            "failed"
        ].append({"event_id": "other"}),
        lambda d, r: r["capabilities"]["policy:source-assurance"]["details"][
            "provider_effects"
        ]["observation_window"]["counts"].update(provider_called_true=1),
    ]
    for corrupt in corruptions:
        d, r = copy.deepcopy((doctor, diagnostics))
        corrupt(d, r)
        with pytest.raises(control.ContractRecoveryControlError):
            control.validate_live_preflight(d, r, _plan(), **kwargs)
    diagnostics["capabilities"]["openclaw:member-chat"] = {
        "state": "blocked",
        "details": {"configured_members": 0},
    }
    control.validate_live_preflight(doctor, diagnostics, _plan(), **kwargs)
    diagnostics["capabilities"]["unexpected:required"] = {"state": "blocked"}
    with pytest.raises(
        control.ContractRecoveryControlError, match="capability_failure"
    ):
        control.validate_live_preflight(doctor, diagnostics, _plan(), **kwargs)
    del diagnostics["capabilities"]["unexpected:required"]
    effects = diagnostics["capabilities"]["policy:source-assurance"]["details"][
        "provider_effects"
    ]
    effects["counts"].update(
        write_requests=2, terminal_results=2, provider_called_true=2
    )
    proof = control.validate_live_preflight(doctor, diagnostics, _plan(), **kwargs)
    assert proof["provider_effect_counts"]["provider_called_true"] == 2


@pytest.mark.parametrize(
    "start,deadline",
    [
        (1000, float("inf")),
        (1000, 1000 + CONTROLLER_SECONDS + 1),
        (1100, 1200),
        (0, 900),
    ],
)
def test_record_deadline_cannot_reset_or_escape_guardian(monkeypatch, start, deadline):
    monkeypatch.setattr(control, "deployment_clock", lambda: 1000.0)
    with pytest.raises(control.ContractRecoveryControlError):
        control._remaining({"started_at": start, "deadline": deadline})


def _controller(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=ROOT, test_mode=True)
    journal = manager.activation_journal.create(_journal_value(paths))
    manager.activation_journal.transition(journal, "recovering")
    atomic_write(
        paths.app_dir / "current-release.json",
        json.dumps(journal["initial_pointer"]).encode(),
        mode=0o600,
    )
    plan_path = tmp_path / "plan.json"
    atomic_write(plan_path, _plan().model_dump_json().encode(), mode=0o600)
    clock = [1000.0]
    calls = []
    monkeypatch.setattr(control, "deployment_clock", lambda: clock[0])
    monkeypatch.setattr(control, "_boot_identity", lambda: "fixture-boot")
    monkeypatch.setattr(control, "reviewed_candidate_commit", lambda _: "2" * 40)
    monkeypatch.setattr(control, "load_versions", lambda _: {"exact": "pinned"})
    monkeypatch.setattr(manager, "_attest_controller_provenance", lambda *_: None)
    monkeypatch.setattr(
        manager,
        "_begin_deployment_budget",
        lambda: DeploymentBudget(clock[0], clock[0] + CONTROLLER_SECONDS),
    )
    doctor, diagnostics, names, consumers = _proof(tmp_path)
    monkeypatch.setattr(manager, "_run_release_doctor", lambda *a, **k: doctor)
    monkeypatch.setattr(manager.services, "runtime_diagnostics", lambda: diagnostics)
    monkeypatch.setattr(control, "expected_doctor_checks", lambda _: names)
    monkeypatch.setattr(control, "expected_consumer_capabilities", lambda *_: consumers)
    monkeypatch.setattr(
        control, "protected_snapshot", lambda _: {"fixture": "immutable"}
    )
    monkeypatch.setattr(
        control, "checkpoint_snapshot", lambda _: {"fixture": "unchanged"}
    )
    monkeypatch.setattr(manager.services, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(
        manager.services, "start_maintenance_gateway", lambda: calls.append("gateway")
    )
    monkeypatch.setattr(manager.services, "assert_maintenance_boundary", lambda: None)

    async def history(*a, **k):
        calls.append("history")

    monkeypatch.setattr(control, "validate_stopped_history", history)
    artifact = tmp_path / "fixture-backup.enc"
    artifact.write_bytes(b"opaque synthetic backup")

    class Backup:
        def __init__(self, *args, **kwargs):
            pass

        def create(self):
            calls.append("backup")
            return {"path": str(artifact)}

        def rehearse(self, *args, **kwargs):
            calls.append("rehearse")
            assert (
                2 * kwargs["timeout_s"] + 30
                < CONTROLLER_SECONDS - control.RECOVERY_SECONDS
            )
            return {
                "receipt_written": True,
                "source_events_appended": 0,
                "receipt_sha256": "a" * 64,
            }

    monkeypatch.setattr(control, "BackupManager", Backup)
    return manager, plan_path, clock, calls


def test_same_journal_retry_reuses_identity_and_deadline_without_finishing_on_failure(
    tmp_path, monkeypatch
):
    manager, plan_path, clock, calls = _controller(tmp_path, monkeypatch)
    records = []

    def child(_manager, _path, record):
        records.append(copy.deepcopy(record))
        calls.append("child")
        assert record["maintenance_deadline"] == record["maintenance_started_at"] + 600
        return {"status": "failed", "publication_complete": True}

    monkeypatch.setattr(control, "run_maintenance_child", child)
    monkeypatch.setattr(
        manager,
        "_recover_interrupted_transaction",
        lambda _: pytest.fail(
            "failed contract cannot restart predecessor or close journal"
        ),
    )
    for advance in (0, 10):
        clock[0] += advance
        with pytest.raises(control.ContractRecoveryControlError, match="not_completed"):
            manager.recover_source_contract(ROOT, plan_path)
    assert records[0]["fingerprint"] == records[1]["fingerprint"]
    assert records[0]["deadline"] == records[1]["deadline"]
    assert records[0]["maintenance_deadline"] == records[1]["maintenance_deadline"]
    assert manager.activation_journal.load()["phase"] == "recovering"
    assert calls[:5] == ["stop", "history", "backup", "rehearse", "gateway"]
    monkeypatch.setattr(control, "reviewed_candidate_commit", lambda _: "3" * 40)
    before = calls[:]
    with pytest.raises(control.ContractRecoveryControlError, match="operation_changed"):
        manager.recover_source_contract(ROOT, plan_path)
    assert calls == before


def test_stop_failure_blocks_backup_child_and_recovery(tmp_path, monkeypatch):
    manager, plan_path, _clock, calls = _controller(tmp_path, monkeypatch)

    def stop():
        raise RuntimeError("fixture could not prove quiescence")

    monkeypatch.setattr(manager.services, "stop", stop)
    with pytest.raises(RuntimeError, match="quiescence"):
        manager.recover_source_contract(ROOT, plan_path)
    assert calls == []
    assert manager.activation_journal.load()["phase"] == "recovering"


def test_child_timeout_is_reaped_before_error_returns(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(control, "deployment_clock", lambda: clock[0])
    actions = []

    class Child:
        returncode = None

        def wait(self, timeout):
            actions.append(("wait", timeout))
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fixture", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            actions.append("terminate")
            self.returncode = -15

        def kill(self):
            pytest.fail("fixture child exits on bounded termination")

    monkeypatch.setattr(control.subprocess, "Popen", lambda *a, **k: Child())
    manager = SimpleNamespace(
        test_mode=True, root=ROOT, paths=SimpleNamespace(root=tmp_path)
    )
    record = {
        "fingerprint": "f" * 64,
        "started_at": 1000.0,
        "deadline": 1000.0 + CONTROLLER_SECONDS,
        "maintenance_started_at": 1000.0,
        "maintenance_deadline": 1600.0,
    }
    with pytest.raises(control.ContractRecoveryControlError, match="timed_out"):
        control.run_maintenance_child(manager, tmp_path / "operation.json", record)
    assert actions == [("wait", 600.0), "terminate", ("wait", 30)]


def test_completed_assessment_requires_publication_then_existing_full_recovery(
    tmp_path,
    monkeypatch,
):
    manager, plan_path, _clock, calls = _controller(tmp_path, monkeypatch)
    terminal = {
        "status": "completed",
        "completed_assessment_contract": control.ASSESSMENT_CONTRACT,
        "publication_complete": False,
    }
    monkeypatch.setattr(control, "run_maintenance_child", lambda *_: terminal.copy())

    def existing_recovery(_repository):
        calls.append("existing_full_recovery")
        assert manager.activation_journal.load()["phase"] == "recovering"
        assert manager._settlement_deadline == manager._recovery_settlement_deadline
        raise RuntimeError("fixture predecessor full Doctor remains failed")

    monkeypatch.setattr(manager, "_recover_interrupted_transaction", existing_recovery)
    with pytest.raises(control.ContractRecoveryControlError, match="not_completed"):
        manager.recover_source_contract(ROOT, plan_path)
    assert "existing_full_recovery" not in calls
    terminal["publication_complete"] = True
    with pytest.raises(RuntimeError, match="Doctor remains failed"):
        manager.recover_source_contract(ROOT, plan_path)
    assert calls[-1] == "existing_full_recovery"
    assert manager.activation_journal.load()["phase"] == "recovering"
    record = control._load(
        manager.activation_journal.directory,
        f"contract-recovery-{_plan().transaction_id}.json",
    )
    assert record["phase"] == "maintenance_terminal"
    assert record["recovery_deadline"] < record["deadline"]
