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
    for field in ("degraded", "poison_event_id", "consecutive_failures"):
        truncated = copy.deepcopy(diagnostics)
        del truncated["capabilities"][next(iter(consumers))]["details"][field]
        with pytest.raises(
            control.ContractRecoveryControlError, match="consumer_not_settled"
        ):
            control.validate_live_preflight(doctor, truncated, _plan(), **kwargs)
    for field, invalid in (
        ("degraded", 0),
        ("poison_event_id", ""),
        ("consecutive_failures", False),
    ):
        mistyped = copy.deepcopy(diagnostics)
        mistyped["capabilities"][next(iter(consumers))]["details"][field] = invalid
        with pytest.raises(
            control.ContractRecoveryControlError, match="consumer_not_settled"
        ):
            control.validate_live_preflight(doctor, mistyped, _plan(), **kwargs)
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
        control,
        "protected_snapshot",
        lambda _: {
            "files": {"synthetic-control": "unchanged"},
            "gateway_target": "captured-pinned-gateway",
            "gateway_sha256": "a" * 64,
        },
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

    async def terminal(*a, **k):
        return None

    monkeypatch.setattr(control, "_operation_terminal", terminal)
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
        record["worker_terminal_confirmed"] = True
        records.append(copy.deepcopy(record))
        calls.append("child")
        assert record["maintenance_deadline"] == record["maintenance_started_at"] + 600
        return {"status": "failed", "publication_complete": True}

    monkeypatch.setattr(control, "run_maintenance_child", child)

    def restored(_repository, *, finish_journal=True):
        assert finish_journal is False
        calls.append("restore_with_open_journal")
        return {"status": "predecessor_restored"}

    monkeypatch.setattr(manager, "_recover_interrupted_transaction", restored)
    for advance in (0, 10):
        clock[0] += advance
        with pytest.raises(control.ContractRecoveryControlError, match="not_completed"):
            manager.recover_source_contract(ROOT, plan_path)
    assert records[0]["fingerprint"] == records[1]["fingerprint"]
    assert records[0]["deadline"] == records[1]["deadline"]
    assert records[0]["maintenance_deadline"] == records[1]["maintenance_deadline"]
    assert manager.activation_journal.load()["phase"] == "recovering"
    assert calls[:5] == ["stop", "history", "backup", "rehearse", "gateway"]
    assert calls.count("restore_with_open_journal") == 2
    assert (
        records[1]["recovery_deadline"]
        == records[0]["maintenance_started_at"] + control.RECOVERY_SECONDS
    )
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

    def child(_manager, _path, record):
        record["worker_terminal_confirmed"] = True
        return terminal.copy()

    monkeypatch.setattr(control, "run_maintenance_child", child)

    def existing_recovery(_repository, *, finish_journal=True):
        calls.append("existing_full_recovery")
        assert manager.activation_journal.load()["phase"] == "recovering"
        assert manager._settlement_deadline == manager._recovery_settlement_deadline
        if not finish_journal:
            calls.append("healthy_but_journal_kept_open")
            return {"status": "predecessor_restored", "journal_open": True}
        raise RuntimeError("fixture predecessor full Doctor remains failed")

    monkeypatch.setattr(manager, "_recover_interrupted_transaction", existing_recovery)
    with pytest.raises(control.ContractRecoveryControlError, match="not_completed"):
        manager.recover_source_contract(ROOT, plan_path)
    assert calls[-1] == "healthy_but_journal_kept_open"
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


@pytest.mark.parametrize("stage", ["backup", "worker", "unreaped_worker"])
def test_failure_cleanup_preserves_primary_and_original_deadline(
    tmp_path, monkeypatch, stage
):
    manager, plan_path, clock, calls = _controller(tmp_path, monkeypatch)
    original = RuntimeError("fixture primary maintenance failure")
    seen_deadlines = []

    class BrokenBackup:
        def __init__(self, *args, **kwargs):
            pass

        def create(self):
            raise original

    def child(_manager, _path, record):
        record["worker_terminal_confirmed"] = stage != "unreaped_worker"
        raise original

    def recovery(_repository, *, finish_journal=True):
        assert finish_journal is False
        seen_deadlines.append(manager._settlement_deadline)
        raise RuntimeError("fixture secondary full Doctor failure")

    if stage == "backup":
        monkeypatch.setattr(control, "BackupManager", BrokenBackup)
    monkeypatch.setattr(control, "run_maintenance_child", child)
    monkeypatch.setattr(manager, "_recover_interrupted_transaction", recovery)
    for advance in (0, 5):
        clock[0] += advance
        with pytest.raises(RuntimeError) as raised:
            manager.recover_source_contract(ROOT, plan_path)
        assert raised.value is original
    if stage == "unreaped_worker":
        assert seen_deadlines == []
    else:
        assert len(seen_deadlines) == 2 and seen_deadlines[0] == seen_deadlines[1]
        assert all("RuntimeError" in note for note in original.__notes__)
    assert manager.activation_journal.load()["phase"] == "recovering"


