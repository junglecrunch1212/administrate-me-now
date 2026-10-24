"""Darwin self-reporting fixture processes; no foreign-PID measurement API.

Only the registry spawns fixed synthetic targets. IPC is inherited, bounded,
MAC-authenticated and sequenced. This is a test guard, not an OS sandbox.
"""

from __future__ import annotations

import ctypes
import hmac
import json
import math
import multiprocessing
import os
import resource
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FRAME_LIMIT = 8192
WORK_LIMIT = 4 * 1024 * 1024
SAMPLE_SECONDS = 0.1
MAX_GAP_SECONDS = 2.0
MAX_PROCESSES = 5
WALL_SECONDS = 180
CPU_SECONDS = 60
RSS_BYTES = 512 * 1024 * 1024


class EvidenceError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


class Wire:
    """Private inherited endpoint plus per-birth/channel/direction authentication."""

    def __init__(self, connection, key, birth, channel, side, limit=FRAME_LIMIT):
        self.connection, self.key, self.birth = connection, key, birth
        self.channel, self.side, self.limit = channel, side, limit
        self.sent = self.received = 0
        self.lock = threading.Lock()

    def send(self, payload):
        with self.lock:
            body = canonical(
                {
                    "birth": self.birth,
                    "channel": self.channel,
                    "side": self.side,
                    "sequence": self.sent,
                    "payload": payload,
                }
            )
            frame = hmac.digest(self.key, body, "sha256") + body
            if len(frame) > self.limit:
                raise EvidenceError("oversize IPC frame")
            self.connection.send_bytes(frame)
            self.sent += 1

    def recv(self):
        frame = self.connection.recv_bytes(self.limit)
        signature, body = frame[:32], frame[32:]
        if not hmac.compare_digest(signature, hmac.digest(self.key, body, "sha256")):
            raise EvidenceError("IPC authentication failed")
        envelope = json.loads(body)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"birth", "channel", "side", "sequence", "payload"}
            or type(envelope["sequence"]) is not int
        ):
            raise EvidenceError("invalid IPC envelope")
        if (
            envelope.get("birth") != self.birth
            or envelope.get("channel") != self.channel
            or envelope.get("side") == self.side
            or envelope.get("side") not in {"parent", "child"}
            or envelope.get("sequence") != self.received
        ):
            raise EvidenceError("IPC ownership, direction or sequence mismatch")
        self.received += 1
        return envelope["payload"]

    def poll(self, timeout=0):
        return self.connection.poll(timeout)

    def close(self):
        self.connection.close()


class _TimeValue(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_int), ("microseconds", ctypes.c_int)]


class _MachBasicInfo(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("resident_size_max", ctypes.c_uint64),
        ("user_time", _TimeValue),
        ("system_time", _TimeValue),
        ("policy", ctypes.c_int),
        ("suspend_count", ctypes.c_int),
    ]


