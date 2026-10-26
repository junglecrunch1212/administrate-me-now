"""One synthetic staged runtime; early namespace before application imports."""

from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from adminme.finance.worker_protocol import write_json
from tests.fixtures.finance_startup import startup_instruction, store_name

LAUNCH = None
if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "staged_lifetime":
        raise SystemExit("fixed synthetic staged lifetime required")
    root = Path(sys.argv[2])
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or any(root.iterdir())
    ):
        raise SystemExit("fresh synthetic root required")
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    nonce = secrets.token_hex(8)
    tenant = "tenant-" + store_name(nonce)
    write_json(sys.stdout.buffer, {"fixture_coordinates": 1, "tenant": tenant})
    if startup_instruction(sys.stdin.buffer) == "stop":
        write_json(sys.stdout.buffer, {"stopped": True, "tenant": tenant})
        raise SystemExit(0)
    LAUNCH = root, nonce, tenant

# ruff: noqa: E402 -- the same owned child waits before heavy runtime imports.
import asyncio
import hashlib
import re
import socket
import time
import threading

import pytest
import uvicorn

from adminme.finance.worker_protocol import FINANCE_TYPES, read_json
from adminme.runtime.application import create_application
from adminme.projections.runner import DEFAULT_PROJECTION_CLASSES
from tests.fixtures.finance_staging import StagedStorage
from tests.fixtures.finance_storage_cases import replay
from tests.fixtures.finance_storage_measurement import (
    REQUEST,
    Trace,
    instrument,
    instrument_executor,
    snapshot,
)


class DiagnosticTrace(Trace):
    """Unadmitted diagnostic only; observer costs stay in all measured timings."""

    def __init__(self, root):
        super().__init__(root)
        self.root = root
        self.cost_lock = threading.Lock()
        self.costs = {}
        self.executors = []
        self.callback_diagnostics = None
        self.probe = {
            "created_at": time.monotonic(),
            "started_at": None,
            "stopped_at": None,
            "runtime_ready_at": None,
            "cancel_requested_at": None,
            "ticks": 0,
            "first_tick_at": None,
            "last_tick_at": None,
            "maximum_tick_gap_seconds": 0,
            "complete": False,
        }

    def account(self, category, started, cpu):
        # Wall includes contention; thread CPU avoids charging other workers.
        wall, used = time.monotonic() - started, time.thread_time() - cpu
        with self.cost_lock:
            row = self.costs.setdefault(
                category, {"calls": 0, "wall_seconds": 0, "thread_cpu_seconds": 0}
            )
            row["calls"] += 1
            row["wall_seconds"] += wall
            row["thread_cpu_seconds"] += used

    def mark(self, kind, **fields):
        started, cpu = time.monotonic(), time.thread_time()
        try:
            return super().mark(kind, **fields)
        finally:
            self.account("trace_mark", started, cpu)

    def flush(self):
        started, cpu = time.monotonic(), time.thread_time()
        try:
            return super().flush()
        finally:
            self.account("trace_flush", started, cpu)

    def resource_mark(self, kind, **fields):
        started, cpu = time.monotonic(), time.thread_time()
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # Linux diagnostic candidate only. Includes observer allocations.
            rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf(
                "SC_PAGE_SIZE"
            )
            values = {
                "rss_bytes": rss,
                "process_cpu_seconds": usage.ru_utime + usage.ru_stime,
                "sampled_at": time.monotonic(),
            }
        finally:
            self.account("resource_sample", started, cpu)
        self.mark(kind, **fields, **values)

    def close(self):
        callback_receipt = (
            self.callback_diagnostics.close()
            if self.callback_diagnostics is not None
            else None
        )
        started, cpu = time.monotonic(), time.thread_time()
        try:
            super().close()
        finally:
            self.account("trace_close", started, cpu)
        receipt = {
            "diagnostic_complete": self.probe["complete"]
            and self.executors
            == [
                "event_log",
                "event_metadata",
                "projection",
                "money_read",
                "authority",
            ],
            "loop_probe": self.probe,
            "executors_instrumented": self.executors,
            "costs": self.costs,
            "trace_rows": self.sequence,
            "trace_encoded_bytes": self.bytes,
            "trace_buffer_bytes": 65536,
            "trace_limit_bytes": 16 * 1024 * 1024,
            "limitations": [
                "Diagnostic only: no cost subtraction or acceptance/resource qualification.",
                "Counters measure trace calls, flush/close and resource samples; accounting itself, wrappers, context lookup, scheduling and this receipt write remain unmeasured.",
                "Wall costs can overlap across threads; do not sum them as process elapsed time.",
                "RSS includes observers; endpoints identify temporal association, not allocating stacks or causality.",
                "Loop probes every 50ms are additional observers, not requests; request cadence and all workload limits remain unchanged.",
            ],
        }
        if self.callback_diagnostics is not None:
            receipt["callback_diagnostics"] = callback_receipt
            receipt["diagnostic_complete"] = bool(
                receipt["diagnostic_complete"]
                and receipt["callback_diagnostics"]["complete"]
            )
        encoded = (
            json.dumps(receipt, separators=(",", ":"))
            if self.callback_diagnostics is not None
            else json.dumps(receipt, indent=2)
        ) + "\n"
        if self.callback_diagnostics is not None and len(encoded.encode()) > 65536:
            encoded = (
                json.dumps(
                    {
                        "diagnostic_complete": False,
                        "callback_diagnostics": {
                            "complete": False,
                            "faults": ["receipt_bytes"],
                        },
                    }
                )
                + "\n"
            )
        (self.root / "diagnostic-observer.json").write_text(encoded)


