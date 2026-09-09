"""Production service lifecycle around disposable parent/HTTP child processes.

Only launchctl and listener enumeration are host seams. Generated LaunchAgents,
ServiceManager, process ancestry, HTTP liveness, and shutdown checks are real.
No installed service, credential, provider, or fixed application port is used.
"""

from __future__ import annotations

import json
import os
import plistlib
import selectors
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import bootstrap.services as services_module
from bootstrap.contracts import DeploymentPaths, OPENCLAW_LABEL, RUNTIME_LABEL
from bootstrap.services import ServiceManager
from bootstrap.wizard import install_launch_agents


REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_PROCESS = """
import http.server, json, os, signal, subprocess, sys
if sys.argv[1] == "parent":
    child = subprocess.Popen([sys.executable, "-u", __file__, "listener"], stdout=subprocess.PIPE, text=True)
    print(child.stdout.readline().strip(), flush=True)
    def stop(*_):
        child.terminate()
        child.wait(timeout=5)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    while True:
        signal.pause()
else:
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
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    print(json.dumps({"pid": os.getpid(), "port": server.server_port}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
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
            [sys.executable, "-u", str(self.script), "parent"],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "fixture listener did not start"
            listener = json.loads(process.stdout.readline())
        self.jobs[label] = process
        self.listeners[label] = listener
        family, field = (
            ("openclaw", "gateway_port")
            if label == OPENCLAW_LABEL
            else ("administrateme", "runtime_port")
        )
        self.versions[family][field] = listener["port"]

    def run(self, command, **kwargs):
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
                process.wait(timeout=5)
        else:
            pytest.fail(f"unexpected launchctl operation: {command[1]}")
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    def close(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            if process.stdout is not None:
                process.stdout.close()


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    install_launch_agents(paths, REPO_ROOT, test_mode=True)
    host = HostBoundary(tmp_path, paths)
    monkeypatch.setattr(services_module.subprocess, "run", host.run)
    monkeypatch.setattr(services_module, "load_versions", lambda: host.versions)
    monkeypatch.setattr(services_module, "SERVICE_QUIESCENCE_TIMEOUT_SECONDS", 0.5)
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


def test_unloaded_job_with_live_child_cannot_prove_stop_or_fresh_start(lifecycle):
    manager, host = lifecycle
    assert manager.start().status == "healthy"
    host.retain_gateway_on_bootout = True
    with pytest.raises(RuntimeError, match="quiescence is unproven"):
        manager.stop()
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
