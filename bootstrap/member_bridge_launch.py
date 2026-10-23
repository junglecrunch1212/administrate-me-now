"""Sealed helper-owned launch lifecycle for a compatible retained bridge.

No daemon or installed-runtime source is replaced. This adapter preserves the
retained installer's configuration transaction, but waits for launchd unload
and a stable new process. An uncertain child is never followed by configuration
rollback or another start. Exceptions and the pending marker are content-free.
Importing the module does not import provider code or third-party packages.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import importlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import time
from typing import Any, Iterator


_LABEL = "com.administrateme.member-bridge"
_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_WAIT_SECONDS = 12.0
_POLL_SECONDS = 0.2
_COMMAND_SECONDS = 5.0
CODES = frozenset(
    {
        "launch_binding_mismatch",
        "launch_config_mismatch",
        "launch_not_quiescent",
        "launch_state_unknown",
        "launch_stop_unknown",
        "launch_start_unknown",
        "launch_recovery_pending",
        "launch_rollback_failed",
        "launch_start_failed",
        "launch_busy",
    }
)


class LaunchFailure(RuntimeError):
    def __init__(self, code: str, *, unknown: bool = False) -> None:
        if code not in CODES:
            code = "launch_state_unknown"
            unknown = True
        super().__init__(code)
        self.code = code
        self.unknown = unknown


@dataclass(frozen=True)
class LaunchState:
    present: bool
    pid: int | None = None
    running: bool = False
    runs: int | None = None


def _command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise LaunchFailure("launch_state_unknown", unknown=True) from exc
    if len(result.stdout) + len(result.stderr) > 8 * 1024 * 1024:
        raise LaunchFailure("launch_state_unknown", unknown=True)
    return result


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _target() -> str:
    return f"{_domain()}/{_LABEL}"


def _owned(path: Path, *, directory: bool = False, private: bool = False) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        metadata.st_uid == os.getuid()
        and (
            stat.S_ISDIR(metadata.st_mode)
            if directory
            else stat.S_ISREG(metadata.st_mode)
        )
        and metadata.st_mode & (0o077 if private else 0o022) == 0
    )


def _expected_plist(installer: Any) -> dict[str, Any]:
    paths = installer.paths
    return {
        "Label": _LABEL,
        "ProgramArguments": [
            str(installer.root / ".venv" / "bin" / "python"),
            "-m",
            "adminme_bridge.main",
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "EnvironmentVariables": {
            "ADMINME_BRIDGE_CONFIG": str(paths.config_path),
            "PATH": _PATH,
        },
        "StandardOutPath": str(paths.log_dir / "bridge.log"),
        "StandardErrorPath": str(paths.log_dir / "bridge-error.log"),
    }


def _validate_owned(installer: Any) -> dict[str, Any]:
    paths = installer.paths
    expected = _expected_plist(installer)
    if (
        paths.root != paths.root.resolve()
        or installer.root != installer.root.resolve()
        or paths.config_path != paths.root / "config.yaml"
        or paths.launch_agent_path.name != f"{_LABEL}.plist"
        or not _owned(paths.root, directory=True, private=True)
        or not _owned(installer.root, directory=True)
        or not _owned(paths.launch_agent_path.parent, directory=True)
        or not _owned(paths.log_dir, directory=True, private=True)
        or not _owned(paths.config_path, private=True)
        or not _owned(paths.launch_agent_path, private=True)
        or not Path(expected["ProgramArguments"][0]).is_file()
        or any(
            ((paths.log_dir / name).exists() or (paths.log_dir / name).is_symlink())
            and not _owned(paths.log_dir / name)
            for name in ("bridge.log", "bridge-error.log")
        )
    ):
        raise LaunchFailure("launch_binding_mismatch")
    try:
        actual = plistlib.loads(paths.launch_agent_path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise LaunchFailure("launch_binding_mismatch") from exc
    if actual != expected:
        raise LaunchFailure("launch_binding_mismatch")
    return expected


def _configuration(installer: Any) -> tuple[bytes, dict[str, Any]]:
    # PyYAML is the retained runtime's existing parser. Do not create another
    # YAML interpretation at this boundary, or import any provider adapter.
    if not _owned(installer.paths.config_path, private=True):
        raise LaunchFailure("launch_binding_mismatch")
    try:
        raw = installer.paths.config_path.read_bytes()
        value = importlib.import_module("yaml").safe_load(raw)
    except Exception as exc:
        raise LaunchFailure("launch_config_mismatch") from exc
    if not isinstance(value, dict):
        raise LaunchFailure("launch_config_mismatch")
    for field in ("requested_packs", "configured_packs", "enabled_packs"):
        items = value.get(field)
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not item for item in items)
            or len(items) != len(set(items))
        ):
            raise LaunchFailure("launch_config_mismatch")
    if (
        not set(value["enabled_packs"]) <= set(value["configured_packs"])
        or not set(value["configured_packs"]) <= set(value["requested_packs"])
        or not isinstance(value.get("pack_config"), dict)
        or set(value["pack_config"]) != set(value["configured_packs"])
        or value.get("provider_writes") != "deny"
        or value.get("observation_only") is not True
    ):
        raise LaunchFailure("launch_config_mismatch")
    return raw, value


def _pending_path(installer: Any) -> Path:
    return installer.paths.root / "launch-recovery-pending.json"


def _require_no_pending(installer: Any) -> None:
    if _pending_path(installer).exists() or _pending_path(installer).is_symlink():
        raise LaunchFailure("launch_recovery_pending", unknown=True)


def _atomic_write(path: Path, raw: bytes) -> None:
    # Use exactly the retained runtime's durable writer; this is not a state or
    # credential migration, and does not change its receipt/history behavior.
    importlib.import_module("bootstrap.contracts").atomic_write(path, raw)


def _mark_unknown(installer: Any, code: str) -> None:
    try:
        _atomic_write(
            _pending_path(installer),
            json.dumps(
                {"contract": "member-bridge-launch-pending-v1", "code": code}
            ).encode("utf-8"),
        )
    except Exception as exc:
        # Callers must still never restore the old configuration or restart.
        raise LaunchFailure("launch_recovery_pending", unknown=True) from exc


def _top_fields(output: str) -> dict[str, str]:
    """Extract launchctl's top-level scalar fields, never environment contents."""
    fields: dict[str, str] = {}
    depth = 0
    for line in output.splitlines():
        stripped = line.strip()
        if depth == 1 and " = " in stripped and not stripped.endswith("{"):
            key, value = stripped.split(" = ", 1)
            if key in fields:
                raise LaunchFailure("launch_state_unknown", unknown=True)
            fields[key] = value
        if stripped.endswith("{"):
            depth += 1
        elif stripped == "}":
            depth -= 1
            if depth < 0:
                raise LaunchFailure("launch_state_unknown", unknown=True)
    if depth != 0:
        raise LaunchFailure("launch_state_unknown", unknown=True)
    return fields


