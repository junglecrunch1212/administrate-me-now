"""Owned Linux supervisor for synthetic Finance evaluation; standard library only."""

from __future__ import annotations

# ruff: noqa: E402 -- start the owned clock before remaining stdlib imports.

import time

STARTED = time.monotonic()

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import threading

MIB = 1024 * 1024
LIMITS = {
    "owned_processes": 4,
    "runtime_executor_rss_bytes": 512 * MIB,
    "runtime_executor_cpu_seconds": 90,
    "whole_tree_rss_bytes": 1024 * MIB,
    "whole_tree_cpu_seconds": 180,
    "scratch_bytes": 512 * MIB,
}


def process_row(pid):
    try:
        value = Path(f"/proc/{pid}/stat").read_text()
        fields = value[value.rindex(")") + 2 :].split()
        hz = os.sysconf("SC_CLK_TCK")
        return {
            "pid": pid,
            "state": fields[0],
            "ppid": int(fields[1]),
            "cpu_seconds": (int(fields[11]) + int(fields[12])) / hz,
            "reaped_cpu_seconds": (int(fields[13]) + int(fields[14])) / hz,
            "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
            "start_ticks": int(fields[19]),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def tree_rows(root):
    rows, pending, visited = [], [root], set()
    while pending:
        pid = pending.pop()
        if pid in visited:
            continue
        visited.add(pid)
        row = process_row(pid)
        if row is None:
            continue
        rows.append(row)
        for task in Path(f"/proc/{pid}/task").glob("*"):
            try:
                pending.extend(int(p) for p in (task / "children").read_text().split())
            except (FileNotFoundError, ProcessLookupError):
                pass
    return rows


def sample(root, runtime_pid, controller_pid, paths, previous_runtime_cpu=0.0):
    rows = tree_rows(root)
    runtime_ids = {runtime_pid} if runtime_pid else set()
    for _ in rows:
        runtime_ids.update(r["pid"] for r in rows if r["ppid"] in runtime_ids)
    runtime = [r for r in rows if r["pid"] in runtime_ids]
    controller = next((r for r in rows if r["pid"] == controller_pid), None)
    runtime_cpu = max(
        previous_runtime_cpu,
        sum(r["cpu_seconds"] + r["reaped_cpu_seconds"] for r in runtime),
        controller["reaped_cpu_seconds"] if controller else 0,
    )
    disk = 0
    for root_path in paths:
        for path in root_path.rglob("*"):
            try:
                if path.is_file():
                    disk += path.stat().st_size
            except FileNotFoundError:
                pass
    return {
        "at": time.monotonic(),
        "pids": rows,
        "owned_processes": len(rows),
        "runtime_executor_rss_bytes": sum(r["rss_bytes"] for r in runtime),
        "runtime_executor_cpu_seconds": runtime_cpu,
        "whole_tree_rss_bytes": sum(r["rss_bytes"] for r in rows),
        "whole_tree_cpu_seconds": sum(
            r["cpu_seconds"] + r["reaped_cpu_seconds"] for r in rows
        ),
        "scratch_bytes": disk,
    }


def violations(row):
    return [key for key, bound in LIMITS.items() if row[key] > bound]


def mode_budget(mode):
    contract = mode.startswith("contract-")
    work, cleanup = (2.0, 4.0) if contract else (200.0, 30.0)
    grace, term, kill = (1.0, 1.0, 1.0) if contract else (15.0, 5.0, 5.0)
    limits = dict(LIMITS)
    if mode in {"staged-bounded", "owner-production"}:
        work = 330.0
        limits.update(runtime_executor_cpu_seconds=180, whole_tree_cpu_seconds=240)
    if mode == "summary-metadata":
        work, cleanup = 120.0, 5.0
        grace, term, kill = 0.5, 1.0, 1.0
        limits.update(
            runtime_executor_rss_bytes=1024 * MIB,
            runtime_executor_cpu_seconds=60,
            whole_tree_cpu_seconds=60,
        )
    return {
        "work": work,
        "cleanup": cleanup,
        "absolute": work + cleanup,
        "grace": grace,
        "term": term,
        "kill": kill,
        "limits": limits,
    }


class OwnerPhaseClock:
    """Validate owned same-host phase clocks independently of IPC polling delay."""

    def __init__(self, caps, origin):
        self.caps = caps
        self.index = 0
        self.active = self.started = None
        self.last_at = origin

    @property
    def complete(self):
        return self.index == len(self.caps)

    def accept(self, message, now):
        if set(message) != {"kind", "name", "edge", "at"} or message["kind"] != "phase":
            raise ValueError("phase_fields")
        at = message["at"]
        if (
            type(at) not in (int, float)
            or not math.isfinite(at)
            or not self.last_at <= at <= now
        ):
            raise ValueError("phase_clock")
        names = list(self.caps)
        if self.complete or message["name"] != names[self.index]:
            raise ValueError("phase_order")
        if message["edge"] == "start" and self.active is None:
            self.active, self.started = message["name"], at
        elif message["edge"] == "end" and self.active == message["name"]:
            if at - self.started > self.caps[self.active]:
                raise TimeoutError("phase_deadline:" + self.active)
            self.index += 1
            self.active = self.started = None
        else:
            raise ValueError("phase_edge")
        self.last_at = at

    def overdue(self, now):
        if self.active is not None and now - self.started > self.caps[self.active]:
            return self.active
        return None


def summary_cleanup_deadlines(absolute, finish_at, stop_at=None):
    """One earliest origin; faults can shorten escalation, never borrow reserves."""
    origin = min(finish_at, stop_at) if stop_at is not None else finish_at
    end = min(absolute, origin + 5.0)
    term_at = end - 3.0  # 2s natural exit, 1s TERM, 1s KILL/reap, 1s final reserve.
    if stop_at is not None:
        term_at = min(term_at, stop_at + 0.5)
    return {
        "origin": origin,
        "term": term_at,
        "kill": term_at + 1.0,
        "reserve": end - 1.0,
        "reap": end - 0.5,
        "end": end,
    }


def summary_cleanup_step(window, now, term_sent, kill_sent, signal_owned):
    """Use absolute phase deadlines; a missed phase cannot consume the next one."""
    if now >= window["reserve"]:
        signal_owned(signal.SIGKILL)
        return term_sent, True, True
    if now >= window["kill"]:
        if not kill_sent:
            signal_owned(signal.SIGKILL)
        return term_sent, True, False
    if now >= window["term"] and not term_sent:
        signal_owned(signal.SIGTERM)
        term_sent = True
    return term_sent, kill_sent, False


def summary_cleanup_complete(report, now):
    first = report.get("work_finished_at")
    start, finished = report.get("started"), report.get("finished")
    schedule = report.get("cleanup_schedule", {})
    end, term_at = schedule.get("end"), schedule.get("term")
    reaped = next(
        (
            row.get("at")
            for row in report.get("reaped", [])
            if row.get("pid") == report.get("controller_pid")
        ),
        None,
    )
    return (
        all(
            type(v) in (int, float) and math.isfinite(v)
            for v in (first, start, finished, end, term_at, reaped, now)
        )
        and start <= first <= start + 120
        and end == min(start + 125, first + 5)
        and term_at == end - 3
        and first <= reaped <= term_at
        and reaped <= finished <= now <= end
        and report.get("cleanup_seconds") == finished - first
        and 0 <= report["cleanup_seconds"] <= 5
        and report.get("elapsed_seconds") == finished - start
        and report["elapsed_seconds"] <= 125
        and report.get("absolute_bound_met") is True
        and report.get("forced") is False
        and report.get("faults") == []
        and type(report.get("controller_exit")) is int
        and report["controller_exit"] == 0
        and report.get("remaining_owned") == []
        and report.get("scratch_removed") is True
    )


def remove_scratch(scratch, *, retain_unknown, remaining_owned):
    if retain_unknown and remaining_owned:
        return False
    shutil.rmtree(scratch)
    return not scratch.exists()


def start_cleanup_sampler(mode, take_sample, emit, faults):
    stop = threading.Event()
    thread = None
    if mode in {"staged-bounded", "summary-metadata", "owner-production"}:

        def collect():
            try:
                while not stop.wait(0.05):
                    emit(take_sample())
            except BaseException as exc:
                faults.append("cleanup_sampler:" + type(exc).__name__)

        thread = threading.Thread(target=collect, daemon=True)
        thread.start()
    return stop, thread


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("watchdog_duplicate_key")
        result[key] = value
    return result


def notify(kind, **fields):
    fd = int(os.environ["FINANCE_WATCHDOG_FD"])
    data = (
        json.dumps(
            {"kind": kind, **fields}, separators=(",", ":"), allow_nan=False
        ).encode()
        + b"\n"
    )
    if len(data) > 2048 or os.write(fd, data) != len(data):
        raise ValueError("watchdog_control_write")
    if kind == "runtime":
        ack_fd = int(os.environ["FINANCE_WATCHDOG_ACK_FD"])
        ack = b""
        while not ack.endswith(b"\n") and len(ack) < 64:
            part = os.read(ack_fd, 1)
            if not part:
                raise ValueError("watchdog_ack_closed")
            ack += part
        if ack != b"runtime_bound\n":
            raise ValueError("watchdog_ack_invalid")


def main(mode, directory):
    if sys.platform != "linux" or mode not in {
        "full",
        "focused-import",
        "allocation-profile",
        "staged-bounded",
        "owner-production",
        "summary-metadata",
        "setup",
        "setup-stop-before",
        "setup-stop-packets",
        "setup-stop-released",
        "contracts",
        "contract-normal",
        "contract-hang",
        "contract-orphan",
        "contract-alarm",
        "contract-protocol",
    }:
        raise ValueError("watchdog_mode")
    root = Path(directory)
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or any(root.iterdir())
    ):
        raise ValueError("watchdog_fresh_root")
    # Ancestor subreaper owns descendants even after controller failure.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "watchdog_subreaper_required")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cpu_cap = 60 if mode == "summary-metadata" else 180
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_cap, cpu_cap))
    if mode == "summary-metadata":
        resource.setrlimit(resource.RLIMIT_AS, (1024 * MIB, 1024 * MIB))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * MIB, 8 * MIB))
    if mode == "owner-production":
        # Packet/SQLCipher files need a distinct cap from retained evidence.
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * MIB, 128 * MIB))
    contract = mode.startswith("contract-")
    budget = mode_budget(mode)
    work, cleanup = budget["work"], budget["cleanup"]
    grace, term, _kill = budget["grace"], budget["term"], budget["kill"]
    limits = budget["limits"]
    absolute = STARTED + work + cleanup
    scratch = root / "scratch"
    scratch.mkdir(mode=0o700)
    trace_file = (root / "watchdog-trace.jsonl").open("w", buffering=1)
    trace_lock = threading.Lock()

    def write_sample(row):
        with trace_lock:
            trace_file.write(json.dumps(row, separators=(",", ":")) + "\n")

    report = {
        "mode": mode,
        "candidate": os.environ.get("GITHUB_SHA"),
        "started": STARTED,
        "limits": limits,
        "work_cap_seconds": work,
        "cleanup_cap_seconds": cleanup,
        "absolute_stop_seconds": work + cleanup,
        "events": [],
        "reaped": [],
        "forced": False,
        "faults": [],
        "maxima": {k: 0 for k in limits},
        "subreaper": True,
        "qualification": False,
    }
    if mode == "summary-metadata":
        report["evidence_limit_bytes"] = 16 * MIB
        report["evidence_max_bytes"] = 0

    owner_phases = {}
    phase_clock = None
    if mode == "owner-production":
        from tests.fixtures.finance_owner_production import (
            PHASES,
            EVIDENCE_FILE_COUNT,
            FINAL_EVIDENCE_FILES,
        )

        owner_phases = PHASES
        phase_clock = OwnerPhaseClock(owner_phases, STARTED)
        report.update(
            phase_caps=owner_phases,
            phases_complete=False,
            evidence_file_limit_bytes=16 * MIB,
            evidence_limit_bytes=32 * MIB,
            inherited_file_limit_bytes=128 * MIB,
            evidence_max_bytes=0,
            evidence_file_count_limit=EVIDENCE_FILE_COUNT,
            reserved_final_evidence_files=sorted(FINAL_EVIDENCE_FILES),
            role_cpu_seconds={
                "controller": 180,
                "runtime": 180,
                "worker_child": 60,
                "runtime_executor_aggregate": 180,
                "whole_tree": 240,
            },
        )

    def write_owner_final(name, value):
        """Count/size final outputs before writing; never discard raw failures."""
        assert mode == "owner-production" and name in FINAL_EVIDENCE_FILES
        raw = (json.dumps(value, indent=2) + "\n").encode()
        files = {p.name: p.stat().st_size for p in root.iterdir() if p.is_file()}
        if len(set(files) | FINAL_EVIDENCE_FILES) > EVIDENCE_FILE_COUNT:
            raise ValueError("evidence_file_count_cap")
        if (
            len(raw) > 16 * MIB
            or sum(files.values()) - files.get(name, 0) + len(raw) > 32 * MIB
        ):
            raise ValueError("evidence_final_size_cap")
        (root / name).write_bytes(raw)

    def event(kind, **fields):
        report["events"].append({"at": time.monotonic(), "kind": kind, **fields})

    read_fd, write_fd = os.pipe()
    ack_read_fd, ack_write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    supervisor = process_row(os.getpid())
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ADMINME_TEST_MODE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GITHUB_SHA": os.environ.get("GITHUB_SHA", "synthetic-contract"),
        "FINANCE_STORAGE_ARTIFACT_DIR": str(root),
        "FINANCE_WATCHDOG_ROOT": str(root),
        "FINANCE_WATCHDOG_PID": str(os.getpid()),
        "FINANCE_WATCHDOG_BIRTH": str(supervisor["start_ticks"]),
        "FINANCE_WATCHDOG_STARTED": str(STARTED),
        "FINANCE_WATCHDOG_FD": str(write_fd),
        "FINANCE_WATCHDOG_ACK_FD": str(ack_read_fd),
        "FINANCE_WATCHDOG_MODE": mode,
    }
    if (
        mode == "staged-bounded"
        and os.environ.get("ADMINME_SYNTHETIC_CALLBACK_DIAGNOSTICS") == "1"
    ):
        env["ADMINME_SYNTHETIC_CALLBACK_DIAGNOSTICS"] = "1"
    controller_script = Path(__file__).with_name("finance_watchdog_controller.py")
    output = (root / "controller-output.txt").open("wb")
    initial = sample(os.getpid(), None, None, [root])
    report["initial_sample"] = initial
    report["maxima"] = {k: initial[k] for k in limits}
    write_sample(initial)
    controller = subprocess.Popen(
        [sys.executable, "-I", "-B", str(controller_script)],
        env=env,
        cwd="/",
        stdout=output,
        stderr=subprocess.STDOUT,
        close_fds=True,
        pass_fds=(write_fd, ack_read_fd),
        start_new_session=True,
    )
    os.close(write_fd)
    os.close(ack_read_fd)
    event("controller.spawned", pid=controller.pid)
    report["supervisor"] = supervisor
    report["controller_pid"] = controller.pid
    runtime_pid = None
    buffer = b""
    known = {}
    runtime_cpu = 0.0
    runtime_members = set()
    reaped_runtime_cpu = 0.0
    runtime_final_received = False
    finish_at = None
    stop_at = None
    summary_window = None
    owner_cleanup_origin = owner_cleanup_end = None
    term_sent = kill_sent = False
    selector = selectors.DefaultSelector()
    selector.register(read_fd, selectors.EVENT_READ)

    def bind_summary_cleanup(*, arm=True):
        nonlocal summary_window
        if mode != "summary-metadata":
            return
        first = (
            finish_at
            if finish_at is not None
            else min(time.monotonic(), STARTED + work)
        )
        if summary_window is not None:
            first = min(first, summary_window["origin"])
        summary_window = summary_cleanup_deadlines(absolute, first, stop_at)
        report["cleanup_schedule"] = summary_window
        report["normal_exit_grace_seconds"] = 2.0
        report["cancel_grace_seconds"] = 0.5
        if arm:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, summary_window["reserve"] - time.monotonic()),
            )

    def bind_owner_cleanup(*, arm=True):
        nonlocal owner_cleanup_origin, owner_cleanup_end
        if mode != "owner-production":
            return
        origins = [
            v for v in (owner_cleanup_origin, finish_at, stop_at) if v is not None
        ]
        owner_cleanup_origin = (
            min(origins) if origins else min(time.monotonic(), STARTED + work)
        )
        owner_cleanup_end = min(absolute, owner_cleanup_origin + cleanup)
        report["cleanup_schedule"] = {
            "origin": owner_cleanup_origin,
            "reserve": owner_cleanup_end - 5,
            "end": owner_cleanup_end,
        }
        if arm:
            signal.setitimer(
                signal.ITIMER_REAL, max(0.001, owner_cleanup_end - 5 - time.monotonic())
            )

    def stop(reason, now):
        nonlocal stop_at, finish_at
        if stop_at is None:
            stop_at = now
            finish_at = min(finish_at or now, now)
            report["faults"].append(reason)
            event("cancel.requested", reason=reason)
            row = process_row(controller.pid)
            if row and row["start_ticks"] == known.get(controller.pid):
                os.kill(controller.pid, signal.SIGUSR1)
            bind_summary_cleanup()
            bind_owner_cleanup()

    def signal_owned(sig):
        report["forced"] = True
        current = {r["pid"]: r for r in tree_rows(os.getpid())}
        known.update({pid: row["start_ticks"] for pid, row in current.items()})
        for pid, birth in list(known.items()):
            row = current.get(pid)
            if pid != os.getpid() and row and row["start_ticks"] == birth:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        event("owned.signal", signal=int(sig))

    def alarm(*args):
        # Kill known owned groups before the final reap/receipt reserve. A second
        # kernel timer terminates this supervisor if finalization itself stalls.
        signal_owned(signal.SIGKILL)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        end = (
            summary_window["end"]
            if summary_window is not None
            else owner_cleanup_end
            if owner_cleanup_end is not None
            else absolute
        )
        signal.setitimer(signal.ITIMER_REAL, max(0.001, end - time.monotonic()))
        raise TimeoutError("absolute_watchdog_alarm")

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(
        signal.ITIMER_REAL,
        max(
            0.001,
            absolute
            - (1 if contract or mode == "summary-metadata" else 5)
            - time.monotonic(),
        ),
    )
    alarm_test_started = False
    owner_export_complete = None
    try:
        while True:
            now = time.monotonic()
            row = sample(os.getpid(), runtime_pid, controller.pid, [root], runtime_cpu)
            runtime_cpu = max(row["runtime_executor_cpu_seconds"], reaped_runtime_cpu)
            if runtime_pid is not None:
                runtime_members.add(runtime_pid)
                for _ in row["pids"]:
                    runtime_members.update(
                        p["pid"] for p in row["pids"] if p["ppid"] in runtime_members
                    )
            known.update({p["pid"]: p["start_ticks"] for p in row["pids"]})
            report["maxima"] = {k: max(report["maxima"][k], row[k]) for k in limits}
            write_sample(row)
            if (
                mode == "contract-alarm"
                and len(row["pids"]) == 4
                and not alarm_test_started
            ):
                alarm_test_started = True
                time.sleep(
                    work + cleanup
                )  # Fixed fault: independent alarm must interrupt.
            exceeded = [key for key, bound in limits.items() if row[key] > bound]
            if mode == "summary-metadata":
                evidence_bytes = sum(
                    p.stat().st_size for p in root.iterdir() if p.is_file()
                )
                report["evidence_max_bytes"] = max(
                    report["evidence_max_bytes"], evidence_bytes
                )
                if evidence_bytes > 16 * MIB - 65536:
                    exceeded.append("evidence_bytes")
            if mode == "owner-production":
                evidence_files = {
                    p.name: p.stat().st_size for p in root.iterdir() if p.is_file()
                }
                sizes = list(evidence_files.values())
                if (
                    len(set(evidence_files) | FINAL_EVIDENCE_FILES)
                    > EVIDENCE_FILE_COUNT
                ):
                    exceeded.append("evidence_file_count")
                evidence_bytes = sum(sizes)
                report["evidence_max_bytes"] = max(
                    report["evidence_max_bytes"], evidence_bytes
                )
                if evidence_bytes > 32 * MIB - 65536 or any(
                    n > 16 * MIB for n in sizes
                ):
                    exceeded.append("evidence_bytes")
            if exceeded:
                stop("containment:" + ",".join(exceeded), now)
            for _, _ in selector.select(timeout=0):
                data = os.read(read_fd, 4096)
                if not data:
                    selector.unregister(read_fd)
                buffer += data
                if len(buffer) > 4096:
                    stop("control_frame_limit", now)
                    buffer = b""
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        msg = json.loads(line, object_pairs_hook=unique_object)
                        if not isinstance(msg, dict):
                            raise ValueError("object")
                        if msg.get("kind") == "runtime" and set(msg) == {
                            "kind",
                            "pid",
                            "birth",
                        }:
                            if (
                                runtime_pid is not None
                                or type(msg["pid"]) is not int
                                or type(msg["birth"]) is not int
                            ):
                                raise ValueError("runtime_binding")
                            current = process_row(msg["pid"])
                            if (
                                not current
                                or current["ppid"] != controller.pid
                                or current["start_ticks"] != msg["birth"]
                            ):
                                raise ValueError("runtime_binding")
                            runtime_pid = msg["pid"]
                            report["runtime_binding"] = current
                            runtime_members.add(runtime_pid)
                            os.write(ack_write_fd, b"runtime_bound\n")
                        elif msg.get("kind") == "runtime_reaped" and set(msg) == {
                            "kind",
                            "pid",
                            "cpu_seconds",
                        }:
                            if (
                                runtime_final_received
                                or msg["pid"] != runtime_pid
                                or type(msg["cpu_seconds"]) not in (int, float)
                                or not math.isfinite(msg["cpu_seconds"])
                                or msg["cpu_seconds"] < 0
                            ):
                                raise ValueError("runtime_final_accounting")
                            current = process_row(runtime_pid)
                            if (
                                current
                                and current["start_ticks"]
                                == report["runtime_binding"]["start_ticks"]
                            ):
                                raise ValueError("runtime_not_reaped")
                            runtime_final_received = True
                            runtime_cpu = max(runtime_cpu, msg["cpu_seconds"])
                            report["runtime_final_cpu_seconds"] = msg["cpu_seconds"]
                        elif mode == "owner-production" and msg.get("kind") == "phase":
                            assert phase_clock is not None
                            phase_clock.accept(msg, time.monotonic())
                            report["phases_complete"] = phase_clock.complete
                        elif msg.get("kind") == "work_finished" and set(msg) == {
                            "kind",
                            "at",
                        }:
                            if (
                                finish_at is not None
                                or type(msg["at"]) not in (int, float)
                                or not math.isfinite(msg["at"])
                                or not STARTED <= msg["at"] <= time.monotonic()
                            ):
                                raise ValueError("work_clock")
                            finish_at = msg["at"]
                            bind_summary_cleanup()
                            bind_owner_cleanup()
                            if finish_at > STARTED + work:
                                stop("work_deadline", now)
                        else:
                            raise ValueError("control_kind")
                        event(
                            "control.accepted",
                            control_kind=msg["kind"],
                            fields={k: v for k, v in msg.items() if k != "kind"},
                        )
                    except TimeoutError as exc:
                        stop(str(exc), time.monotonic())
                    except (ValueError, TypeError, KeyError) as exc:
                        stop("invalid_control:" + str(exc)[:100], now)
            # An on-time completion may already be queued when the supervisor
            # wakes after the deadline. Apply ordered source-clock transitions
            # before testing the still-active phase; no deadline gains IPC slack.
            if phase_clock is not None:
                phase_now = time.monotonic()
                overdue = phase_clock.overdue(phase_now)
                if overdue is not None:
                    stop("containment:phase_deadline:" + overdue, phase_now)
            while True:
                try:
                    pid, status, usage = os.wait4(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if not pid:
                    break
                if pid in runtime_members:
                    reaped_runtime_cpu += usage.ru_utime + usage.ru_stime
                    runtime_cpu = max(runtime_cpu, reaped_runtime_cpu)
                code = os.waitstatus_to_exitcode(status)
                report["reaped"].append(
                    {
                        "pid": pid,
                        "exit_code": code,
                        "cpu_seconds": usage.ru_utime + usage.ru_stime,
                        "at": time.monotonic(),
                    }
                )
                if pid == controller.pid:
                    controller.returncode = code
                    event("controller.reaped", exit_code=code)
            alive = [r for r in tree_rows(os.getpid()) if r["pid"] != os.getpid()]
            if controller.returncode is not None:
                if not alive:
                    break
                if stop_at is None:
                    stop("controller_exit_with_descendants", now)
                    signal_owned(signal.SIGTERM)
                    term_sent = True
            if finish_at is None and now >= STARTED + work:
                stop("work_deadline", now)
            cleanup_start = stop_at if stop_at is not None else finish_at
            if summary_window is not None:
                term_sent, kill_sent, reserve = summary_cleanup_step(
                    summary_window, time.monotonic(), term_sent, kill_sent, signal_owned
                )
                if reserve:
                    report["faults"].append("cleanup_deadline")
                    break
            elif cleanup_start is not None:
                if now >= cleanup_start + grace and not term_sent:
                    signal_owned(signal.SIGTERM)
                    term_sent = True
                if now >= cleanup_start + grace + term and not kill_sent:
                    signal_owned(signal.SIGKILL)
                    kill_sent = True
                if now >= min(cleanup_start + cleanup - 1, absolute - 1):
                    signal_owned(signal.SIGKILL)
                    report["faults"].append("cleanup_deadline")
                    break
            if now >= absolute - 1:
                signal_owned(signal.SIGKILL)
                report["faults"].append("absolute_deadline")
                break
            time.sleep(min(0.05, max(0, absolute - 1 - time.monotonic())))
    except BaseException as exc:
        report["faults"].append("watchdog_exception:" + type(exc).__name__)
        finish_at = finish_at or min(time.monotonic(), STARTED + work)
        signal_owned(signal.SIGKILL)
    finally:
        # The same deadline caps reap, sampler settlement and receipt/scratch I/O.
        if mode == "summary-metadata":
            if summary_window is None:
                bind_summary_cleanup(arm=False)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.setitimer(
                signal.ITIMER_REAL, max(0.001, summary_window["end"] - time.monotonic())
            )
        if mode == "owner-production":
            bind_owner_cleanup(arm=False)
            # Hard termination covers stuck scratch removal, hashing or export,
            # using the earliest observed terminal origin, never a fresh 30s.
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.setitimer(
                signal.ITIMER_REAL, max(0.001, owner_cleanup_end - time.monotonic())
            )
        # Final reserve: bounded nonblocking reap, then retained receipts/scratch removal.
        cleanup_maxima = {key: 0 for key in limits}

        def record_cleanup_sample(row):
            # Only this sampler writes these maxima; fold them after join so
            # finalization cannot overwrite an intermediate cleanup peak.
            for key in limits:
                cleanup_maxima[key] = max(cleanup_maxima[key], row[key])
            write_sample(row)

        cleanup_sampling_stop, cleanup_sampler = start_cleanup_sampler(
            mode,
            lambda: sample(
                os.getpid(), runtime_pid, controller.pid, [root], runtime_cpu
            ),
            record_cleanup_sample
            if mode in {"summary-metadata", "owner-production"}
            else write_sample,
            report["faults"],
        )
        end = (
            summary_window["end"]
            if summary_window is not None
            else owner_cleanup_end
            if owner_cleanup_end is not None
            else min(absolute, (finish_at or time.monotonic()) + cleanup)
        )
        reap_reserve = 5.0 if mode == "owner-production" else 0.5
        while time.monotonic() < end - reap_reserve:
            try:
                pid, status, usage = os.wait4(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid:
                if pid in runtime_members:
                    reaped_runtime_cpu += usage.ru_utime + usage.ru_stime
                    runtime_cpu = max(runtime_cpu, reaped_runtime_cpu)
                report["reaped"].append(
                    {
                        "pid": pid,
                        "exit_code": os.waitstatus_to_exitcode(status),
                        "cpu_seconds": usage.ru_utime + usage.ru_stime,
                        "at": time.monotonic(),
                    }
                )
                if pid == controller.pid:
                    controller.returncode = os.waitstatus_to_exitcode(status)
            elif not [r for r in tree_rows(os.getpid()) if r["pid"] != os.getpid()]:
                break
            else:
                time.sleep(0.01)
        report["remaining_owned"] = [
            r for r in tree_rows(os.getpid()) if r["pid"] != os.getpid()
        ]
        report["controller_exit"] = controller.returncode
        report["adopted_runtime_cpu_seconds"] = reaped_runtime_cpu
        report["work_finished_at"] = finish_at
        report["final_sample"] = sample(
            os.getpid(), runtime_pid, controller.pid, [root], runtime_cpu
        )
        report["maxima"] = {
            k: max(report["maxima"][k], report["final_sample"][k]) for k in limits
        }
        write_sample(report["final_sample"])
        output.close()
        selector.close()
        os.close(read_fd)
        os.close(ack_write_fd)
        # Preserve synthetic trace prefixes if the controller could not finish.
        for index, path in enumerate(scratch.rglob("measurement-*.jsonl")):
            shutil.copyfile(path, root / ("emergency-" + str(index) + "-" + path.name))
        report["scratch_removed"] = remove_scratch(
            scratch,
            retain_unknown=mode
            in {"staged-bounded", "summary-metadata", "owner-production"},
            remaining_owned=report["remaining_owned"],
        )
        if (
            mode in {"staged-bounded", "summary-metadata", "owner-production"}
            and not report["scratch_removed"]
        ):
            report["faults"].append("uncertain_ownership_scratch_retained")
        if cleanup_sampler is not None:
            cleanup_sampling_stop.set()
            join_timeout = (
                min(1.0, max(0.0, end - time.monotonic()))
                if summary_window is not None
                else 1.0
            )
            cleanup_sampler.join(timeout=join_timeout)
            if cleanup_sampler.is_alive():
                report["faults"].append("cleanup_sampler_unsettled")
            report["post_cleanup_sample"] = sample(
                os.getpid(), runtime_pid, controller.pid, [root], runtime_cpu
            )
            write_sample(report["post_cleanup_sample"])
            if mode in {"summary-metadata", "owner-production"}:
                report["cleanup_maxima"] = cleanup_maxima
                report["maxima"] = {
                    key: max(report["maxima"][key], cleanup_maxima[key])
                    for key in limits
                }
            report["maxima"] = {
                k: max(report["maxima"][k], report["post_cleanup_sample"][k])
                for k in limits
            }
        trace_file.close()
        report["finished"] = time.monotonic()
        report["elapsed_seconds"] = report["finished"] - STARTED
        report["cleanup_seconds"] = (
            report["finished"] - finish_at if finish_at else None
        )
        report["absolute_bound_met"] = report["finished"] <= absolute
        if mode == "summary-metadata":
            for key, bound in limits.items():
                if report["maxima"][key] > bound:
                    report["faults"].append("final_containment:" + key)
            evidence_bytes = sum(
                p.stat().st_size for p in root.iterdir() if p.is_file()
            )
            report["evidence_max_bytes"] = max(
                report["evidence_max_bytes"], evidence_bytes
            )
            if evidence_bytes > 16 * MIB - 65536:
                report["faults"].append("final_containment:evidence_bytes")
        if mode == "owner-production":
            for key, bound in limits.items():
                if report["maxima"][key] > bound:
                    report["faults"].append("final_containment:" + key)
            if not report["phases_complete"] or not runtime_final_received:
                report["faults"].append("incomplete_phase_or_runtime_receipt")
            final_files = {
                p.name: p.stat().st_size for p in root.iterdir() if p.is_file()
            }
            sizes = list(final_files.values())
            if len(set(final_files) | FINAL_EVIDENCE_FILES) > EVIDENCE_FILE_COUNT:
                report["faults"].append("final_containment:evidence_file_count")
            if sum(sizes) > 32 * MIB - 65536 or any(n > 16 * MIB for n in sizes):
                report["faults"].append("final_containment:evidence_bytes")
        if mode == "owner-production":
            write_owner_final("watchdog.json", report)
        else:
            (root / "watchdog.json").write_text(json.dumps(report, indent=2) + "\n")
        if mode == "owner-production":
            # All raw files already live outside scratch. Export every file,
            # including failures, after cleanup; hash in chunks, without copying
            # or truncating evidence. The manifest intentionally excludes itself.
            files = []
            for path in sorted(p for p in root.iterdir() if p.is_file()):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                files.append(
                    {
                        "name": path.name,
                        "bytes": path.stat().st_size,
                        "sha256": digest.hexdigest(),
                    }
                )
            write_owner_final(
                "evidence-manifest.json",
                {
                    "candidate": report["candidate"],
                    "files": files,
                    "scratch_removed": report["scratch_removed"],
                    "final_file_count": len(files) + 2,
                    "file_count_limit": EVIDENCE_FILE_COUNT,
                },
            )
            # A separate final receipt binds the manifest and accounts for
            # export itself. It is deliberately outside its own hash manifest.
            export_sample = sample(
                os.getpid(), runtime_pid, controller.pid, [root], runtime_cpu
            )
            export_at = time.monotonic()
            export_faults = [
                key for key, bound in limits.items() if export_sample[key] > bound
            ]
            evidence_files = {
                p.name: p.stat().st_size for p in root.iterdir() if p.is_file()
            }
            evidence_sizes = list(evidence_files.values())
            if len(set(evidence_files) | FINAL_EVIDENCE_FILES) > EVIDENCE_FILE_COUNT:
                export_faults.append("evidence_file_count")
            if sum(evidence_sizes) > 32 * MIB - 16384 or any(
                n > 16 * MIB for n in evidence_sizes
            ):
                export_faults.append("evidence_bytes")
            owner_export_complete = not export_faults and export_at <= owner_cleanup_end
            write_owner_final(
                "export-receipt.json",
                {
                    "manifest_sha256": hashlib.sha256(
                        (root / "evidence-manifest.json").read_bytes()
                    ).hexdigest(),
                    "export_sample": export_sample,
                    "export_finished_at": export_at,
                    "cleanup_seconds_including_export": export_at
                    - owner_cleanup_origin,
                    "cleanup_deadline": owner_cleanup_end,
                    "final_file_count": len(set(evidence_files) | FINAL_EVIDENCE_FILES),
                    "complete": owner_export_complete,
                    "faults": export_faults,
                    "qualification": False,
                },
            )
    summary_complete = (
        summary_cleanup_complete(report, time.monotonic())
        if mode == "summary-metadata"
        else None
    )
    signal.setitimer(signal.ITIMER_REAL, 0)
    if summary_complete is not None:
        return 0 if summary_complete else 1
    clean = (
        not report["remaining_owned"]
        and report["scratch_removed"]
        and report["absolute_bound_met"]
        and (
            mode != "owner-production"
            or (owner_export_complete is True and time.monotonic() <= owner_cleanup_end)
        )
    )
    if contract:
        expected_forced = mode != "contract-normal"
        passed = (
            clean
            and report["maxima"]["owned_processes"] == 4
            and report["forced"] is expected_forced
        )
        passed = passed and len(report["reaped"]) >= (1 if not expected_forced else 3)
        passed = passed and (
            controller.returncode == 0
            if not expected_forced
            else controller.returncode != 0
        )
        required_fault = {
            "contract-hang": "work_deadline",
            "contract-orphan": "controller_exit_with_descendants",
            "contract-alarm": "watchdog_exception:TimeoutError",
            "contract-protocol": "invalid_control:runtime_binding",
        }.get(mode)
        passed = passed and (
            required_fault in report["faults"]
            if required_fault
            else not report["faults"]
        )
        report["contract_passed"] = passed
        (root / "watchdog.json").write_text(json.dumps(report, indent=2) + "\n")
        return 0 if passed else 1
    return (
        0
        if clean
        and not report["forced"]
        and not report["faults"]
        and controller.returncode == 0
        and finish_at is not None
        else 1
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("fixed mode and fresh owned root required")
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