class DarwinSelfMetrics:
    """Construction/execution only inside a freshly spawned authorized fixture."""

    def __init__(self):
        if sys.platform != "darwin":
            raise EvidenceError("native Darwin collector required; no fallback")
        # Fixed system library, no dynamic search or hardware/system query.
        self.library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        self.task = ctypes.c_uint.in_dll(self.library, "mach_task_self_").value
        self.info = self.library.task_info
        self.info.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
        ]
        self.info.restype = ctypes.c_int

    def sample(self):
        info = _MachBasicInfo()
        count = ctypes.c_uint(ctypes.sizeof(info) // ctypes.sizeof(ctypes.c_int))
        result = self.info(self.task, 20, ctypes.byref(info), ctypes.byref(count))
        if result != 0 or count.value != ctypes.sizeof(info) // 4:
            raise EvidenceError(f"self task_info unavailable: {result}; no fallback")
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return metric_record(
            usage.ru_utime,
            usage.ru_stime,
            int(info.resident_size),
            int(usage.ru_maxrss),
            int(info.resident_size_max),
        )


def metric_record(user_seconds, system_seconds, rss_bytes, peak_bytes, mach_peak_bytes):
    # Darwin XNU assigns ru_maxrss directly from resident_size_max (bytes).
    # Do not apply Linux's KiB multiplier; Mach time fields omit live threads.
    numbers = [user_seconds, system_seconds, rss_bytes, peak_bytes, mach_peak_bytes]
    if any(
        type(value) not in {int, float} or not math.isfinite(value) or value < 0
        for value in numbers
    ):
        raise EvidenceError("invalid self resource counter")
    if any(type(value) is not int for value in numbers[2:]) or rss_bytes > max(
        peak_bytes, mach_peak_bytes
    ):
        raise EvidenceError("invalid RSS units or counters")
    return {
        "at": time.monotonic(),
        "user_cpu_seconds": user_seconds,
        "system_cpu_seconds": system_seconds,
        "cpu_seconds": user_seconds + system_seconds,
        "rss_bytes": rss_bytes,
        "peak_rss_bytes": peak_bytes,
        "mach_peak_rss_bytes": mach_peak_bytes,
    }


class FixtureGuard:
    """Deny Python subprocess and nonfixture network/file accesses; not a sandbox.

    Native libraries can bypass Python audit hooks. No claim of OS confinement
    or arbitrary native-child discovery is made. Violations persist even caught.
    """

    def __init__(self, root, port, faults):
        self.root = Path(root).resolve()
        self.port = port
        self.faults = faults
        self.read_roots = (
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            Path("/usr/share/zoneinfo").resolve(),
            Path("/var/db/timezone/zoneinfo").resolve(),
            Path(__file__).resolve().parents[2],
        )

    def deny(self, reason):
        self.faults.append(reason)
        raise PermissionError(reason)

    def __call__(self, event, args):
        if event in {
            "subprocess.Popen",
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.exec",
            "os.system",
        }:
            self.deny("unregistered process launch")
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
            address = args[1] if event != "socket.getaddrinfo" else args[:2]
            if (
                not isinstance(address, tuple)
                or len(address) < 2
                or address[0] != "127.0.0.1"
                or address[1] != self.port
            ):
                self.deny("outside exact fixture loopback endpoint")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            )
            if path.is_relative_to(self.root):
                return
            if not writing and (
                any(path.is_relative_to(root) for root in self.read_roots)
                or str(path).startswith(("/System/Library/", "/usr/lib/"))
                or str(path)
                in {
                    "/dev/null",
                    "/dev/urandom",
                    "/etc/localtime",
                    "/private/etc/localtime",
                }
            ):
                return
            self.deny("outside fixture filesystem scope")