class DiagnosticHandler:
    """Route ASGI span includes dependencies, handler and response encoding."""

    def __init__(self, app, route, trace):
        self.app, self.route, self.trace = app, route, trace

    async def __call__(self, scope, receive, send):
        self.trace.mark("handler.start", route=self.route)
        try:
            return await self.app(scope, receive, send)
        finally:
            self.trace.mark("handler.end", route=self.route)


async def diagnostic_readback(trace, name, operation):
    trace.resource_mark("readback.start", span=name)
    try:
        return await operation()
    finally:
        trace.resource_mark("readback.end", span=name)


async def diagnostic_loop(trace):
    health = trace.probe
    health["started_at"] = time.monotonic()
    expected = health["started_at"] + 0.05
    try:
        while True:
            await asyncio.sleep(max(0, expected - time.monotonic()))
            now = time.monotonic()
            previous = health["last_tick_at"] or health["started_at"]
            health["maximum_tick_gap_seconds"] = max(
                health["maximum_tick_gap_seconds"], now - previous
            )
            health["ticks"] += 1
            health["first_tick_at"] = health["first_tick_at"] or now
            health["last_tick_at"] = now
            trace.mark("loop.delay", expected=expected, delay=max(0, now - expected))
            expected = now + 0.05
    except BaseException as exc:
        health["outcome"] = type(exc).__name__
        raise
    finally:
        health["stopped_at"] = time.monotonic()


class TimedASGI:
    """Direct await preserves the staged import request's task ownership."""

    def __init__(self, app, trace):
        self.app, self.trace = app, trace

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = (
            dict(scope["headers"]).get(b"x-measurement-id", b"").decode("ascii")
        )
        if not re.fullmatch(r"[a-z0-9_:]{0,100}", request_id):
            raise ValueError("synthetic_measurement_id")
        token = REQUEST.set(request_id or None)
        callback_diagnostics = self.trace.callback_diagnostics
        if callback_diagnostics is not None:
            callback_diagnostics.begin_request(request_id)
        path = scope["path"]
        route = (
            "import"
            if path.startswith("/api/member/v1/finance/imports/")
            else "planning"
            if path == "/api/member/v1/finance/planning"
            else "core"
            if path == "/api/member/v1/surfaces/reminders"
            else "setup"
        )
        self.trace.mark("http.start", route=route)

        async def sent(message):
            if message["type"] == "http.response.start":
                self.trace.mark("http.headers", route=route, status=message["status"])
            if message["type"] == "http.response.body" and not message.get("more_body"):
                self.trace.mark("http.last_byte", route=route)
            await send(message)

        try:
            return await self.app(scope, receive, sent)
        finally:
            if callback_diagnostics is not None:
                callback_diagnostics.end_request(request_id)
            self.trace.mark("http.finished", route=route)
            REQUEST.reset(token)