def _top_block(output: str, name: str) -> list[str]:
    depth = 0
    capture = False
    values: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if depth == 1 and stripped == name + " = {":
            if capture or values:
                raise LaunchFailure("launch_state_unknown", unknown=True)
            capture = True
        elif capture and depth == 2 and stripped != "}":
            values.append(stripped)
        if stripped.endswith("{"):
            depth += 1
        elif stripped == "}":
            if capture and depth == 2:
                capture = False
            depth -= 1
    return values


def _disabled_override() -> None:
    result = _command(["/bin/launchctl", "print-disabled", _domain()])
    if result.returncode:
        raise LaunchFailure("launch_state_unknown", unknown=True)
    values = re.findall(
        r'^\s*"' + re.escape(_LABEL) + r'"\s*=>\s*(\w+)', result.stdout, re.M
    )
    if values and values != ["false"]:
        raise LaunchFailure("launch_not_quiescent")


def launch_probe(installer: Any) -> LaunchState:
    """Read one owned job without treating a failed print as confirmed absence."""
    expected = _validate_owned(installer)
    result = _command(["/bin/launchctl", "print", _target()])
    if result.returncode:
        if result.returncode != 113 or "Could not find service" not in result.stderr:
            raise LaunchFailure("launch_state_unknown", unknown=True)
        # A missing GUI domain can also look absent; do not bootstrap blindly.
        domain = _command(["/bin/launchctl", "print", _domain()])
        if domain.returncode:
            raise LaunchFailure("launch_state_unknown", unknown=True)
        return LaunchState(False)
    fields = _top_fields(result.stdout)
    if (
        fields.get("path") != str(installer.paths.launch_agent_path)
        or fields.get("program") != expected["ProgramArguments"][0]
        or fields.get("stdout path") != expected["StandardOutPath"]
        or fields.get("stderr path") != expected["StandardErrorPath"]
        or _top_block(result.stdout, "arguments") != expected["ProgramArguments"]
        or "ADMINME_BRIDGE_CONFIG => " + str(installer.paths.config_path)
        not in _top_block(result.stdout, "environment")
    ):
        raise LaunchFailure("launch_binding_mismatch")
    raw_pid = fields.get("pid")
    if raw_pid is not None and (not raw_pid.isdecimal() or int(raw_pid) <= 0):
        raise LaunchFailure("launch_state_unknown", unknown=True)
    pid = int(raw_pid) if raw_pid is not None else None
    raw_runs = fields.get("runs")
    if raw_runs is not None and not raw_runs.isdecimal():
        raise LaunchFailure("launch_state_unknown", unknown=True)
    runs = int(raw_runs) if raw_runs is not None else None
    return LaunchState(
        True, pid, fields.get("state") == "running" and pid is not None, runs
    )


