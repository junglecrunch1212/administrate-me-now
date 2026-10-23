"""Opt-in synthetic production HTTP benchmark in an isolated spawned server.

The client event loop is independent from the application. The old TestClient
harness also regressed with an empty control request; those failed v1 results
remain documented. The <=10% raw client p95 gate is unchanged. Run only in
hosted CI/an authorized disposable lab, never the family runtime.
"""

from __future__ import annotations

import asyncio
import json
import math
import multiprocessing
import os
import platform
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from adminme.demo import FIXTURE_MEMBER_ID, canonical_seed_events
from adminme.openclaw_topology import member_agent_id
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = 500
SECRET = "synthetic-finance-benchmark-loopback-secret"
HEADERS = {"Tailscale-User-Login": "fixture-benchmark.ts.net"}


def _p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def _settings(root: Path):
    return replace(
        RuntimeSettings.credential_free(
            root,
            encryption_key=b"b" * 32,
            repository_root=REPO_ROOT,
            member_identities={"fixture-benchmark.ts.net": FIXTURE_MEMBER_ID},
            member_roles={FIXTURE_MEMBER_ID: "principal"},
            openclaw_enabled=True,
            openclaw_shared_secret=SECRET,
            member_chat_enabled_members=frozenset({FIXTURE_MEMBER_ID}),
        ),
        finance_publications_enabled=True,
        finance_model_members=frozenset({FIXTURE_MEMBER_ID}),
    )