class TracedStorage(StagedStorage):
    def __init__(self, *args, trace, **kwargs):
        self.trace = trace
        super().__init__(*args, **kwargs)

    def _reap(self, graceful=True):
        pid = self.process.pid if self.process is not None else None
        had_stage = self.stage_descriptor is not None
        try:
            return super()._reap(graceful)
        finally:
            self.trace.mark(
                "storage.reap_receipt",
                pid=pid,
                confirmed=self.process is None and not self.quarantined,
                had_stage=had_stage,
                stage_removed=self.stage_descriptor is None,
            )


async def durable_snapshot(runtime, storage):
    state = await diagnostic_readback(
        storage.trace, "metadata_snapshot", lambda: snapshot(runtime, storage)
    )

    def event_digest():
        digest = hashlib.sha256()
        count = 0
        placeholders = ",".join("?" for _ in FINANCE_TYPES)
        cursor = runtime.event_log._conn.execute(
            "SELECT event_id,type,owner_scope,source_account_id,payload FROM events "
            "WHERE tenant_id=? AND type IN (" + placeholders + ") ORDER BY event_id",
            (runtime.settings.instance.tenant_id, *sorted(FINANCE_TYPES)),
        )
        try:
            for row in cursor:
                count += 1
                if count > 131:
                    raise ValueError("synthetic_snapshot_event_limit")
                digest.update(
                    json.dumps(
                        [
                            row["event_id"],
                            row["type"],
                            row["owner_scope"],
                            row["source_account_id"],
                            hashlib.sha256(row["payload"].encode()).hexdigest(),
                        ],
                        separators=(",", ":"),
                    ).encode()
                )
        finally:
            cursor.close()
        return {"count": count, "identity_payload_sha256": digest.hexdigest()}

    def heads(conn):
        return {
            table: [list(row) for row in conn.execute("SELECT * FROM " + table)]
            for table in ("finance_publication_heads", "finance_dated_projection_heads")
        }

    events = await diagnostic_readback(
        storage.trace,
        "event_digest",
        lambda: runtime.event_log._run_on_connection_thread(event_digest),
    )
    accepted_heads = await diagnostic_readback(
        storage.trace, "heads", lambda: runtime.projections.read("money", heads)
    )
    assert events["count"] == len(state["finance_events"])
    assert len(state["audit"]) <= 1
    projections = {cls.name for cls in DEFAULT_PROJECTION_CLASSES}
    assert len(projections) == 15 and {"money", "parties"} <= projections
    assert projections <= set(runtime.projections.projection_names())
    assert {"projection:" + name for name in projections} <= set(state["consumers"])
    assert all(not any(row.values()) for row in state["consumers"].values())
    assert not state["active_executor"]
    return {
        "state": "read",
        "counts": state["counts"],
        "events": events,
        "heads": accepted_heads,
        "money_checkpoint": state["money_checkpoint"],
        "money_checkpoint_verified": True,
        "projection_names": sorted(projections),
        "consumer_names": sorted(state["consumers"]),
        "money_lag": 0,
        "audit": [
            {
                "outcome": r["payload"]["outcome"],
                "reason": r["payload"]["reason"],
                "provider_effects": r["payload"]["provider_effects"],
            }
            for r in state["audit"]
        ],
        "authority": state["authority"],
        "active_executor": False,
        "lifetimes": state["lifetimes"],
        "stage_lifetimes": list(storage.stage_lifetimes),
        "guards": state["guards"],
        "handoffs": [
            r
            for r in state["boundaries"]
            if r["stage"] == "preparation_reaped_before_money"
        ],
        "starts": [r for r in state["boundaries"] if r["stage"] == "executor_spawned"],
        "measured_at": time.monotonic(),
    }