def _daemon_pids() -> set[int]:
    result = _command(["/bin/ps", "-Aww", "-o", "pid=,uid=,command="])
    if result.returncode:
        raise LaunchFailure("launch_state_unknown", unknown=True)
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        if not re.search(r"(?:^|\s)-m\s+adminme_bridge\.main(?:\s|$)", line):
            continue
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdecimal() or not fields[1].isdecimal():
            raise LaunchFailure("launch_state_unknown", unknown=True)
        if int(fields[1]) == os.getuid():
            pids.add(int(fields[0]))
    return pids


def check_initial_start_quiescence(installer: Any, packs: list[str] | set[str]) -> None:
    """Require the exact initial-start recovery ceiling before any side effect."""
    _require_no_pending(installer)
    _raw, config = _configuration(installer)
    if (
        not packs
        or len(packs) != len(set(packs))
        or not set(packs) <= set(config["requested_packs"])
        or config["enabled_packs"]
    ):
        raise LaunchFailure("launch_not_quiescent")
    if installer.test_mode:
        # Isolated composition uses the retained installer's synthetic service.
        if installer.status().get("service") != "stopped":
            raise LaunchFailure("launch_not_quiescent")
        return
    _validate_owned(installer)
    _disabled_override()
    if launch_probe(installer).present or _daemon_pids():
        raise LaunchFailure("launch_not_quiescent")


@contextmanager
def _transaction(installer: Any) -> Iterator[None]:
    path = installer.paths.root / "launch-lifecycle.lock"
    if not _owned(installer.paths.root, directory=True, private=True):
        raise LaunchFailure("launch_binding_mismatch")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise LaunchFailure("launch_busy") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise LaunchFailure("launch_binding_mismatch")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LaunchFailure("launch_busy") from exc
        yield
    finally:
        os.close(descriptor)


def _stop(installer: Any) -> None:
    _validate_owned(installer)
    try:
        state = launch_probe(installer)
        if not state.present and not _daemon_pids():
            return
        command_unknown = False
        if state.present:
            # One unload, followed by read-only settlement. A command timeout
            # is not proof of failure; only confirmed absence permits rollback.
            try:
                _command(["/bin/launchctl", "bootout", _target()])
            except LaunchFailure:
                command_unknown = True
        deadline = time.monotonic() + _WAIT_SECONDS
        while time.monotonic() < deadline:
            if not launch_probe(installer).present and not _daemon_pids():
                if command_unknown:
                    # An in-flight unload could remove a later replacement.
                    # Physical absence alone cannot authorize a new start.
                    raise LaunchFailure("launch_stop_unknown", unknown=True)
                return
            time.sleep(_POLL_SECONDS)
    except LaunchFailure as exc:
        _mark_unknown(installer, "launch_stop_unknown")
        raise LaunchFailure("launch_stop_unknown", unknown=True) from exc
    _mark_unknown(installer, "launch_stop_unknown")
    raise LaunchFailure("launch_stop_unknown", unknown=True)


def _cleanup_failed_start(installer: Any) -> None:
    try:
        _stop(installer)
    except LaunchFailure as exc:
        _mark_unknown(installer, "launch_start_unknown")
        raise LaunchFailure("launch_start_unknown", unknown=True) from exc
    raise LaunchFailure("launch_start_failed")


