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

RUNTIME_LIVENESS_TIMEOUT_SECONDS = 300
RUNTIME_LIVENESS_POLL_SECONDS = 1
OPENCLAW_LIVENESS_TIMEOUT_SECONDS = 120
OPENCLAW_LIVENESS_POLL_SECONDS = 1
SERVICE_READINESS_TIMEOUT_SECONDS = 120
RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS = 30
SOURCE_ASSURANCE_COUNT_NAMES = (
    "source_facts",
    "obligations",
    "accounted",
    "pending",
    "missing",
    "eligible_candidates",
    "no_candidate",
    "quarantined",
    "failed",
)


def _diagnostic_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def source_assurance_terminal_evidence(
    diagnostics: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any]]:
    """Extract the one content-free source settlement contract."""

    document = diagnostics or {}
    capability = (document.get("capabilities") or {}).get(
        "policy:source-assurance"
    ) or {}
    details = capability.get("details") or {}
    raw_counts = details.get("counts") or {}
    counts = {
        name: _diagnostic_count(raw_counts.get(name))
        for name in SOURCE_ASSURANCE_COUNT_NAMES
    }
    freshness = details.get("freshness") or {}
    evidence = {
        "contract": details.get("contract"),
        "state": details.get("state"),
        "counts": counts,
        "declared_source_event_types": details.get("declared_source_event_types"),
        "declared_obligations": details.get("declared_obligations"),
        "content_exposed": details.get("content_exposed"),
        "recovery": details.get("recovery"),
        "freshness": {
            "state": freshness.get("state"),
            "refreshed_at": freshness.get("refreshed_at"),
            "stale_seconds": freshness.get("stale_seconds"),
            "max_stale_seconds": freshness.get("max_stale_seconds"),
            "refresh_in_progress": freshness.get("refresh_in_progress"),
            "revision_current": freshness.get("revision_current"),
            "pending_recheck_required": freshness.get("pending_recheck_required"),
            "settled_revision": freshness.get("settled_revision"),
            "refresh_error_type": freshness.get("refresh_error_type"),
        },
    }
    ok = (
        capability.get("state") == "healthy"
        and details.get("state") == "healthy"
        and counts["pending"] == 0
        and counts["missing"] == 0
        and counts["failed"] == 0
        and counts["accounted"] == counts["obligations"]
        and _diagnostic_count(details.get("declared_obligations")) > 0
        and details.get("content_exposed") is False
        and freshness.get("state") == "fresh"
        and freshness.get("revision_current") is True
        and freshness.get("refresh_error_type") is None
    )
    return ok, evidence


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

    def _wait_until_unloaded(self, label: str) -> bool:
        for _ in range(50):
            if not self._launchctl_loaded(label):
                return True
            time.sleep(0.1)
        return False

    def _launchctl_definition_matches(self, label: str, path: Path) -> bool:
        definition = plistlib.loads(path.read_bytes())
        arguments = definition.get("ProgramArguments") or []
        expected_program = str(arguments[0]) if arguments else ""
        expected_working_directory = str(definition.get("WorkingDirectory") or "")
        expected_repository_root = str(
            (definition.get("EnvironmentVariables") or {}).get(
                "ADMINME_REPOSITORY_ROOT", ""
            )
        )
        uid = int(getattr(os, "getuid", lambda: 0)())
        completed = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            return False
        output = completed.stdout
        return all(
            (
                expected_program and f"program = {expected_program}" in output,
                expected_working_directory
                and f"working directory = {expected_working_directory}" in output,
                expected_repository_root
                and f"ADMINME_REPOSITORY_ROOT => {expected_repository_root}" in output,
            )
        )

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
        # The language-backed pipelines begin replay as part of runtime
        # startup.  Bring the Gateway to a proved-live HTTP state first so a
        # reviewed replay cannot turn a normal service restart into a durable
        # generation of transport-failure defaults.
        for label in (OPENCLAW_LABEL, RUNTIME_LABEL):
            self._validate_plist(label)
        if self.test_mode:
            self._write_test_state(
                {OPENCLAW_LABEL: "running", RUNTIME_LABEL: "running"}
            )
            return self.status()
        uid = int(getattr(os, "getuid", lambda: 0)())
        domain = f"gui/{uid}"
        # Quiesce both old definitions before starting either new one.  This
        # prevents an already-running runtime from consuming source events
        # while the Gateway is between exact releases.
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
                        f"launchd could not reload {label}: {completed.stderr.strip()}"
                    )
                if not self._wait_until_unloaded(label):
                    raise RuntimeError(
                        f"launchd retained the previous {label} definition"
                    )
        for label in (OPENCLAW_LABEL, RUNTIME_LABEL):
            path = self.paths.launch_agent_dir / f"{label}.plist"
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
            for _ in range(50):
                if self._launchctl_definition_matches(label, path):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(f"launchd loaded a stale {label} release definition")
            kickstart = subprocess.run(
                ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
                check=False,
                capture_output=True,
                text=True,
            )
            # launchctl can return EX_OSERR (37) when RunAtLoad wins the race
            # and starts a freshly bootstrapped job before kickstart executes.
            # A loaded job is the authoritative result; the health wait below
            # still proves that it actually reaches a terminal runtime state.
            if kickstart.returncode and not self._launchctl_loaded(label):
                raise RuntimeError(
                    f"launchd could not start {label}: {kickstart.stderr.strip()}"
                )
            if label == OPENCLAW_LABEL:
                if not self._wait_for_openclaw_liveness():
                    raise RuntimeError(
                        "OpenClaw Gateway did not become live within the "
                        "bounded cold-start window before runtime startup"
                    )
            elif label == RUNTIME_LABEL:
                if not self._wait_for_runtime_liveness():
                    raise RuntimeError(
                        "AdministrateMe runtime did not become live within the "
                        "bounded cold-start window"
                    )
        result = self.status()
        deadline = time.monotonic() + SERVICE_READINESS_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if result.status in {"healthy", "failed"}:
                break
            time.sleep(1)
            result = self.status()
        return result

    def _openclaw_liveness(self) -> bool:
        """Use OpenClaw's credential-free local health endpoint."""

        port = load_versions()["openclaw"]["gateway_port"]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health",
                timeout=5,
            ) as response:
                data = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return False
        return data.get("ok") is True and data.get("status") == "live"

    def _wait_for_openclaw_liveness(
        self, timeout_seconds: int = OPENCLAW_LIVENESS_TIMEOUT_SECONDS
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        gateway_live = self._openclaw_liveness()
        while not gateway_live and time.monotonic() < deadline:
            time.sleep(OPENCLAW_LIVENESS_POLL_SECONDS)
            gateway_live = self._openclaw_liveness()
        return gateway_live

    def _wait_for_runtime_liveness(
        self, timeout_seconds: int = RUNTIME_LIVENESS_TIMEOUT_SECONDS
    ) -> bool:
        """Allow bounded cold schema/replay work without weakening liveness."""

        deadline = time.monotonic() + timeout_seconds
        runtime_live = self._runtime_liveness()
        while not runtime_live and time.monotonic() < deadline:
            time.sleep(RUNTIME_LIVENESS_POLL_SECONDS)
            runtime_live = self._runtime_liveness()
        return runtime_live

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

    def _runtime_liveness(self) -> bool:
        """Use the intentionally cheap endpoint while launchd is starting."""

        port = load_versions()["administrateme"]["runtime_port"]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/system/live",
                timeout=5,
            ) as response:
                data = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return False
        return data.get("status") == "alive" and data.get("runtime_started") is True

    def runtime_is_live(self) -> bool:
        """Expose the cheap content-free runtime liveness proof to upgrades."""

        return self._runtime_liveness()

    def _runtime_health(self) -> str:
        data = self.runtime_diagnostics()
        if not data:
            return "degraded"
        if not data.get("ready"):
            return "failed" if data.get("status") == "blocked" else "degraded"
        return "healthy" if data.get("status") == "healthy" else "observation_only"

    def runtime_diagnostics(self) -> dict[str, Any] | None:
        """Return the local runtime's secret-free readiness document."""

        if self.test_mode:
            return {
                "status": "observation_only",
                "ready": True,
                "capabilities": {
                    "policy:source-assurance": {
                        "name": "source-assurance",
                        "kind": "source_disposition_policy",
                        "state": "healthy",
                        "reason": None,
                        "details": {
                            "contract": "source-assurance-v1",
                            "state": "healthy",
                            "counts": {
                                "source_facts": 1,
                                "obligations": 1,
                                "accounted": 1,
                                "pending": 0,
                                "missing": 0,
                                "eligible_candidates": 1,
                                "no_candidate": 0,
                                "quarantined": 0,
                                "failed": 0,
                            },
                            "declared_source_event_types": 1,
                            "declared_obligations": 1,
                            "content_exposed": False,
                            "freshness": {
                                "state": "fresh",
                                "stale_seconds": 0.0,
                                "max_stale_seconds": 300.0,
                                "refresh_in_progress": False,
                                "revision_current": True,
                                "pending_recheck_required": False,
                                "settled_revision": True,
                                "refresh_error_type": None,
                            },
                        },
                    }
                },
            }
        port = load_versions()["administrateme"]["runtime_port"]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/system/ready",
                timeout=RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS,
            ) as response:
                data = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

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
