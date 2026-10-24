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
from functools import wraps
from pathlib import Path
from typing import Any, cast

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

    def __init__(self, app, output_path):
        import cProfile

        self.app = app
        self.output_path = output_path
        self.profile = cProfile.Profile()
        self.count = 0
        self.started = False
        self.completed = False
        self.loop_thread_id = None
        self.wall_calls = {}
        self.executor_spans = []
        self.http_spans = []
        self.profile_started_at = 0.0
        # Diagnostic-only instrumentation distinguishes main-loop blocking
        # from work already dispatched to the native projection worker. It is
        # installed only in this separate profile server, never the p95 gate.
        import threading
        import concurrent.futures
        from adminme.finance import disclosure, planning_store
        from adminme.products.openclaw import main as native
        from adminme.projections.runner import ProjectionRunner

        self.wall_lock = threading.Lock()

        def record(label, elapsed):
            with self.wall_lock:
                self.wall_calls.setdefault(label, []).append(elapsed)

        original_submit = concurrent.futures.ThreadPoolExecutor.submit

        def observed_submit(executor, func, /, *args, **kwargs):
            queued_at = time.perf_counter()
            active = self.started and not self.completed
            prefix = getattr(executor, "_thread_name_prefix", "other")
            lane = (
                prefix
                if prefix
                in {
                    "adminme-projections",
                    "adminme-authority-reader",
                    "adminme-event-log",
                }
                else "other"
            )
            operation = getattr(func, "__name__", "callback")
            if (
                args
                and isinstance(args[0], str)
                and args[0]
                in {
                    "parties",
                    "money",
                    "commitments",
                    "tasks",
                    "lists",
                    "recurrences",
                }
            ):
                operation += ":" + args[0]

            def begin():
                began_at = time.perf_counter()
                if active:
                    record(f"executor_wait:{lane}:{operation}", began_at - queued_at)
                try:
                    return func(*args, **kwargs)
                finally:
                    if active:
                        finished_at = time.perf_counter()
                        with self.wall_lock:
                            self.executor_spans.append(
                                {
                                    "operation": f"{lane}:{operation}",
                                    "queued_ms": round(
                                        (queued_at - self.profile_started_at) * 1000, 3
                                    ),
                                    "started_ms": round(
                                        (began_at - self.profile_started_at) * 1000, 3
                                    ),
                                    "finished_ms": round(
                                        (finished_at - self.profile_started_at) * 1000,
                                        3,
                                    ),
                                }
                            )

            return original_submit(executor, begin)

        concurrent.futures.ThreadPoolExecutor.submit = observed_submit

        def instrument(owner, name, label):
            original = getattr(owner, name)

            @wraps(original)
            def timed(*args, **kwargs):
                active = self.started and not self.completed
                started = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    if active:
                        thread = (
                            "http_loop"
                            if threading.get_ident() == self.loop_thread_id
                            else "authority_reader"
                            if threading.current_thread().name.startswith(
                                "adminme-authority-reader"
                            )
                            else "worker"
                        )
                        record(f"{label}:{thread}", time.perf_counter() - started)

            setattr(owner, name, timed)

        instrument(disclosure, "validate_current_authority", "final_authority")
        instrument(planning_store, "query_projection", "planning_query")
        instrument(native, "_session", "resolve_session")
        instrument(ProjectionRunner, "_execute_sync", "projection_dispatch")
        instrument(ProjectionRunner, "_execute_statement", "projection_statement")
        instrument(ProjectionRunner, "_apply_one_for", "projection_event_apply")
        instrument(ProjectionRunner, "_call_many_query", "core_snapshot")

    async def __call__(self, scope, receive, send):
        finance = scope["type"] == "http" and scope.get("path", "").startswith(
            "/api/openclaw/v1/tools/finance-"
        )
        if finance and not self.started:
            import threading

            self.loop_thread_id = threading.get_ident()
            self.profile_started_at = time.perf_counter()
            self.started = True
            self.profile.enable()
        began_at = time.perf_counter()
        tracked = self.started and not self.completed and scope["type"] == "http"
        try:
            await self.app(scope, receive, send)
        finally:
            if tracked:
                self.http_spans.append(
                    {
                        "route": scope["path"],
                        "started_ms": round(
                            (began_at - self.profile_started_at) * 1000, 3
                        ),
                        "finished_ms": round(
                            (time.perf_counter() - self.profile_started_at) * 1000, 3
                        ),
                    }
                )
        if finance:
            self.count += 1
            if self.count == 50:
                import pstats

                self.profile.disable()
                self.completed = True
                # pstats exposes this public mapping at runtime; its stub omits it.
                statistics = cast(Any, pstats.Stats(self.profile)).stats

                def row(item):
                    (
                        (filename, line, function),
                        (primitive, calls, own, cumulative, _),
                    ) = item
                    return {
                        "function": function,
                        "file": filename.rsplit("/", 1)[-1],
                        "line": line,
                        "calls": calls,
                        "self_ms": round(own * 1000, 3),
                        "cumulative_ms": round(cumulative * 1000, 3),
                    }

                with self.wall_lock:
                    wall_calls = {
                        key: list(values) for key, values in self.wall_calls.items()
                    }
                    executor_spans = list(self.executor_spans)
                self.output_path.write_text(
                    json.dumps(
                        {
                            "diagnostic_only": True,
                            "profiled_finance_calls": self.count,
                            "executor_spans": executor_spans,
                            "http_spans": list(self.http_spans),
                            "wall_calls_instrumented_ms": {
                                key: {
                                    "calls": len(values),
                                    "total": round(sum(values) * 1000, 3),
                                    "p95": round(_p95(values) * 1000, 3),
                                    "max": round(max(values) * 1000, 3),
                                }
                                for key, values in wall_calls.items()
                            },
                            "top_self": [
                                row(item)
                                for item in sorted(
                                    statistics.items(),
                                    key=lambda item: item[1][2],
                                    reverse=True,
                                )[:20]
                            ],
                            "domain_cumulative": [
                                row(item)
                                for item in sorted(
                                    statistics.items(),
                                    key=lambda item: item[1][3],
                                    reverse=True,
                                )
                                if "/adminme/" in item[0][0]
                            ][:30],
                        }
                    )
                )


