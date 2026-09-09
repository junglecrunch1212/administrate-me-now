"""Production service lifecycle around disposable parent/HTTP child processes.

Launchctl and listener enumeration are host seams. Generated LaunchAgents,
ServiceManager, process ancestry, HTTP liveness, and shutdown checks are real.
The recovery scenario isolates pin/plugin selection and Doctor responses while
using the real public controller, journal, control-file writes, and services.
No installed service, credential, provider, or fixed application port is used.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import selectors
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import bootstrap.services as services_module
from bootstrap.activation_transaction import ActivationJournal
from bootstrap.contracts import (
    DeploymentPaths,
    OPENCLAW_LABEL,
    RUNTIME_LABEL,
    atomic_write,
)
from bootstrap.services import SERVICE_STOP_TIMEOUT_SECONDS, ServiceManager
from bootstrap.upgrade import UpgradeError, UpgradeManager
from bootstrap.wizard import install_launch_agents


REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_PROCESS = """
import json, os, selectors, signal, subprocess, sys, time
if sys.argv[1] == "parent":
    child = subprocess.Popen([sys.executable, "-u", __file__, "listener", sys.argv[2]], stdout=subprocess.PIPE, text=True)
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        print("fixture parent waiting for listener receipt", file=sys.stderr, flush=True)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=8):
                raise RuntimeError("fixture child did not publish its listener receipt")
            receipt = child.stdout.readline().strip()
            if not receipt:
                raise RuntimeError("fixture child exited before its listener receipt")
        print(receipt, flush=True)
        while True:
            signal.pause()
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=30)
        child.stdout.close()
