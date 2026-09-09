"""Controller-liveness guardian for one bounded fixed-commit update shell."""

from __future__ import annotations

import math
import os
import select
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn


CONTROLLER_TIMEOUT_SECONDS = 13 * 60 * 60


def _deployment_clock() -> float:
    # Match the supervisor and deployment_budget clock without package imports.
    # Older macOS system Python gives time.monotonic() a process-local origin.
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def _controller_deadline(timeout_seconds: float, *, now: float) -> float:
    """Keep supervisor preparation and guardian launch inside the same budget."""
    if (
        not math.isfinite(now)
        or now < 0
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > CONTROLLER_TIMEOUT_SECONDS
    ):
        raise SystemExit("invalid update-controller guardian boundary")
    raw_start = os.environ.get("ADMINME_CONTROLLER_STARTED_MONOTONIC")
    raw_deadline = os.environ.get("ADMINME_CONTROLLER_DEADLINE_MONOTONIC")
    if raw_start is None and raw_deadline is None:
        if os.environ.get("ADMINME_UPDATE_SUPERVISED") == "1":
            raise SystemExit("missing supervised controller deadline")
        return now + timeout_seconds
    try:
        start = float(raw_start) if raw_start is not None else float("nan")
        deadline = float(raw_deadline) if raw_deadline is not None else float("nan")
    except ValueError as exc:
        raise SystemExit("invalid supervised controller deadline") from exc
    if (
        not math.isfinite(start)
        or not math.isfinite(deadline)
        or start < 0
        or start > now
        or deadline <= now
        or not math.isclose(deadline - start, timeout_seconds, rel_tol=0, abs_tol=0.001)
        or deadline > now + timeout_seconds
    ):
        raise SystemExit("expired or invalid supervised controller deadline")
    return deadline


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_group(process: subprocess.Popen[bytes]) -> bool:
    for signal_value, timeout in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 30.0)):
        process.poll()
        if not _process_group_exists(process.pid):
            break
        try:
            os.killpg(process.pid, signal_value)
        except ProcessLookupError:
            break
        except PermissionError:
            process.poll()
            if not _process_group_exists(process.pid):
                break
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process.poll()
            if not _process_group_exists(process.pid):
                break
            time.sleep(0.02)
    if process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    return not _process_group_exists(process.pid)


def _prove_lifecycle_exclusive(descriptor: int, *, timeout: float = 40.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            time.sleep(0.02)
        except OSError:
            return False
    return False


def _retain_unknown_terminality() -> NoReturn:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while True:
        time.sleep(3600)


def main() -> int:
    if len(sys.argv) < 11 or sys.argv[9] != "--":
        raise SystemExit("invalid update-controller guardian invocation")
    ready_descriptor = int(sys.argv[1])
    supervisor_descriptor = int(sys.argv[2])
    terminal_descriptor = int(sys.argv[3])
    operation_lock_descriptor = int(sys.argv[4])
    lifecycle_path = Path(sys.argv[5])
    lifecycle_coordinate = (int(sys.argv[6]), int(sys.argv[7]))
    timeout_seconds = float(sys.argv[8])
    command = sys.argv[10:]
    if not command:
        raise SystemExit("invalid update-controller guardian boundary")
    deadline = _controller_deadline(timeout_seconds, now=_deployment_clock())
    lock = os.fstat(operation_lock_descriptor)
    if (
        not stat.S_ISREG(lock.st_mode)
        or lock.st_uid != os.getuid()
        or lock.st_nlink != 1
        or stat.S_IMODE(lock.st_mode) != 0o600
    ):
        raise SystemExit("unsafe update operation lock")
    lifecycle_descriptor = os.open(
        lifecycle_path,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    lifecycle = os.fstat(lifecycle_descriptor)
    lifecycle_public = lifecycle_path.lstat()
    if (
        lifecycle_path.is_symlink()
        or not stat.S_ISREG(lifecycle.st_mode)
        or lifecycle.st_uid != os.getuid()
        or lifecycle.st_nlink != 1
        or stat.S_IMODE(lifecycle.st_mode) != 0o600
        or (lifecycle.st_dev, lifecycle.st_ino) != lifecycle_coordinate
        or (lifecycle_public.st_dev, lifecycle_public.st_ino) != lifecycle_coordinate
    ):
        os.close(lifecycle_descriptor)
        raise SystemExit("unsafe controller lifecycle lease")
    import fcntl

    fcntl.flock(lifecycle_descriptor, fcntl.LOCK_SH)
    # The controller must hold an independently opened lock description.  A
    # duplicated descriptor shares flock state with this guardian, so an EX
    # conversion could otherwise succeed while an escaped controller child
    # still retained that duplicate.
    controller_lifecycle_descriptor = os.open(
        lifecycle_path,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    controller_lifecycle = os.fstat(controller_lifecycle_descriptor)
    controller_lifecycle_public = lifecycle_path.lstat()
    if (
        controller_lifecycle.st_dev,
        controller_lifecycle.st_ino,
    ) != lifecycle_coordinate or (
        controller_lifecycle_public.st_dev,
        controller_lifecycle_public.st_ino,
    ) != lifecycle_coordinate:
        os.close(controller_lifecycle_descriptor)
        os.close(lifecycle_descriptor)
        raise SystemExit("controller lifecycle lease changed")
    fcntl.flock(controller_lifecycle_descriptor, fcntl.LOCK_SH)
    os.set_inheritable(operation_lock_descriptor, True)
    os.set_inheritable(controller_lifecycle_descriptor, True)
    process: subprocess.Popen[bytes] | None = None
    terminal_proven = False
    try:
        process = subprocess.Popen(
            command,
            start_new_session=True,
            pass_fds=(operation_lock_descriptor, controller_lifecycle_descriptor),
        )
        os.close(controller_lifecycle_descriptor)
        controller_lifecycle_descriptor = -1
        os.write(ready_descriptor, b"1")
        os.close(ready_descriptor)
        ready_descriptor = -1
        while process.poll() is None:
            remaining = deadline - _deployment_clock()
            if remaining <= 0:
                terminal_proven = _terminate_group(process)
                if not terminal_proven:
                    _retain_unknown_terminality()
                return 75
            readable, _writable, _exceptional = select.select(
                [supervisor_descriptor],
                [],
                [],
                min(0.1, remaining),
            )
            if readable and os.read(supervisor_descriptor, 1) == b"":
                terminal_proven = _terminate_group(process)
                if not terminal_proven:
                    _retain_unknown_terminality()
                return 74
        return_code = int(process.returncode or 0)
        if _process_group_exists(process.pid):
            terminal_proven = _terminate_group(process)
            if not terminal_proven:
                _retain_unknown_terminality()
            return 73
        terminal_proven = True
        return return_code
    except BaseException:
        if controller_lifecycle_descriptor >= 0:
            os.close(controller_lifecycle_descriptor)
            controller_lifecycle_descriptor = -1
        if process is not None and _process_group_exists(process.pid):
            if not _terminate_group(process):
                _retain_unknown_terminality()
        terminal_proven = True
        raise
    finally:
        if terminal_proven:
            terminal_proven = _prove_lifecycle_exclusive(lifecycle_descriptor)
        if terminal_proven:
            os.write(terminal_descriptor, b"1")
        if ready_descriptor >= 0:
            os.close(ready_descriptor)
        os.close(supervisor_descriptor)
        os.close(terminal_descriptor)
        if controller_lifecycle_descriptor >= 0:
            os.close(controller_lifecycle_descriptor)
        os.close(lifecycle_descriptor)
        os.close(operation_lock_descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
