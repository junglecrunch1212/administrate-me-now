"""Idempotent user-level service control and truthful status classification."""

from __future__ import annotations

import json
import os
import plistlib
import re
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
SERVICE_QUIESCENCE_TIMEOUT_SECONDS = 5
SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS = 5
# The bus alone may drain callbacks for five seconds; workbook, adapter,
# request and native connection shutdown also need time. This is a reviewed
# total stop ceiling, not a guarantee that every service exits within it.
SERVICE_STOP_TIMEOUT_SECONDS = 120
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
_SOURCE_SETTLEMENT_DERIVED_DEGRADATIONS = frozenset(
    {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
)
_SOURCE_SETTLEMENT_READINESS_DIMENSIONS = frozenset({"observation_effects"})


def isolated_degraded_adapter_ids(
    diagnostics: object,
) -> frozenset[str] | None:
    """Return the exact isolated degraded-adapter boundary, or fail closed.

    A reviewed repair may begin while one or more adapters are degraded only
    when reads, event ingest, observation containment, and write suppression
    remain intact and every non-adapter capability is healthy.  ``frozenset()``
    means the runtime is fully ready; ``None`` means the document is absent or
    exposes any broader degradation.
    """

    if not isinstance(diagnostics, dict):
        return None
    capabilities = diagnostics.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        return None
    degraded: set[str] = set()
    for capability_id, capability in capabilities.items():
        if not isinstance(capability_id, str) or not isinstance(capability, dict):
            return None
        state = capability.get("state")
        if state not in {"degraded", "blocked"}:
            continue
        if capability.get("kind") != "adapter" or state != "degraded":
            return None
        degraded.add(capability_id)
    if not degraded:
        return frozenset() if diagnostics.get("ready") is True else None
    if (
        diagnostics.get("status") != "degraded"
        or diagnostics.get("safe_reads") is not True
        or diagnostics.get("event_ingest_ready") is not True
        or diagnostics.get("observation_active") is not True
        or diagnostics.get("write_admission")
        not in {"blocked", "observation_suppressed"}
    ):
        return None
    return frozenset(degraded)


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


def _source_settlement_only_blocked_readiness(
    diagnostics: dict[str, Any],
) -> bool:
    """Recognize the one blocked startup state that has a bounded repair path."""

    deployment = diagnostics.get("deployment_readiness") or {}
    capabilities = diagnostics.get("capabilities") or {}
    source = capabilities.get("policy:source-assurance") or {}
    details = source.get("details") or {}
    freshness = details.get("freshness") or {}
    if not (
        diagnostics.get("status") == "blocked"
        and diagnostics.get("ready") is False
        and diagnostics.get("safe_reads") is True
        and diagnostics.get("event_ingest_ready") is True
        and diagnostics.get("observation_active") is True
        and deployment.get("contract") == "deployment-readiness-v1"
        and deployment.get("state") == "blocked"
        and deployment.get("ready") is False
        and frozenset(deployment.get("blocking_dimensions") or ())
        == _SOURCE_SETTLEMENT_READINESS_DIMENSIONS
        and source.get("state") == "degraded"
        and details.get("contract") == "source-assurance-v1"
        and details.get("state") == "degraded"
        and details.get("content_exposed") is False
        and freshness.get("refresh_error_type") is None
    ):
        return False
    return not any(
        capability_id not in _SOURCE_SETTLEMENT_DERIVED_DEGRADATIONS
        and isinstance(capability, dict)
        and capability.get("state") in {"degraded", "blocked"}
        for capability_id, capability in capabilities.items()
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


@dataclass(frozen=True)
class _ProcessIdentity:
    pid: int
    parent_pid: int
    uid: int
    started_at: str

    def same_process(self, other: _ProcessIdentity) -> bool:
        # Parentage can change during shutdown; PID alone can be recycled.
        return (self.pid, self.uid, self.started_at) == (
            other.pid,
            other.uid,
            other.started_at,
        )


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

    def _launchctl_loaded(
        self, label: str, timeout: float = SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS
    ) -> bool:
        return self._launchctl_job(label, timeout) is not None

    def _wait_until_unloaded(self, label: str) -> bool:
        for _ in range(50):
            if not self._launchctl_loaded(label):
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _process_probe(
        command: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if timeout <= 0:
            raise RuntimeError("service process proof deadline expired")
        try:
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                "service process ownership could not be verified"
            ) from exc

    def _managed_pid(self, label: str, timeout: float) -> int | None:
        result = self._launchctl_job(label, timeout)
        if result is None:
            return None
        matches = re.findall(
            r"^\s*pid\s*=\s*([1-9][0-9]*)\s*$", result.stdout, re.MULTILINE
        )
        if len(matches) > 1 or ("pid =" in result.stdout and not matches):
            raise RuntimeError("launchd process identity is malformed")
        return int(matches[0]) if matches else None

    def _launchctl_job(
        self, label: str, timeout: float
    ) -> subprocess.CompletedProcess[str] | None:
        result = self._process_probe(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout
        )
        if result.returncode == 0:
            return result
        # Permission, domain, and command errors are not evidence of absence.
        if (
            result.returncode == 113
            and f'Could not find service "{label}"' in result.stderr
        ):
            return None
        raise RuntimeError("launchd service absence could not be verified")

    def _process_table(self, timeout: float) -> dict[int, _ProcessIdentity]:
        # No arguments or environment are read: those may contain credentials.
        result = self._process_probe(["ps", "-axo", "pid=,ppid=,uid=,lstart="], timeout)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError("service process inventory is unavailable")
        processes: dict[int, _ProcessIdentity] = {}
        for line in result.stdout.splitlines():
            fields = line.split(None, 3)
            if len(fields) != 4 or not all(value.isdecimal() for value in fields[:3]):
                raise RuntimeError("service process inventory is malformed")
            pid, parent, uid = (int(value) for value in fields[:3])
            if pid in processes or not fields[3].strip():
                raise RuntimeError("service process inventory is malformed")
            processes[pid] = _ProcessIdentity(pid, parent, uid, fields[3].strip())
        return processes

    def _listener_pids(self, label: str, timeout: float) -> frozenset[int]:
        versions = load_versions()
        port = (
            versions["openclaw"]["gateway_port"]
            if label == OPENCLAW_LABEL
            else versions["administrateme"]["runtime_port"]
        )
        result = self._process_probe(
            ["lsof", "-nP", "-a", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], timeout
        )
        if (
            result.returncode == 1
            and not result.stdout.strip()
            and not result.stderr.strip()
        ):
            return frozenset()
        lines = result.stdout.splitlines()
        if (
            result.returncode
            or result.stderr.strip()
            or not lines
            or any(re.fullmatch(r"[1-9][0-9]*", line) is None for line in lines)
        ):
            raise RuntimeError("service listener ownership is unavailable")
        return frozenset(int(line) for line in lines)

    @staticmethod
    def _managed_tree(
        managed: int | None, processes: dict[int, _ProcessIdentity]
    ) -> dict[int, _ProcessIdentity]:
        if managed is None:
            return {}
        if managed not in processes or processes[managed].uid != os.getuid():
            raise RuntimeError("managed service process identity is unavailable")
        tree = {managed: processes[managed]}
        while True:
            children = {
                pid: process
                for pid, process in processes.items()
                if pid not in tree
                and process.parent_pid in tree
                and process.uid == os.getuid()
            }
            if not children:
                return tree
            tree.update(children)

    @staticmethod
    def _proof_remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("service process proof deadline expired")
        return min(SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS, remaining)

    def _owned_gateway_snapshot(
        self, deadline: float
    ) -> tuple[_ProcessIdentity, frozenset[_ProcessIdentity]] | None:
        managed = self._managed_pid(OPENCLAW_LABEL, self._proof_remaining(deadline))
        if managed is None:
            return None
        processes = self._process_table(self._proof_remaining(deadline))
        tree = self._managed_tree(managed, processes)
        listeners = self._listener_pids(OPENCLAW_LABEL, self._proof_remaining(deadline))
        if not listeners or not listeners.issubset(tree):
            return None
        if (
            self._managed_pid(OPENCLAW_LABEL, self._proof_remaining(deadline))
            != managed
        ):
            return None
        return tree[managed], frozenset(tree[pid] for pid in listeners)

    def _managed_openclaw_liveness(self, *, deadline: float | None = None) -> bool:
        if deadline is None:
            deadline = time.monotonic() + SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS
        try:
            before = self._owned_gateway_snapshot(deadline)
            if before is None or not self._openclaw_liveness(
                timeout_seconds=self._proof_remaining(deadline)
            ):
                return False
            # The HTTP reply must be bracketed by the same managed process
            # and exact owned listeners, including a legitimate CLI child.
            after = self._owned_gateway_snapshot(deadline)
            return time.monotonic() < deadline and before == after
        except RuntimeError:
            return False

    def _stop_label(self, label: str) -> None:
        # Prove that every captured process (even if reparented) has exited
        # and no listener remains. This is not a claim that an unobserved child
        # can never escape its parent's ancestry while launchctl is stopping.
        deadline = time.monotonic() + SERVICE_STOP_TIMEOUT_SECONDS
        managed = self._managed_pid(label, self._proof_remaining(deadline))
        listeners = self._listener_pids(label, self._proof_remaining(deadline))
        processes = self._process_table(self._proof_remaining(deadline))
        captured = self._managed_tree(managed, processes)
        for pid in listeners:
            if pid not in processes:
                raise RuntimeError("service listener identity changed before stop")
            captured[pid] = processes[pid]
        if self._launchctl_loaded(label, self._proof_remaining(deadline)):
            # bootout may itself wait for graceful process exit. It shares the
            # whole stop budget rather than a fresh five-second probe window.
            result = self._process_probe(
                ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                deadline - time.monotonic(),
            )
            if result.returncode and self._launchctl_loaded(
                label, self._proof_remaining(deadline)
            ):
                raise RuntimeError(f"launchd could not stop {label}")
        while time.monotonic() < deadline:
            managed_now = self._managed_pid(label, self._proof_remaining(deadline))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            listeners_now = self._listener_pids(label, self._proof_remaining(deadline))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            current = self._process_table(self._proof_remaining(deadline))
            survivors = any(
                pid in current and identity.same_process(current[pid])
                for pid, identity in captured.items()
            )
            if (
                time.monotonic() < deadline
                and managed_now is None
                and not listeners_now
                and not survivors
            ):
                unloaded = not self._launchctl_loaded(
                    label, self._proof_remaining(deadline)
                )
                if unloaded and time.monotonic() < deadline:
                    return
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise RuntimeError(
            f"service {label} stop deadline expired; quiescence is unproven"
        )

    def _launchctl_definition_matches(
        self,
        label: str,
        path: Path,
        timeout: float = SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS,
    ) -> bool:
        definition = plistlib.loads(path.read_bytes())
        arguments = definition.get("ProgramArguments") or []
        expected_program = str(arguments[0]) if arguments else ""
        expected_working_directory = str(definition.get("WorkingDirectory") or "")
        expected_repository_root = str(
            (definition.get("EnvironmentVariables") or {}).get(
                "ADMINME_REPOSITORY_ROOT", ""
            )
        )
        completed = self._launchctl_job(label, timeout)
        if completed is None:
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
        if data.get("RunAtLoad") is not True:
            raise RuntimeError(f"launchd definition must start on load: {path.name}")
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
            self._stop_label(label)
        for label in (OPENCLAW_LABEL, RUNTIME_LABEL):
            path = self.paths.launch_agent_dir / f"{label}.plist"
            completed = self._process_probe(
                ["launchctl", "bootstrap", domain, str(path)],
                SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS,
            )
            if completed.returncode and not self._launchctl_loaded(label):
                raise RuntimeError(
                    f"launchd could not load {label}: {completed.stderr.strip()}"
                )
            definition_deadline = time.monotonic() + SERVICE_QUIESCENCE_TIMEOUT_SECONDS
            while time.monotonic() < definition_deadline:
                matches = self._launchctl_definition_matches(
                    label, path, self._proof_remaining(definition_deadline)
                )
                if matches and time.monotonic() < definition_deadline:
                    break
                time.sleep(min(0.1, max(0, definition_deadline - time.monotonic())))
            else:
                raise RuntimeError(f"launchd loaded a stale {label} release definition")
            # Both reviewed definitions use RunAtLoad. Bootstrap already
            # starts the job; kickstart -k here kills that just-started parent
            # and can strand a CLI child outside launchd supervision.
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
        if not self._managed_openclaw_liveness():
            raise RuntimeError("OpenClaw health is not owned by its managed process")
        return result

    def _openclaw_liveness(self, *, timeout_seconds: float = 5) -> bool:
        """Use OpenClaw's credential-free local health endpoint."""

        port = load_versions()["openclaw"]["gateway_port"]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health",
                timeout=timeout_seconds,
            ) as response:
                data = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return False
        return data.get("ok") is True and data.get("status") == "live"

    def _wait_for_openclaw_liveness(
        self, timeout_seconds: int = OPENCLAW_LIVENESS_TIMEOUT_SECONDS
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._managed_openclaw_liveness(deadline=deadline):
                return time.monotonic() < deadline
            time.sleep(
                min(OPENCLAW_LIVENESS_POLL_SECONDS, max(0, deadline - time.monotonic()))
            )
        return False

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

    def stop(self, *, require_openclaw_live_before_stop: bool = False) -> ServiceStatus:
        if self.test_mode:
            if (
                require_openclaw_live_before_stop
                and self._read_test_state().get(OPENCLAW_LABEL) != "running"
            ):
                raise RuntimeError(
                    "OpenClaw Gateway was not live at the repair quiescence boundary"
                )
            self._write_test_state(
                {OPENCLAW_LABEL: "stopped", RUNTIME_LABEL: "stopped"}
            )
            return self.status()
        for label in (RUNTIME_LABEL, OPENCLAW_LABEL):
            if (
                label == OPENCLAW_LABEL
                and require_openclaw_live_before_stop
                and not self._managed_openclaw_liveness()
            ):
                raise RuntimeError(
                    "OpenClaw Gateway was not live at the repair quiescence boundary"
                )
            self._stop_label(label)
        return self.status()

    def assert_maintenance_boundary(self) -> None:
        """Require the old runtime absent and the unchanged Gateway owned/live."""
        if self.test_mode:
            state = self._read_test_state()
            if (
                state.get(RUNTIME_LABEL) != "stopped"
                or state.get(OPENCLAW_LABEL) != "running"
            ):
                raise RuntimeError("source maintenance service boundary is invalid")
            return
        deadline = time.monotonic() + SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS
        if (
            self._launchctl_loaded(RUNTIME_LABEL, self._proof_remaining(deadline))
            or self._listener_pids(RUNTIME_LABEL, self._proof_remaining(deadline))
            or not self._managed_openclaw_liveness()
        ):
            raise RuntimeError("source maintenance service boundary is invalid")

    def start_maintenance_gateway(self) -> None:
        """Start only the already installed Gateway; never install a selector."""
        path = self._validate_plist(OPENCLAW_LABEL)
        if self.test_mode:
            state = self._read_test_state()
            if state.get(RUNTIME_LABEL) != "stopped":
                raise RuntimeError("source maintenance requires stopped runtime")
            self._write_test_state({**state, OPENCLAW_LABEL: "running"})
            self.assert_maintenance_boundary()
            return
        self._stop_label(RUNTIME_LABEL)
        if not self._launchctl_loaded(OPENCLAW_LABEL):
            result = self._process_probe(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS,
            )
            if result.returncode:
                raise RuntimeError("maintenance Gateway could not start")
        if not self._launchctl_definition_matches(OPENCLAW_LABEL, path):
            raise RuntimeError("maintenance Gateway definition changed")
        if not self._wait_for_openclaw_liveness():
            raise RuntimeError("maintenance Gateway is not owned and live")
        self.assert_maintenance_boundary()

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
            # A reviewed historical replay can temporarily block deployment
            # readiness even though safe reads, event ingest, and observation
            # containment are intact. Keep that exact state distinguishable
            # from a structural startup failure so the upgrade controller can
            # enter its bounded source-settlement loop.
            if _source_settlement_only_blocked_readiness(data):
                return "degraded"
            return "failed" if data.get("status") == "blocked" else "degraded"
        if data.get("status") == "healthy":
            return "healthy"
        if data.get("status") == "observation_only":
            return "observation_only"
        return "degraded"

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
        except urllib.error.HTTPError as exc:
            # The readiness route intentionally returns HTTP 503 with its
            # content-free diagnostics contract while activation is blocked.
            # Preserve that body so the updater can distinguish a reviewed
            # source-settlement gap from a structural degradation. Other HTTP
            # failures remain unavailable and fail closed.
            if exc.code != 503:
                exc.close()
                return None
            try:
                data = json.loads(exc.read())
            except (OSError, json.JSONDecodeError):
                return None
            finally:
                exc.close()
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
            try:
                remaining_listener = any(
                    self._listener_pids(label, SERVICE_PROCESS_PROBE_TIMEOUT_SECONDS)
                    for label in (RUNTIME_LABEL, OPENCLAW_LABEL)
                )
            except RuntimeError:
                remaining_listener = True
            if remaining_listener:
                return ServiceStatus(
                    "failed",
                    "unknown",
                    "unknown",
                    observation,
                    "unloaded services retain a listener or ownership is unproven",
                )
            return ServiceStatus(
                "stopped",
                "stopped",
                "stopped",
                observation,
                "both services and listeners are absent",
            )
        runtime = self._runtime_health() if runtime_loaded else "stopped"
        openclaw = (
            ("running" if self._managed_openclaw_liveness() else "degraded")
            if openclaw_loaded
            else "stopped"
        )
        if runtime in {"healthy", "observation_only"} and openclaw == "running":
            overall = "healthy" if runtime == "healthy" else "healthy"
        elif runtime == "failed":
            overall = "failed"
        else:
            overall = "degraded"
        return ServiceStatus(
            overall, runtime, openclaw, observation, "live launchd and loopback health"
        )
