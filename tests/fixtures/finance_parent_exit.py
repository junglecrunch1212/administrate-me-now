"""Hosted Linux ownership proof; only known descendants/pipe handles are used."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def parent(mode):
    from adminme.finance.worker import FinanceWorker

    child = FinanceWorker("parent-exit", timeout=10)
    print(json.dumps({"worker_pid": child.process.pid}), flush=True)
    child.send({"operation": "apply_stream"})
    if mode == "blocked_output":
        import array
        import fcntl
        import termios
        import struct
        import io

        raw = json.dumps(
            {"synthetic": "x" * 524288}, separators=(",", ":"), sort_keys=True
        )
        digest = hashlib.sha256(raw.encode()).hexdigest()
        child.send(
            {
                "operation": "apply",
                "envelope": {
                    "event_id": "synthetic-event",
                    "tenant_id": "synthetic-tenant",
                    "owner_scope": "private:synthetic-owner",
                    "visibility_scope": "private:synthetic-owner",
                    "sensitivity": "privileged",
                    "actor_identity": "synthetic-owner",
                    "source_account_id": "synthetic-book",
                    "type": "finance.provenance_archived",
                },
            }
        )
        child.send_bytes(
            json.dumps(
                {
                    "owner_id": "synthetic-owner",
                    "book_id": "synthetic-book",
                    "root_sha256": digest,
                    "content_sha256": digest,
                    "kind": "evidence",
                    "canonical_json": raw,
                    "approved_evidence": [],
                }
            ).encode()
        )
        assert child.receive()["kind"] == "sql"
        child.send({"kind": "rows", "rows": []})
        # Identify the large INSERT frame without consuming its body. A pipe
        # can block a large write before every byte slot is occupied, because
        # its page/atomic-write constraints differ from F_GETPIPE_SZ capacity.
        fd = child.process.stdout.fileno()
        capacity = fcntl.fcntl(fd, fcntl.F_GETPIPE_SZ)
        while True:
            header = child.process.stdout.read(4)
            assert len(header) == 4, "child ended before the large output frame"
            declared = struct.unpack("!I", header)[0]
            if declared > capacity + io.DEFAULT_BUFFER_SIZE:
                break
            message = json.loads(child.process.stdout.read(declared))
            assert message["kind"] == "usage", message
        deadline = time.monotonic() + 3
        previous, stable_since = None, None
        while time.monotonic() < deadline:
            available = array.array("i", [0])
            fcntl.ioctl(fd, termios.FIONREAD, available, True)
            assert child.process.poll() is None, "child exited before parent"
            if available[0] > 0 and available[0] == previous:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 0.1:
                    print(
                        json.dumps(
                            {
                                "blocked_output": True,
                                "declared_frame_bytes": declared,
                                "pipe_capacity_bytes": capacity,
                                "unread_pipe_bytes": available[0],
                            }
                        ),
                        flush=True,
                    )
                    os._exit(0)
            else:
                stable_since = None
            previous = available[0]
            time.sleep(0.01)
        raise AssertionError("owned large output frame did not stall without a reader")
    print(json.dumps({"blocked_input": True}), flush=True)
    os._exit(0)  # Deliberately bypass FinanceWorker.close and its timer.


def supervisor(mode):
    # Set adoption only on this fresh, disposable supervisor process. It never
    # reads a process table or modifies the pytest runner/host's process policy.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER
        raise RuntimeError("owned subreaper unavailable")
    child_pid = None
    parent_process = subprocess.Popen(
        [sys.executable, "-I", "-B", __file__, "parent", mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    try:
        out, error = parent_process.communicate(timeout=8)
        records = [json.loads(line) for line in out.splitlines()]
        child_pid = records[0]["worker_pid"]
        assert parent_process.returncode == 0, error.decode()[:1000]
        assert records[-1][mode] is True
        started = time.monotonic()
        while time.monotonic() - started < 5:
            found, status = os.waitpid(child_pid, os.WNOHANG)
            if found:
                child_pid = None
                print(
                    json.dumps(
                        {
                            "mode": mode,
                            "parent_evidence": records[-1],
                            "reaped": True,
                            "exit_status": status,
                            "seconds_after_parent_exit": time.monotonic() - started,
                        }
                    )
                )
                return
            time.sleep(0.01)
        raise AssertionError("owned Finance child survived abrupt parent exit")
    finally:
        if parent_process.poll() is None:
            parent_process.kill()
            out, _ = parent_process.communicate(timeout=3)
            if child_pid is None and out:
                child_pid = json.loads(out.splitlines()[0])["worker_pid"]
        if child_pid is not None:
            # PID belongs to this supervisor's unreaped adopted child, so it
            # cannot have been reused. No other PID is inspected or signalled.
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(child_pid, 0)


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    (parent if sys.argv[1] == "parent" else supervisor)(sys.argv[2])