async def serve(root, nonce, tenant):
    trace, patches = DiagnosticTrace(root), pytest.MonkeyPatch()
    storage = TracedStorage(root, namespace_nonce=nonce, trace=trace)
    instrument(storage, patches, trace)
    settings = storage.settings()
    assert settings.instance.tenant_id == tenant
    app = create_application(settings)
    storage.install(app, patches)
    app.add_middleware(TimedASGI, trace=trace)
    expected = {
        "/api/member/v1/surfaces/{surface}": "core",
        "/api/member/v1/finance/planning": "planning",
    }
    observed = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in expected:
            patches.setattr(
                route, "app", DiagnosticHandler(route.app, expected[path], trace)
            )
            observed.append(path)
    assert sorted(observed) == sorted(expected)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="on")
    )
    running = asyncio.create_task(server.serve(sockets=[listener]))
    loop_probe = asyncio.create_task(diagnostic_loop(trace))
    try:
        while not server.started:
            if running.done():
                await running
                raise RuntimeError("synthetic_runtime_not_ready")
            await asyncio.sleep(0.01)
        runtime = app.state.runtime
        if runtime.event_log._metadata_reader is None:
            raise RuntimeError("synthetic_diagnostic_metadata_reader_missing")
        for executor, name in (
            (runtime.event_log._executor, "event_log"),
            (runtime.event_log._metadata_reader._executor, "event_metadata"),
            (runtime.projections._executor, "projection"),
            (runtime.projections._money_executor, "money_read"),
            (runtime.projections._parties_executor, "authority"),
        ):
            if not callable(getattr(executor, "submit", None)):
                raise RuntimeError("synthetic_diagnostic_executor_unavailable:" + name)
            instrument_executor(executor, name, patches, trace)
            trace.executors.append(name)
        if os.environ.get("ADMINME_SYNTHETIC_CALLBACK_DIAGNOSTICS") == "1":
            from adminme.finance import planning_store
            from tests.fixtures.finance_callback_diagnostics import CallbackDiagnostics

            trace.callback_diagnostics = CallbackDiagnostics()
            trace.callback_diagnostics.install(
                runtime.projections, planning_store, patches, REQUEST
            )
        trace.probe["runtime_ready_at"] = time.monotonic()
        trace.mark("runtime.ready")
        write_json(
            sys.stdout.buffer,
            {
                "ready": True,
                "url": f"http://127.0.0.1:{listener.getsockname()[1]}",
                "tenant": tenant,
            },
        )
        while True:
            command = await asyncio.to_thread(read_json, sys.stdin.buffer)
            if set(command) != {"op"} or command["op"] not in {
                "inspect",
                "replay",
                "shutdown",
            }:
                raise ValueError("synthetic_measurement_command")
            op = command["op"]
            trace.mark("control.start", operation=op)
            lock = runtime.settings_command_locks.setdefault(
                "finance-closed-package-import", asyncio.Lock()
            )
            async with lock:
                if op == "replay":
                    await replay(runtime, storage)
                await storage.close()
                result = await durable_snapshot(runtime, storage)
            trace.mark("control.end", operation=op)
            trace.flush()
            write_json(sys.stdout.buffer, result)
            if op == "shutdown":
                break
    finally:
        try:
            await storage.close()
            server.should_exit = True
            await running
        finally:
            listener.close()
            health = trace.probe
            health["done_before_cancel"] = loop_probe.done()
            health["cancel_requested_at"] = time.monotonic()
            health["cancel_accepted"] = loop_probe.cancel()
            probe_result = (await asyncio.gather(loop_probe, return_exceptions=True))[0]
            health["gather_outcome"] = type(probe_result).__name__
            health["complete"] = bool(
                not health["done_before_cancel"]
                and health["cancel_accepted"]
                and isinstance(probe_result, asyncio.CancelledError)
                and health.get("outcome") == "CancelledError"
                and health["ticks"] > 0
                and health["started_at"] is not None
                and health["runtime_ready_at"] is not None
                and health["started_at"]
                <= health["runtime_ready_at"]
                <= health["cancel_requested_at"]
                <= health["stopped_at"]
            )
            trace.mark("loop.probe_stopped", **health)
            patches.undo()
            trace.mark("runtime.stopped")
            trace.close()


if LAUNCH is not None:
    asyncio.run(serve(*LAUNCH))