def _entry(
    target,
    arguments,
    root,
    port,
    birth,
    key,
    identity,
    metrics_connection,
    work_connection,
    stop,
    wall_seconds,
):
    """Only child self APIs are called here, never in the parent/operational host."""
    metrics = Wire(metrics_connection, key, birth, "metrics", "child")
    work = Wire(work_connection, key, birth, "work", "child", WORK_LIMIT)
    faults: list[str] = []
    done = threading.Event()
    sampler = None
    provider = None
    started = time.monotonic()
    outcome = "returned"
    # These caps affect this new disposable child, never host security/services.
    try:
        metrics.send(
            {
                "kind": "hello",
                "pid": os.getpid(),
                "birth": birth,
                "target": target,
                "identity": identity,
                "at": started,
            }
        )
        inherited = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = min([CPU_SECONDS, *(v for v in inherited if v >= 0)])
        resource.setrlimit(resource.RLIMIT_CPU, (ceiling, ceiling))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        provider = DarwinSelfMetrics()
        metrics.send(
            {"kind": "sample", "metrics": provider.sample(), "barrier": "startup"}
        )
        guard = FixtureGuard(root, port, faults)
        sys.addaudithook(guard)

        def monitor():
            try:
                while not done.is_set():
                    barrier = None
                    if metrics.poll(SAMPLE_SECONDS):
                        command = metrics.recv()
                        if set(command) != {"snapshot"} or not isinstance(
                            command["snapshot"], str
                        ):
                            raise EvidenceError("invalid telemetry command")
                        barrier = command["snapshot"]
                    value = provider.sample()
                    metrics.send(
                        {"kind": "sample", "metrics": value, "barrier": barrier}
                    )
                    if (
                        value["rss_bytes"] > RSS_BYTES
                        or time.monotonic() - started > wall_seconds
                    ):
                        raise EvidenceError("fixture resource ceiling exceeded")
            except BaseException as exc:
                faults.append(type(exc).__name__ + ": " + str(exc)[:160])
                try:
                    metrics.send({"kind": "fault", "reason": faults[-1]})
                finally:
                    stop.set()

        sampler = threading.Thread(
            target=monitor, daemon=True, name="fixture-self-metrics"
        )
        sampler.start()
        if target == "runtime":
            from tests.acceptance.test_finance_mixed_load import _serve

            _serve(
                Path(root) / "instance",
                arguments["listener"],
                work,
                planning_workload=arguments.get("planning", False),
                stop_event=stop,
            )
        elif target in {"core", "finance"}:
            from tests.fixtures.finance_resource_diagnostic import load_client

            load_client(
                arguments["url"],
                target,
                arguments["actor"],
                arguments["session"],
                arguments.get("planning"),
                work,
                Path(root) / f"{target}-requests.jsonl",
            )
        elif target == "worker":
            # Explicit small owned worker, not a heavy-analysis qualification.
            allocation = bytearray(4 * 1024 * 1024)
            for index in range(0, len(allocation), 4096):
                allocation[index] = 1
            work.send({"worker_ready": True})
            stop.wait(10)
        elif target == "failure":
            raise RuntimeError("synthetic target failure")
        else:
            raise EvidenceError("unregistered target")
    except BaseException as exc:
        outcome = "failed"
        faults.append(type(exc).__name__ + ": " + str(exc)[:160])
        raise
    finally:
        done.set()
        if sampler is not None:
            sampler.join(2)
            if sampler.is_alive():
                faults.append("sampler did not stop")
        try:
            final = provider.sample() if provider is not None else None
        except BaseException as exc:
            final = None
            faults.append("terminal sampling: " + type(exc).__name__)
        metrics.send(
            {
                "kind": "terminal",
                "outcome": outcome,
                "faults": faults,
                "metrics": final,
                "pid": os.getpid(),
                "birth": birth,
            }
        )
        metrics.close()
        work.close()


@dataclass
class Member:
    birth: str
    target: str
    pid: int
    identity: dict
    process: Any
    wire: Wire
    work: Wire
    stop: Any
    hello: bool = False
    samples: list[dict] = field(default_factory=list)
    terminal: dict | None = None
    errors: list[str] = field(default_factory=list)
    barriers: set[str] = field(default_factory=set)
    frames: int = 0

    def accept(self, record):
        self.frames += 1
        if self.frames > 20000 or self.terminal is not None:
            raise EvidenceError("duplicate terminal or excessive telemetry")
        if not isinstance(record, dict):
            raise EvidenceError("invalid lifecycle record")
        kind = record.get("kind")
        if kind == "hello":
            if (
                self.hello
                or record.get("pid") != self.pid
                or record.get("birth") != self.birth
                or record.get("target") != self.target
                or record.get("identity") != self.identity
            ):
                raise EvidenceError("registered birth/PID/candidate mismatch")
            self.hello = True
            return
        if not self.hello:
            raise EvidenceError("sample before owned registration")
        if kind == "fault":
            self.errors.append(str(record.get("reason"))[:200])
            return
        if kind not in {"sample", "terminal"}:
            raise EvidenceError("unknown lifecycle record")
        if kind == "terminal" and (
            record.get("pid") != self.pid or record.get("birth") != self.birth
        ):
            raise EvidenceError("terminal ownership mismatch")
        value = record.get("metrics")
        if not isinstance(value, dict):
            raise EvidenceError("missing resource sample")
        required = {
            "at",
            "user_cpu_seconds",
            "system_cpu_seconds",
            "cpu_seconds",
            "rss_bytes",
            "peak_rss_bytes",
            "mach_peak_rss_bytes",
        }
        if set(value) != required or any(
            type(v) not in {int, float} or not math.isfinite(v) or v < 0
            for v in value.values()
        ):
            raise EvidenceError("invalid resource sample")
        if any(
            type(value[key]) is not int
            for key in ("rss_bytes", "peak_rss_bytes", "mach_peak_rss_bytes")
        ) or value["rss_bytes"] > max(
            value["peak_rss_bytes"], value["mach_peak_rss_bytes"]
        ):
            raise EvidenceError("invalid RSS units or counters")
        if (
            value["cpu_seconds"]
            != value["user_cpu_seconds"] + value["system_cpu_seconds"]
        ):
            raise EvidenceError("CPU units mismatch")
        if self.samples:
            previous = self.samples[-1]
            if value["at"] <= previous["at"] or any(
                value[k] < previous[k]
                for k in (
                    "cpu_seconds",
                    "user_cpu_seconds",
                    "system_cpu_seconds",
                    "peak_rss_bytes",
                    "mach_peak_rss_bytes",
                )
            ):
                raise EvidenceError("sampling counter/time regression")
            if value["at"] - previous["at"] > MAX_GAP_SECONDS:
                self.errors.append("sampling gap")
        self.samples.append(value)
        if kind == "sample" and record.get("barrier"):
            self.barriers.add(record["barrier"])
        if kind == "terminal":
            self.terminal = record
            self.errors.extend(record.get("faults", []))
            if record.get("outcome") != "returned":
                self.errors.append("target did not return normally")

    def verdict(self):
        errors = list(self.errors)
        if not self.hello or len(self.samples) < 2 or self.terminal is None:
            errors.append("missing registration, samples or terminal receipt")
        if self.process.exitcode is None:
            errors.append("exit not accounted")
        elif self.process.exitcode != 0:
            errors.append("nonzero process exit")
        return {
            "birth": self.birth,
            "target": self.target,
            "pid": self.pid,
            "exitcode": self.process.exitcode,
            "samples": len(self.samples),
            "complete": not errors,
            "errors": errors,
            "startup_sample": self.samples[0] if self.samples else None,
            "terminal": self.terminal,
        }


