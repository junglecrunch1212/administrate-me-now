"""Owned Linux supervisor for synthetic Finance evaluation; standard library only."""

from __future__ import annotations

# ruff: noqa: E402 -- start the owned clock before remaining stdlib imports.

import time

STARTED = time.monotonic()

import ctypes
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
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    contract = mode.startswith("contract-")
    work, cleanup = (2.0, 4.0) if contract else (200.0, 30.0)
    grace, term, _kill = (1.0, 1.0, 1.0) if contract else (15.0, 5.0, 5.0)
    absolute = STARTED + work + cleanup
    scratch = root / "scratch"
    scratch.mkdir(mode=0o700)
    trace_file = (root / "watchdog-trace.jsonl").open("w", buffering=1)
    report = {
        "mode": mode,
        "candidate": os.environ.get("GITHUB_SHA"),
        "started": STARTED,
        "limits": LIMITS,
        "work_cap_seconds": work,
        "cleanup_cap_seconds": cleanup,
        "absolute_stop_seconds": work + cleanup,
        "events": [],
        "reaped": [],
        "forced": False,
        "faults": [],
        "maxima": {k: 0 for k in LIMITS},
        "subreaper": True,
        "qualification": False,
    }

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
    controller_script = Path(__file__).with_name("finance_watchdog_controller.py")
    output = (root / "controller-output.txt").open("wb")
    initial = sample(os.getpid(), None, None, [root])
    report["initial_sample"] = initial
    report["maxima"] = {k: initial[k] for k in LIMITS}
    trace_file.write(json.dumps(initial, separators=(",", ":")) + "\n")
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
    term_sent = kill_sent = False
    selector = selectors.DefaultSelector()
    selector.register(read_fd, selectors.EVENT_READ)

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
        signal.setitimer(signal.ITIMER_REAL, max(0.001, absolute - time.monotonic()))
        raise TimeoutError("absolute_watchdog_alarm")

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(
        signal.ITIMER_REAL,
        max(0.001, absolute - (1 if contract else 5) - time.monotonic()),
    )
    alarm_test_started = False
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
            report["maxima"] = {k: max(report["maxima"][k], row[k]) for k in LIMITS}
            trace_file.write(json.dumps(row, separators=(",", ":")) + "\n")
            if (
                mode == "contract-alarm"
                and len(row["pids"]) == 4
                and not alarm_test_started
            ):
                alarm_test_started = True
                time.sleep(
                    work + cleanup
                )  # Fixed fault: independent alarm must interrupt.
            exceeded = violations(row)
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
                            if finish_at > STARTED + work:
                                stop("work_deadline", now)
                        else:
                            raise ValueError("control_kind")
                        event(
                            "control.accepted",
                            control_kind=msg["kind"],
                            fields={k: v for k, v in msg.items() if k != "kind"},
                        )
                    except (ValueError, TypeError, KeyError) as exc:
                        stop("invalid_control:" + str(exc)[:100], now)
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
            if cleanup_start is not None:
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
        # Final reserve: bounded nonblocking reap, then retained receipts/scratch removal.
        end = min(absolute, (finish_at or time.monotonic()) + cleanup)
        while time.monotonic() < end - 0.5:
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
            k: max(report["maxima"][k], report["final_sample"][k]) for k in LIMITS
        }
        trace_file.write(
            json.dumps(report["final_sample"], separators=(",", ":")) + "\n"
        )
        trace_file.close()
        output.close()
        selector.close()
        os.close(read_fd)
        os.close(ack_write_fd)
        # Preserve synthetic trace prefixes if the controller could not finish.
        for index, path in enumerate(scratch.rglob("measurement-*.jsonl")):
            shutil.copyfile(path, root / ("emergency-" + str(index) + "-" + path.name))
        shutil.rmtree(scratch)
        report["scratch_removed"] = not scratch.exists()
        report["finished"] = time.monotonic()
        report["elapsed_seconds"] = report["finished"] - STARTED
        report["cleanup_seconds"] = (
            report["finished"] - finish_at if finish_at else None
        )
        report["absolute_bound_met"] = report["finished"] <= absolute
        (root / "watchdog.json").write_text(json.dumps(report, indent=2) + "\n")
    signal.setitimer(signal.ITIMER_REAL, 0)
    clean = (
        not report["remaining_owned"]
        and report["scratch_removed"]
        and report["absolute_bound_met"]
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
