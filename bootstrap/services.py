"""Idempotent user-level service control and truthful status classification."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bootstrap.contracts import (
    OPENCLAW_LABEL,
    RUNTIME_LABEL,
    DeploymentPaths,
    atomic_write,
    load_versions,
)


@dataclass(frozen=True)
class ServiceStatus:
    status: str
    runtime: str
    openclaw: str
    observation_mode: bool
    detail: str

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "runtime": self.runtime,
            "openclaw": self.openclaw,
            "observation_mode": self.observation_mode,
            "detail": self.detail,
        }


class ServiceManager:
    def __init__(self, paths: DeploymentPaths, *, test_mode: bool = False) -> None:
        self.paths = paths
        self.test_mode = test_mode

    def _read_test_state(self) -> dict[str, str]:
        if not self.paths.service_state_path.exists():
            return {RUNTIME_LABEL: "stopped", OPENCLAW_LABEL: "stopped"}
        return json.loads(self.paths.service_state_path.read_text("utf-8"))

    def _write_test_state(self, values: dict[str, str]) -> None:
        atomic_write(
            self.paths.service_state_path,
            json.dumps(values, sort_keys=True).encode("utf-8"),
        )

    def _launchctl_loaded(self, label: str) -> bool:
        uid = int(getattr(os, "getuid", lambda: 0)())
        completed = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0

    def _validate_plist(self, label: str) -> Path:
        path = self.paths.launch_agent_dir / f"{label}.plist"
        if not path.is_file():
            raise RuntimeError(f"launchd definition is missing: {path.name}")
        data = plistlib.loads(path.read_bytes())
        if data.get("Label") != label:
            raise RuntimeError(f"launchd label mismatch in {path.name}")
        arguments = data.get("ProgramArguments") or []
        rendered = json.dumps(arguments).lower()
        if any(word in rendered for word in ("secret", "token", "password", "key_hex")):
            raise RuntimeError(
                f"launchd arguments contain a forbidden secret hint: {path.name}"
            )
        return path

    def start(self) -> ServiceStatus:
        for label in (OPENCLAW_LABEL, RUNTIME_LABEL):
            self._validate_plist(label)
        if self.test_mode:
            self._write_test_state(
                {OPENCLAW_LABEL: "running", RUNTIME_LABEL: "running"}
            )
            return self.status()
        uid = int(getattr(os, "getuid", lambda: 0)())
        domain = f"gui/{uid}"
        for label in (OPENCLAW_LABEL, RUNTIME_LABEL):
            path = self.paths.launch_agent_dir / f"{label}.plist"
            if self._launchctl_loaded(label):
                completed = subprocess.run(
                    ["launchctl", "bootout", f"{domain}/{label}"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode and self._launchctl_loaded(label):
                    raise RuntimeError(
                        f"launchd could not reload {label}: {completed.stderr.strip()}"
                    )
            completed = subprocess.run(
                ["launchctl", "bootstrap", domain, str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode and not self._launchctl_loaded(label):
                raise RuntimeError(
                    f"launchd could not load {label}: {completed.stderr.strip()}"
                )
            subprocess.run(
                ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
                check=True,
            )
        result = self.status()
        for _ in range(30):
            if result.status in {"healthy", "failed"}:
                break
            time.sleep(1)
            result = self.status()
        return result

    def stop(self) -> ServiceStatus:
        if self.test_mode:
            self._write_test_state(
                {OPENCLAW_LABEL: "stopped", RUNTIME_LABEL: "stopped"}
            )
            return self.status()
        uid = int(getattr(os, "getuid", lambda: 0)())
        domain = f"gui/{uid}"
        for label in (RUNTIME_LABEL, OPENCLAW_LABEL):
            if self._launchctl_loaded(label):
                completed = subprocess.run(
                    ["launchctl", "bootout", f"{domain}/{label}"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode and self._launchctl_loaded(label):
                    raise RuntimeError(
                        f"launchd could not stop {label}: {completed.stderr.strip()}"
                    )
        return self.status()

    def mark_failed_for_test(self, label: str = RUNTIME_LABEL) -> None:
        if not self.test_mode:
            raise RuntimeError("failure injection is test-only")
        state = self._read_test_state()
        state[label] = "failed"
        self._write_test_state(state)

    def _runtime_health(self) -> str:
        port = load_versions()["administrateme"]["runtime_port"]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/system/ready",
                timeout=2,
            ) as response:
                data = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return "degraded"
        if not data.get("ready"):
            return "failed" if data.get("status") == "blocked" else "degraded"
        return "healthy" if data.get("status") == "healthy" else "observation_only"

    def status(self) -> ServiceStatus:
        observation = True
        if self.test_mode:
            states = self._read_test_state()
            runtime = states.get(RUNTIME_LABEL, "stopped")
            openclaw = states.get(OPENCLAW_LABEL, "stopped")
            if runtime == openclaw == "stopped":
                overall = "stopped"
            elif "failed" in {runtime, openclaw}:
                overall = "failed"
            elif runtime == openclaw == "running":
                overall = "healthy"
            else:
                overall = "degraded"
            return ServiceStatus(
                overall, runtime, openclaw, observation, "fixture launchd state"
            )
        runtime_loaded = self._launchctl_loaded(RUNTIME_LABEL)
        openclaw_loaded = self._launchctl_loaded(OPENCLAW_LABEL)
        if not runtime_loaded and not openclaw_loaded:
            return ServiceStatus(
                "stopped",
                "stopped",
                "stopped",
                observation,
                "both services are unloaded",
            )
        runtime = self._runtime_health() if runtime_loaded else "stopped"
        openclaw = "running" if openclaw_loaded else "stopped"
        if runtime in {"healthy", "observation_only"} and openclaw == "running":
            overall = "healthy" if runtime == "healthy" else "healthy"
        elif runtime == "failed":
            overall = "failed"
        else:
            overall = "degraded"
        return ServiceStatus(
            overall, runtime, openclaw, observation, "live launchd and loopback health"
        )
