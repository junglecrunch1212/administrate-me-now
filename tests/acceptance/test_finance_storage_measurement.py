"""One approved fixed-schedule synthetic measurement; no production activation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import contextmanager
import gc
import hashlib
import json
import os
import platform
import signal
import resource
import sys
import threading
import time
from uuid import uuid4

import httpx
import pytest

from tests.acceptance.test_finance_activation import (
    APPROVALS,
    CONTROL,
    IMPORTS,
    packet_for,
)
from tests.acceptance.test_finance_storage_isolation import (
    LIMITS,
    MIB,
    OwnedRun,
    percentiles,
)
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, PRINCIPAL
from tests.fixtures.finance_import_load import sized_package
from tests.fixtures.finance_planning import seal
from tests.fixtures.finance_startup import StartupFrames
from tests.fixtures import finance_watchdog as watchdog
from adminme.finance.worker_protocol import write_json, read_json
from tests.fixtures.finance_provenance import seal_publication

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_SYNTHETIC_STORAGE_MEASUREMENT") != "1"
    or sys.platform != "linux",
    reason="one explicitly selected hosted synthetic measurement only",
)

SCHEDULE = (
    ("warm_core", 30, 40, 0),
    ("baseline_i", 40, 55, 0),
    ("imports", 55, 90, 0),
    ("warm_loaded", 90, 100, 10),
    ("baseline_a", 100, 130, 0),
    ("mixed", 130, 160, 10),
    ("baseline_b", 160, 190, 0),
)
CORE_PATH = "/api/member/v1/surfaces/reminders"
PLANNING_PATH = "/api/member/v1/finance/planning"


def planned_requests(started):
    return [
        {
            "id": f"{name}:{kind}:{slot}",
            "phase": name,
            "kind": kind,
            "slot": slot,
            "scheduled": started + start + slot / hz,
        }
        for name, start, end, planning_hz in SCHEDULE
        for kind, hz in (("core", 20), ("planning", planning_hz))
        if hz
        for slot in range(int((end - start - 1) * hz))
    ]


def complete_request_evidence(report):
    observed = {r["id"]: r for r in report["requests"]}
    assert len(observed) == len(report["requests"]), "duplicate request evidence"
    report["request_evidence"] = [
        observed.get(
            row["id"],
            {
                **row,
                "started": None,
                "milliseconds": None,
                "error": "not_started_after_abort",
            },
        )
        for row in report["planned_requests"]
    ]


class MeasuredRun(OwnedRun):
    runtime_script = "finance_startup.py"
    runtime_case = "measured_lifetime"
    setup_deadline = 10
    setup_complete_deadline = 10

    def __init__(self, root, *, name="measured_lifetime"):
        self.sample_lock = threading.Lock()
        self.pipe_lock = threading.Lock()
        self.calls = set()
        self.work_end = None
        self.setup_stop = threading.Event()
        self.setup_trace_lock = threading.Lock()
        self.setup_stages = []
        self.startup = StartupFrames()
        self.setup_hook = None
        self.client = None
        super().__init__(name, root)
        self.record["setup_stages"] = self.setup_stages
        self.setup_mark("controller.started", process_birth=self.started)

    def setup_mark(self, stage, **fields):
        with self.setup_trace_lock:
            self.setup_stages.append({"stage": stage, "at": time.monotonic(), **fields})

    def setup_check(self):
        if self.setup_stop.is_set():
            raise RuntimeError("synthetic_setup_cancelled")
        if time.monotonic() >= self.started + self.setup_deadline:
            raise TimeoutError("fixed synthetic setup deadline")

    @contextmanager
    def setup_stage(self, name):
        self.setup_mark(name + ".start")
        try:
            self.setup_check()
            yield
            self.setup_check()
        except BaseException as exc:
            self.setup_mark(name + ".end", outcome="failed", error=type(exc).__name__)
            raise
        else:
            self.setup_mark(name + ".end", outcome="passed")

    def sample(self):
        with self.sample_lock:
            return super().sample()

    def _monitor(self):
        try:
            while not self.stop.wait(0.05):
                sample = self.sample()
                violations = [
                    key
                    for key, value in self.limits.items()
                    if key in sample
                    and sample[key] > value
                    and (key != "work_seconds" or self.work_end is None)
                ]
                if violations:
                    self.record["violations"].extend(violations)
                    self.kill()
                    return
                if self.work_end is not None and time.monotonic() - self.work_end > 30:
                    self.record["violations"].append("cleanup_seconds")
                    self.kill()
                    return
        except BaseException as exc:
            self.record["violations"].append("monitor:" + type(exc).__name__)
            self.kill()

    def command(self, op):
        with self.pipe_lock:
            if op is None:
                value = self.startup.read(self.process.stdout)
                self.setup_mark(
                    "runtime.startup_receipt",
                    frame=self.startup.count,
                    fixture_coordinates=value.get("fixture_coordinates"),
                    http_ready=value.get("ready"),
                )
                return value
            return self.startup.control_result(super().command(op))

    async def setup_boundary(self, name):
        self.setup_mark("boundary." + name)
        if self.setup_hook is not None:
            await self.setup_hook(name)

    async def thread(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.calls.add(task)
        task.add_done_callback(self.calls.discard)
        return await asyncio.shield(task)

    async def call(self, op):
        return await self.thread(self.command, op)

    def finish_work(self):
        self.work_end = time.monotonic()
        self.record["work_finished_at"] = self.work_end
        self.record["work_elapsed_seconds"] = self.work_end - self.started
        self.record["work_terminal_sample"] = self.sample()


class PhasedRun(MeasuredRun):
    runtime_case = "phased_lifetime"
    limits = {**LIMITS, "work_seconds": 200}
    setup_deadline = 15
    setup_complete_deadline = 30

    def __init__(self, root, *, name="measured_lifetime"):
        self.supervisor_pid = int(os.environ["FINANCE_WATCHDOG_PID"])
        self.supervisor_birth = int(os.environ["FINANCE_WATCHDOG_BIRTH"])
        self.clock_start = float(os.environ["FINANCE_WATCHDOG_STARTED"])
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.child_cpu_before = before.ru_utime + before.ru_stime
        self.runtime_released = False
        self.runtime_cpu = 0.0
        parent = watchdog.process_row(self.supervisor_pid)
        assert parent and parent["start_ticks"] == self.supervisor_birth
        assert os.getppid() == self.supervisor_pid
        super().__init__(root, name=name)
        row = watchdog.process_row(self.runtime_pid)
        watchdog.notify("runtime", pid=self.runtime_pid, birth=row["start_ticks"])
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, task.cancel)
        self.record["watchdog"] = {
            "pid": self.supervisor_pid,
            "birth": self.supervisor_birth,
        }

    def sample(self):
        with self.sample_lock:
            parent = watchdog.process_row(self.supervisor_pid)
            assert parent and parent["start_ticks"] == self.supervisor_birth
            row = watchdog.sample(
                self.supervisor_pid,
                self.runtime_pid,
                os.getpid(),
                [self.output],
                self.runtime_cpu,
            )
            self.runtime_cpu = row["runtime_executor_cpu_seconds"]
            self.known.update(
                {
                    p["pid"]: p["start_ticks"]
                    for p in row["pids"]
                    if p["pid"] not in {os.getpid(), self.supervisor_pid}
                }
            )
            row["work_seconds"] = row["at"] - self.started
            self.record["samples"].append(row)
            return row

    def release_runtime(self):
        with self.pipe_lock:
            assert not self.runtime_released and self.startup.count == 1
            write_json(self.process.stdin, {"launch": 1})
            self.runtime_released = True
            self.setup_mark("runtime.released")

    def stop_launcher(self):
        with self.pipe_lock:
            assert not self.runtime_released
            if self.startup.count == 0:
                self.startup.read(self.process.stdout)
            write_json(self.process.stdin, {"stop": 1})
            value = read_json(self.process.stdout, 2048)
            assert value == {"stopped": True, "tenant": self.startup.tenant}
            return value

    def close(self):
        try:
            super().close()
        finally:
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            watchdog.notify(
                "runtime_reaped",
                pid=self.runtime_pid,
                cpu_seconds=usage.ru_utime + usage.ru_stime - self.child_cpu_before,
            )

    def finish_work(self):
        super().finish_work()
        watchdog.notify("work_finished", at=self.work_end)


async def phased_packets(run, active):
    await run.setup_boundary("before_coordinates")
    coordinates = await run.call(None)
    await run.setup_boundary("coordinates")
    packets = await bounded_work(
        run, active, run.thread(prepare_packets, run, coordinates), 15
    )
    run.setup_mark("fixture.ready_before_release")
    await run.setup_boundary("packets_ready")
    remaining = run.started + 15 - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("fixture missed fixed preparation deadline")
    await asyncio.sleep(remaining)
    run.setup_deadline = 30
    await run.thread(run.release_runtime)
    await run.setup_boundary("runtime_released")
    ready = await run.call(None)
    assert ready["tenant"] == coordinates["tenant"]
    await run.setup_boundary("http_ready")
    run.setup_mark("branches.joined")
    await run.setup_boundary("joined")
    return ready, *packets


def settled(state):
    return not state["active_executor"] and all(
        not any(row.values()) for row in state["consumers"].values()
    )


def coordinates(state):
    return {
        "authority": state["authority"],
        "counts": state["counts"],
        "finance_events": state["finance_events"],
        "money_checkpoint": state["money_checkpoint"],
    }


def prepare_packets(run, ready):
    """Same deterministic construction as 8b8a0c2e; owned off-loop work only."""
    report = run.record
    with run.setup_stage("fixture.branch"):
        with run.setup_stage("fixture.generate"):
            p, d, package = sized_package(ready["tenant"], PRINCIPAL)
        with run.setup_stage("fixture.initial_packet"):
            manifest, packet = packet_for(p, d, package)
            wire = json.dumps(packet, separators=(",", ":")).encode()
        with run.setup_stage("fixture.successor_publication"):
            later = deepcopy(p)
            later["prior_publication_id"] = p["publication_id"]
            seal_publication(later)
        with run.setup_stage("fixture.successor_projection"):
            later_d = deepcopy(d)
            later_d["prior_projection_id"] = d["projection_id"]
            later_d["projection_id"] = "projection-measured-successor"
            later_d["accounting_publication_id"] = later["publication_id"]
            later_d["accounting_content_sha256"] = later["content_sha256"]
            later_d = seal(later_d)
        with run.setup_stage("fixture.successor_packet"):
            next_manifest, next_packet = packet_for(later, later_d, package)
            next_wire = json.dumps(next_packet, separators=(",", ":")).encode()
        report["geometry"] = {
            "accepted_units": len(p["accepted_journal_revision_ids"]),
            "proof_blobs": len(package.blobs),
            "shared_variants": len(d["shared_event_inventory"]),
            "context_references": sum(
                len(c["record_ids"]) for c in d["detail_manifest"]
            ),
            "wire_bytes": len(wire),
        }
        assert report["geometry"]["accepted_units"] == 8022
        assert report["geometry"]["proof_blobs"] == 98
        selection = {
            "book_id": p["book_id"],
            "context_id": d["contexts"][0]["context_id"],
        }
        expected = (
            p["publication_id"],
            d["projection_id"],
            later["publication_id"],
            later_d["projection_id"],
        )
        with run.setup_stage("fixture.pins"):
            report["constructed_pins"] = {
                "initial": {
                    "publication": manifest["publication"],
                    "projection": manifest["projection"],
                    "wire_sha256": hashlib.sha256(wire).hexdigest(),
                    "wire_bytes": len(wire),
                },
                "successor": {
                    "publication": next_manifest["publication"],
                    "projection": next_manifest["projection"],
                    "wire_sha256": hashlib.sha256(next_wire).hexdigest(),
                    "wire_bytes": len(next_wire),
                },
            }
        with run.setup_stage("fixture.release"):
            del p, d, package, packet, later, later_d, next_packet
            gc.collect()  # Controller buffers only; startup is fully measured.
        return manifest, next_manifest, wire, next_wire, selection, expected


def window_summary(rows, expected):
    timed = [r for r in rows if r.get("milliseconds") is not None]
    value = percentiles(timed)
    value["scheduled"] = expected
    value["recorded"] = len(rows)
    value["valid"] = len(rows) == expected and all(
        r.get("status") == 200 and "error" not in r for r in rows
    )
    value["unstarted"] = sum(r.get("started") is None for r in rows)
    return value


def compare_pair(left, right, loaded, *, comparable):
    statistics = (
        ("p95_ms", 1.0),
        ("observed_p99_ms", 3.0),
        ("cpu_cores", 0.02),
    )
    spreads = {}
    for field, floor in statistics:
        a, b = left.get(field), right.get(field)
        spreads[field] = {
            "left": a,
            "right": b,
            "allowed": max(floor, 0.2 * min(a, b))
            if a is not None and b is not None
            else None,
            "within": a is not None
            and b is not None
            and abs(a - b) <= max(floor, 0.2 * min(a, b)),
        }
    absolute = all(
        row.get("p95_ms") is not None
        and row["p95_ms"] <= 25
        and row["observed_p99_ms"] <= 75
        for row in (left, right, loaded)
    )
    stable = (
        comparable
        and all(row["valid"] for row in (left, right, loaded))
        and all(
            row.get("p95_ms") is not None
            and row["p95_ms"] <= 25
            and row["observed_p99_ms"] <= 75
            for row in (left, right)
        )
        and all(row["within"] for row in spreads.values())
    )
    comparisons = {}
    for name, baseline in (("left", left), ("right", right)):
        differences = {
            field: loaded[field] - baseline[field]
            if loaded.get(field) is not None and baseline.get(field) is not None
            else None
            for field, _ in statistics
        }
        comparisons[name] = {
            **differences,
            "within": all(value is not None for value in differences.values())
            and differences["p95_ms"] <= 5
            and differences["observed_p99_ms"] <= 15
            and differences["cpu_cores"] <= 1.0,
        }
    both = all(row["within"] for row in comparisons.values())
    dependent = comparisons["left"]["within"] != comparisons["right"]["within"]
    verdict = (
        "inconclusive" if not stable or dependent else "passed" if both else "failed"
    )
    return {
        "comparable": comparable,
        "spreads": spreads,
        "stable": stable,
        "absolute_verdict": "passed" if absolute else "failed",
        "against_each": comparisons,
        "added_verdict": verdict,
        "overall_verdict": "failed" if not absolute else verdict,
        "larger_baseline_policy_only": {
            field: loaded[field] - max(left[field], right[field])
            if all(r.get(field) is not None for r in (left, right, loaded))
            else None
            for field, _ in statistics
        },
        "normal_cpu_qualification": "not_measured",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "stable",
        "drift",
        "cold",
        "common_cold",
        "missing",
        "dependent",
        "absolute",
        "coordinates",
    ],
)
def test_baseline_verdict_preserves_invalidity_and_absolute_failure(mutation):
    left = {"p95_ms": 5.0, "observed_p99_ms": 9.0, "cpu_cores": 0.1, "valid": True}
    right, loaded = dict(left), {**left, "p95_ms": 6.0, "observed_p99_ms": 10.0}
    if mutation == "drift":
        right["p95_ms"] = 9.0
    elif mutation == "cold":
        left["p95_ms"] = 80.0
        left["observed_p99_ms"] = 90.0
    elif mutation == "common_cold":
        left.update(p95_ms=80.0, observed_p99_ms=90.0)
        right.update(left)
        loaded.update(p95_ms=81.0, observed_p99_ms=91.0)
    elif mutation == "missing":
        loaded["valid"] = False
    elif mutation == "dependent":
        right["p95_ms"] = 6.0
        loaded["p95_ms"] = 10.5
    elif mutation == "absolute":
        left.update(p95_ms=24.0, observed_p99_ms=74.0)
        right.update(left)
        loaded.update(p95_ms=26.0, observed_p99_ms=76.0)
    result = compare_pair(left, right, loaded, comparable=mutation != "coordinates")
    assert result["added_verdict"] == (
        "passed" if mutation in {"stable", "absolute"} else "inconclusive"
    )
    if mutation in {"cold", "common_cold", "absolute"}:
        assert result["overall_verdict"] == "failed"
    assert set(result["against_each"]) == {"left", "right"}


async def bounded_work(run, active, work, end):
    report = run.record
    task = asyncio.create_task(work)
    active.add(task)
    try:
        done, _ = await asyncio.wait(
            {task}, timeout=max(0, run.started + end - time.monotonic())
        )
        if not done or time.monotonic() > run.started + end:
            report["faults"].append(
                {"kind": "phase_deadline", "end": end, "at": time.monotonic()}
            )
            task.cancel()
            raise TimeoutError("fixed synthetic phase deadline")
        return task.result()
    finally:
        if task.done():
            active.discard(task)


async def setup_runtime(run, active):
    report = run.record

    async def setup():
        await run.setup_boundary("before_coordinates")
        with run.setup_stage("runtime.coordinates_wait"):
            coordinates = await run.call(None)
            assert set(coordinates) == {"fixture_coordinates", "tenant"}
            assert coordinates["fixture_coordinates"] == 1
            assert isinstance(coordinates["tenant"], str)

        await run.setup_boundary("coordinates")

        async def readiness():
            with run.setup_stage("runtime.readiness_branch"):
                ready = await run.call(None)
                assert ready["ready"]
                assert ready["tenant"] == coordinates["tenant"]
                await run.setup_boundary("http_ready")
                return ready

        run.setup_mark("branches.submitted")
        fixture_task = asyncio.create_task(
            run.thread(prepare_packets, run, coordinates)
        )
        readiness_task = asyncio.create_task(readiness())
        branches = (fixture_task, readiness_task)
        active.update(branches)
        try:
            packets, ready = await asyncio.gather(*branches)
            run.setup_check()
            run.setup_mark("branches.joined")
            await run.setup_boundary("joined")
            return ready, *packets
        except BaseException as exc:
            run.setup_stop.set()
            run.setup_mark("branches.join_failed", error=type(exc).__name__)
            for branch in branches:
                branch.cancel()
            await asyncio.gather(*branches, return_exceptions=True)
            raise
        finally:
            active.difference_update(branches)

    (
        ready,
        manifest,
        next_manifest,
        wire,
        next_wire,
        selection,
        expected,
    ) = await bounded_work(
        run,
        active,
        phased_packets(run, active) if isinstance(run, PhasedRun) else setup(),
        run.setup_complete_deadline,
    )
    with run.setup_stage("controller.client"):
        client = run.client = httpx.AsyncClient(
            base_url=ready["url"],
            trust_env=False,
            timeout=1,
            limits=httpx.Limits(
                max_connections=6, max_keepalive_connections=6, keepalive_expiry=4
            ),
        )

    async def controls():
        control = await client.put(
            CONTROL,
            headers=DEMO_HEADERS,
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "binding": manifest["binding"],
                "books_enabled": True,
                "cos_analysis_enabled": False,
            },
        )
        assert control.status_code == 200, control.text
        report["setup_control"] = control.json()
        await run.setup_boundary("control")
        ids = []
        report["setup_approval_ids"] = ids
        for value in (manifest, next_manifest):
            response = await client.post(
                APPROVALS,
                headers=DEMO_HEADERS,
                json={
                    "request_id": str(uuid4()),
                    "expected_policy_revision": 1,
                    "manifest": value,
                },
            )
            assert response.status_code == 200, response.text
            ids.append(response.json()["approval_event_id"])
            await run.setup_boundary("approval_" + str(len(ids)))
        state = await run.call("inspect")
        assert settled(state), state["consumers"]
        await run.setup_boundary("settled")
        return ids, state

    with run.setup_stage("runtime.controls_and_settlement"):
        approvals, state = await bounded_work(
            run, active, controls(), run.setup_complete_deadline
        )
    run.setup_mark("setup.completed")
    report["setup"] = {
        "finished": time.monotonic(),
        "state": state,
        "resources": run.sample(),
    }

    return (
        ready,
        manifest,
        next_manifest,
        wire,
        next_wire,
        selection,
        expected,
        approvals,
        state,
    )


async def cleanup_run(run, active, *, shutdown_sent=False):
    report = run.record
    run.setup_stop.set()
    run.setup_mark("owned_work.stopping")
    run.finish_work()
    cleanup_started = time.monotonic()
    for task in active:
        task.cancel()
    try:
        async with asyncio.timeout(
            min(
                15, max(4, run.started + run.setup_complete_deadline - time.monotonic())
            )
        ):
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            if run.calls:
                await asyncio.gather(
                    *(asyncio.shield(call) for call in list(run.calls)),
                    return_exceptions=True,
                )
            if (
                isinstance(run, PhasedRun)
                and not run.runtime_released
                and run.process.poll() is None
            ):
                report["cleanup_shutdown"] = await run.thread(run.stop_launcher)
                shutdown_sent = True
            if not shutdown_sent and run.process.poll() is None:
                # A cancelled setup may leave the second startup frame
                # unread. Consume it before issuing a control command.
                while not run.startup.ready and run.startup.count < 2:
                    await run.call(None)
                assert run.startup.ready, "synthetic HTTP readiness unavailable"
                report["cleanup_shutdown"] = await run.call("shutdown")
    except BaseException as exc:
        report["cleanup_request_error"] = type(exc).__name__
    if run.client is not None:
        await run.client.aclose()
    try:
        run.close()
    finally:
        if run.calls:
            _, unresolved = await asyncio.wait(
                list(run.calls),
                timeout=max(0, cleanup_started + 29 - time.monotonic()),
            )
            report["unsettled_owned_calls"] = len(unresolved)
        report["total_cleanup_seconds"] = time.monotonic() - cleanup_started
        run.setup_mark(
            "owned_work.cleaned",
            unresolved_calls=report.get("unsettled_owned_calls", 0),
        )
        (run.output / (run.name + ".json")).write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert report["total_cleanup_seconds"] < 30
    assert not report.get("unsettled_owned_calls", 0)


async def test_fixed_schedule_representative_storage_measurement(tmp_path):
    run = PhasedRun(tmp_path / "measured")
    report = run.record
    report.update(
        schedule=SCHEDULE,
        phases=[],
        requests=[],
        planned_requests=planned_requests(run.started),
        imports=[],
        faults=[],
        source_tree="9e739956d85316dff9b54880914e576268fe56fb",
        lock_tree="cc2102f6e9dd2fd029bd636566806493c1df91dc",
        production_source_changed=False,
        environment={
            "python": sys.version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "affinity": sorted(os.sched_getaffinity(0)),
            "run_id": os.getenv("GITHUB_RUN_ID"),
        },
        live_grant_scope="UNKNOWN; no live inspection",
        request_policy={
            "core_hz": 20,
            "planning_hz": 10,
            "core_inflight": 4,
            "planning_inflight": 1,
            "timeout_seconds": 1,
            "keepalive_expiry_seconds": 4,
            "retries": 0,
        },
    )
    client = None
    active = set()
    completed = False
    shutdown_sent = False

    async def until(elapsed):
        remaining = run.started + elapsed - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def bounded(work, end):
        return await bounded_work(run, active, work, end)

    try:
        (
            ready,
            manifest,
            next_manifest,
            wire,
            next_wire,
            selection,
            expected,
            approvals,
            state,
        ) = await setup_runtime(run, active)
        client = run.client
        initial_authority = state["authority"]

        async def import_sequence():
            for index, (name, approval, payload) in enumerate(
                (
                    ("initial", approvals[0], wire),
                    ("duplicate", approvals[0], wire),
                    ("successor", approvals[1], next_wire),
                )
            ):

                async def stream():
                    for offset in range(0, len(payload), 65536):
                        yield payload[offset : offset + 65536]
                        await asyncio.sleep(0)

                row = {"name": name, "started": time.monotonic()}
                report["imports"].append(row)
                response = await client.post(
                    IMPORTS + approval,
                    headers={
                        **DEMO_HEADERS,
                        "Content-Type": "application/json",
                        "X-Measurement-Id": name,
                    },
                    content=stream(),
                    timeout=35,
                )
                row.update(
                    finished=time.monotonic(),
                    status=response.status_code,
                    receipt=response.json(),
                )
                assert response.status_code == 200, response.text
                assert (
                    response.json()["accounting"]["publication_id"]
                    == expected[2 if index == 2 else 0]
                )
                assert (
                    response.json()["dated"]["projection_id"]
                    == expected[3 if index == 2 else 1]
                )
                if name == "duplicate":
                    assert (
                        row["receipt"]["accounting"]["event_id"]
                        == report["imports"][0]["receipt"]["accounting"]["event_id"]
                    )
                row["durable"] = await run.call("inspect")
                assert settled(row["durable"])
                assert row["durable"]["authority"] == initial_authority
                row["resources"] = run.sample()
            before = report["imports"][-1]["durable"]
            report["replay_started"] = time.monotonic()
            after = await run.call("replay")
            report["replay"] = {
                "finished": time.monotonic(),
                "durable": after,
                "resources": run.sample(),
            }
            assert coordinates(before) == coordinates(after)
            assert settled(after)

        for name, start, end, finance_hz in SCHEDULE:
            await until(start)
            phase = {
                "name": name,
                "start_offset": start,
                "end_offset": end,
                "started": time.monotonic(),
                "begin_coordinates": coordinates(state),
                "begin_settled": settled(state),
                "start_resources": run.sample(),
                "requests": [],
            }
            report["phases"].append(phase)
            owned_requests = set()

            async def arrivals(kind, hz, capacity):
                own = set()
                count = int((end - start - 1) * hz)
                for slot in range(count):
                    planned = run.started + start + slot / hz
                    await asyncio.sleep(max(0, planned - time.monotonic()))
                    now = time.monotonic()
                    row = {
                        "id": f"{name}:{kind}:{slot}",
                        "phase": name,
                        "kind": kind,
                        "slot": slot,
                        "scheduled": planned,
                        "started": None,
                        "milliseconds": None,
                    }
                    report["requests"].append(row)
                    phase["requests"].append(row)
                    if now - planned >= 1 / hz:
                        row["error"] = "missed_arrival_slot"
                        continue
                    if len(own) >= capacity:
                        row["error"] = "inflight_limit"
                        continue

                    async def request(row=row, slot=slot, kind=kind):
                        row["started"] = time.monotonic()
                        row["schedule_delay_ms"] = (
                            row["started"] - row["scheduled"]
                        ) * 1000
                        try:
                            params = None
                            if kind == "planning":
                                params = {
                                    **selection,
                                    "report_id": "planning_events"
                                    if slot % 2
                                    else "planning_projection",
                                    "horizon_days": (7, 30, 90, 365)[slot % 4],
                                    "offset": 50 * (slot % 5),
                                }
                            row["path"] = (
                                PLANNING_PATH if kind == "planning" else CORE_PATH
                            )
                            row["parameters"] = params
                            response = await client.get(
                                PLANNING_PATH if kind == "planning" else CORE_PATH,
                                headers={**DEMO_HEADERS, "X-Measurement-Id": row["id"]},
                                params=params,
                            )
                            row["status"] = response.status_code
                            if kind == "planning" and response.status_code == 200:
                                body = response.json()
                                row["qualified"] = (
                                    body.get("state") == "qualified"
                                    and body.get("projection", {}).get("id")
                                    == expected[3]
                                    and body.get("publication", {}).get(
                                        "publication_id"
                                    )
                                    == expected[2]
                                )
                                if not row["qualified"]:
                                    row["error"] = "planning_result_coordinates"
                        except BaseException as exc:
                            row["error"] = type(exc).__name__
                            if isinstance(exc, asyncio.CancelledError):
                                raise
                        finally:
                            row["finished"] = time.monotonic()
                            row["milliseconds"] = (
                                row["finished"] - row["started"]
                            ) * 1000

                    task = asyncio.create_task(request())
                    own.add(task)
                    owned_requests.add(task)
                    active.add(task)
                    task.add_done_callback(own.discard)
                    task.add_done_callback(active.discard)

            async def traffic():
                streams = [arrivals("core", 20, 4)]
                if finance_hz:
                    streams.append(arrivals("planning", finance_hz, 1))
                await asyncio.gather(*streams)
                if owned_requests:
                    await asyncio.gather(*owned_requests)

            async def drain_boundary():
                await until(end - 1)
                phase["drain_started"] = time.monotonic()
                phase["drain_resources"] = run.sample()

            traffic_task = asyncio.create_task(traffic())
            tasks = [traffic_task, asyncio.create_task(drain_boundary())]
            if name == "imports":
                tasks.append(asyncio.create_task(import_sequence()))
            active.update(tasks)
            done, pending = await asyncio.wait(
                tasks, timeout=max(0, run.started + end - time.monotonic())
            )
            if pending or time.monotonic() > run.started + end:
                phase["error"] = "phase_deadline"
                for task in pending:
                    task.cancel()
                raise TimeoutError(name + " missed fixed end")
            for task in done:
                active.discard(task)
                task.result()
            phase["active_finished"] = time.monotonic()
            state = await bounded(run.call("inspect"), end)
            phase["end_coordinates"] = coordinates(state)
            phase["end_settled"] = settled(state)
            phase["core"] = window_summary(
                [r for r in phase["requests"] if r["kind"] == "core"],
                int((end - start - 1) * 20),
            )
            phase["planning"] = window_summary(
                [r for r in phase["requests"] if r["kind"] == "planning"],
                int((end - start - 1) * finance_hz),
            )
            assert phase["core"]["valid"] and phase["planning"]["valid"], {
                "phase": name,
                "core": phase["core"],
                "planning": phase["planning"],
            }
            assert state["authority"] == initial_authority
            assert phase["end_settled"]
            await until(end)
            phase["finished"] = time.monotonic()
            phase["end_resources"] = run.sample()
            elapsed = phase["end_resources"]["at"] - phase["start_resources"]["at"]
            phase["core"]["cpu_cores"] = (
                phase["end_resources"]["runtime_executor_cpu_seconds"]
                - phase["start_resources"]["runtime_executor_cpu_seconds"]
            ) / elapsed
            phase["cpu_window_seconds"] = elapsed
            phase["cpu_segments"] = {}
            for segment, first, last in (
                ("active", phase["start_resources"], phase["drain_resources"]),
                ("drain", phase["drain_resources"], phase["end_resources"]),
            ):
                phase["cpu_segments"][segment] = {
                    "seconds": last["at"] - first["at"],
                    "runtime_executor_cpu_seconds": last["runtime_executor_cpu_seconds"]
                    - first["runtime_executor_cpu_seconds"],
                    "whole_tree_cpu_seconds": last["whole_tree_cpu_seconds"]
                    - first["whole_tree_cpu_seconds"],
                }
            if name == "baseline_i":
                report["warmed_before_import"] = phase["end_resources"]
            if name == "baseline_a":
                report["warmed_before_query"] = phase["end_resources"]

        async def finish():
            nonlocal shutdown_sent
            terminal = await run.call("inspect")
            assert terminal["authority"] == initial_authority
            assert terminal["counts"]["finance_publications"] == 2
            assert terminal["counts"]["finance_dated_projections"] == 2
            assert len(terminal["audit"]) == 3
            assert all(r["payload"]["outcome"] == "accepted" for r in terminal["audit"])
            assert all(
                r["reaped"] and r["exit_code"] == 0 for r in terminal["lifetimes"]
            )
            assert settled(terminal)
            report["terminal"] = terminal
            report["settled_after"] = run.sample()
            await run.call("shutdown")
            shutdown_sent = True

        await bounded(finish(), 200)
        report["state"] = "correctness_passed"
        completed = True
    except BaseException as exc:
        report["failure"] = {
            "class": type(exc).__name__,
            "message": str(exc)[:1000],
            "at": time.monotonic(),
        }
        raise
    finally:
        try:
            await cleanup_run(run, active, shutdown_sent=shutdown_sent)
        finally:
            try:
                complete_request_evidence(report)
                summarize(report)
            except BaseException as exc:
                report["summary_error"] = type(exc).__name__
            (run.output / (run.name + ".json")).write_text(
                json.dumps(report, indent=2) + "\n"
            )
        assert not completed or report["work_elapsed_seconds"] <= 200


def summarize(report):
    phases = {p["name"]: p for p in report["phases"]}
    report["qualification"] = {
        "native": False,
        "added_verdict": "incomplete",
        "normal_cpu": "not_measured",
    }
    if all(
        n in phases and "cpu_cores" in phases[n].get("core", {})
        for n in ("baseline_i", "imports", "baseline_a", "mixed", "baseline_b")
    ):
        i, imp, a, mixed, b = [
            phases[n]
            for n in ("baseline_i", "imports", "baseline_a", "mixed", "baseline_b")
        ]
        same = all(
            p["begin_settled"]
            and p["end_settled"]
            and p["begin_coordinates"] == a["begin_coordinates"]
            and p["end_coordinates"] == a["begin_coordinates"]
            for p in (a, mixed, b)
        )
        report["qualification"]["query"] = compare_pair(
            a["core"], b["core"], mixed["core"], comparable=same
        )
        report["qualification"]["import"] = compare_pair(
            i["core"],
            a["core"],
            imp["core"],
            comparable=i["begin_coordinates"]["authority"]
            == a["begin_coordinates"]["authority"]
            and i["begin_settled"]
            and a["begin_settled"],
        )
        verdicts = [
            report["qualification"][name]["added_verdict"]
            for name in ("query", "import")
        ]
        report["qualification"]["added_verdict"] = (
            "failed"
            if "failed" in verdicts
            else "inconclusive"
            if "inconclusive" in verdicts
            else "passed"
        )
    if report.get("warmed_before_import"):
        before = report["warmed_before_import"]["runtime_executor_rss_bytes"]
        report["resources"] = {
            "added_peak_bytes": max(
                s["runtime_executor_rss_bytes"] for s in report["samples"]
            )
            - before,
            "startup_and_warmup_included": True,
        }
        if report.get("settled_after"):
            report["resources"]["retained_growth_bytes"] = (
                report["settled_after"]["runtime_executor_rss_bytes"] - before
            )
            report["resources"]["retained_8_mib"] = (
                report["resources"]["retained_growth_bytes"] <= 8 * MIB
            )
        report["resources"]["peak_128_mib"] = (
            report["resources"]["added_peak_bytes"] <= 128 * MIB
        )