class _SyntheticServerTiming:
    """Measure server work separately without relaxing the raw client gate."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        started = time.perf_counter()

        async def observed(message):
            if scope["type"] == "http" and message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (
                            b"x-synthetic-server-us",
                            str(
                                int((time.perf_counter() - started) * 1_000_000)
                            ).encode(),
                        ),
                    ],
                }
            await send(message)

        await self.app(scope, receive, observed)


class _SyntheticCpuProfile:
    """Diagnostic only: never used by the latency acceptance measurement."""

    def __init__(self, app):
        import cProfile

        self.app = app
        self.profile = cProfile.Profile()
        self.count = 0
        self.started = False

    async def __call__(self, scope, receive, send):
        finance = scope["type"] == "http" and scope.get("path", "").startswith(
            "/api/openclaw/v1/tools/finance-"
        )
        if finance and not self.started:
            self.started = True
            self.profile.enable()
        await self.app(scope, receive, send)
        if finance:
            self.count += 1
            if self.count == 50:
                import pstats

                self.profile.disable()
                print(
                    "SYNTHETIC_FINANCE_CPU_PROFILE: diagnostic, not latency evidence",
                    flush=True,
                )
                pstats.Stats(self.profile).strip_dirs().sort_stats(
                    "tottime"
                ).print_stats(45)
                pstats.Stats(self.profile).strip_dirs().sort_stats(
                    "cumulative"
                ).print_stats("main.py|disclosure.py|runner.py|bus.py|log.py")


def _serve(root: Path, listener: socket.socket, ready, profile: bool = False) -> None:
    app = create_application(_settings(root))
    app.add_middleware(_SyntheticServerTiming)
    if profile:
        app.add_middleware(_SyntheticCpuProfile)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            runtime = application.state.runtime
            ids = await runtime.events.write_batch(
                canonical_seed_events(runtime.settings.instance.tenant_id)
            )
            await runtime.wait_for_projection("parties", ids[5])
            await runtime.wait_for_projection("tasks", ids[-1])
            ready.send(runtime.member_chat_binding(FIXTURE_MEMBER_ID))
            ready.close()
            yield

    app.router.lifespan_context = lifespan
    uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(
        sockets=[listener]
    )


async def _finance_call(
    client, actor: str, session: str, number: int
) -> tuple[bool, float]:
    calculation = number % 2 == 0
    payload: dict[str, Any] = {
        "provenance": {
            "actor_id": actor,
            "agent_id": member_agent_id(FIXTURE_MEMBER_ID),
            "session_key": session,
            "session_id": "fixture-session",
            "channel_id": "administrateme-portal",
            "account_id": "member-portal",
            "message_id": f"finance-run:fixture-{number}",
            "correlation_id": f"fixture-{number}",
            "call_kind": "tool",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
        }
    }
    if calculation:
        payload.update(
            calculator_id="reserve_coverage",
            version="1",
            hypothetical=True,
            inputs={
                "currency": "USD",
                "liquid_cash_minor": 120000,
                "monthly_reference_spending_minor": 30000,
                "chosen_reserve_floor_minor": 60000,
                "liquidity_confirmed": True,
                "reference_basis": "chosen_essential_spending",
            },
        )
    started = time.perf_counter()
    response = await client.post(
        "/api/openclaw/v1/tools/finance-" + ("calculate" if calculation else "status"),
        headers={"Authorization": f"Bearer {SECRET}"},
        json=payload,
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    if calculation:
        assert response.json()["values"]["reference_months_covered"] == "4.000000", (
            response.text
        )
    else:
        assert "publication" in response.json(), response.text
    return calculation, elapsed


def _client(url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=url,
        timeout=10,
        transport=httpx.AsyncHTTPTransport(
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
        ),
    )


def _finance_load(url: str, actor: str, session: str, ready, stop) -> None:
    """Independent process/event loop: finance client work cannot delay core timing."""

    async def load():
        statuses: list[float] = []
        calculations: list[float] = []
        async with _client(url) as client:
            ready.send({"ready": True})
            start = time.perf_counter()
            for index in range(800):
                if stop.is_set():
                    break
                calculation, elapsed = await _finance_call(
                    client, actor, session, index + 100
                )
                (calculations if calculation else statuses).append(elapsed)
                # Ten tool calls/second is a bounded synthetic stress workload.
                await asyncio.sleep(
                    max(0, start + (index + 1) * 0.1 - time.perf_counter())
                )
        ready.send({"statuses": statuses, "calculations": calculations})

    try:
        asyncio.run(load())
    except Exception as exc:
        ready.send({"error": type(exc).__name__})
    finally:
        ready.close()


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_BENCHMARK") != "1",
    reason="Opt-in isolated hosted HTTP benchmark; not the family runtime",
)
async def test_light_finance_mixed_load_p95_gate(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    listener = socket.socket()
    # An explicitly supplied listening socket must carry TCP_NODELAY. Otherwise
    # sequential small responses can incur the delayed-ACK/Nagle ~40 ms stall,
    # while concurrent traffic flushes it and falsely improves the loaded phase.
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    process = context.Process(
        target=_serve, args=(tmp_path / "instance", listener, send)
    )
    process.start()
    send.close()
    load_process = None
    load_receive = None
    stop_load = context.Event()
    try:
        assert await asyncio.to_thread(receive.poll, 45), (
            "synthetic HTTP server did not start"
        )
        actor, session = receive.recv()
        async with (
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                transport=httpx.AsyncHTTPTransport(
                    socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
                ),
            ) as core_client,
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                transport=httpx.AsyncHTTPTransport(
                    socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
                ),
            ) as finance_client,
        ):
            grant = await core_client.post(
                "/api/member/v1/cos/delegations",
                headers=HEADERS,
                json={
                    "request_id": "synthetic-benchmark-grant",
                    "context_scope": "member_private",
                    "permissions": ["read_context"],
                },
            )
            assert grant.status_code in {200, 201}, grant.text
            calls = 0
            statuses: list[float] = []
            calculations: list[float] = []

            async def finance_call() -> float:
                nonlocal calls
                calls += 1
                calculation, elapsed = await _finance_call(
                    finance_client, actor, session, calls
                )
                (calculations if calculation else statuses).append(elapsed)
                return elapsed

            server_timings: list[float] = []

            async def core_call(index: int) -> float:
                route = (
                    "/api/core/v1/what-now"
                    if index % 2 == 0
                    else "/api/core/v1/commitments/active"
                )
                started = time.perf_counter()
                response = await core_client.get(route, headers=HEADERS)
                elapsed = time.perf_counter() - started
                assert response.status_code == 200, response.text
                server_timings.append(
                    int(response.headers["x-synthetic-server-us"]) / 1_000_000
                )
                return elapsed

            cold_status = await finance_call()
            for index in range(30):
                await core_call(index)
                await finance_call()

            async def core_phase() -> list[float]:
                values = []
                start = time.perf_counter()
                for index in range(SAMPLES):
                    values.append(await core_call(index))
                    await asyncio.sleep(
                        max(0, start + (index + 1) * 0.05 - time.perf_counter())
                    )
                return values

            server_timings.clear()
            baseline = await core_phase()
            baseline_server = list(server_timings)
            server_timings.clear()
            load_receive, load_send = context.Pipe(duplex=False)
            load_process = context.Process(
                target=_finance_load,
                args=(f"http://127.0.0.1:{port}", actor, session, load_send, stop_load),
            )
            load_process.start()
            load_send.close()
            assert await asyncio.to_thread(load_receive.poll, 15), (
                "finance load client did not start"
            )
            assert load_receive.recv() == {"ready": True}
            mixed = await core_phase()
            mixed_server = list(server_timings)
            server_timings.clear()
            stop_load.set()
            assert await asyncio.to_thread(load_receive.poll, 15), (
                "finance load client did not settle"
            )
            measured = load_receive.recv()
            assert "error" not in measured, measured
            statuses = measured["statuses"]
            calculations = measured["calculations"]
            assert len(statuses) + len(calculations) >= 100, (
                "insufficient admitted finance load"
            )
            finance = statuses + calculations
            after = await core_phase()
            after_server = list(server_timings)
            baseline_p95 = max(_p95(baseline), _p95(after))
            report = {
                "contract": "synthetic-light-finance-http-mixed-load-v3",
                "python": platform.python_version(),
                "samples_per_phase": SAMPLES,
                "core_requests_per_second": 20,
                "finance_requests_per_second": 10,
                "finance_samples": len(finance),
                "independent_load_processes": True,
                "cold_status_ms": round(cold_status * 1000, 3),
                "baseline_before_server_p95_ms": round(_p95(baseline_server) * 1000, 3),
                "baseline_after_server_p95_ms": round(_p95(after_server) * 1000, 3),
                "mixed_server_p95_ms": round(_p95(mixed_server) * 1000, 3),
                "baseline_before_p95_ms": round(_p95(baseline) * 1000, 3),
                "baseline_after_p95_ms": round(_p95(after) * 1000, 3),
                "mixed_core_p95_ms": round(_p95(mixed) * 1000, 3),
                "regression_ratio": _p95(mixed) / baseline_p95,
                "warm_status_p95_ms": round(_p95(statuses) * 1000, 3),
                "calculation_p95_ms": round(_p95(calculations) * 1000, 3),
                "mixed_finance_p95_ms": round(_p95(finance) * 1000, 3),
                "heavy_worker_validated": False,
                "reminder_delivery_validated": False,
                "large_ledger_validated": False,
                "deployment_hardware_validated": False,
            }
            (tmp_path / "finance-light-load-results.json").write_text(
                json.dumps(report, indent=2)
            )
            print(json.dumps(report, sort_keys=True))
            assert _p95(mixed) <= baseline_p95 * 1.10, report
            assert _p95(statuses) < 0.100, report
            assert _p95(calculations) < 0.500, report
    finally:
        stop_load.set()
        if load_process is not None:
            await asyncio.to_thread(load_process.join, 5)
            if load_process.is_alive():
                load_process.terminate()
                await asyncio.to_thread(load_process.join, 5)
            if load_process.is_alive():
                load_process.kill()
                await asyncio.to_thread(load_process.join, 5)
        if load_receive is not None:
            load_receive.close()
        receive.close()
        listener.close()
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_PROFILE") != "1",
    reason="Isolated hosted diagnostic only",
)
async def test_profile_light_finance_request_cost(tmp_path):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    listener = socket.socket()
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = context.Process(
        target=_serve, args=(tmp_path / "profile-instance", listener, send, True)
    )
    server.start()
    send.close()
    try:
        assert await asyncio.to_thread(receive.poll, 45)
        actor, session = receive.recv()
        async with (
            _client(f"http://127.0.0.1:{port}") as finance,
            _client(f"http://127.0.0.1:{port}") as core,
        ):
            grant = await core.post(
                "/api/member/v1/cos/delegations",
                headers=HEADERS,
                json={
                    "request_id": "synthetic-profile-grant",
                    "context_scope": "member_private",
                    "permissions": ["read_context"],
                },
            )
            assert grant.status_code in {200, 201}, grant.text

            async def finance_calls():
                started = time.perf_counter()
                for index in range(50):
                    await _finance_call(finance, actor, session, index + 500)
                    await asyncio.sleep(
                        max(0, started + (index + 1) * 0.1 - time.perf_counter())
                    )

            async def core_calls():
                started = time.perf_counter()
                for index in range(100):
                    response = await core.get(
                        "/api/core/v1/what-now"
                        if index % 2 == 0
                        else "/api/core/v1/commitments/active",
                        headers=HEADERS,
                    )
                    assert response.status_code == 200
                    await asyncio.sleep(
                        max(0, started + (index + 1) * 0.05 - time.perf_counter())
                    )

            await asyncio.gather(finance_calls(), core_calls())
            await asyncio.sleep(0.1)
    finally:
        receive.close()
        listener.close()
        server.terminate()
        await asyncio.to_thread(server.join, 5)
        if server.is_alive():
            server.kill()
            await asyncio.to_thread(server.join, 5)