def _serve(
    root: Path,
    listener: socket.socket,
    ready,
    profile: bool = False,
    planning_workload: bool = False,
) -> None:
    app = create_application(_settings(root))
    app.add_middleware(_SyntheticServerTiming)
    if profile:
        app.add_middleware(
            _SyntheticCpuProfile, output_path=root / "finance-cpu-profile.json"
        )
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
            binding = runtime.member_chat_binding(FIXTURE_MEMBER_ID)
            if planning_workload:
                # Synthetic inventory only. Seed through the same governed
                # import/projection composition used by production.
                from adminme.finance.importer import import_publication
                from adminme.finance.planning_store import import_projection
                from adminme.finance.publication import content_hash
                from adminme.lib.session import build_internal_session
                from tests.acceptance.test_finance_publications import publication
                from tests.fixtures.finance_planning import (
                    shared_projection,
                    with_conservative_minima,
                    with_grouped_issues,
                )

                accounting = publication(runtime.settings.instance.tenant_id)
                accounting["owner_id"] = FIXTURE_MEMBER_ID
                for report in accounting["reports"]:
                    for metric in report["metrics"]:
                        metric["owner_id"] = FIXTURE_MEMBER_ID
                accounting["content_sha256"] = content_hash(accounting)
                owner = build_internal_session(
                    FIXTURE_MEMBER_ID, "principal", accounting["tenant_id"]
                )
                await import_publication(runtime, owner, accounting)
                projection = with_grouped_issues(
                    with_conservative_minima(shared_projection(accounting))
                )
                await import_projection(runtime, owner, projection)
                largest = max(
                    projection["detail_manifest"], key=lambda m: m["row_count"]
                )
                inventory = {
                    r["record_id"]: r for r in projection["shared_event_inventory"]
                }
                expected = [
                    {
                        **{
                            k: v
                            for k, v in inventory[record_id].items()
                            if k != "record_id"
                        },
                        "context_id": largest["context_id"],
                    }
                    for record_id in largest["record_ids"]
                ]
                ready.send(
                    (
                        *binding,
                        {
                            "book_id": accounting["book_id"],
                            "publication_id": accounting["publication_id"],
                            "projection_id": projection["projection_id"],
                            "context_id": largest["context_id"],
                            "expected_events": expected,
                            "inventory_bodies": len(inventory),
                            "context_references": sum(
                                m["row_count"] for m in projection["detail_manifest"]
                            ),
                        },
                    )
                )
            else:
                ready.send(binding)
            ready.close()
            yield

    app.router.lifespan_context = lifespan
    uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(
        sockets=[listener]
    )


