"""Restore settlement and quiescence use the existing deployment proof policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bootstrap.backup_restore import BackupError, BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.service_settlement import (
    POST_SWITCH_CONSUMER_PROGRESS_SECONDS,
    POST_SWITCH_REPLAY_SETTLEMENT_SECONDS,
    run_release_doctor,
    wait_for_settled_doctor,
)
from bootstrap.services import ServiceManager
from bootstrap.source_identity import write_source_marker


@pytest.fixture(autouse=True)
def credential_free_restore_lab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")


def _report(*failed: str) -> dict:
    return {
        "status": "failed" if failed else "healthy",
        "checks": [{"name": name, "ok": False} for name in failed],
    }


def _readiness(*, pending: int = 0) -> dict:
    return {
        "ready": True,
        "observation_active": True,
        "write_admission": "observation_suppressed",
        "capabilities": {
            "policy:source-assurance": {
                "state": "degraded" if pending else "healthy",
                "details": {
                    "state": "degraded" if pending else "healthy",
                    "counts": {
                        "obligations": 1,
                        "accounted": 0 if pending else 1,
                        "pending": pending,
                        "missing": 0,
                        "failed": 0,
                    },
                    "declared_obligations": 1,
                    "content_exposed": False,
                    "freshness": {
                        "state": "fresh",
                        "revision_current": True,
                        "refresh_error_type": None,
                    },
                },
            }
        },
    }


@pytest.fixture
def restore_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = DeploymentPaths.from_root(tmp_path / "restore-lab")
    manager = BackupManager(paths, root=tmp_path, test_mode=True)
    # Only the settlement policy takes the production branch. Every external
    # boundary remains mocked or uses the disposable credential-free store.
    manager.test_mode = False
    services = Mock(spec=ServiceManager)
    services.runtime_is_live.return_value = True
    services.runtime_diagnostics.return_value = _readiness()
    manager._services = services
    doctor_run = Mock()
    doctor = Mock(return_value=SimpleNamespace(run=doctor_run))
    monkeypatch.setattr("bootstrap.backup_restore.Doctor", doctor)
    monkeypatch.setattr(
        manager, "_selected_restore_release", lambda: (manager.root, lambda: None)
    )
    monkeypatch.setattr(
        "bootstrap.backup_restore.run_release_doctor",
        lambda paths, release, **kwargs: doctor(
            paths, root=release, test_mode=kwargs["test_mode"]
        ).run(include_synthetic_proof=kwargs["include_synthetic_proof"]),
    )
    sleep = Mock()
    monkeypatch.setattr(
        "bootstrap.service_settlement.time",
        SimpleNamespace(sleep=sleep),
    )
    return manager, services, doctor, doctor_run, sleep


def test_restore_waits_for_late_source_settlement_then_runs_full_doctor(restore_proof):
    manager, services, doctor, run, sleep = restore_proof
    run.side_effect = [
        _report("source_assurance_terminal_dispositions"),
        _report(),
        _report(),
    ]
    services.runtime_diagnostics.side_effect = [
        _readiness(pending=1),
        _readiness(pending=1),
        _readiness(),
    ]

    assert manager._post_restore_doctor(services_restarted=True) == _report()
    assert [call.kwargs["include_synthetic_proof"] for call in run.call_args_list] == [
        False,
        False,
        True,
    ]
    assert services.runtime_diagnostics.call_count == 3
    assert sleep.call_count == 3
    assert doctor.call_count == 3
    assert all(
        call.args == (manager.paths,)
        and call.kwargs == {"root": manager.root, "test_mode": False}
        for call in doctor.call_args_list
    )
    services.stop.assert_not_called()
    services.start.assert_not_called()


@pytest.mark.parametrize("emerges_during_wait", [False, True])
def test_restore_returns_first_structural_failure_without_waiting(
    restore_proof, emerges_during_wait
):
    manager, services, _doctor, run, sleep = restore_proof
    structural = _report("encrypted_event_log_and_migrations")
    if emerges_during_wait:
        run.side_effect = [
            _report("source_assurance_terminal_dispositions"),
            structural,
        ]
        diagnostics = _readiness(pending=1)
        diagnostics["capabilities"]["pipeline:fixture"] = {
            "kind": "reactive_pipeline",
            "state": "degraded",
        }
        services.runtime_diagnostics.return_value = diagnostics
    else:
        run.return_value = structural

    assert manager._post_restore_doctor(services_restarted=True) == structural
    assert run.call_count == (2 if emerges_during_wait else 1)
    assert all(
        call.kwargs["include_synthetic_proof"] is False for call in run.call_args_list
    )
    sleep.assert_not_called()


def test_restore_missing_live_readiness_has_only_three_retries(restore_proof):
    manager, services, _doctor, run, sleep = restore_proof
    transient = _report("source_assurance_terminal_dispositions")
    run.return_value = transient
    services.runtime_diagnostics.return_value = None

    assert manager._post_restore_doctor(services_restarted=True) == transient
    assert run.call_count == 4
    assert sleep.call_count == 3


def test_restore_source_settlement_has_one_fixed_six_hour_budget(
    restore_proof, monkeypatch
):
    manager, services, _doctor, run, sleep = restore_proof
    transient = _report("source_assurance_terminal_dispositions")
    run.return_value = transient
    services.runtime_diagnostics.return_value = _readiness(pending=1)
    now = [0.0]
    monkeypatch.setattr("bootstrap.service_settlement.deployment_clock", lambda: now[0])
    sleep.side_effect = lambda _seconds: now.__setitem__(0, 21600.0)

    assert POST_SWITCH_REPLAY_SETTLEMENT_SECONDS == 21600
    result = manager._post_restore_doctor(services_restarted=True)
    assert result["status"] == "failed"
    assert result["checks"][-1]["evidence"]["reason"] == "deadline_exhausted"
    # Expiration cannot launch another Doctor and later accept its success.
    assert run.call_count == 1
    assert sleep.call_count == 1


@pytest.mark.parametrize("restarted", [False, True])
def test_restore_stopped_and_test_mode_validation_do_not_wait(restore_proof, restarted):
    manager, services, _doctor, run, sleep = restore_proof
    manager.test_mode = restarted
    transient = _report("source_assurance_terminal_dispositions")
    run.return_value = transient

    assert manager._post_restore_doctor(services_restarted=restarted) == transient
    run.assert_called_once_with(include_synthetic_proof=False)
    services.runtime_diagnostics.assert_not_called()
    sleep.assert_not_called()


@pytest.mark.parametrize("stop_fails", [False, True])
def test_failed_restore_quiesces_before_reverting_state(
    tmp_path, monkeypatch, stop_fails
):
    paths = DeploymentPaths.from_root(tmp_path / "ordering-lab")
    for directory in (paths.instance_dir, paths.backup_dir, paths.rollback_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (paths.instance_dir / "identity").write_text("before")
    backup = paths.backup_dir / "fixture.ambackup"
    backup.write_bytes(b"encrypted-fixture")
    manager = BackupManager(paths, root=tmp_path, test_mode=True)
    monkeypatch.setattr(
        manager, "_references", lambda: {"backup": "backup", "event_log": "log"}
    )
    monkeypatch.setattr(manager.store, "get", lambda _reference: b"r" * 32)
    monkeypatch.setattr(manager, "_prove_rebuild", AsyncMock(return_value={}))

    def authenticate(_source, _key, archive):
        archive.write_bytes(b"authenticated fixture")
        return {"manifest_sha256": "fixture-digest"}, "fixture-digest"

    def extract(_archive, staging):
        state = staging / "state"
        state.mkdir()
        (state / "identity").write_text("restored")
        return {"manifest_sha256": "fixture-digest"}

    monkeypatch.setattr(
        "bootstrap.backup_restore._authenticate_backup_to_path", authenticate
    )
    monkeypatch.setattr(
        "bootstrap.backup_restore._extract_and_validate_archive_path", extract
    )
    services = Mock(spec=ServiceManager)
    services.status.return_value = SimpleNamespace(status="healthy")
    manager._services = services
    order = []

    def stop():
        identity = (paths.instance_dir / "identity").read_text()
        order.append(f"stop:{identity}")
        if stop_fails and identity == "restored":
            raise RuntimeError("fixture stop failed")

    services.stop.side_effect = stop
    services.start.side_effect = lambda: order.append("start")
    original_replace = os.replace

    def replace(source, destination):
        if Path(source) == paths.instance_dir:
            identity = (paths.instance_dir / "identity").read_text()
            assert order[-1] == f"stop:{identity}"
            order.append(f"move:{identity}")
        original_replace(source, destination)

    monkeypatch.setattr("bootstrap.backup_restore.os.replace", replace)
    monkeypatch.setattr(
        manager,
        "_post_restore_doctor",
        lambda **_kwargs: _report("encrypted_event_log_and_migrations"),
    )
    error = RuntimeError if stop_fails else BackupError
    with pytest.raises(
        error, match="stop failed" if stop_fails else "failed Doctor validation"
    ):
        manager.restore(backup)
    assert (paths.instance_dir / "identity").read_text() == (
        "restored" if stop_fails else "before"
    )
    assert order == [
        "stop:before",
        "move:before",
        "start",
        "stop:restored",
        *([] if stop_fails else ["move:restored", "start"]),
    ]


@pytest.fixture
def selected_release(tmp_path):
    paths = DeploymentPaths.from_root(tmp_path / "selected-release-lab")
    paths.create()
    commit = "d" * 40
    release = paths.app_dir / "releases" / commit
    (release / ".venv" / "bin").mkdir(parents=True)
    python = release / ".venv" / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o700)
    versions = release / "bootstrap" / "versions.json"
    versions.parent.mkdir()
    versions.write_bytes(
        (
            Path(__file__).resolve().parents[3] / "bootstrap" / "versions.json"
        ).read_bytes()
    )
    write_source_marker(release, commit)
    pointer = paths.app_dir / "current-release.json"
    pointer.write_text(json.dumps({"commit": commit, "path": str(release)}))
    pointer.chmod(0o600)
    controller = tmp_path / "newer-controller"
    controller.mkdir()
    manager = BackupManager(paths, root=controller, test_mode=True)
    manager.test_mode = False
    return manager, release, pointer


def test_restore_uses_selected_release_python_and_root_not_newer_controller(
    selected_release, monkeypatch
):
    manager, release, _pointer = selected_release
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(_report()), stderr="")

    monkeypatch.setattr("bootstrap.service_settlement.subprocess.run", run)
    monkeypatch.setattr(
        "bootstrap.backup_restore.Doctor",
        Mock(side_effect=AssertionError("controller Doctor must not run")),
    )
    assert manager._post_restore_doctor(services_restarted=False) == _report()
    [(command, options)] = calls
    assert command[0] == str(release / ".venv" / "bin" / "python")
    assert command[-1] == "--no-synthetic-proof"
    assert options["cwd"] == release
    assert options["env"]["ADMINME_REPOSITORY_ROOT"] == str(release)
    assert options["env"]["ADMINME_INSTANCE_DIR"] == str(manager.paths.instance_dir)
    assert "PYTHONPATH" not in options["env"]
    assert "VIRTUAL_ENV" not in options["env"]
    assert manager.root != release


@pytest.mark.parametrize(
    "invalid", ["missing_pointer", "malformed_pointer", "wrong_path", "missing_runtime"]
)
def test_restore_invalid_selection_fails_before_state_or_secret_access(
    selected_release, monkeypatch, invalid
):
    manager, release, pointer = selected_release
    if invalid == "missing_pointer":
        pointer.unlink()
    elif invalid == "malformed_pointer":
        pointer.write_text("{invalid json")
    elif invalid == "wrong_path":
        pointer.write_text(json.dumps({"commit": "d" * 40, "path": str(manager.root)}))
    else:
        (release / ".venv" / "bin" / "python").unlink()
    backup = manager.paths.backup_dir / "fixture.ambackup"
    backup.write_bytes(b"fixture-encrypted")
    secret_access = Mock(side_effect=AssertionError("must fail before secret access"))
    monkeypatch.setattr(manager, "_references", secret_access)
    manager._services = Mock(spec=ServiceManager)
    with pytest.raises(BackupError, match="release"):
        manager.restore(backup)
    secret_access.assert_not_called()
    manager.services.stop.assert_not_called()
    manager.services.start.assert_not_called()


def test_restore_rejects_pointer_change_during_readiness_poll(
    selected_release, monkeypatch
):
    manager, _release, pointer = selected_release
    calls = []

    def doctor(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps(_report("source_assurance_terminal_dispositions")),
            stderr="",
        )

    def mutate_pointer(_seconds):
        pointer.write_text("{}")

    monkeypatch.setattr("bootstrap.service_settlement.subprocess.run", doctor)
    monkeypatch.setattr(
        "bootstrap.service_settlement.time",
        SimpleNamespace(sleep=mutate_pointer),
    )
    services = Mock(spec=ServiceManager)
    services.runtime_diagnostics.return_value = _readiness(pending=1)
    manager._services = services
    with pytest.raises(BackupError, match="selected release changed"):
        manager._post_restore_doctor(services_restarted=True)
    assert len(calls) == 1
    assert services.runtime_diagnostics.call_count == 1


@pytest.fixture
def settlement_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(
        "bootstrap.service_settlement.time",
        # The supervisor's Python and the selected runtime must never compare
        # process-relative monotonic origins. Only the shared OS clock seam is
        # available here, so any accidental local-clock fallback fails the test.
        SimpleNamespace(sleep=sleep),
    )
    monkeypatch.setattr(
        "bootstrap.service_settlement.deployment_clock", lambda: clock.now
    )
    return clock


_REPLAY_ID = "pipeline:pipeline:noise_filtering"
_PROJECTION_ID = "projection:source_assurance"


def _consumer(checkpoint, *, lag=1, version=23, kind="reactive_pipeline"):
    return {
        "kind": kind,
        "state": "healthy",
        "details": {
            "version": version,
            "checkpoint_event_id": checkpoint,
            "lag_count": lag,
            "consecutive_failures": 0,
            "poison_event_id": None,
        },
    }


def _settlement_services(diagnostics):
    services = Mock(spec=ServiceManager)
    services.runtime_is_live.return_value = True
    services.runtime_diagnostics.side_effect = diagnostics
    return services


def _settlement_failure(result):
    assert result["status"] == "failed"
    check = result["checks"][-1]
    assert check["name"] == "bounded_service_settlement"
    assert check["ok"] is False
    assert check["evidence"]["contract"] == "bounded-service-settlement-v1"
    return check["evidence"]


def test_checkpoint_advancement_can_settle_after_more_than_one_hour(settlement_clock):
    clock = settlement_clock
    checkpoints = []

    def diagnostics():
        pending = clock.now < 3900
        result = _readiness(pending=int(pending))
        checkpoint = f"ev_{int(clock.now // 300):020d}"
        checkpoints.append(checkpoint)
        result["capabilities"][_REPLAY_ID] = _consumer(
            checkpoint, lag=10 if pending else 0
        )
        return result

    def doctor(*, include_synthetic_proof):
        if clock.now < 3900:
            return _report("source_assurance_terminal_dispositions")
        return _report()

    run = Mock(side_effect=doctor)
    services = _settlement_services(diagnostics)
    result = wait_for_settled_doctor(
        run_doctor=run, services=services, test_mode=False, poll_seconds=300
    )

    assert result == _report()
    assert 3600 < clock.now < POST_SWITCH_REPLAY_SETTLEMENT_SECONDS
    assert checkpoints == sorted(set(checkpoints))
    assert len(checkpoints) > 10
    assert [call.kwargs for call in run.call_args_list] == [
        {"include_synthetic_proof": False},
        {"include_synthetic_proof": False},
        {"include_synthetic_proof": True},
    ]


@pytest.mark.parametrize("decreasing_lag", [False, True])
def test_one_stalled_consumer_cannot_borrow_other_progress_or_ingress(
    settlement_clock, decreasing_lag
):
    clock = settlement_clock

    def diagnostics():
        step = int(clock.now // 100)
        result = _readiness(pending=step + 1)
        result["capabilities"][_REPLAY_ID] = _consumer(
            "ev_00000000000000000001",
            lag=1000 - step if decreasing_lag else 1000 + step,
        )
        result["capabilities"][_REPLAY_ID]["details"]["last_success_at"] = step
        result["capabilities"][_PROJECTION_ID] = _consumer(
            f"ev_{step:020d}", kind="projection"
        )
        return result

    run = Mock(return_value=_report("source_assurance_terminal_dispositions"))
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(diagnostics),
        test_mode=False,
        poll_seconds=100,
    )

    evidence = _settlement_failure(result)
    assert evidence["reason"] == "consumer_stalled"
    assert evidence["consumer_id"] == _REPLAY_ID
    assert clock.now == POST_SWITCH_CONSUMER_PROGRESS_SECONDS == 600
    run.assert_called_once_with(include_synthetic_proof=False)


@pytest.mark.parametrize("absolute_deadline", [600, 100000])
def test_tiny_checkpoint_progress_never_extends_either_hard_deadline(
    settlement_clock, absolute_deadline
):
    clock = settlement_clock

    def diagnostics():
        result = _readiness(pending=1)
        result["capabilities"][_REPLAY_ID] = _consumer(
            f"ev_{int(clock.now // 100):020d}"
        )
        return result

    run = Mock(return_value=_report("source_assurance_terminal_dispositions"))
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(diagnostics),
        test_mode=False,
        poll_seconds=100,
        timeout_seconds=100000,
        absolute_deadline=absolute_deadline,
    )

    assert _settlement_failure(result)["reason"] == "deadline_exhausted"
    assert clock.now == min(absolute_deadline, 21600)
    run.assert_called_once_with(include_synthetic_proof=False)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("version", "consumer_identity_changed"),
        ("missing", "consumer_identity_changed"),
        ("kind", "consumer_identity_changed"),
        ("checkpoint_regression", "consumer_checkpoint_regressed"),
        ("checkpoint_cleared", "consumer_checkpoint_regressed"),
        ("checkpoint_invalid", "consumer_evidence_invalid"),
        ("version_missing", "consumer_evidence_invalid"),
        ("lag_missing", "consumer_evidence_invalid"),
    ],
)
def test_consumer_identity_and_checkpoint_evidence_are_frozen(
    settlement_clock, change, reason
):
    clock = settlement_clock

    def diagnostics():
        result = _readiness(pending=1)
        consumer = _consumer("ev_00000000000000000010")
        result["capabilities"][_REPLAY_ID] = consumer
        if clock.now:
            if change == "missing":
                del result["capabilities"][_REPLAY_ID]
            elif change == "kind":
                consumer["kind"] = "projection"
            else:
                field, value = {
                    "version": ("version", 24),
                    "checkpoint_regression": (
                        "checkpoint_event_id",
                        "ev_00000000000000000009",
                    ),
                    "checkpoint_cleared": ("checkpoint_event_id", None),
                    "checkpoint_invalid": ("checkpoint_event_id", {}),
                    "version_missing": ("version", None),
                    "lag_missing": ("lag_count", None),
                }[change]
                consumer["details"][field] = value
        return result

    run = Mock(return_value=_report("source_assurance_terminal_dispositions"))
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(diagnostics),
        test_mode=False,
        poll_seconds=100,
    )

    evidence = _settlement_failure(result)
    assert evidence["reason"] == reason
    assert evidence["consumer_id"] == _REPLAY_ID
    assert clock.now == 100
    run.assert_called_once_with(include_synthetic_proof=False)


def test_observed_empty_backlog_starts_a_new_pending_episode(settlement_clock):
    clock = settlement_clock
    observations = []

    def diagnostics():
        now = clock.now
        result = _readiness(pending=int(now < 1500))
        lag = 1 if now == 0 or 1000 <= now < 1500 else 0
        checkpoint = (
            "ev_00000000000000000001" if now < 1500 else "ev_00000000000000000002"
        )
        result["capabilities"][_REPLAY_ID] = _consumer(checkpoint, lag=lag)
        result["capabilities"][_PROJECTION_ID] = _consumer(
            f"ev_{int(now // 100):020d}", lag=int(now < 1500), kind="projection"
        )
        observations.append((now, checkpoint, lag))
        return result

    run = Mock(
        side_effect=lambda **kwargs: (
            _report("source_assurance_terminal_dispositions")
            if clock.now < 1500
            else _report()
        )
    )
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(diagnostics),
        test_mode=False,
        poll_seconds=100,
    )

    assert result == _report()
    assert (1000, "ev_00000000000000000001", 1) in observations
    assert clock.now == 1600
    assert run.call_args.kwargs == {"include_synthetic_proof": True}


@pytest.mark.parametrize("completion", ["checkpoint", "empty_backlog"])
def test_watchdog_reads_final_progress_before_rejecting_stale_checkpoint(
    settlement_clock, completion
):
    clock = settlement_clock
    observations = []

    def diagnostics():
        # Progress becomes observable during the final bounded readiness call,
        # after the preceding poll had seen the unchanged checkpoint.
        if clock.now == 600:
            clock.now += 1
        done = clock.now >= 600
        result = _readiness(pending=int(not done))
        checkpoint = (
            "ev_00000000000000000002"
            if done and completion == "checkpoint"
            else "ev_00000000000000000001"
        )
        lag = 0 if done and completion == "empty_backlog" else 1
        result["capabilities"][_REPLAY_ID] = _consumer(checkpoint, lag=lag)
        observations.append(clock.now)
        return result

    run = Mock(
        side_effect=lambda **kwargs: (
            _report("source_assurance_terminal_dispositions")
            if clock.now < 600
            else _report()
        )
    )
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(diagnostics),
        test_mode=False,
        poll_seconds=100,
    )

    assert result == _report()
    assert observations[-1] == 601
    assert run.call_args.kwargs == {"include_synthetic_proof": True}


@pytest.mark.parametrize("failure", ["poison_event_id", "consecutive_failures"])
def test_consumer_poison_and_failure_fail_even_with_a_healthy_state_label(
    settlement_clock, failure
):
    diagnostics = _readiness(pending=1)
    consumer = _consumer("ev_00000000000000000001")
    consumer["details"][failure] = (
        "ev_poison_fixture" if failure == "poison_event_id" else 1
    )
    diagnostics["capabilities"][_REPLAY_ID] = consumer
    run = Mock(return_value=_report("source_assurance_terminal_dispositions"))
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(lambda: diagnostics),
        test_mode=False,
    )

    assert _settlement_failure(result)["reason"] == "consumer_failed"
    assert settlement_clock.now == 0
    run.assert_called_once_with(include_synthetic_proof=False)


def test_failed_source_assurance_is_not_waitable_pending_work(settlement_clock):
    diagnostics = _readiness(pending=1)
    diagnostics["capabilities"]["policy:source-assurance"]["details"]["counts"][
        "failed"
    ] = 1
    result = wait_for_settled_doctor(
        run_doctor=Mock(return_value=_report("source_assurance_terminal_dispositions")),
        services=_settlement_services(lambda: diagnostics),
        test_mode=False,
    )
    assert _settlement_failure(result)["reason"] == "source_assurance_failed"
    assert settlement_clock.now == 0


def test_readiness_blackouts_do_not_reset_consumer_progress(settlement_clock):
    clock = settlement_clock

    def diagnostics():
        if clock.now not in {0, 300}:
            return None
        result = _readiness(pending=1)
        result["capabilities"][_REPLAY_ID] = _consumer("ev_00000000000000000001")
        return result

    result = wait_for_settled_doctor(
        run_doctor=Mock(return_value=_report("source_assurance_terminal_dispositions")),
        services=_settlement_services(diagnostics),
        test_mode=False,
        poll_seconds=100,
    )
    assert _settlement_failure(result)["reason"] == "consumer_stalled"
    assert clock.now == 600


def test_structural_readiness_cannot_be_overruled_by_basic_doctor_success(
    settlement_clock,
):
    diagnostics = _readiness(pending=1)
    diagnostics["capabilities"]["adapter:fixture"] = {
        "kind": "adapter",
        "state": "blocked",
    }
    run = Mock(
        side_effect=[_report("source_assurance_terminal_dispositions"), _report()]
    )
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(lambda: diagnostics),
        test_mode=False,
    )
    assert _settlement_failure(result)["reason"] == "structural_degradation"
    assert all(
        not call.kwargs["include_synthetic_proof"] for call in run.call_args_list
    )
    assert settlement_clock.now == 0


def test_full_doctor_success_after_expiry_is_still_failure(settlement_clock):
    clock = settlement_clock

    def doctor(*, include_synthetic_proof):
        if include_synthetic_proof:
            clock.now = 300
        return _report()

    run = Mock(side_effect=doctor)
    services = _settlement_services(lambda: _readiness())
    result = wait_for_settled_doctor(
        run_doctor=run, services=services, test_mode=False, absolute_deadline=300
    )
    evidence = _settlement_failure(result)
    assert evidence["reason"] == "deadline_exhausted"
    assert evidence["phase"] == "full_doctor"
    assert run.call_count == 2
    services.runtime_diagnostics.assert_not_called()


def test_full_doctor_is_not_started_without_its_complete_remaining_budget(
    settlement_clock,
):
    clock = settlement_clock

    def doctor(*, include_synthetic_proof):
        assert include_synthetic_proof is False
        clock.now += 100
        return _report()

    run = Mock(side_effect=doctor)
    result = wait_for_settled_doctor(
        run_doctor=run,
        services=_settlement_services(lambda: _readiness()),
        test_mode=False,
        absolute_deadline=200,
    )
    evidence = _settlement_failure(result)
    assert evidence["reason"] == "proof_budget_exhausted"
    assert evidence["phase"] == "full_doctor"
    run.assert_called_once_with(include_synthetic_proof=False)


@pytest.mark.parametrize("phase", ["readiness", "liveness"])
def test_readiness_and_liveness_calls_reserve_their_bounded_time(
    settlement_clock, phase
):
    clock = settlement_clock

    def doctor(**kwargs):
        clock.now += 115 if phase == "readiness" else 120
        return _report("source_assurance_terminal_dispositions")

    def diagnostics():
        clock.now += 26
        return None

    services = _settlement_services(diagnostics)
    result = wait_for_settled_doctor(
        run_doctor=doctor,
        services=services,
        test_mode=False,
        absolute_deadline=140 if phase == "readiness" else 150,
    )
    evidence = _settlement_failure(result)
    assert evidence["reason"] == "proof_budget_exhausted"
    assert evidence["phase"] == phase
    assert services.runtime_diagnostics.call_count == int(phase == "liveness")
    services.runtime_is_live.assert_not_called()


@pytest.mark.parametrize("timeout", [7, 1000])
def test_selected_release_doctor_timeout_cannot_exceed_its_existing_bound(
    selected_release, monkeypatch, timeout
):
    manager, release, _pointer = selected_release
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(_report())))
    monkeypatch.setattr("bootstrap.service_settlement.subprocess.run", run)

    assert (
        run_release_doctor(
            manager.paths,
            release,
            include_synthetic_proof=True,
            require_runtime=True,
            timeout_seconds=timeout,
        )
        == _report()
    )
    assert run.call_args.kwargs["timeout"] == min(timeout, 120)
