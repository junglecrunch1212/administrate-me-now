"""Deny worker Python I/O outside trusted code and self metrics.

This is an audited Python boundary, not an OS sandbox. Native extensions are
trusted pinned dependencies. No credentials, database handles or file paths
from the package are admitted here.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


class CalculationGuard:
    def __init__(self):
        self.fault_count = 0
        self.read_roots = (
            Path(__file__).resolve().parents[2],
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            Path("/usr/share/zoneinfo").resolve(),
            Path("/var/db/timezone/zoneinfo").resolve(),
            Path("/System/Library").resolve(),
            Path("/usr/lib").resolve(),
        )
        self.read_files = frozenset(
            Path(p).resolve()
            for p in (
                "/etc/localtime",
                "/private/etc/localtime",
                "/dev/urandom",
                "/proc/self/statm",
            )
        )

    def deny(self):
        self.fault_count += 1
        raise PermissionError("finance_worker_io_denied")

    def __call__(self, event, args):
        if event.startswith("socket.") or event in {
            "subprocess.Popen",
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.exec",
            "os.system",
            "os.remove",
            "os.rename",
            "os.mkdir",
            "os.rmdir",
            "os.link",
            "os.symlink",
            "os.chmod",
            "os.chown",
            "os.truncate",
            "os.chdir",
            "os.putenv",
            "os.unsetenv",
        }:
            self.deny()
        if event == "open":
            if not args or not isinstance(args[0], (str, bytes, os.PathLike)):
                self.deny()
            path = Path(os.fsdecode(args[0])).resolve()
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            ):
                self.deny()
            if path not in self.read_files and not any(
                path.is_relative_to(root) for root in self.read_roots
            ):
                self.deny()