async def _finance_call(
    client,
    actor: str,
    session: str,
    number: int,
    planning: dict[str, Any] | None = None,
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
    if planning is not None:
        payload.update(
            {
                key: planning[key]
                for key in (
                    "book_id",
                    "publication_id",
                    "projection_id",
                    "context_id",
                )
            }
        )
        payload.update(
            report_id="planning_events" if calculation else "planning_projection",
            horizon_days=365,
        )
        if calculation:
            # Spread deterministic reads across the full context, including
            # deep pages, rather than repeatedly measuring its first page.
            payload["offset"] = (number * 7919) % len(planning["expected_events"])
    elif calculation:
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
        "/api/openclaw/v1/tools/finance-"
        + (
            "query"
            if planning is not None
            else "calculate"
            if calculation
            else "status"
        ),
        headers={"Authorization": f"Bearer {SECRET}"},
        json=payload,
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    if planning is not None:
        result = response.json()
        assert result.get("projection", {}).get("id") == planning["projection_id"], (
            response.text
        )
        if calculation:
            assert result.get("events") == [
                planning["expected_events"][payload["offset"]]
            ], response.text
        else:
            assert result.get("summary", {}).get("minimum") == {
                "kind": "CONSERVATIVE_SOURCE_SCALAR",
                "value_minor": 100,
                "unknown_reason": None,
            }, response.text
        assert result["provider_effect_count"] == 0 and result.get("answer_ref"), (
            response.text
        )
    elif calculation:
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


def _finance_load(
    url: str, actor: str, session: str, ready, stop, planning=None
) -> None:
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
                    client, actor, session, index + 100, planning
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
    await _measure_mixed_load(tmp_path, planning_workload=False)


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_PLANNING_BENCHMARK") != "1",
    reason="Opt-in hosted accepted-planning workload; not the family runtime",
)
async def test_accepted_planning_mixed_load_p95_gate(tmp_path: Path) -> None:
    await _measure_mixed_load(tmp_path, planning_workload=True)


