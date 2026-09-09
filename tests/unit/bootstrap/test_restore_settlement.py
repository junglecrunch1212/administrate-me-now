"""Restore settlement and quiescence use the existing deployment proof policy."""

from __future__ import annotations

import os
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bootstrap.backup_restore import BackupError, BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.service_settlement import POST_SWITCH_REPLAY_SETTLEMENT_SECONDS
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
        SimpleNamespace(monotonic=time.monotonic, sleep=sleep),
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


def test_restore_source_settlement_has_one_fixed_hour_budget(
    restore_proof, monkeypatch
):
    manager, services, _doctor, run, sleep = restore_proof
    transient = _report("source_assurance_terminal_dispositions")
    run.return_value = transient
    services.runtime_diagnostics.return_value = _readiness(pending=1)
    now = [0.0]
    monkeypatch.setattr("bootstrap.service_settlement.time.monotonic", lambda: now[0])
    sleep.side_effect = lambda _seconds: now.__setitem__(0, 3600.0)

    assert POST_SWITCH_REPLAY_SETTLEMENT_SECONDS == 3600
    assert manager._post_restore_doctor(services_restarted=True) == transient
    assert run.call_count == 2
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
        SimpleNamespace(monotonic=time.monotonic, sleep=mutate_pointer),
    )
    services = Mock(spec=ServiceManager)
    services.runtime_diagnostics.return_value = _readiness(pending=1)
    manager._services = services
    with pytest.raises(BackupError, match="selected release changed"):
        manager._post_restore_doctor(services_restarted=True)
    assert len(calls) == 1
    assert services.runtime_diagnostics.call_count == 1
