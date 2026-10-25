"""One synthetic staged runtime; early namespace before application imports."""

from __future__ import annotations

import json
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
    snapshot,
)


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
    state = await snapshot(runtime, storage)

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

    events = await runtime.event_log._run_on_connection_thread(event_digest)
    accepted_heads = await runtime.projections.read("money", heads)
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
    trace, patches = Trace(root), pytest.MonkeyPatch()
    storage = TracedStorage(root, namespace_nonce=nonce, trace=trace)
    instrument(storage, patches, trace)
    settings = storage.settings()
    assert settings.instance.tenant_id == tenant
    app = create_application(settings)
    storage.install(app, patches)
    app.add_middleware(TimedASGI, trace=trace)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="on")
    )
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        while not server.started:
            if running.done():
                await running
                raise RuntimeError("synthetic_runtime_not_ready")
            await asyncio.sleep(0.01)
        runtime = app.state.runtime
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
        await storage.close()
        server.should_exit = True
        await running
        listener.close()
        patches.undo()
        trace.mark("runtime.stopped")
        trace.close()


if LAUNCH is not None:
    asyncio.run(serve(*LAUNCH))
