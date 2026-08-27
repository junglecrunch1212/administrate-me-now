"""Lease-owning guardian for one bounded release-preparation command."""

from __future__ import annotations

import fcntl
import os
import select
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn


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


def _retain_unknown_terminality() -> NoReturn:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while True:
        time.sleep(3600)


def main() -> int:
    if len(sys.argv) < 14 or sys.argv[12] != "--":
        raise SystemExit("invalid preparation-worker invocation")
    activity = Path(sys.argv[1])
    inherited_descriptor = int(sys.argv[2])
    ready_descriptor = int(sys.argv[3])
    controller_descriptor = int(sys.argv[4])
    expected_coordinate = (int(sys.argv[5]), int(sys.argv[6]))
    lifecycle_path = Path(sys.argv[7])
    lifecycle_coordinate = (int(sys.argv[8]), int(sys.argv[9]))
    operation_lock_descriptor = int(sys.argv[10])
    timeout_seconds = float(sys.argv[11])
    command = sys.argv[13:]
    if timeout_seconds <= 0:
        raise SystemExit("invalid preparation-worker timeout")
    inherited_metadata = os.fstat(inherited_descriptor)
    descriptor = os.open(
        activity,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    metadata = os.fstat(descriptor)
    public = activity.lstat()
    if (
        activity.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (metadata.st_dev, metadata.st_ino) != (public.st_dev, public.st_ino)
        or (metadata.st_dev, metadata.st_ino) != expected_coordinate
        or (inherited_metadata.st_dev, inherited_metadata.st_ino) != expected_coordinate
    ):
        raise SystemExit("unsafe preparation activity lease")
    fcntl.flock(descriptor, fcntl.LOCK_SH)
    os.close(inherited_descriptor)
    os.set_inheritable(descriptor, True)
    lifecycle_descriptor = os.open(
        lifecycle_path,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    lifecycle_metadata = os.fstat(lifecycle_descriptor)
    lifecycle_public = lifecycle_path.lstat()
    operation_lock_metadata = os.fstat(operation_lock_descriptor)
    if (
        lifecycle_path.is_symlink()
        or not stat.S_ISREG(lifecycle_metadata.st_mode)
        or lifecycle_metadata.st_uid != os.getuid()
        or lifecycle_metadata.st_nlink != 1
        or stat.S_IMODE(lifecycle_metadata.st_mode) != 0o600
        or (lifecycle_metadata.st_dev, lifecycle_metadata.st_ino)
        != lifecycle_coordinate
        or (lifecycle_public.st_dev, lifecycle_public.st_ino) != lifecycle_coordinate
        or not stat.S_ISREG(operation_lock_metadata.st_mode)
        or operation_lock_metadata.st_uid != os.getuid()
        or operation_lock_metadata.st_nlink != 1
        or stat.S_IMODE(operation_lock_metadata.st_mode) != 0o600
    ):
        os.close(lifecycle_descriptor)
        raise SystemExit("unsafe controller lifecycle boundary")
    fcntl.flock(lifecycle_descriptor, fcntl.LOCK_SH)
    os.set_inheritable(lifecycle_descriptor, True)
    os.set_inheritable(operation_lock_descriptor, True)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            start_new_session=True,
            pass_fds=(descriptor, lifecycle_descriptor, operation_lock_descriptor),
        )
        os.write(ready_descriptor, b"1")
        os.close(ready_descriptor)
        ready_descriptor = -1
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if not _terminate_group(process):
                    _retain_unknown_terminality()
                return 75
            readable, _writable, _exceptional = select.select(
                [controller_descriptor],
                [],
                [],
                min(0.1, remaining),
            )
            if readable and os.read(controller_descriptor, 1) == b"":
                if not _terminate_group(process):
                    _retain_unknown_terminality()
                return 74
        return_code = process.returncode
        if _process_group_exists(process.pid):
            if not _terminate_group(process):
                _retain_unknown_terminality()
            return 73
        return int(return_code or 0)
    except BrokenPipeError:
        if process is not None and _process_group_exists(process.pid):
            if not _terminate_group(process):
                _retain_unknown_terminality()
        return 77
    except BaseException:
        if process is not None and _process_group_exists(process.pid):
            if not _terminate_group(process):
                _retain_unknown_terminality()
        raise
    finally:
        if ready_descriptor >= 0:
            os.close(ready_descriptor)
        os.close(controller_descriptor)
        os.close(lifecycle_descriptor)
        os.close(operation_lock_descriptor)
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
