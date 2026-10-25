"""One bounded before/after pure Finance helper probe; hosted Linux only."""

from __future__ import annotations

import argparse
import ctypes
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

BEFORE = "7419175f5508cf21bc7b86e046f65c5c2893269e"
AFTER = "49331292a06ce7cab9eb0504ad197a55684fae88"
TREES = {
    "before": "7b89c73a0420eb73925119b9f0550629389586c6",
    "after": "1873e65d8a36d8af74ed5608b49b62dac6934d73",
}
LOCK = "cc2102f6e9dd2fd029bd636566806493c1df91dc"
MIB = 1024 * 1024
PHASES = (
    "projection cold",
    "projection cache hit",
    "initial proof cold",
    "initial proof cache hit",
    "successor proof cold",
    "successor proof cache hit",
)


@dataclass(frozen=True)
class Limits:
    child_wall: float = 120
    child_cpu: float = 90
    child_rss: int = 512 * MIB
    total_wall: float = 270
    total_cpu: float = 180
    combined_rss: int = 1024 * MIB
    scratch: int = 32 * MIB
    artifact: int = 2 * MIB
    samples: int = 1200


class ProbeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ProbeFailure(message)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_json(path, value):
    path.write_bytes(encode(value) + b"\n")


def directory_bytes(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def process_usage(pid):
    # Linux stat fields 14/15 are CPU ticks and field24 is resident pages.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid,
        "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
        "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
        "process_group": int(fields[2]),
        "state": fields[0],
    }


def group_members(group):
    members = []
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            try:
                if process_usage(int(path.name))["process_group"] == group:
                    members.append(int(path.name))
            except (FileNotFoundError, ProcessLookupError):
                pass
    return members


def adopt_orphans():
    # Allows the supervisor to reap an unexpected descendant after group termination.
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.prctl(36, 1, 0, 0, 0) == 0, "Could not become a child subreaper")


def clear_group(group):
    members = group_members(group)
    if members:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2
    while members:
        try:
            while os.waitpid(-group, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        members = group_members(group)
        require(
            time.monotonic() < deadline or not members, "Process group cleanup deadline"
        )
        if members:
            time.sleep(0.01)
    return []


def install_signal_handlers():
    def interrupted(signum, frame):
        raise ProbeFailure(f"Supervisor received signal {signum}")

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)


def child_limits(limits):
    # Parent death kills the child, but cannot create a confirmed cleanup receipt.
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        os._exit(125)
    # Kernel CPU/file limits supplement the supervisor's sampled limits.
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(limits.child_cpu),) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (min(limits.artifact, 128 * 1024),) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def wait_child(child, *, force=False):
    if force:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + (2 if force else 0)
    while True:
        pid, status, usage = os.wait4(child.pid, os.WNOHANG)
        if pid:
            child.returncode = os.waitstatus_to_exitcode(status)
            return {
                "exit_code": child.returncode,
                "cpu_seconds": usage.ru_utime + usage.ru_stime,
                "high_water_rss_bytes": usage.ru_maxrss * 1024,
                "reaped": True,
            }
        if not force:
            return None
        require(
            time.monotonic() < deadline, "Child could not be reaped within two seconds"
        )
        time.sleep(0.01)