def test_existing_recovery_can_restore_healthy_predecessor_without_closing_journal(
    tmp_path, monkeypatch
):
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=ROOT, test_mode=True)
    journal = manager.activation_journal.create(_journal_value(paths))
    manager.activation_journal.transition(journal, "recovering")
    actions = []
    manager.services = SimpleNamespace(
        stop=lambda: actions.append("stop"),
        start=lambda: (
            actions.append("start")
            or SimpleNamespace(status="healthy", runtime="running", openclaw="running")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_install_reviewed_openclaw",
        lambda _: actions.append("pinned_gateway"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *a, **k: actions.append("plugin"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *a, **k: actions.append("launchagents"),
    )
    doctor, _diagnostics, names, _consumers = _proof(tmp_path)
    for check in doctor["checks"]:
        check["ok"] = True
    doctor["status"] = "healthy"

    def proof(_release):
        assert set(check["name"] for check in doctor["checks"]) == names
        actions.append("full_doctor")
        return doctor

    monkeypatch.setattr(manager, "_post_switch_doctor", proof)
    result = manager._recover_interrupted_transaction(ROOT, finish_journal=False)
    assert result["status"] == "predecessor_restored"
    assert result["journal_open"] is True
    assert actions[-2:] == ["start", "full_doctor"]
    assert manager.activation_journal.load()["phase"] == "recovering"
    assert not list(manager.activation_journal.history.glob("*.json"))


def _saved_operation(manager):
    name = f"contract-recovery-{_plan().transaction_id}.json"
    return name, control._load(manager.activation_journal.directory, name)


def _healthy_proof(manager):
    doctor = manager._run_release_doctor(ROOT)
    doctor["status"] = "healthy"
    for check in doctor["checks"]:
        check["ok"] = True
    diagnostics = manager.services.runtime_diagnostics()
    diagnostics["deployment_readiness"].update(ready=True, blocking_dimensions=[])
    source = diagnostics["capabilities"]["policy:source-assurance"]["details"]
    source["counts"]["failed"] = 0
    source["failed"] = []
    source["watermark_event_id"] = "00000000000000000000000009"


def test_publication_reentry_uses_fresh_full_proof_and_retains_all_deadlines(
    tmp_path, monkeypatch
):
    manager, plan_path, clock, calls = _controller(tmp_path, monkeypatch)
    terminal = {
        "status": "completed",
        "completed_assessment_contract": control.ASSESSMENT_CONTRACT,
        "publication_complete": False,
    }
    children = []

    def child(_manager, _path, record):
        record["worker_terminal_confirmed"] = True
        children.append(copy.deepcopy(record))
        return terminal.copy()

    def recovery(_repository, *, finish_journal=True):
        if not finish_journal:
            journal = manager.activation_journal.load()
            control.record_contract_predecessor_controls(manager, journal)
            control.record_contract_predecessor_restoration(manager, journal)
            return {"status": "predecessor_restored"}
        return {"status": "recovered"}

    monkeypatch.setattr(control, "run_maintenance_child", child)
    monkeypatch.setattr(manager, "_recover_interrupted_transaction", recovery)
    with pytest.raises(control.ContractRecoveryControlError, match="not_completed"):
        manager.recover_source_contract(ROOT, plan_path)
    _name, initial = _saved_operation(manager)
    _healthy_proof(manager)
    monkeypatch.setattr(
        manager.services, "status", lambda: SimpleNamespace(status="healthy")
    )
    monkeypatch.setattr(
        control, "checkpoint_snapshot", lambda _: {"fixture": "legitimate-old-replay"}
    )

    async def stored_terminal(*a, **k):
        return terminal.copy()

    monkeypatch.setattr(control, "_operation_terminal", stored_terminal)
    clock[0] += 700  # Original language phase has expired; it must never restart.

    def suffix(_manager, _path, record):
        record["worker_terminal_confirmed"] = True
        children.append(copy.deepcopy(record))
        assert record["execution_mode"] == "publication_only"
        assert record["publication_checkpoints"] == {"fixture": "legitimate-old-replay"}
        terminal["publication_complete"] = True
        return terminal.copy()

    monkeypatch.setattr(control, "run_maintenance_child", suffix)
    assert manager.recover_source_contract(ROOT, plan_path)["status"] == "recovered"
    assert len(children) == 2
    assert children[1]["fingerprint"] == initial["fingerprint"]
    assert children[1]["plan"] == initial["plan"]
    for field in ("deadline", "maintenance_deadline", "recovery_deadline"):
        assert children[1][field] == initial[field]
    assert children[1]["publication_deadline"] == clock[0] + 600
    # Crash after complete publication needs only existing recovery, no child.
    before = len(children)
    assert manager.recover_source_contract(ROOT, plan_path)["status"] == "recovered"
    assert len(children) == before


@pytest.mark.parametrize("unspent", [True, False])
def test_only_unspent_command_can_refresh_after_source_failed_cleanup(
    tmp_path, monkeypatch, unspent
):
    manager, plan_path, clock, calls = _controller(tmp_path, monkeypatch)
    real_backup = control.BackupManager
    primary = RuntimeError("fixture backup failed before command append")

    class BrokenBackup:
        def __init__(self, *a, **k):
            pass

        def create(self):
            raise primary

    def recovery(_repository, *, finish_journal=True):
        if not finish_journal:
            control.record_contract_predecessor_controls(
                manager, manager.activation_journal.load()
            )
            raise RuntimeError("fixture exact source remains failed")
        return {"status": "recovered"}

    monkeypatch.setattr(control, "BackupManager", BrokenBackup)
    monkeypatch.setattr(manager, "_recover_interrupted_transaction", recovery)
    with pytest.raises(RuntimeError) as raised:
        manager.recover_source_contract(ROOT, plan_path)
    assert raised.value is primary
    _name, initial = _saved_operation(manager)
    assert initial["cleanup_intent"] and initial["predecessor_controls_restored"]
    assert not initial.get("predecessor_restoration_proved")
    monkeypatch.setattr(control, "BackupManager", real_backup)
    monkeypatch.setattr(
        manager.services, "status", lambda: SimpleNamespace(status="degraded")
    )
    monkeypatch.setattr(
        control, "checkpoint_snapshot", lambda _: {"fixture": "newly-settled-prefix"}
    )
    source = manager.services.runtime_diagnostics()["capabilities"][
        "policy:source-assurance"
    ]["details"]
    source["watermark_event_id"] = "00000000000000000000000009"
    no_attempt_checks = []

    async def no_attempts(*a, **k):
        no_attempt_checks.append("read")
        return unspent

    monkeypatch.setattr(control, "_operation_has_no_attempts", no_attempts)
    clock[0] += 10
    seen = []

    def child(_manager, _path, record):
        record["worker_terminal_confirmed"] = True
        seen.append(copy.deepcopy(record))
        return {
            "status": "completed",
            "publication_complete": True,
            "completed_assessment_contract": control.ASSESSMENT_CONTRACT,
        }

    monkeypatch.setattr(control, "run_maintenance_child", child)
    if unspent:
        assert manager.recover_source_contract(ROOT, plan_path)["status"] == "recovered"
        assert len(no_attempt_checks) == 2  # live and stopped lineage/reservation proof
        assert seen[0]["checkpoints"] == {"fixture": "newly-settled-prefix"}
        assert (
            seen[0]["preflight"]["watermark_event_id"] == source["watermark_event_id"]
        )
        for field in ("fingerprint", "plan", "deadline", "recovery_deadline"):
            assert seen[0][field] == initial[field]
    else:
        with pytest.raises(
            control.ContractRecoveryControlError, match="checkpoints_changed"
        ):
            manager.recover_source_contract(ROOT, plan_path)
        assert not seen
        assert len(no_attempt_checks) == 1


@pytest.mark.parametrize("publication_complete", [False, True])
def test_ordinary_rollback_cannot_archive_unfinished_declared_operation(
    tmp_path, monkeypatch, publication_complete
):
    manager, plan_path, _clock, _calls = _controller(tmp_path, monkeypatch)

    def child(_manager, _path, record):
        record["worker_terminal_confirmed"] = True
        return {"status": "failed"}

    monkeypatch.setattr(control, "run_maintenance_child", child)
    monkeypatch.setattr(
        manager, "_recover_interrupted_transaction", lambda *a, **k: None
    )
    with pytest.raises(control.ContractRecoveryControlError, match="not_completed"):
        manager.recover_source_contract(ROOT, plan_path)
    monkeypatch.delattr(manager, "_recover_interrupted_transaction")
    actions = []
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda _: None)
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin", lambda *a, **k: None
    )
    monkeypatch.setattr("bootstrap.upgrade.install_launch_agents", lambda *a, **k: None)
    monkeypatch.setattr(
        manager.services,
        "start",
        lambda: SimpleNamespace(
            status="healthy", runtime="running", openclaw="running"
        ),
    )
    _healthy_proof(manager)

    def full_doctor(_release):
        actions.append("full_doctor")
        return manager._run_release_doctor(ROOT)

    monkeypatch.setattr(manager, "_post_switch_doctor", full_doctor)

    async def terminal(*a, **k):
        return {
            "status": "completed",
            "completed_assessment_contract": control.ASSESSMENT_CONTRACT,
            "publication_complete": publication_complete,
        }

    monkeypatch.setattr(control, "_operation_terminal", terminal)
    if publication_complete:
        assert manager._recover_interrupted_transaction(ROOT)["status"] == "recovered"
        assert manager.activation_journal.load() is None
    else:
        with pytest.raises(
            control.ContractRecoveryControlError, match="publication_unfinished"
        ):
            manager._recover_interrupted_transaction(ROOT)
        assert manager.activation_journal.load()["phase"] == "recovering"
        assert not list(manager.activation_journal.history.glob("*.json"))
    assert actions == ["full_doctor"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["absent", "pending", "reserved", "failed"])
async def test_unspent_proof_checks_all_exact_reservations_and_current_lineage(
    tmp_path, monkeypatch, state
):
    record = {"plan": _plan().model_dump(mode="json"), "fingerprint": "a" * 64}
    batch = control._batch(record)
    request = {
        "event_id": "00000000000000000000000009",
        "payload": {"batch_fingerprint": control.contract_batch_fingerprint(batch)},
    }
    calls = []

    class Log:
        def __init__(self, config, key, *, read_only):
            assert read_only is True

        async def get_by_idempotency(self, tenant, key):
            assert tenant == "fixture-tenant"
            assert key == control.contract_request_key(
                batch.request_id, _plan().target.source_event_id
            )
            return None if state == "absent" else request

        async def get_by_idempotency_keys(self, tenant, keys):
            assert tenant == "fixture-tenant"
            assert keys == [
                f"source-assessment-contract-attempt:{request['event_id']}:{slot}"
                for slot in range(3)
            ]
            calls.append("three-exact-slots")
            return [{"event_id": "spent"}] if state == "reserved" else []

        async def close(self):
            calls.append("closed")

    async def terminal(log, request_id):
        assert request_id == request["event_id"]
        return {"status": "failed" if state == "failed" else "pending"}

    async def lineage(log, target, *, required_tenant_id):
        assert target == _plan().target
        assert required_tenant_id == "fixture-tenant"
        calls.append("frozen-lineage")

    monkeypatch.setattr(control, "EventLog", Log)
    monkeypatch.setattr(control, "_keys", lambda *a: (b"synthetic", "synthetic"))
    monkeypatch.setattr(
        control,
        "load_instance_config",
        lambda _: SimpleNamespace(tenant_id="fixture-tenant"),
    )
    monkeypatch.setattr(control, "contract_recovery_terminal", terminal)
    monkeypatch.setattr(control, "validate_contract_recovery_target", lineage)
    result = await control._operation_has_no_attempts(
        SimpleNamespace(instance_dir=tmp_path), record, test_mode=True
    )
    assert result is (state in {"absent", "pending"})
    assert ("frozen-lineage" in calls) is result
    assert ("three-exact-slots" in calls) is (state in {"pending", "reserved"})
    assert calls[-1] == "closed"
