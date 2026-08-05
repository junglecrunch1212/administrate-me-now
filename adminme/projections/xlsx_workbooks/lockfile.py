"""
File-level locking helper for xlsx workbooks.

Per ADMINISTRATEME_BUILD.md §3.11 step 1: acquire a file-level lock on the
workbook before regenerating so the reverse daemon does not race the
forward daemon. A sidecar ``.lock`` file per workbook holds the flock.

``fcntl.flock`` is advisory only — both daemons must cooperate. Prompt 07c
acquires the same lock in reverse.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _try_lock(fd: object) -> None:
    if os.name == "nt":
        fd.seek(0)  # type: ignore[attr-defined]
        if fd.read(1) == "":  # type: ignore[attr-defined]
            fd.write("\0")  # type: ignore[attr-defined]
            fd.flush()  # type: ignore[attr-defined]
        fd.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[name-defined,attr-defined]
    else:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[name-defined,attr-defined]


def _unlock(fd: object) -> None:
    if os.name == "nt":
        fd.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[name-defined,attr-defined]
    else:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)  # type: ignore[name-defined,attr-defined]


@contextmanager
def acquire_workbook_lock(
    lock_path: Path, *, timeout_s: float = 10.0
) -> Iterator[None]:
    """Acquire an exclusive advisory lock on ``lock_path``.

    Creates the lock file if missing. Polls every 100ms until the timeout
    is reached. Raises ``TimeoutError`` if the lock cannot be acquired in
    time.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    try:
        fd = open(lock_path, "a+")
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                _try_lock(fd)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire lock on {lock_path} within {timeout_s}s"
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        if fd is not None:
            fd.close()
