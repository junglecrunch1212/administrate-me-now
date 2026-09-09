"""Bounded, fail-closed OS identity checks for service lifecycle."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from bootstrap.contracts import DeploymentPaths, OPENCLAW_LABEL
from bootstrap.services import ServiceManager, _ProcessIdentity


def test_gateway_health_requires_stable_managed_listener_ownership(
    tmp_path, monkeypatch
):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    parent = _ProcessIdentity(100, 1, os.getuid(), "fixture-start")
    child = _ProcessIdentity(101, 100, os.getuid(), "fixture-child-start")
    processes = {100: parent, 101: child}
    monkeypatch.setattr(manager, "_managed_pid", lambda *_: 100)
    monkeypatch.setattr(manager, "_process_table", lambda *_: processes)
    monkeypatch.setattr(manager, "_listener_pids", lambda *_: frozenset({101}))
    monkeypatch.setattr(manager, "_openclaw_liveness", lambda **_: True)
    assert manager._managed_openclaw_liveness()

    # A live listener reparented outside the managed tree cannot prove health.
    processes[101] = _ProcessIdentity(101, 1, os.getuid(), child.started_at)
    assert not manager._managed_openclaw_liveness()
    processes[101] = child

    def replaced_during_http(**_):
        processes[101] = _ProcessIdentity(101, 100, os.getuid(), "new-start")
        return True

    monkeypatch.setattr(manager, "_openclaw_liveness", replaced_during_http)
    assert not manager._managed_openclaw_liveness()


@pytest.mark.parametrize(
    "output,code,stderr",
    [
        ("101\n", 0, "permission failure"),
        ("pnot-a-pid\n", 0, ""),
        ("", 2, ""),
    ],
)
def test_listener_probe_refuses_unavailable_or_malformed_inventory(
    tmp_path, monkeypatch, output, code, stderr
):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    monkeypatch.setattr(
        manager,
        "_process_probe",
        lambda *_: SimpleNamespace(stdout=output, returncode=code, stderr=stderr),
    )
    with pytest.raises(RuntimeError, match="listener ownership"):
        manager._listener_pids(OPENCLAW_LABEL, 1)


def test_process_probe_has_a_timeout_and_no_sensitive_process_fields(
    tmp_path, monkeypatch
):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    observed = {}

    def run(command, **kwargs):
        observed.update(command=command, **kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("bootstrap.services.subprocess.run", run)
    with pytest.raises(RuntimeError, match="ownership could not be verified"):
        manager._process_table(0.25)
    assert observed["command"] == ["ps", "-axo", "pid=,ppid=,uid=,lstart="]
    assert observed["timeout"] == 0.25


def test_listener_probe_requests_numeric_pid_only_output(tmp_path, monkeypatch):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    commands = []

    def probe(command, timeout):
        commands.append(command)
        # macOS -Fp also emits f33/f34 descriptor fields. -t is PID-only.
        output = "101\n101\n" if command[-1] == "-t" else "p101\nf33\nf34\n"
        return SimpleNamespace(stdout=output, returncode=0, stderr="")

    monkeypatch.setattr(manager, "_process_probe", probe)
    assert manager._listener_pids(OPENCLAW_LABEL, 1) == frozenset({101})
    assert commands[0][-1] == "-t"


@pytest.mark.parametrize(
    "code,stderr,absent",
    [
        (
            113,
            f'Bad request.\nCould not find service "{OPENCLAW_LABEL}" in domain for user gui: 501\n',
            True,
        ),
        (1, "Operation not permitted", False),
        (113, 'Could not find service "another-label"', False),
        (113, "", False),
    ],
)
def test_only_exact_missing_job_signature_proves_absence(
    tmp_path, monkeypatch, code, stderr, absent
):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    monkeypatch.setattr(
        manager,
        "_process_probe",
        lambda *_: SimpleNamespace(returncode=code, stdout="", stderr=stderr),
    )
    if absent:
        assert manager._managed_pid(OPENCLAW_LABEL, 1) is None
        assert not manager._launchctl_loaded(OPENCLAW_LABEL)
    else:
        with pytest.raises(RuntimeError, match="absence could not be verified"):
            manager._managed_pid(OPENCLAW_LABEL, 1)
        with pytest.raises(RuntimeError, match="absence could not be verified"):
            manager._launchctl_loaded(OPENCLAW_LABEL)


def test_late_gateway_health_cannot_extend_start_deadline(tmp_path, monkeypatch):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    clock = [0.0]
    monkeypatch.setattr("bootstrap.services.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("bootstrap.services.time.sleep", lambda _: None)
    parent = _ProcessIdentity(100, 1, os.getuid(), "fixture-parent")
    snapshot = (parent, frozenset({parent}))
    monkeypatch.setattr(manager, "_owned_gateway_snapshot", lambda _: snapshot)
    timeouts = []

    def late_http(*, timeout_seconds):
        timeouts.append(timeout_seconds)
        clock[0] = 120.0
        return True

    monkeypatch.setattr(manager, "_openclaw_liveness", late_http)
    assert not manager._wait_for_openclaw_liveness()
    assert timeouts == [5]


def test_quiescence_rejects_unload_proof_returned_after_deadline(tmp_path, monkeypatch):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    clock = [0.0]
    monkeypatch.setattr("bootstrap.services.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("bootstrap.services.time.sleep", lambda _: None)
    monkeypatch.setattr(manager, "_managed_pid", lambda *_: None)
    monkeypatch.setattr(manager, "_listener_pids", lambda *_: frozenset())
    monkeypatch.setattr(manager, "_process_table", lambda *_: {})
    calls = 0

    def unloaded(_label, timeout):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert timeout <= 5
            clock[0] = 5.0
        return False

    monkeypatch.setattr(manager, "_launchctl_loaded", unloaded)
    with pytest.raises(RuntimeError, match="quiescence is unproven"):
        manager._stop_label(OPENCLAW_LABEL)


def test_quiescence_refuses_a_captured_reparented_process_after_port_closes(
    tmp_path, monkeypatch
):
    manager = ServiceManager(DeploymentPaths.from_root(tmp_path / "deployment"))
    clock = [0.0]
    loaded = [True]
    parent = _ProcessIdentity(100, 1, os.getuid(), "fixture-parent")
    child = _ProcessIdentity(101, 100, os.getuid(), "fixture-child")
    monkeypatch.setattr("bootstrap.services.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "bootstrap.services.time.sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(manager, "_managed_pid", lambda *_: 100 if loaded[0] else None)
    monkeypatch.setattr(
        manager,
        "_listener_pids",
        lambda *_: frozenset({101}) if loaded[0] else frozenset(),
    )
    monkeypatch.setattr(
        manager,
        "_process_table",
        lambda *_: (
            {100: parent, 101: child}
            if loaded[0]
            else {101: _ProcessIdentity(101, 1, os.getuid(), child.started_at)}
        ),
    )
    monkeypatch.setattr(manager, "_launchctl_loaded", lambda *_: loaded[0])

    def bootout(*_):
        loaded[0] = False
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(manager, "_process_probe", bootout)
    with pytest.raises(RuntimeError, match="quiescence is unproven"):
        manager._stop_label(OPENCLAW_LABEL)
    assert clock[0] == pytest.approx(5)