else:
    import http.server, socketserver
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status = "alive" if self.path == "/api/system/live" else "healthy" if self.path == "/api/system/ready" else "live"
            body = json.dumps({"ok": True, "status": status, "runtime_started": True, "ready": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    class NumericLoopbackServer(http.server.HTTPServer):
        def server_bind(self):
            # HTTPServer normally resolves the host name before returning.
            # This fixture needs only an ephemeral numeric loopback listener.
            socketserver.TCPServer.server_bind(self)
            self.server_name = "fixture-loopback"
            self.server_port = self.server_address[1]
    print("fixture child binding numeric loopback", file=sys.stderr, flush=True)
    server = NumericLoopbackServer(("127.0.0.1", 0), Handler)
    print(json.dumps({"pid": os.getpid(), "port": server.server_port}), flush=True)
    def drain(*_):
        time.sleep(float(sys.argv[2]))
        sys.exit(0)
    signal.signal(signal.SIGTERM, drain)
    try:
        server.serve_forever()
    finally:
        server.server_close()
"""


class HostBoundary:
    def __init__(self, tmp_path, paths):
        self.paths = paths
        self.script = tmp_path / "fixture_service.py"
        self.script.write_text(HOST_PROCESS, encoding="utf-8")
        self.jobs = {}
        self.processes = []
        self.listeners = {}
        self.commands = []
        self.probes = []
        self.shutdown_delays = {}
        self.terminating = set()
        self.wait_for_bootout = True
        self.retain_gateway_on_bootout = False
        self.actual_run = subprocess.run
        self.versions = {
            "openclaw": {"gateway_port": self._unused_port()},
            "administrateme": {"runtime_port": self._unused_port()},
        }

    @staticmethod
    def _unused_port():
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            return server.getsockname()[1]

    def port(self, label):
        family, field = (
            ("openclaw", "gateway_port")
            if label == OPENCLAW_LABEL
            else ("administrateme", "runtime_port")
        )
        return self.versions[family][field]

    def start_job(self, label):
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(self.script),
                "parent",
                str(self.shutdown_delays.get(label, 0)),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            available = bool(selector.select(timeout=10))
            receipt = process.stdout.readline() if available else ""
        if not receipt:
            if process.poll() is None:
                process.terminate()
            _stdout, stderr = process.communicate(timeout=10)
            pytest.fail(
                "fixture listener did not start; "
                f"parent_exit={process.returncode}; stderr={stderr[-2000:]}"
            )
        listener = json.loads(receipt)
        self.jobs[label] = process
        self.listeners[label] = listener
        family, field = (
            ("openclaw", "gateway_port")
            if label == OPENCLAW_LABEL
            else ("administrateme", "runtime_port")
        )
        self.versions[family][field] = listener["port"]

    def run(self, command, **kwargs):
        self.probes.append((command, kwargs.get("timeout")))
        # launchd reaps exited managed parents. Reproduce that host boundary
        # when bootout returns before the child's graceful drain completes.
        for process in self.processes:
            if process.pid in self.terminating:
                process.poll()
        if command[0] not in {"launchctl", "lsof"}:
            return self.actual_run(command, **kwargs)
        self.commands.append(command)
        stdout, stderr, code = "", "", 0
        if command[0] == "lsof":
            assert command[-1] == "-t"
            port = int(
                next(value for value in command if value.startswith("-iTCP:")).split(
                    ":"
                )[1]
            )
            for listener in self.listeners.values():
                if listener["port"] != port:
                    continue
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        stdout += f"{listener['pid']}\n"
                except OSError:
                    pass
            code = 0 if stdout else 1
        elif command[1] == "bootstrap":
            definition = plistlib.loads(Path(command[3]).read_bytes())
            assert definition["RunAtLoad"] is True
            self.start_job(definition["Label"])
        elif command[1] == "print":
            label = command[-1].rsplit("/", 1)[-1]
            process = self.jobs.get(label)
            if process is None:
                code = 113
                stderr = f'Could not find service "{label}" in domain for user gui:{os.getuid()}'
            else:
                definition = plistlib.loads(
                    (self.paths.launch_agent_dir / f"{label}.plist").read_bytes()
                )
                stdout = (
                    f"state = running\npid = {process.pid}\n"
                    f"program = {definition['ProgramArguments'][0]}\n"
                    f"working directory = {definition['WorkingDirectory']}\n"
                    f"ADMINME_REPOSITORY_ROOT => {definition['EnvironmentVariables']['ADMINME_REPOSITORY_ROOT']}\n"
                )
        elif command[1] == "bootout":
            label = command[-1].rsplit("/", 1)[-1]
            process = self.jobs.pop(label)
            if label != OPENCLAW_LABEL or not self.retain_gateway_on_bootout:
                process.terminate()
                self.terminating.add(process.pid)
                if self.wait_for_bootout:
                    process.wait(timeout=kwargs["timeout"])
        else:
            pytest.fail(f"unexpected launchctl operation: {command[1]}")
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    def close(self):
        for process in self.processes:
            if process.poll() is None and process.pid not in self.terminating:
                process.terminate()
            process.wait(timeout=30)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    install_launch_agents(paths, REPO_ROOT, test_mode=True)
    host = HostBoundary(tmp_path, paths)
    monkeypatch.setattr(services_module.subprocess, "run", host.run)
    monkeypatch.setattr(services_module, "load_versions", lambda: host.versions)
    monkeypatch.setattr(services_module, "SERVICE_QUIESCENCE_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(services_module, "SERVICE_STOP_TIMEOUT_SECONDS", 0.5)
    try:
        yield ServiceManager(paths), host
    finally:
        host.close()


def test_run_at_load_keeps_the_managed_parent_and_owned_gateway_child(lifecycle):
    manager, host = lifecycle
    assert manager.start().status == "healthy"
    assert host.jobs[OPENCLAW_LABEL].pid != host.listeners[OPENCLAW_LABEL]["pid"]
    assert manager._managed_openclaw_liveness()
    assert not any("kickstart" in command for command in host.commands)
    assert [
        Path(command[3]).stem
        for command in host.commands
        if command[:2] == ["launchctl", "bootstrap"]
    ] == [OPENCLAW_LABEL, RUNTIME_LABEL]
    assert manager.stop(require_openclaw_live_before_stop=True).status == "stopped"
    assert all(process.poll() == 0 for process in host.processes)


@pytest.mark.parametrize("wait_for_bootout", [True, False])
def test_graceful_child_drain_longer_than_five_seconds_proves_stop(
    lifecycle, monkeypatch, wait_for_bootout
):
    manager, host = lifecycle
    assert SERVICE_STOP_TIMEOUT_SECONDS == 120
    monkeypatch.setattr(
        services_module, "SERVICE_STOP_TIMEOUT_SECONDS", SERVICE_STOP_TIMEOUT_SECONDS
    )
    host.wait_for_bootout = wait_for_bootout
    host.shutdown_delays[OPENCLAW_LABEL] = 6.2
    assert manager.start().status == "healthy"
    captured = tuple(host.processes)
    host.probes.clear()

    started = time.monotonic()
    assert manager.stop(require_openclaw_live_before_stop=True).status == "stopped"
    elapsed = time.monotonic() - started

    assert 6 <= elapsed < 30
    assert all(process.poll() == 0 for process in captured)
    bootouts = [
        timeout
        for command, timeout in host.probes
        if command[:2] == ["launchctl", "bootout"]
    ]
    assert len(bootouts) == 2
    assert all(5 < timeout <= SERVICE_STOP_TIMEOUT_SECONDS for timeout in bootouts)
    assert all(
        0 < timeout <= services_module.SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS
        for command, timeout in host.probes
        if command[0] in {"ps", "lsof", "launchctl"}
        and command[:2] != ["launchctl", "bootout"]
    )


def test_unloaded_job_with_live_child_cannot_prove_stop_or_fresh_start(lifecycle):
    manager, host = lifecycle
    assert manager.start().status == "healthy"
    host.retain_gateway_on_bootout = True
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="quiescence is unproven"):
        manager.stop()
    assert 0.4 <= time.monotonic() - started < 5
    assert not host.jobs
    assert manager._openclaw_liveness()  # The stale listener still answers.
    assert not manager._managed_openclaw_liveness()
    assert manager.status().status == "failed"
    assert host.processes[0].poll() is None  # No arbitrary termination fallback.
    prior_starts = sum(
        command[1] == "bootstrap"
        for command in host.commands
        if command[0] == "launchctl"
    )
    with pytest.raises(RuntimeError, match="quiescence is unproven"):
        manager.start()
    assert (
        sum(
            command[1] == "bootstrap"
            for command in host.commands
            if command[0] == "launchctl"
        )
        == prior_starts
    )


def test_interrupted_recovery_preserves_controls_until_real_stop_and_supported_retry(
    lifecycle, monkeypatch
):
    services, host = lifecycle
    paths = host.paths
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    monkeypatch.setattr(services_module, "SERVICE_STOP_TIMEOUT_SECONDS", 2)
    host.wait_for_bootout = False
    host.shutdown_delays[OPENCLAW_LABEL] = 4.2
    assert services.start().status == "healthy"
    captured = tuple(host.processes)

    predecessor_commit, candidate_commit = "1" * 40, "2" * 40
    predecessor = paths.app_dir / "releases" / predecessor_commit
    candidate = paths.app_dir / "releases" / candidate_commit
    for release in (predecessor, candidate):
        (release / "bootstrap").mkdir(mode=0o700, parents=True)
        release.chmod(0o700)
        # Real LaunchAgent generation uses the selected release's exact pin.
        (release / "bootstrap" / "versions.json").write_bytes(
            (REPO_ROOT / "bootstrap" / "versions.json").read_bytes()
        )
    (paths.app_dir / "releases").chmod(0o700)
    config = paths.instance_dir / "config"
    config.mkdir(mode=0o700, exist_ok=True)
    pointer = paths.app_dir / "current-release.json"
    readiness = config / "deployment-readiness.json"
    settings = config / "settings-control.json"
    initial_pointer = {"path": str(predecessor), "commit": predecessor_commit}
    snapshots = {
        "pointer": (pointer, initial_pointer),
        "readiness": (
            readiness,
            {"contract": "deployment-readiness-v1", "sentinel": "prior"},
        ),
        "settings_control": (settings, {"revision": 0, "schema_version": 2}),
    }
    journal_input = {
        "transaction_id": "5" * 32,
        "operation": "activate",
        "phase": "created",
        "candidate_commit": candidate_commit,
        "candidate_path": str(candidate),
        "target_commit": candidate_commit,
        "target_path": str(candidate),
        "predecessor_commit": predecessor_commit,
        "predecessor_path": str(predecessor),
        "was_running": True,
        "pending_attestation_id": "3" * 32,
        "pending_attestation_sha256": "4" * 64,
        "fresh_backup": None,
        "rollback_eligibility": None,
        "active_readiness": None,
    }
    expected_restored = {}
    for name, (path, value) in snapshots.items():
        payload = json.dumps(value, indent=2).encode()
        expected_restored[path] = payload
        journal_input[f"initial_{name}"] = value
        journal_input[f"initial_{name}_bytes"] = base64.b64encode(payload).decode()
        journal_input[f"initial_{name}_sha256"] = hashlib.sha256(payload).hexdigest()
        atomic_write(path, b'{"sentinel":"candidate"}\n', mode=0o600)

    selector = paths.root / "bin" / "fixture-openclaw-selector"
    selector.parent.mkdir(mode=0o700, exist_ok=True)
    plugin = config / "fixture-plugin-registration.json"
    adapters = config / "adapters.json"
    for path in (selector, plugin, adapters):
        atomic_write(path, b'{"sentinel":"candidate"}\n', mode=0o600)
    controls = [
        pointer,
        readiness,
        settings,
        selector,
        plugin,
        adapters,
        *(
            paths.launch_agent_dir / f"{label}.plist"
            for label in (OPENCLAW_LABEL, RUNTIME_LABEL)
        ),
    ]

    def control_identity():
        return {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mode)
            for path in controls
        }

    owner = ActivationJournal(paths)
    journal = owner.transition(owner.create(journal_input), "doctor_running")
    manager = UpgradeManager(paths, root=REPO_ROOT, test_mode=True)
    manager.services = services
    mutations = []

    def after_quiescence(name):
        assert all(process.poll() == 0 for process in captured)
        mutations.append(name)

    def select_pin(release):
        after_quiescence("pin")
        assert release == predecessor
        atomic_write(selector, b'{"sentinel":"prior"}\n', mode=0o600)

    def select_plugin(_paths, release, **_kwargs):
        after_quiescence("plugin")
        assert release == predecessor
        atomic_write(plugin, b'{"sentinel":"prior"}\n', mode=0o600)

    def select_agents(_paths, release, **kwargs):
        after_quiescence("launchagents")
        install_launch_agents(_paths, release, **kwargs)

    def doctor(release):
        assert release == predecessor
        assert services.status().status == "healthy"
        mutations.append("doctor")
        return {"status": "healthy", "checks": []}

    monkeypatch.setattr(manager, "_install_reviewed_openclaw", select_pin)
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin", select_plugin
    )
    monkeypatch.setattr("bootstrap.upgrade.install_launch_agents", select_agents)
    monkeypatch.setattr(manager, "_post_switch_doctor", doctor)
    before = control_identity()
    with pytest.raises(UpgradeError, match="service quiescence is unproven"):
        manager.rollback()

    assert control_identity() == before
    assert mutations == []
    assert owner.load()["transaction_id"] == journal["transaction_id"]
    assert owner.load()["phase"] == "recovering"
    assert list(owner.history.iterdir()) == []
    assert len(host.processes) == 2  # No predecessor start after uncertain stop.
    assert captured[0].poll() is None  # The owned child is still draining.
    assert not host.jobs

    # The captured process exits naturally; no PID-based fallback or control
    # repair is used to make the supported retry pass.
    for process in captured:
        assert process.wait(timeout=10) == 0
    host.shutdown_delays[OPENCLAW_LABEL] = 0
    result = manager.rollback()
    assert result["status"] == "recovered"
    assert result["transaction_id"] == journal["transaction_id"]
    assert mutations == ["pin", "plugin", "launchagents", "doctor"]
    assert {path: path.read_bytes() for path in expected_restored} == expected_restored
    assert adapters.read_bytes() == before[adapters][0]
    assert services.status().status == "healthy"
    assert not owner.path.exists()
    assert json.loads(Path(result["history"]).read_text())["phase"] == "recovered"
    assert len(host.processes) == 4