class Registry:
    """All handles originate here; no PID attachment or discovery operation."""

    def __init__(self, root: Path, port: int, identity: dict, *, wall_seconds=180):
        if (
            wall_seconds not in {180, 1800}
            or not root.is_dir()
            or not 1024 <= port <= 65535
            or set(identity) != {"candidate", "checkout"}
        ):
            raise EvidenceError("invalid fixture scope")
        if any(
            not isinstance(value, str)
            or len(value) != 40
            or any(c not in "0123456789abcdef" for c in value)
            for value in identity.values()
        ):
            raise EvidenceError("exact candidate/checkout required")
        self.root, self.port, self.identity = root.resolve(), port, identity
        self.wall_seconds = wall_seconds
        self.context = multiprocessing.get_context("spawn")
        self.members: list[Member] = []
        self.errors: list[str] = []
        self.created = time.monotonic()

    def spawn(self, target, **arguments):
        if (
            target not in {"runtime", "core", "finance", "worker", "failure"}
            or len(self.members) >= MAX_PROCESSES
        ):
            raise EvidenceError("unregistered target or process cap")
        expected = {
            "runtime": {"listener"},
            "core": {"url", "actor", "session"},
            "finance": {"url", "actor", "session"},
        }.get(target, set())
        if set(arguments) - {"planning"} != expected or (
            "planning" in arguments and target not in {"runtime", "core", "finance"}
        ):
            raise EvidenceError("unexpected fixture target arguments")
        if (
            target in {"core", "finance"}
            and arguments["url"] != f"http://127.0.0.1:{self.port}"
        ):
            raise EvidenceError("outside exact fixture endpoint")
        if target == "runtime" and arguments["listener"].getsockname() != (
            "127.0.0.1",
            self.port,
        ):
            raise EvidenceError("outside exact fixture listener")
        birth, key = uuid.uuid4().hex, os.urandom(32)
        parent_metrics, child_metrics = self.context.Pipe()
        parent_work, child_work = self.context.Pipe()
        stop = self.context.Event()
        process = self.context.Process(
            target=_entry,
            args=(
                target,
                arguments,
                str(self.root),
                self.port,
                birth,
                key,
                self.identity,
                child_metrics,
                child_work,
                stop,
                self.wall_seconds,
            ),
        )
        process.start()
        child_metrics.close()
        child_work.close()
        assert process.pid is not None
        member = Member(
            birth,
            target,
            process.pid,
            self.identity,
            process,
            Wire(parent_metrics, key, birth, "metrics", "parent"),
            Wire(parent_work, key, birth, "work", "parent", WORK_LIMIT),
            stop,
        )
        self.members.append(member)
        self._retain(
            {
                "kind": "registered",
                "birth": birth,
                "target": target,
                "pid": member.pid,
                "identity": self.identity,
            }
        )
        return member

    def _retain(self, value):
        try:
            with (self.root / "lifecycle.jsonl").open("a") as stream:
                stream.write(json.dumps(value, sort_keys=True) + "\n")
                stream.flush()
        except OSError as exc:
            self.errors.append("evidence retention failed: " + type(exc).__name__)
            raise EvidenceError(self.errors[-1]) from exc

    def drain(self, *, check_deadline=True):
        if check_deadline and time.monotonic() - self.created > self.wall_seconds:
            self.errors.append("fixture parent watchdog expired")
            raise EvidenceError(self.errors[-1])
        for member in self.members:
            while member.wire.poll():
                try:
                    record = member.wire.recv()
                    self._retain({"birth": member.birth, "record": record})
                    member.accept(record)
                except EOFError:
                    break
                except (OSError, ValueError, EvidenceError) as exc:
                    member.errors.append(type(exc).__name__ + ": " + str(exc)[:160])
                    break

    async def barrier(self, label):
        import asyncio

        active = [member for member in self.members if member.terminal is None]
        for member in active:
            member.wire.send({"snapshot": label})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.drain()
            if all(label in member.barriers for member in active):
                return self.snapshot(label)
            await asyncio.sleep(0.02)
        self.errors.append("missing boundary sample: " + label)
        raise EvidenceError(self.errors[-1])

    def snapshot(self, phase):
        members = [
            member for member in self.members if member.target in {"runtime", "worker"}
        ]
        errors = [
            *self.errors,
            *(error for member in members for error in member.errors),
        ]
        if not members or any(not member.samples for member in members):
            errors.append("missing registered process sample")
        samples = [member.samples[-1] for member in members if member.samples]
        return {
            "at": time.monotonic(),
            "phase": phase,
            "errors": errors,
            "cpu_seconds": sum(row["cpu_seconds"] for row in samples)
            if not errors
            else None,
            "rss_bytes": sum(
                member.samples[-1]["rss_bytes"]
                for member in members
                if member.samples and member.terminal is None
            )
            if not errors
            else None,
            "sum_lifetime_peaks_upper_bound_bytes": sum(
                row["peak_rss_bytes"] for row in samples
            ),
            "processes": [
                {
                    "birth": m.birth,
                    "target": m.target,
                    "exited": m.terminal is not None,
                    "metrics": m.samples[-1] if m.samples else None,
                }
                for m in members
            ],
        }

    async def close(self):
        import asyncio

        for member in self.members:
            member.stop.set()
            if member.target in {"core", "finance"} and member.process.is_alive():
                try:
                    member.work.send(None)
                except (OSError, EOFError):
                    pass
        deadline = time.monotonic() + 10
        while (
            any(member.process.is_alive() for member in self.members)
            and time.monotonic() < deadline
        ):
            self.drain(check_deadline=False)
            await asyncio.sleep(0.05)
        for member in self.members:
            if member.process.is_alive():
                member.errors.append(
                    "forced cancellation; terminal coverage incomplete"
                )
                member.process.terminate()
            await asyncio.to_thread(member.process.join, 3)
            if member.process.is_alive():
                member.process.kill()
                await asyncio.to_thread(member.process.join, 3)
        self.drain(check_deadline=False)
        verdicts = [member.verdict() for member in self.members]
        result = {
            "registered_coverage_complete": bool(verdicts)
            and not self.errors
            and all(v["complete"] for v in verdicts),
            "whole_os_process_tree_coverage_proven": False,
            "scope": "registered fixture processes and their threads only",
            "errors": self.errors,
            "members": verdicts,
            "all_registered_processes_stopped": all(
                not m.process.is_alive() for m in self.members
            ),
        }
        try:
            self._retain({"kind": "cleanup", **result})
        finally:
            for member in self.members:
                member.wire.close()
                member.work.close()
        return result