class Supervisor:
    """Only this process launches a child, and it always waits before the next."""

    def __init__(self, output, limits=Limits(), budget_root=None):
        adopt_orphans()
        self.output = output
        self.limits = limits
        self.budget_root = budget_root or output
        self.started = time.monotonic()
        self.cpu_started = time.process_time()
        self.child_cpu = 0.0
        self.samples = 0
        self.children = []
        self.failed = False

    def budget_failure(self, row):
        limits = self.limits
        checks = (
            (row["child_wall_seconds"] >= limits.child_wall, "child wall deadline"),
            (row["child"]["cpu_seconds"] >= limits.child_cpu, "child CPU limit"),
            (row["child"]["rss_bytes"] > limits.child_rss, "child RSS limit"),
            (row["total_wall_seconds"] >= limits.total_wall, "total wall deadline"),
            (row["total_cpu_seconds"] >= limits.total_cpu, "total CPU limit"),
            (row["combined_rss_bytes"] > limits.combined_rss, "combined RSS limit"),
            (row["scratch_bytes"] > limits.scratch, "evidence scratch limit"),
            (
                row["scratch_bytes"] > limits.artifact - 64 * 1024,
                "artifact reserve limit",
            ),
            (self.samples > limits.samples, "sample count limit"),
            (row["group_members"] != [row["child"]["pid"]], "unexpected child process"),
        )
        return next((name for failed, name in checks if failed), None)

    def final_metrics(self):
        return {
            "wall_seconds": time.monotonic() - self.started,
            "cpu_seconds": self.child_cpu + time.process_time() - self.cpu_started,
            "artifact_bytes": directory_bytes(self.budget_root),
            "supervisor_rss_bytes": process_usage(os.getpid())["rss_bytes"],
        }

    def final_failure(self, metrics):
        checks = (
            (metrics["wall_seconds"] >= self.limits.total_wall, "total wall deadline"),
            (metrics["cpu_seconds"] >= self.limits.total_cpu, "total CPU limit"),
            (metrics["artifact_bytes"] > self.limits.scratch, "evidence scratch limit"),
            (metrics["artifact_bytes"] > self.limits.artifact, "artifact limit"),
            (
                metrics["supervisor_rss_bytes"] > self.limits.combined_rss,
                "supervisor RSS limit",
            ),
        )
        return next((name for failed, name in checks if failed), None)

    def run(self, label, command, cwd, environment=None, validate=None):
        require(not self.failed, "Previous child failed; no subsequent launch")
        preflight = self.final_failure(self.final_metrics())
        if preflight:
            self.failed = True
            raise ProbeFailure(preflight)
        launched = time.monotonic()
        record = {"label": label, "command": command, "launched_monotonic": launched}
        self.children.append(record)
        child = terminal = validated = None
        failure = original = None
        cleanup_errors = []
        remaining = None
        with (
            (self.output / f"{label}.jsonl").open("wb") as stdout,
            (self.output / f"{label}.stderr.log").open("wb") as stderr,
            (self.output / f"{label}.samples.jsonl").open("w") as trace,
        ):
            try:
                child = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                    preexec_fn=lambda: child_limits(self.limits),
                )
                record["pid"] = child.pid
                next_sample = time.monotonic()
                while True:
                    terminal = wait_child(child)
                    if terminal is not None:
                        break
                    own = process_usage(os.getpid())
                    try:
                        usage = process_usage(child.pid)
                    except (FileNotFoundError, ProcessLookupError):
                        terminal = wait_child(child)
                        if terminal is not None:
                            break
                        raise  # A missing row without terminal status is not a pass.
                    members = sorted(group_members(child.pid))
                    if not members:
                        terminal = wait_child(child)
                        if terminal is not None:
                            break
                    now = time.monotonic()
                    row = {
                        "role": "probe supervisor",
                        "phase": label,
                        "monotonic": now,
                        "child_wall_seconds": now - launched,
                        "total_wall_seconds": now - self.started,
                        "child": usage,
                        "supervisor": own,
                        "total_cpu_seconds": self.child_cpu
                        + usage["cpu_seconds"]
                        + time.process_time()
                        - self.cpu_started,
                        "combined_rss_bytes": own["rss_bytes"] + usage["rss_bytes"],
                        "scratch_bytes": directory_bytes(self.budget_root),
                        "group_members": members,
                    }
                    self.samples += 1
                    failure = self.budget_failure(row)
                    trace.write(json.dumps(row, sort_keys=True) + "\n")
                    trace.flush()
                    if failure:
                        raise ProbeFailure(failure)
                    next_sample += 0.2
                    time.sleep(max(0, next_sample - time.monotonic()))
                require(
                    terminal["exit_code"] == 0, f"child exit {terminal['exit_code']}"
                )
                require(
                    terminal["cpu_seconds"] < self.limits.child_cpu,
                    "terminal child CPU limit",
                )
                require(
                    time.monotonic() - launched < self.limits.child_wall,
                    "terminal child wall deadline",
                )
                require(
                    terminal["high_water_rss_bytes"] <= self.limits.child_rss,
                    "terminal child RSS limit",
                )
                require(
                    not group_members(child.pid), "Unexpected surviving child process"
                )
                if validate:
                    validated = validate()
            except BaseException as exc:
                self.failed = True
                original = exc
                failure = failure or f"{type(exc).__name__}: {exc}"
            finally:
                # Cleanup failures must not erase the original error or PID receipt.
                termination_requested = child is not None and terminal is None
                if termination_requested:
                    try:
                        terminal = wait_child(child, force=True)
                    except BaseException as exc:
                        cleanup_errors.append(
                            f"Reap unconfirmed: {type(exc).__name__}: {exc}"
                        )
                if child is not None:
                    try:
                        remaining = clear_group(child.pid)
                    except BaseException as exc:
                        cleanup_errors.append(
                            f"Group cleanup unconfirmed: {type(exc).__name__}: {exc}"
                        )
                if cleanup_errors:
                    self.failed = True
                    failure = failure or "Cleanup failed"
                record.update(
                    failure=failure,
                    cleanup_errors=cleanup_errors,
                    forced=bool(termination_requested and child.returncode == -9),
                    termination_requested=termination_requested,
                    wall_seconds=time.monotonic() - launched,
                    terminal=terminal,
                    remaining_group_members=remaining,
                    cleanup_confirmed=bool(
                        terminal
                        and terminal["reaped"]
                        and remaining == []
                        and not cleanup_errors
                    ),
                )
                if terminal:
                    self.child_cpu += terminal["cpu_seconds"]
                try:
                    write_json(self.output / "processes.json", self.children)
                    metrics = self.final_metrics()
                    final_failure = self.final_failure(metrics)
                    record["after_receipt_metrics"] = metrics
                    if final_failure:
                        self.failed = True
                        failure = failure or final_failure
                        record["failure"] = failure
                    write_json(self.output / "processes.json", self.children)
                    # This check is after the final receipt write.
                    post_write_failure = self.final_failure(self.final_metrics())
                    if post_write_failure:
                        self.failed = True
                        failure = failure or post_write_failure
                        record["failure"] = failure
                        write_json(self.output / "processes.json", self.children)
                except BaseException as exc:
                    self.failed = True
                    failure = failure or f"Receipt failure: {type(exc).__name__}: {exc}"
                    print(
                        json.dumps(
                            {"receipt_write_failure": failure, "process": record}
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
        if failure:
            raise ProbeFailure(failure) from original
        return validated


def probe_child(source, label):
    """Import the pinned source, build synthetic inputs, then make exactly six calls."""
    from copy import deepcopy
    import tracemalloc

    def deny_process_or_network(event, arguments):
        if event in {
            "subprocess.Popen",
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.system",
            "socket.connect",
            "socket.bind",
            "sqlite3.connect",
        }:
            raise ProbeFailure(f"Forbidden side effect in pure helper probe: {event}")
        if (
            event == "open"
            and len(arguments) >= 3
            and arguments[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        ):
            raise ProbeFailure("File writes are forbidden inside the pure helper probe")

    sys.addaudithook(deny_process_or_network)
    started = time.monotonic()
    cpu_started = time.process_time()
    marks = []  # Only small scalar records; never buffers, frames or input graphs.
    observer_wall = observer_cpu = 0.0
    active = None
    hits = []

    def emit(value):
        print(json.dumps(value, sort_keys=True), flush=True)

    def mark(boundary, **extra):
        nonlocal observer_wall, observer_cpu
        mark_start, cpu_start = time.monotonic(), time.process_time()
        current, peak = tracemalloc.get_traced_memory()
        row = {
            "event": "measurement",
            "role": "isolated pure validation probe",
            "revision": label,
            "pid": os.getpid(),
            "phase": active,
            "boundary": boundary,
            "monotonic": mark_start,
            "wall_seconds": mark_start - started,
            "cpu_seconds": cpu_start - cpu_started,
            "rss_bytes": process_usage(os.getpid())["rss_bytes"],
            "traced_current_bytes": current,
            "traced_peak_bytes": peak,
            "tracer_internal_bytes": tracemalloc.get_tracemalloc_memory(),
            **extra,
        }
        require(len(marks) < 24, "measurement mark limit")
        marks.append((active, boundary))
        emit(row)
        observer_wall += time.monotonic() - mark_start
        observer_cpu += time.process_time() - cpu_start

    mark("before imports")
    sys.path[:0] = [str(source), str(source / "bridge")]
    from adminme.finance import dated_projection as dated
    from adminme.finance import publication_provenance as proof
    from adminme.finance.publication import canonical_bytes
    from tests.fixtures.finance_import_load import sized_package
    from tests.fixtures.finance_provenance import seal_publication

    for name, module in tuple(sys.modules.items()):
        if name == "adminme" or name.startswith(("adminme.", "tests.")):
            path = getattr(module, "__file__", None)
            if path:
                require(
                    Path(path).resolve().is_relative_to(source),
                    f"Source escaped pinned checkout: {name}",
                )
    mark("after imports")
    tracemalloc.start()
    mark("before fixture")
    publication, projection, package = sized_package(
        "tenant-synthetic", "member-synthetic"
    )
    mark("after fixture")
    later = deepcopy(publication)
    later["prior_publication_id"] = publication["publication_id"]
    seal_publication(later)

    def pin(value, encoder=canonical_bytes):
        raw = encoder(value)
        return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    def inputs():
        return {
            "publication": pin(publication),
            "projection": pin(projection),
            "successor": pin(later),
            "index": pin(package.index, lambda x: x),
            "blobs": {
                key: pin(raw, lambda x: x) for key, raw in sorted(package.blobs.items())
            },
            "approved_evidence": sorted(package.approved_evidence),
        }

    geometry = {
        "accepted_units": len(publication["accepted_journal_revision_ids"]),
        "proof_blobs": len(package.blobs),
        "shared_variants": len(projection["shared_event_inventory"]),
        "context_references": sum(
            len(c["record_ids"]) for c in projection["detail_manifest"]
        ),
    }
    require(
        geometry
        == dict(
            accepted_units=8022,
            proof_blobs=98,
            shared_variants=3307,
            context_references=38382,
        ),
        "fixture geometry",
    )
    original_inputs = inputs()
    require(
        6_020_614 <= original_inputs["projection"]["bytes"] <= 8 * MIB,
        "dated fixture size",
    )
    proof_size = len(package.index) + sum(map(len, package.blobs.values()))
    require(18 * MIB <= proof_size < 18 * MIB + 2 * 8022, "provenance fixture size")
    mark("inputs prepared", geometry=geometry, input_pins=original_inputs)

    class Receipts(OrderedDict):
        def __contains__(self, key):
            hit = super().__contains__(key)
            hits.append(hit)
            mark("receipt lookup", cache_hit=hit)
            return hit

        def get(self, key, default=None):
            value = super().get(key, default)
            hit = value is not None
            hits.append(hit)
            mark("receipt lookup", cache_hit=hit)
            return value

    outputs = {}

    def measured(phase, function, encoder):
        nonlocal active
        active = phase
        prior_hits = len(hits)
        tracemalloc.reset_peak()
        mark("entry")
        result = function()
        mark("return")
        require(
            hits[prior_hits:] == [phase.endswith("cache hit")], "receipt path mismatch"
        )
        outputs[phase] = pin(result, encoder)
        active = None
        return result

    dated._validation_receipts = Receipts()
    measured(
        PHASES[0], lambda: dated._validate_owned_projection(projection), canonical_bytes
    )
    measured(
        PHASES[1], lambda: dated._validate_owned_projection(projection), canonical_bytes
    )
    proof._proof_receipts = Receipts()
    initial = measured(
        PHASES[2],
        lambda: proof.verify_package(publication, package),
        proof._proof_bytes,
    )
    measured(
        PHASES[3],
        lambda: proof.verify_package(publication, package),
        proof._proof_bytes,
    )
    previous_pin = pin(initial, proof._proof_bytes)
    proof._proof_receipts = Receipts()
    measured(
        PHASES[4],
        lambda: proof.verify_package(later, package, initial),
        proof._proof_bytes,
    )
    measured(
        PHASES[5],
        lambda: proof.verify_package(later, package, initial),
        proof._proof_bytes,
    )
    require(pin(initial, proof._proof_bytes) == previous_pin, "previous proof mutated")
    require(inputs() == original_inputs, "caller inputs mutated")
    for cold, warm in zip(PHASES[::2], PHASES[1::2]):
        require(outputs[cold] == outputs[warm], "cold/cache output mismatch")
    mark("complete")
    emit(
        {
            "event": "complete",
            "revision": label,
            "geometry": geometry,
            "inputs": original_inputs,
            "previous": previous_pin,
            "outputs": outputs,
            "cache_hits": hits,
            "mark_count": len(marks),
            "observer_wall_seconds": observer_wall,
            "observer_cpu_seconds": observer_cpu,
            "schema_cache_note": "Receipt caches reset only; schema/import caches retain their natural fixture/earlier-call state",
            "tracing_note": "Imports untraced; fixture allocations traced; RSS includes native and pre-tracing memory",
        }
    )


def read_child(output, label):
    rows = [
        json.loads(line)
        for line in (output / f"{label}.jsonl").read_text().splitlines()
    ]
    complete = [r for r in rows if r["event"] == "complete"]
    require(len(complete) == 1, "missing/duplicate completion")
    marks = [r for r in rows if r["event"] == "measurement"]
    require(len(marks) == complete[0]["mark_count"] == 24, "incomplete marks")
    for phase in PHASES:
        require(
            [r["boundary"] for r in marks if r["phase"] == phase]
            == ["entry", "receipt lookup", "return"],
            "incomplete phase",
        )
    require(complete[0]["cache_hits"] == [False, True] * 3, "cache paths")
    return complete[0], marks


def compare(before, after, output):
    require(sys.platform == "linux", "Hosted Linux required")
    require(
        not output.exists(), "Evidence directory already exists; no overwrite/retry"
    )
    output.mkdir(parents=True)
    controller = Supervisor(output, budget_root=output.parent)
    install_signal_handlers()
    verdict = {"success": False, "before": BEFORE, "after": AFTER, "lock": LOCK}
    try:
        results = {}
        for label, source, revision in (
            ("before", before, BEFORE),
            ("after", after, AFTER),
        ):
            pin = json.loads((source / ".probe-source.json").read_text())
            require(
                pin
                == {
                    "revision": revision,
                    "production_tree": TREES[label],
                    "lock": LOCK,
                },
                "source pin mismatch",
            )
            environment = {
                "PATH": os.environ["PATH"],
                "HOME": str(output),
                "TMPDIR": str(output),
                "ADMINME_TEST_MODE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            results[label] = controller.run(
                label,
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(__file__).resolve()),
                    "child",
                    str(source),
                    label,
                ],
                source,
                environment,
                validate=lambda: read_child(output, label),
            )
        left, right = (results[k][0] for k in ("before", "after"))
        for key in ("geometry", "inputs", "previous", "outputs", "cache_hits"):
            require(left[key] == right[key], f"before/after {key} mismatch")
        differences = []
        for phase in PHASES:
            pair = []
            for label in ("before", "after"):
                marks = {
                    r["boundary"]: r for r in results[label][1] if r["phase"] == phase
                }
                samples = [
                    json.loads(line)
                    for line in (output / f"{label}.samples.jsonl")
                    .read_text()
                    .splitlines()
                ]
                observed = [
                    r["child"]["rss_bytes"]
                    for r in samples
                    if marks["entry"]["monotonic"]
                    <= r["monotonic"]
                    <= marks["return"]["monotonic"]
                ]
                marks["return"]["sampled_phase_rss"] = {
                    "observations": len(observed),
                    "maximum_bytes": max(observed) if observed else None,
                }
                pair.append(
                    {
                        "lookup_live_delta_bytes": marks["receipt lookup"][
                            "traced_current_bytes"
                        ]
                        - marks["entry"]["traced_current_bytes"],
                        "peak_above_entry_bytes": marks["return"]["traced_peak_bytes"]
                        - marks["entry"]["traced_current_bytes"],
                        "helper_wall_seconds": marks["return"]["wall_seconds"]
                        - marks["entry"]["wall_seconds"],
                        "helper_cpu_seconds": marks["return"]["cpu_seconds"]
                        - marks["entry"]["cpu_seconds"],
                        "lookup_rss_bytes": marks["receipt lookup"]["rss_bytes"],
                        "lookup_rss_delta_bytes": marks["receipt lookup"]["rss_bytes"]
                        - marks["entry"]["rss_bytes"],
                        "lookup_tracer_internal_bytes": marks["receipt lookup"][
                            "tracer_internal_bytes"
                        ],
                    }
                )
            differences.append(
                {
                    "phase": phase,
                    "before": pair[0],
                    "after": pair[1],
                    "after_minus_before": {k: pair[1][k] - pair[0][k] for k in pair[0]},
                    "sampled_phase_rss": {
                        label: next(
                            r
                            for r in results[label][1]
                            if r["phase"] == phase and r["boundary"] == "return"
                        )["sampled_phase_rss"]
                        for label in ("before", "after")
                    },
                }
            )
        verdict.update(success=True, differences=differences)
        verdict["measurement_process_accounting"] = {
            "supervisors": 1,
            "children_created": len(controller.children),
            "maximum_active_owned_processes": 2,
            "self_checks_and_toolchain_setup": "Separate stage, not included in these three created processes",
        }
    except BaseException as exc:
        controller.failed = True
        verdict["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        verdict.update(
            children=controller.children,
            samples=controller.samples,
            wall_seconds=time.monotonic() - controller.started,
        )
        verdict["scope"] = (
            "Pure helper diagnosis only; not full import, durable/replay/cancellation or resource acceptance"
        )
        verdict["final_metrics"] = controller.final_metrics()
        limit_failure = controller.final_failure(verdict["final_metrics"])
        if limit_failure:
            controller.failed = True
            verdict.update(success=False, failure=verdict.get("failure", limit_failure))
        write_json(output / "verdict.json", verdict)
        final_metrics = controller.final_metrics()
        final_failure = controller.final_failure(final_metrics)
        print(
            json.dumps(
                {"after_final_receipt_metrics": final_metrics, "failure": final_failure}
            ),
            flush=True,
        )
        if final_failure:
            controller.failed = True
            verdict.update(
                success=False,
                failure=verdict.get("failure", final_failure),
                final_metrics=final_metrics,
            )
            write_json(output / "verdict.json", verdict)
            raise ProbeFailure(final_failure)
        require(not controller.failed, verdict.get("failure", "Comparison failed"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("child", "compare"))
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    if args.mode == "child":
        probe_child(Path(args.paths[0]).resolve(), args.paths[1])
    else:
        compare(*(Path(p).resolve() for p in args.paths))


if __name__ == "__main__":
    main()