async def _measure_mixed_load(tmp_path: Path, *, planning_workload: bool) -> None:
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
        target=_serve,
        args=(tmp_path / "instance", listener, send, False, planning_workload),
    )
    process.start()
    send.close()
    load_process = None
    load_receive = None
    stop_load = context.Event()
    try:
        assert await asyncio.to_thread(
            receive.poll, 120 if planning_workload else 45
        ), "synthetic HTTP server did not start"
        binding = receive.recv()
        actor, session = binding[:2]
        planning = binding[2] if planning_workload else None
        if planning is not None:
            assert planning["inventory_bodies"] == 3307
            assert planning["context_references"] == 38382
            assert len(planning["expected_events"]) == 2818
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
                    finance_client, actor, session, calls, planning
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
                args=(
                    f"http://127.0.0.1:{port}",
                    actor,
                    session,
                    load_send,
                    stop_load,
                    planning,
                ),
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
                "contract": (
                    "synthetic-accepted-planning-http-mixed-load-v1"
                    if planning_workload
                    else "synthetic-light-finance-http-mixed-load-v3"
                ),
                "python": platform.python_version(),
                "samples_per_phase": SAMPLES,
                "core_requests_per_second": 20,
                "finance_requests_per_second": 10,
                "finance_samples": len(finance),
                "independent_load_processes": True,
                ("cold_summary_ms" if planning_workload else "cold_status_ms"): round(
                    cold_status * 1000, 3
                ),
                "baseline_before_server_p95_ms": round(_p95(baseline_server) * 1000, 3),
                "baseline_after_server_p95_ms": round(_p95(after_server) * 1000, 3),
                "mixed_server_p95_ms": round(_p95(mixed_server) * 1000, 3),
                "baseline_before_p95_ms": round(_p95(baseline) * 1000, 3),
                "baseline_after_p95_ms": round(_p95(after) * 1000, 3),
                "mixed_core_p95_ms": round(_p95(mixed) * 1000, 3),
                "regression_ratio": _p95(mixed) / baseline_p95,
                (
                    "summary_p95_ms" if planning_workload else "warm_status_p95_ms"
                ): round(_p95(statuses) * 1000, 3),
                ("detail_p95_ms" if planning_workload else "calculation_p95_ms"): round(
                    _p95(calculations) * 1000, 3
                ),
                "mixed_finance_p95_ms": round(_p95(finance) * 1000, 3),
                "heavy_worker_validated": False,
                "reminder_delivery_validated": False,
                "large_ledger_validated": False,
                "deployment_hardware_validated": False,
            }
            if planning is not None:
                report["planning_inventory_bodies"] = planning["inventory_bodies"]
                report["planning_context_references"] = planning["context_references"]
                report["queried_context_rows"] = len(planning["expected_events"])
            (tmp_path / "finance-light-load-results.json").write_text(
                json.dumps(report, indent=2)
            )
            print(json.dumps(report, sort_keys=True))
            assert _p95(mixed) <= baseline_p95 * 1.10, report
            assert _p95(statuses) < (0.500 if planning_workload else 0.100), report
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
    await _profile_workload(tmp_path, planning_workload=False)


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_PLANNING_PROFILE") != "1",
    reason="Isolated hosted accepted-planning diagnostic only",
)
async def test_profile_accepted_planning_request_cost(tmp_path):
    await _profile_workload(tmp_path, planning_workload=True)


async def _profile_workload(tmp_path, *, planning_workload):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    listener = socket.socket()
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = context.Process(
        target=_serve,
        args=(tmp_path / "profile-instance", listener, send, True, planning_workload),
    )
    server.start()
    send.close()
    try:
        assert await asyncio.to_thread(receive.poll, 120 if planning_workload else 45)
        binding = receive.recv()
        actor, session = binding[:2]
        planning = binding[2] if planning_workload else None
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
                    await _finance_call(finance, actor, session, index + 500, planning)
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
            report_path = tmp_path / "profile-instance" / "finance-cpu-profile.json"
            for _ in range(50):
                if report_path.exists():
                    break
                await asyncio.sleep(0.01)
            report = json.loads(report_path.read_text())
            assert (
                report["profiled_finance_calls"] == 50
                and report["top_self"]
                and report["domain_cumulative"]
            )
            label = (
                "SYNTHETIC_PLANNING_CPU_PROFILE"
                if planning_workload
                else "SYNTHETIC_FINANCE_CPU_PROFILE"
            )
            print(
                label
                + " "
                + json.dumps(
                    {
                        k: v
                        for k, v in report.items()
                        if k not in {"executor_spans", "http_spans"}
                    }
                ),
                flush=True,
            )
            artifact_dir = os.getenv("FINANCE_PROFILE_ARTIFACT_DIR")
            if artifact_dir:
                target = Path(artifact_dir)
                target.mkdir(parents=True, exist_ok=True)
                (
                    target
                    / (
                        "planning-profile.json"
                        if planning_workload
                        else "light-profile.json"
                    )
                ).write_text(json.dumps(report, indent=2))
            waits = report["wall_calls_instrumented_ms"]
            assert "executor_wait:adminme-projections:_call_many_query" in waits
            assert "executor_wait:adminme-projections:_apply_one_for:tasks" in waits
            assert not any(":timed" in key for key in waits)
    finally:
        receive.close()
        listener.close()
        server.terminate()
        await asyncio.to_thread(server.join, 5)
        if server.is_alive():
            server.kill()
            await asyncio.to_thread(server.join, 5)