def _start_command(
    installer: Any, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    try:
        return _command(arguments)
    except LaunchFailure as exc:
        # launchctl can time out after submitting its IPC. Even a currently
        # absent job cannot rule out a later registration. Clean up what is
        # visible, retain the new config, and never turn this into a retry.
        try:
            _stop(installer)
        except LaunchFailure:
            pass
        _mark_unknown(installer, "launch_start_unknown")
        raise LaunchFailure("launch_start_unknown", unknown=True) from exc


def _start(installer: Any) -> None:
    _require_no_pending(installer)
    _validate_owned(installer)
    _configuration(installer)
    _disabled_override()
    # This is also used to restore previously running siblings after a known
    # failure. It never performs a second bootout when already stopped.
    _stop(installer)
    try:
        result = _start_command(
            installer,
            [
                "/bin/launchctl",
                "bootstrap",
                _domain(),
                str(installer.paths.launch_agent_path),
            ],
        )
        if result.returncode:
            _cleanup_failed_start(installer)
        state = launch_probe(installer)
        if not state.present:
            _cleanup_failed_start(installer)
        if state.pid is None:
            # Never retry a process which may already have run. A first run is
            # the only case in which one kickstart can resolve a pending load.
            if state.runs != 0:
                _cleanup_failed_start(installer)
            # RunAtLoad usually starts it. A single non-restarting kickstart is
            # safe if the job exists but has no process; never use -k here.
            result = _start_command(
                installer, ["/bin/launchctl", "kickstart", _target()]
            )
            if result.returncode:
                _cleanup_failed_start(installer)
        deadline = time.monotonic() + _WAIT_SECONDS
        stable_pid: int | None = None
        stable_reads = 0
        while time.monotonic() < deadline:
            state = launch_probe(installer)
            if not state.present:
                _cleanup_failed_start(installer)
            if state.running:
                if state.pid == stable_pid:
                    stable_reads += 1
                else:
                    stable_pid, stable_reads = state.pid, 1
                if stable_reads >= 3:
                    if _daemon_pids() != {state.pid}:
                        _cleanup_failed_start(installer)
                    return
            else:
                stable_pid, stable_reads = None, 0
            time.sleep(_POLL_SECONDS)
    except LaunchFailure as exc:
        if exc.code in {"launch_start_failed", "launch_start_unknown"}:
            raise
        if exc.unknown:
            try:
                _stop(installer)
            except LaunchFailure:
                pass
            _mark_unknown(installer, "launch_start_unknown")
            raise LaunchFailure("launch_start_unknown", unknown=True) from exc
        _cleanup_failed_start(installer)
    _cleanup_failed_start(installer)


def compatible_installer_class(base_class: type[Any]) -> type[Any]:
    """Adapt only launch lifecycle; retain constructor, proof and receipt seams."""

    class CompatibleInstaller(base_class):  # type: ignore[misc, valid-type]
        def verify_initial_start_recovery(self, packs: list[str] | set[str]) -> None:
            check_initial_start_quiescence(self, packs)

        def _stop(self) -> None:
            if self.test_mode:
                super()._stop()
            else:
                _stop(self)

        def _start(self) -> None:
            if self.test_mode:
                super()._start()
            else:
                _start(self)

        def enable_pack(self, pack: str) -> dict[str, Any]:
            _require_no_pending(self)
            if self.test_mode:
                return super().enable_pack(pack)
            with _transaction(self):
                _require_no_pending(self)
                _validate_owned(self)
                raw, value = _configuration(self)
                if pack not in value["configured_packs"]:
                    raise LaunchFailure("launch_config_mismatch")
                if pack in value["enabled_packs"]:
                    return {
                        "status": "enabled-awaiting-central-attestation",
                        "pack": pack,
                        "idempotent": True,
                    }
                was_running = launch_probe(self).present
                # Stop first, before changing enabled packs. An uncertain stop
                # leaves the original configuration untouched and blocks retry.
                self._stop()
                value["enabled_packs"] = sorted([*value["enabled_packs"], pack])
                try:
                    _atomic_write(
                        self.paths.config_path,
                        importlib.import_module("yaml")
                        .safe_dump(value, sort_keys=True)
                        .encode("utf-8"),
                    )
                    self._start()
                except Exception as exc:
                    if isinstance(exc, LaunchFailure) and exc.unknown:
                        _mark_unknown(self, exc.code)
                        raise
                    # Even an unexpected write/start failure must settle the
                    # child before restoring the prior bytes. Never suppress
                    # failed sibling restoration as the retained code did.
                    try:
                        self._stop()
                        _atomic_write(self.paths.config_path, raw)
                        if was_running:
                            self._start()
                    except Exception as recovery_error:
                        _mark_unknown(self, "launch_rollback_failed")
                        raise LaunchFailure(
                            "launch_rollback_failed", unknown=True
                        ) from recovery_error
                    if isinstance(exc, LaunchFailure):
                        raise
                    raise LaunchFailure("launch_start_failed") from exc
                return {
                    "status": "enabled-awaiting-central-attestation",
                    "pack": pack,
                    "provider_capability_state": "not_attested",
                    "central_provider_authority": False,
                }

    return CompatibleInstaller
