"""Bounded, synthetic-only timing; never imported by production code."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
from pathlib import Path
import socket
import sys
import threading
import time

import pytest
import uvicorn

from adminme.finance.access_control import policy_for_owner
from adminme.finance.worker_protocol import read_json, write_json
from adminme.runtime.application import create_application
from tests.acceptance.test_openclaw_native_seam import PRINCIPAL
from tests.fixtures.finance_storage_cases import inspect, replay
from tests.fixtures.finance_storage_isolation import SyntheticStorage

REQUEST = contextvars.ContextVar("synthetic_measurement_request", default=None)


class Trace:
    def __init__(self, root):
        self.file = (root / "measurement-trace.jsonl").open("w", buffering=65536)
        self.lock = threading.Lock()
        self.sequence = 0
        self.bytes = 0
        self.closed = False

    def mark(self, kind, **fields):
        with self.lock:
            if self.closed:
                return
            self.sequence += 1
            row = {"at": time.monotonic(), "n": self.sequence, "kind": kind, **fields}
            if REQUEST.get() is not None:
                row["request"] = REQUEST.get()
            line = json.dumps(row, separators=(",", ":")) + "\n"
            self.bytes += len(line)
            if self.bytes > 16 * 1024 * 1024:
                raise RuntimeError("synthetic_trace_limit")
            self.file.write(line)

    def flush(self):
        with self.lock:
            self.file.flush()

    def close(self):
        with self.lock:
            self.closed = True
            self.file.close()


def instrument(storage, patches, trace):
    def asynchronous(name):
        original = getattr(storage, name)

        @functools.wraps(original)
        async def wrapped(*args, **kwargs):
            trace.mark("storage.start", operation=name)
            try:
                return await original(*args, **kwargs)
            finally:
                trace.mark("storage.end", operation=name)

        patches.setattr(storage, name, wrapped)

    for name in ("prepare", "append", "apply", "close"):
        asynchronous(name)
    for name in ("_spawn", "_reap"):
        original = getattr(storage, name)

        def wrapped(*args, _name=name, _original=original, **kwargs):
            trace.mark("storage.start", operation=_name)
            try:
                return _original(*args, **kwargs)
            finally:
                trace.mark("storage.end", operation=_name)

        patches.setattr(storage, name, wrapped)
    original_checkpoint = storage.checkpoint

    async def checkpoint(stage):
        trace.mark("storage.checkpoint", stage=stage)
        await original_checkpoint(stage)

    patches.setattr(storage, "checkpoint", checkpoint)


def instrument_executor(executor, name, patches, trace):
    original = executor.submit

    def submit(function, *args, **kwargs):
        submitted = time.monotonic()
        request = REQUEST.get()
        function_name = getattr(function, "__qualname__", type(function).__name__)

        def call():
            started = time.monotonic()
            try:
                return function(*args, **kwargs)
            finally:
                trace.mark(
                    "native.queue",
                    executor=name,
                    function=function_name,
                    request=request,
                    submitted=submitted,
                    started=started,
                    finished=time.monotonic(),
                )

        return original(call)

    patches.setattr(executor, "submit", submit)


async def snapshot(runtime, storage):
    result = await inspect(runtime, storage)
    control = policy_for_owner(runtime, PRINCIPAL).authority_coordinates()
    result["authority"] = {
        "tenant": runtime.settings.instance.tenant_id,
        "owner": PRINCIPAL,
        "role": runtime.settings.member_roles[PRINCIPAL],
        "control": control,
    }
    result["consumers"] = {
        name: {
            "lag": (await runtime.bus.subscriber_status(name))["lag_count"],
            "draining": state.draining,
            "triggered": state.trigger.is_set(),
            "degraded": state.degraded,
            "poison": state.poison_event_id,
        }
        for name, state in runtime.bus._subscribers.items()
    }
    result["measured_at"] = time.monotonic()
    return result


async def serve(
    root: Path, *, namespace_nonce: str, announced_tenant: str, focused=False
):
    storage = SyntheticStorage(root, namespace_nonce=namespace_nonce)
    patches = pytest.MonkeyPatch()
    trace = Trace(root)
    trace.mark("runtime.start")
    instrument(storage, patches, trace)
    settings = storage.settings()
    if settings.instance.tenant_id != announced_tenant:
        trace.mark("runtime.namespace_mismatch")
        trace.close()
        patches.undo()
        raise ValueError("synthetic_startup_tenant_mismatch")
    trace.mark("runtime.namespace_verified")
    app = create_application(settings)
    storage.install(app, patches)
    successor_committed = asyncio.Event()
    if focused:

        async def delayed_successor(stage):
            if stage == "committed" and REQUEST.get() == "successor":
                successor_committed.set()
                trace.mark("focused.successor_committed")
                await asyncio.sleep(5)

        storage.hook = delayed_successor

    @app.middleware("http")
    async def timed(request, call_next):
        token = REQUEST.set(request.headers.get("X-Measurement-Id"))
        started = time.monotonic()
        trace.mark("http.start", path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
            trace.mark(
                "http.headers",
                path=request.url.path,
                status=response.status_code,
                started=started,
            )
            return response
        finally:
            REQUEST.reset(token)

    async def loop_delay():
        expected = time.monotonic() + 0.05
        while True:
            await asyncio.sleep(max(0, expected - time.monotonic()))
            now = time.monotonic()
            trace.mark("loop.delay", expected=expected, delay=max(0, now - expected))
            trace.flush()
            expected = now + 0.05

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="on")
    )
    loop_probe = asyncio.create_task(loop_delay())
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        while not server.started:
            if running.done():
                await running
                raise RuntimeError("synthetic server did not start")
            await asyncio.sleep(0.01)
        runtime = app.state.runtime
        for executor, name in (
            (runtime.event_log._executor, "event_log"),
            (runtime.event_log._metadata_reader._executor, "event_metadata"),
            (runtime.projections._executor, "projection"),
            (runtime.projections._money_executor, "money_read"),
            (runtime.projections._parties_executor, "authority"),
        ):
            instrument_executor(executor, name, patches, trace)
        trace.mark("runtime.ready")
        write_json(
            sys.stdout.buffer,
            {
                "ready": True,
                "url": f"http://127.0.0.1:{listener.getsockname()[1]}",
                "tenant": runtime.settings.instance.tenant_id,
            },
        )
        while True:
            command = await asyncio.to_thread(read_json, sys.stdin.buffer)
            op = command.get("op")
            trace.mark("control.start", operation=op)
            if op == "inspect":
                result = await snapshot(runtime, storage)
            elif op == "replay":
                await replay(runtime, storage)
                result = await snapshot(runtime, storage)
            elif op == "wait_successor_committed" and focused:
                await asyncio.wait_for(successor_committed.wait(), timeout=30)
                result = {"committed": True}
            elif op == "settled_import" and focused:
                import_lock = runtime.settings_command_locks.setdefault(
                    "finance-closed-package-import", asyncio.Lock()
                )
                async with import_lock:
                    await storage.close()
                    result = await snapshot(runtime, storage)
            elif op == "shutdown":
                import_lock = runtime.settings_command_locks.setdefault(
                    "finance-closed-package-import", asyncio.Lock()
                )
                async with import_lock:
                    await storage.close()
                    result = await snapshot(runtime, storage)
            else:
                raise ValueError("synthetic measurement command denied")
            if focused and op == "shutdown":
                # Reproduce the original four-second waiter timeout without
                # extending work or cleanup deadlines or losing the result.
                await asyncio.sleep(5)
            trace.mark("control.end", operation=op)
            trace.flush()
            write_json(sys.stdout.buffer, result)
            if op == "shutdown":
                break
    finally:
        await storage.close()
        server.should_exit = True
        await running
        listener.close()
        loop_probe.cancel()
        await asyncio.gather(loop_probe, return_exceptions=True)
        patches.undo()
        trace.mark("runtime.stopped")
        trace.close()
