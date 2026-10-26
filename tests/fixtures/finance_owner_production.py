"""Isolated NON-QUALIFYING memory diagnostic; never collected by pytest.

The external controller drives shipping HTTP owner control/approval/import and a
single ordinary Money rebuild. Runtime lifecycle, importer, EventLog, subscribers,
and projections are not replaced. No advisor override, model or provider runs.
The exact 9a application and watchdog remain unchanged. Fixture-only observation
replaces timed load windows; no resource or latency qualification is attempted.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import sys
import time
import traceback
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
PHASES = {
    "synthetic_fixture_build": 45,
    "runtime_startup": 30,
    "settled_preimport_observation": 10,
    "single_initial_import": 120,
    "settled_postimport_observation": 20,
    "single_full_money_replay": 60,
    "settled_postreplay_observation": 20,
    "shutdown_receipt": 15,
}
MIB = 1024**2
EVIDENCE_FILE_CAP = 16 * MIB
EVIDENCE_TOTAL_CAP = 32 * MIB
EVIDENCE_FILE_COUNT = 64
FINAL_EVIDENCE_FILES = frozenset(
    {"watchdog.json", "evidence-manifest.json", "export-receipt.json"}
)
CONTROL = "/api/member/v1/settings/finance"
APPROVALS = "/api/member/v1/finance/import-approvals"
SURFACE = "/api/member/v1/surfaces/finance"
PLANNING = "/api/member/v1/finance/planning"
# An ordinary authenticated, meaningful owner read, without Finance payloads.
CORE = "/api/member/v1/surfaces/reminders"


def core_response_fingerprint(body):
    """Preserve raw evidence; exclude only two validated observation clocks."""

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("synthetic_core_duplicate_key")
            value[key] = item
        return value

    def nonfinite(value):
        raise ValueError("synthetic_core_nonfinite")

    value = json.loads(body, object_pairs_hook=unique, parse_constant=nonfinite)
    try:
        if (
            value["surface"] != "reminders"
            or value["cockpit"]["version"] != "deterministic-reminders-cockpit-v2"
            or value["freshness"]["contract"] != "surface-freshness-v1"
        ):
            raise ValueError("synthetic_core_contract")
        clocks = {
            path + ".generated_at": value[path]["generated_at"]
            for path in ("cockpit", "freshness")
        }
        for clock in clocks.values():
            if not isinstance(clock, str) or not 1 <= len(clock) <= 64:
                raise ValueError("synthetic_core_clock")
            parsed = datetime.fromisoformat(clock)
            if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
                raise ValueError("synthetic_core_clock")
        if clocks["cockpit.generated_at"] != clocks["freshness.generated_at"]:
            raise ValueError("synthetic_core_clock_mismatch")
        for path in ("cockpit", "freshness"):
            del value[path]["generated_at"]
    except (KeyError, TypeError) as exc:
        raise ValueError("synthetic_core_contract") from exc
    stable = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return {
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "semantic_sha256": hashlib.sha256(stable).hexdigest(),
        "observed_clocks": clocks,
    }


def append(root, name, row):
    raw = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
    # Raw evidence is at the artifact root; scratch is separately bounded.
    names = {p.name for p in root.iterdir() if p.is_file()}
    if len(names | FINAL_EVIDENCE_FILES | {name}) > EVIDENCE_FILE_COUNT:
        raise ValueError("evidence_file_count_cap")
    path = root / name
    size = path.stat().st_size if path.exists() else 0
    if len(raw) > 65536 or size + len(raw) > EVIDENCE_FILE_CAP:
        raise ValueError("evidence_record_or_file_cap")
    if (
        sum(p.stat().st_size for p in root.iterdir() if p.is_file()) + len(raw)
        > EVIDENCE_TOTAL_CAP - 65536
    ):
        raise ValueError("evidence_total_cap")
    with path.open("ab", buffering=0) as stream:
        stream.write(raw)


async def receive(sock):
    data = bytearray()
    loop = asyncio.get_running_loop()
    while not data.endswith(b"\n"):
        part = await loop.sock_recv(sock, 1)
        if not part or len(data) >= 65536:
            raise ValueError("runtime_control_closed_or_oversize")
        data.extend(part)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("runtime_control_object")
    return value


async def send(sock, value):
    raw = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    if len(raw) > 65536:
        raise ValueError("runtime_control_oversize")
    await asyncio.get_running_loop().sock_sendall(sock, raw)


async def runtime_main(root, listener_fd, control_fd, checkout):
    sys.path[:0] = [str(REPO), str(REPO / "bridge")]
    from tests.fixtures.finance_native_resources import (
        FixtureGuard,
        prepare_synthetic_runtime,
    )

    prepare_synthetic_runtime(root, checkout)
    from adminme.finance import release
    from adminme.runtime.application import create_application
    from tests.acceptance.test_finance_activation import configured
    from tests.fixtures.finance_owner_native_memory import BOUNDARIES, Observations
    from adminme.finance.worker import worker_usage
    import uvicorn

    assert release.ADVISOR_AVAILABLE is False
    parent = os.getppid()
    listener = socket.socket(fileno=listener_fd)
    control = socket.socket(fileno=control_fd)
    control.setblocking(False)
    faults = []
    guard = FixtureGuard(root, listener.getsockname()[1], faults, finance_workers=True)
    sys.addaudithook(guard)
    observations = Observations()
    app = create_application(configured(root / "instance"))
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", lifespan="off", access_log=False)
    )
    # The actual production lifecycle owns every runtime component and shutdown.
    async with app.router.lifespan_context(app):
        serving = asyncio.create_task(server.serve(sockets=[listener]))

        async def parent_guard():
            while os.getppid() == parent and not server.should_exit:
                await asyncio.sleep(0.2)
            if os.getppid() != parent:
                faults.append("controller_parent_lost")
                server.should_exit = True
                control.close()

        watching = asyncio.create_task(parent_guard())
        try:
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("runtime_startup_failed")
                await asyncio.sleep(0.01)
            await send(
                control, {"ready": True, "advisor_available": release.ADVISOR_AVAILABLE}
            )
            replayed = False
            while True:
                command = await receive(control)
                if command == {"operation": "rebuild_money"} and not replayed:
                    replayed = True
                    await app.state.runtime.projections.rebuild("money")
                    await send(control, {"rebuilt": "money"})
                elif command.get("operation") in {
                    "observe_" + boundary for boundary in BOUNDARIES
                } and set(command) == {"operation"}:
                    async with asyncio.timeout(5):
                        while True:
                            state = await app.state.runtime.bus.subscriber_status(
                                "projection:money"
                            )
                            assert not state.get("degraded"), "money_degraded"
                            if (
                                state["lag_count"] == 0
                                and not state.get("draining", False)
                                and worker_usage()["active"] is None
                            ):
                                break
                            await asyncio.sleep(0.02)
                    await send(
                        control,
                        observations.capture(
                            command["operation"].removeprefix("observe_")
                        ),
                    )
                elif command == {"operation": "shutdown"}:
                    break
                else:
                    raise ValueError("runtime_control_operation")
        finally:
            server.should_exit = True
            watching.cancel()
            await asyncio.gather(watching, return_exceptions=True)
            await serving
    # Sent only AFTER the normal runtime lifespan has settled.
    await send(
        control,
        {
            "shutdown": True,
            "guard_faults": faults,
            "finance_launches": guard.finance_launches,
        },
    )
    control.close()
    if faults:
        raise RuntimeError("fixture_guard_fault")


class Run:
    def __init__(self, root):
        from tests.fixtures.finance_watchdog import notify

        self.root, self.notify = root, notify
        self.phase_name = None
        self.phases = []
        self.rows = []
        self.runtime = None
        self.control = None
        self.listener = None
        self.sequence = 0
        self.deadline = 0.0

    @asynccontextmanager
    async def phase(self, name):
        assert list(PHASES)[len(self.phases)] == name
        self.phase_name = name
        row = {"phase": name, "started": time.monotonic(), "cap_seconds": PHASES[name]}
        self.deadline = row["started"] + PHASES[name]
        self.notify("phase", name=name, edge="start")
        append(self.root, "phases.jsonl", {**row, "edge": "start"})
        try:
            async with asyncio.timeout_at(self.deadline):
                yield
            row["complete"] = True
        except BaseException as exc:
            row.update(
                complete=False, error_type=type(exc).__name__, error=str(exc)[:2000]
            )
            raise
        finally:
            row["finished"] = time.monotonic()
            append(self.root, "phases.jsonl", {**row, "edge": "end"})
            self.notify("phase", name=name, edge="end")
            self.phases.append(row)

    async def request(
        self,
        client,
        method,
        path,
        *,
        body=None,
        params=None,
        timeout=2,
        full=False,
        request_kind="control",
        slot=None,
    ):
        self.sequence += 1
        row = {
            "request": self.sequence,
            "phase": self.phase_name,
            "request_kind": request_kind,
            "slot": slot,
            "method": method,
            "path": path,
            "params": params,
            "scheduled": slot["due"] if slot else time.monotonic(),
            "started": time.monotonic(),
        }
        value = None
        try:
            # asyncio timeout is a total wall-clock bound, including pool/response.
            async with asyncio.timeout(
                min(timeout, max(0.001, self.deadline - time.monotonic()))
            ):
                response = await client.request(
                    method, path, json=body, params=params, timeout=timeout
                )
                row.update(
                    status=response.status_code,
                    bytes=len(response.content),
                    sha256=hashlib.sha256(response.content).hexdigest(),
                )
                if full or response.status_code != 200:
                    row["body"] = response.text[:20000]
                    row["body_truncated"] = len(response.text) > 20000
                response.raise_for_status()
                value = response.json()
                if request_kind == "core":
                    row.update(core_response_fingerprint(response.content))
                if request_kind == "planning":
                    assert (
                        value.get("state") == "qualified"
                        and value.get("projection", {}).get("id") == self.projection_id
                        and value.get("publication", {}).get("publication_id")
                        == self.publication_id
                    ), value
                row["validated"] = True
        except BaseException as exc:
            row.update(
                error_type=type(exc).__name__, error=str(exc)[:2000], validated=False
            )
            raise
        finally:
            row["finished"] = time.monotonic()
            row["milliseconds"] = (row["finished"] - row["scheduled"]) * 1000
            append(self.root, "requests.jsonl", row)
            self.rows.append(row)
        return value

    async def load(self, client, *, planning=False, stop=None, seconds=10):
        """Fixed open-loop slots, no queue/retry; retain every rejected/failed slot."""
        started = time.monotonic()
        tasks = set()
        active = {"core": 0, "planning": 0}

        async def call(request_kind, slot):
            try:
                await self.request(
                    client,
                    "GET",
                    CORE if request_kind == "core" else PLANNING,
                    params=None
                    if request_kind == "core"
                    else {
                        **self.selection,
                        "report_id": "planning_events"
                        if slot["index"] % 2
                        else "planning_projection",
                        "horizon_days": (7, 30, 90, 365)[slot["index"] % 4],
                        "offset": 50 * (slot["index"] % 5),
                    },
                    request_kind=request_kind,
                    slot=slot,
                )
            except Exception:
                pass  # The raw error remains in the row and fails the verdict.
            finally:
                active[request_kind] -= 1

        try:
            for index in range(math.ceil(seconds * 20)):
                due = started + index / 20
                await asyncio.sleep(max(0, due - time.monotonic()))
                if stop is not None and stop.is_set():
                    break
                for request_kind in (
                    ["core", "planning"] if planning and index % 2 == 0 else ["core"]
                ):
                    slot = {
                        "index": index // (2 if request_kind == "planning" else 1),
                        "due": due,
                    }
                    limit = 1 if request_kind == "planning" else 4
                    # Record scheduled arrival before dispatch so abrupt termination
                    # leaves an identifiable missing terminal, never a false pass.
                    append(
                        self.root,
                        "schedule.jsonl",
                        {
                            "phase": self.phase_name,
                            "request_kind": request_kind,
                            **slot,
                        },
                    )
                    if active[request_kind] >= limit:
                        row = {
                            "phase": self.phase_name,
                            "request_kind": request_kind,
                            "slot": slot,
                            "scheduled": due,
                            "started": None,
                            "finished": time.monotonic(),
                            "milliseconds": None,
                            "validated": False,
                            "error_type": "inflight_limit",
                        }
                        append(self.root, "requests.jsonl", row)
                        self.rows.append(row)
                    else:
                        active[request_kind] += 1
                        task = asyncio.create_task(call(request_kind, slot))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def alongside(self, client, operation, seconds):
        stop = asyncio.Event()
        loading = asyncio.create_task(self.load(client, stop=stop, seconds=seconds))
        try:
            return await operation
        finally:
            stop.set()
            await loading

    async def command(self, operation):
        await send(self.control, {"operation": operation})
        return await receive(self.control)

    async def observe(self, boundary):
        from tests.fixtures.finance_watchdog import process_row, tree_rows

        before_at = time.monotonic()
        before = process_row(self.runtime.pid)
        assert [row["pid"] for row in tree_rows(self.runtime.pid)] == [self.runtime.pid]
        result = await self.command("observe_" + boundary)
        after = process_row(self.runtime.pid)
        assert before["start_ticks"] == after["start_ticks"]
        assert result["pid"] == self.runtime.pid and result["boundary"] == boundary
        assert result["qualification"] is False and result["reset_counters"] is False
        assert [row["pid"] for row in tree_rows(self.runtime.pid)] == [self.runtime.pid]
        append(
            self.root,
            "native-memory-observations.jsonl",
            {
                **result,
                "phase": self.phase_name,
                "external_before_at": before_at,
                "external_before": before,
                "external_after_at": time.monotonic(),
                "external_after": after,
            },
        )

    async def readback(self, client):
        value = await self.request(client, "GET", SURFACE, full=True)
        assert (
            value["cockpit"]["accepted_books"]["publication_id"] == self.publication_id
        )
        for params in ({"book_id": self.book_id}, self.selection):
            value = await self.request(
                client, "GET", PLANNING, params=params, full=True
            )
            assert value["projection"]["id"] == self.projection_id, value
        choices = await self.request(
            client,
            "GET",
            "/api/member/v1/finance/choices",
            params={"book_id": self.book_id},
            full=True,
        )
        assert choices["publication"]["publication_id"] == self.publication_id
        return {
            "publication_id": self.publication_id,
            "projection_id": self.projection_id,
            "selection_sha256": hashlib.sha256(
                json.dumps(value, sort_keys=True).encode()
            ).hexdigest(),
        }


def percentile(values, proportion):
    return (
        sorted(values)[max(0, math.ceil(len(values) * proportion) - 1)]
        if values
        else None
    )


def verdict(run):
    trace = [
        json.loads(line)
        for line in (run.root / "watchdog-trace.jsonl").read_text().splitlines()
        if line
    ]
    schedule = [
        json.loads(line)
        for line in (run.root / "schedule.jsonl").read_text().splitlines()
        if line
    ]
    windows = {}
    for phase in run.phases:
        name = phase["phase"]
        samples = [s for s in trace if phase["started"] <= s["at"] <= phase["finished"]]
        rows = [
            r for r in run.rows if r["phase"] == name and r["request_kind"] == "core"
        ]
        if not rows:
            continue
        timings = [r["milliseconds"] for r in rows if r.get("milliseconds") is not None]
        expected = sum(
            s["phase"] == name and s["request_kind"] == "core" for s in schedule
        )
        windows[name] = {
            "recorded": len(rows),
            "scheduled": expected,
            "valid": len(rows) == expected and all(r["validated"] for r in rows),
            "p95_ms": percentile(timings, 0.95),
            "observed_p99_ms": percentile(timings, 0.99),
            "semantic_hashes": sorted(
                {r["semantic_sha256"] for r in rows if "semantic_sha256" in r}
            ),
            "cpu_cores": (
                samples[-1]["runtime_executor_cpu_seconds"]
                - samples[0]["runtime_executor_cpu_seconds"]
            )
            / (samples[-1]["at"] - samples[0]["at"])
            if len(samples) >= 2
            else None,
        }
    left, right, loaded = [
        windows.get(n, {})
        for n in (
            "loaded_core_baseline_before",
            "loaded_core_baseline_after",
            "loaded_planning_probe",
        )
    ]
    fields = {"p95_ms": (1, 5), "observed_p99_ms": (3, 15), "cpu_cores": (0.02, 1)}
    complete = all(
        row.get("valid") and all(row.get(k) is not None for k in fields)
        for row in (left, right, loaded)
    )
    stable = complete and all(
        abs(left[k] - right[k]) <= max(floor, 0.2 * min(left[k], right[k]))
        for k, (floor, _) in fields.items()
    )
    comparable = (
        run.after_import == run.after_replay == run.final_readback
        and len(left.get("semantic_hashes", [])) == 1
        and left.get("semantic_hashes")
        == right.get("semantic_hashes")
        == loaded.get("semantic_hashes")
    )
    absolute = complete and all(
        row["p95_ms"] <= 25 and row["observed_p99_ms"] <= 75
        for row in (left, right, loaded)
    )
    against = (
        {
            name: {k: loaded[k] - row[k] for k in fields}
            for name, row in (("left", left), ("right", right))
        }
        if complete
        else {}
    )
    added = complete and all(
        delta[k] <= fields[k][1] for delta in against.values() for k in fields
    )
    planning = [r for r in run.rows if r["request_kind"] == "planning"]
    planning_count = sum(s["request_kind"] == "planning" for s in schedule)
    planning_p95 = percentile(
        [r["milliseconds"] for r in planning if r.get("milliseconds") is not None], 0.95
    )
    planning_ok = (
        len(planning) == planning_count == 100
        and all(r["validated"] for r in planning)
        and planning_p95 is not None
        and planning_p95 <= 500
    )
    baseline_phase = next(
        p for p in run.phases if p["phase"] == "preimport_core_baseline"
    )
    retained_phase = next(
        p for p in run.phases if p["phase"] == "loaded_core_baseline_after"
    )
    before = [
        s
        for s in trace
        if baseline_phase["started"] <= s["at"] <= baseline_phase["finished"]
    ]
    after = [
        s
        for s in trace
        if retained_phase["started"] <= s["at"] <= retained_phase["finished"]
    ]
    memory = {"complete": bool(before and after), "startup_included_in_peak": True}
    if before and after:
        baseline = before[-1]["runtime_executor_rss_bytes"]
        memory.update(
            added_peak_bytes=max(s["runtime_executor_rss_bytes"] for s in trace)
            - baseline,
            retained_growth_bytes=after[-1]["runtime_executor_rss_bytes"] - baseline,
        )
    memory_ok = (
        memory["complete"]
        and memory["added_peak_bytes"] <= 128 * MIB
        and memory["retained_growth_bytes"] <= 8 * MIB
    )
    fixed_names = (
        "preimport_core_baseline",
        "loaded_core_baseline_before",
        "loaded_core_baseline_after",
        "loaded_planning_probe",
    )
    fixed_complete = all(
        windows.get(name, {}).get("recorded") == 200 for name in fixed_names
    )
    requests_ok = fixed_complete and all(row["validated"] for row in run.rows)
    passed = bool(
        stable
        and comparable
        and absolute
        and added
        and planning_ok
        and memory_ok
        and requests_ok
    )
    return {
        "passed": passed,
        "qualification": False,
        "scope": "one owner import and full Money replay; not native-v2 or repeated-import qualification",
        "windows": windows,
        "comparable": comparable,
        "stable": stable,
        "absolute_latency": absolute,
        "added_against_each": against,
        "added_latency_cpu": added,
        "planning_p95_ms": planning_p95,
        "planning_passed": planning_ok,
        "memory": memory,
        "memory_passed": memory_ok,
        "all_requests_passed": requests_ok,
        "final_cleanup_required": True,
    }


async def controller(root):
    import httpx
    from tests.fixtures.finance_watchdog import process_row
    from tests.acceptance.test_finance_activation import configured, packet_for
    from tests.acceptance.test_openclaw_native_seam import PRINCIPAL, DEMO_HEADERS
    from tests.fixtures.finance_import_load import sized_package
    from adminme.finance import release

    assert release.ADVISOR_AVAILABLE is False
    run = Run(root)
    checkout = os.environ["GITHUB_SHA"]
    if len(checkout) != 40 or any(c not in "0123456789abcdef" for c in checkout):
        raise ValueError("exact_candidate_required")
    failed = True
    try:
        async with run.phase("synthetic_fixture_build"):
            p, d, package = sized_package(
                configured(root / "scratch/runtime/instance").instance.tenant_id,
                PRINCIPAL,
            )
            manifest, packet = packet_for(p, d, package)
            run.book_id, run.publication_id, run.projection_id = (
                p["book_id"],
                p["publication_id"],
                d["projection_id"],
            )
            run.selection = {
                "book_id": run.book_id,
                "projection_id": run.projection_id,
                "context_id": d["contexts"][0]["context_id"],
                "report_id": "planning_projection",
                "horizon_days": 30,
            }
            packet_path = root / "scratch/packet.json"
            packet_path.write_text(json.dumps(packet, separators=(",", ":")))
            append(
                root,
                "fixture.jsonl",
                {
                    "candidate": checkout,
                    "shipping_source": "9a23c9372650e6502bf2f76d08f318fcca891729",
                    "qualification": False,
                    "diagnostic": "runtime_only_sqlcipher_native_aggregate",
                    "units": 8022,
                    "blobs": len(package.blobs),
                    "provenance_bytes": len(package.index)
                    + sum(map(len, package.blobs.values())),
                    "max_blob_bytes": max(map(len, package.blobs.values())),
                    "wire_bytes": packet_path.stat().st_size,
                    "manifest": manifest,
                    "advisor_available": release.ADVISOR_AVAILABLE,
                },
            )
            del p, d, package, packet  # no forced GC or allocator changes
        run.listener = socket.socket()
        run.listener.bind(("127.0.0.1", 0))
        run.listener.listen(128)
        run.control, child_control = socket.socketpair()
        run.control.setblocking(False)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{run.listener.getsockname()[1]}",
            headers=DEMO_HEADERS,
            limits=httpx.Limits(
                max_connections=6, max_keepalive_connections=6, keepalive_expiry=4
            ),
            timeout=2,
            trust_env=False,
        ) as client:
            async with run.phase("runtime_startup"):
                runtime_root = root / "scratch/runtime"
                runtime_root.mkdir(mode=0o700)
                # Only inherited exact loopback/control FDs; no fork/resource tracker.
                env = {
                    k: os.environ[k]
                    for k in ("PATH", "LANG", "LC_ALL", "ADMINME_TEST_MODE")
                }
                run.runtime = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        str(Path(__file__).resolve()),
                        "runtime",
                        str(runtime_root),
                        str(run.listener.fileno()),
                        str(child_control.fileno()),
                        checkout,
                    ],
                    env=env,
                    cwd="/",
                    close_fds=True,
                    pass_fds=(run.listener.fileno(), child_control.fileno()),
                )
                child_control.close()
                run.listener.close()
                run.notify(
                    "runtime",
                    pid=run.runtime.pid,
                    birth=process_row(run.runtime.pid)["start_ticks"],
                )
                ready = await receive(run.control)
                assert ready == {"ready": True, "advisor_available": False}, ready
                control = await run.request(
                    client,
                    "PUT",
                    CONTROL,
                    body={
                        "request_id": str(uuid4()),
                        "expected_revision": 0,
                        "binding": manifest["binding"],
                        "books_enabled": True,
                        "cos_analysis_enabled": False,
                    },
                    full=True,
                )
                assert control["advisor_available"] is False
                approval = await run.request(
                    client,
                    "POST",
                    APPROVALS,
                    body={
                        "request_id": str(uuid4()),
                        "expected_policy_revision": control["revision"],
                        "manifest": manifest,
                    },
                    full=True,
                )
                await run.request(client, "GET", CORE)
            async with run.phase("settled_preimport_observation"):
                await run.observe("preimport")
            async with run.phase("single_initial_import"):
                # One production request, no retry. Large JSON encode/decode is
                # included in phase, CPU/RSS and scratch accounting.
                body = json.loads(packet_path.read_text())
                result = await run.request(
                    client,
                    "POST",
                    "/api/member/v1/finance/imports/" + approval["approval_event_id"],
                    body=body,
                    timeout=117,
                    full=True,
                )
                assert result["accounting"]["publication_id"] == run.publication_id, (
                    result
                )
                del body
            async with run.phase("settled_postimport_observation"):
                run.after_import = await run.readback(client)
                await run.observe("postimport")
            async with run.phase("single_full_money_replay"):
                assert await run.command("rebuild_money") == {"rebuilt": "money"}
            async with run.phase("settled_postreplay_observation"):
                run.after_replay = await run.readback(client)
                await run.observe("postreplay")
            async with run.phase("shutdown_receipt"):
                run.final_readback = await run.readback(client)
                shutdown = await run.command("shutdown")
                assert (
                    shutdown
                    == {
                        "shutdown": True,
                        "guard_faults": [],
                        "finance_launches": 3,  # import preparation, Money delivery, full replay
                    }
                ), shutdown
                while run.runtime.poll() is None:
                    await asyncio.sleep(0.02)
                assert run.runtime.returncode == 0
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                run.notify(
                    "runtime_reaped",
                    pid=run.runtime.pid,
                    cpu_seconds=usage.ru_utime + usage.ru_stime,
                )
        assert run.after_import == run.after_replay == run.final_readback
        assert all(row["validated"] for row in run.rows)
        append(
            root,
            "diagnostic-result.jsonl",
            {
                "diagnostic_complete": True,
                "qualification": False,
                "latency_qualification": "omitted_for_native_observation",
                "resource_qualification": "not_attempted",
                "same_readback_after_import_replay_and_final": True,
                "observations": 3,
                "normal_runtime_shutdown": True,
                "external_cleanup_and_export_proof_required": True,
            },
        )
        failed = False
    except BaseException as exc:
        append(
            root,
            "failure.jsonl",
            {
                "phase": run.phase_name,
                "error_type": type(exc).__name__,
                "error": str(exc)[:12000],
                "error_truncated": len(str(exc)) > 12000,
                "traceback": traceback.format_exc(limit=30)[:16000],
                "traceback_truncated": len(traceback.format_exc(limit=30)) > 16000,
                "at": time.monotonic(),
            },
        )
    finally:
        if run.control:
            run.control.close()
        if run.listener:
            run.listener.close()
        # On a fault the independent subreaper terminates/reaps the owned tree;
        # it retains raw evidence and only deletes scratch with known ownership.
        run.notify("work_finished", at=time.monotonic())
    return int(failed)


def main(root):
    def cancelled(*_):
        raise InterruptedError("watchdog_cancelled")

    signal.signal(signal.SIGUSR1, cancelled)
    return asyncio.run(controller(root))


if __name__ == "__main__":
    if len(sys.argv) != 6 or sys.argv[1] != "runtime":
        raise SystemExit("fixed runtime role required")
    asyncio.run(
        runtime_main(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    )
