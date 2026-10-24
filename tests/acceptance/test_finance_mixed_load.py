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

    def __init__(
        self, app, output_path, authority_control=False, tasks_diagnostic=None
    ):
        import cProfile

        self.app = app
        self.output_path = output_path
        self.profile = cProfile.Profile()
        self.cpu_detail = os.getenv("ADMINME_FINANCE_CPU_DETAIL") == "1"
        self.authority_control = authority_control
        self.tasks_diagnostic = tasks_diagnostic
        self.count = 0
        self.started = False
        self.completed = False
        self.loop_thread_id = None
        self.wall_calls = {}
        self.phase_wall_calls = {}
        self.phase_cpu_calls = {}
        self.phase = "warmup"
        self.executor_spans = []
        self.loop_spans = []
        self.http_spans = []
        self.profile_started_at = 0.0
        # Diagnostic-only instrumentation distinguishes main-loop blocking
        # from work already dispatched to the native projection worker. It is
        # installed only in this separate profile server, never the p95 gate.
        import threading
        import concurrent.futures
        from adminme.events import log as event_log
        from adminme.finance import disclosure, planning_store
        from starlette.responses import JSONResponse
        from fastapi import routing as response_routing
        from fastapi._compat import ModelField
        from adminme.products.openclaw import main as native
        from adminme.projections.runner import ProjectionRunner

        self.wall_lock = threading.Lock()

        def record(label, elapsed, phase, cpu_elapsed=None):
            with self.wall_lock:
                self.wall_calls.setdefault(label, []).append(elapsed)
                self.phase_wall_calls.setdefault(f"{phase}:{label}", []).append(elapsed)
                if cpu_elapsed is not None:
                    self.phase_cpu_calls.setdefault(f"{phase}:{label}", []).append(
                        cpu_elapsed
                    )

        original_submit = concurrent.futures.ThreadPoolExecutor.submit

        def observed_submit(executor, func, /, *args, **kwargs):
            queued_at = time.perf_counter()
            active = self.started and not self.completed
            phase = self.phase
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
                    *({"context_fabric"} if tasks_diagnostic is not None else set()),
                }
            ):
                operation += ":" + args[0]

            def begin():
                began_at = time.perf_counter()
                cpu_started = time.thread_time()
                if active:
                    record(
                        f"executor_wait:{lane}:{operation}", began_at - queued_at, phase
                    )
                try:
                    return func(*args, **kwargs)
                finally:
                    if active:
                        finished_at = time.perf_counter()
                        cpu_elapsed = time.thread_time() - cpu_started
                        with self.wall_lock:
                            self.executor_spans.append(
                                {
                                    "operation": f"{lane}:{operation}",
                                    "phase": phase,
                                    "thread_cpu_ms": round(cpu_elapsed * 1000, 3),
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

        # Callback occupancy excludes selector sleep and suspended coroutines.
        # Thread CPU excludes descheduling/native waits, but the remainder of
        # wall time cannot distinguish SQL I/O, scheduling and GIL contention.
        # This instrumentation exists only in the separate diagnostic server.
        original_handle_run = asyncio.Handle._run  # type: ignore[attr-defined]

        def observed_handle_run(handle):
            if not self.started or self.completed:
                return original_handle_run(handle)
            phase = self.phase
            began_at = time.perf_counter()
            cpu_started = time.thread_time()
            callback = handle._callback
            owner = getattr(callback, "__self__", None)
            if isinstance(owner, asyncio.Task):
                coroutine = owner.get_coro()
                label = getattr(coroutine, "__qualname__", type(coroutine).__name__)
            else:
                label = getattr(callback, "__qualname__", type(callback).__name__)
            try:
                return original_handle_run(handle)
            finally:
                # The stop callback serializes after all measured responses;
                # exclude it from both occupancy and the on-disk measurements.
                if not self.completed:
                    self.loop_spans.append(
                        {
                            "callback": label,
                            "phase": phase,
                            "thread_cpu_ms": round(
                                (time.thread_time() - cpu_started) * 1000, 3
                            ),
                            "started_ms": round(
                                (began_at - self.profile_started_at) * 1000, 3
                            ),
                            "finished_ms": round(
                                (time.perf_counter() - self.profile_started_at) * 1000,
                                3,
                            ),
                        }
                    )

        asyncio.Handle._run = observed_handle_run  # type: ignore[attr-defined]

        def instrument(owner, name, label):
            original = getattr(owner, name)

            @wraps(original)
            def timed(*args, **kwargs):
                active = self.started and not self.completed
                phase = self.phase
                started = time.perf_counter()
                cpu_started = time.thread_time()
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
                        record(
                            f"{label}:{thread}",
                            time.perf_counter() - started,
                            phase,
                            time.thread_time() - cpu_started,
                        )

            setattr(owner, name, timed)

        original_count_since = event_log.EventLog.count_since

        @wraps(original_count_since)
        async def observed_count_since(*args, **kwargs):
            if not self.started or self.completed:
                return await original_count_since(*args, **kwargs)
            import sys

            phase = self.phase
            started = time.perf_counter()
            # Code locations only; never retain frames, subscriber identities,
            # event contents or private arguments in the synthetic artifact.
            frame = sys._getframe(1)
            locations = []
            try:
                for _ in range(3):
                    if frame is None:
                        break
                    locations.append(
                        frame.f_code.co_filename.rsplit("/", 1)[-1]
                        + ":"
                        + frame.f_code.co_name
                    )
                    frame = frame.f_back
            finally:
                del frame
            try:
                return await original_count_since(*args, **kwargs)
            finally:
                record(
                    "lag_count:" + ">".join(locations),
                    time.perf_counter() - started,
                    phase,
                )

        event_log.EventLog.count_since = observed_count_since

        original_serialize = response_routing.serialize_response

        @wraps(original_serialize)
        async def observed_serialize(*args, **kwargs):
            active = self.started and not self.completed
            phase = self.phase
            started = time.perf_counter()
            try:
                return await original_serialize(*args, **kwargs)
            finally:
                if active:
                    # This wrapper may suspend for synchronous endpoint
                    # validation; thread CPU across that await would be false
                    # attribution. Actual loop occupancy is recorded separately.
                    record("response_serialize", time.perf_counter() - started, phase)

        response_routing.serialize_response = observed_serialize

        # These nested timings attribute JSON work by executing thread. They
        # overlap envelope/response timings and must not be summed as disjoint.
        instrument(json, "loads", "json_decode")
        instrument(json, "dumps", "json_encode")
        instrument(event_log, "_row_to_event", "event_envelope_decode")
        instrument(JSONResponse, "render", "response_json_render")
        instrument(ModelField, "serialize_json", "response_pydantic_json")
        instrument(disclosure, "validate_current_authority", "final_authority")
        instrument(planning_store, "query_projection", "planning_query")
        instrument(native, "_session", "resolve_session")
        instrument(ProjectionRunner, "_execute_sync", "projection_dispatch")
        instrument(ProjectionRunner, "_execute_statement", "projection_statement")
        instrument(ProjectionRunner, "_apply_one_for", "projection_event_apply")
        instrument(ProjectionRunner, "_call_many_query", "core_snapshot")

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        # Test-only controls exist solely in the isolated profile middleware.
        # Start after warmup and stop after the last baseline response, so cold
        # imports and synchronous profile serialization cannot distort a core
        # tail inside the diagnostic window.
        if scope["type"] == "http" and path.startswith("/__synthetic/profile/"):
            operation = path.rsplit("/", 1)[-1]
            if operation == "start":
                import threading

                if self.tasks_diagnostic is not None:
                    await self.tasks_diagnostic.start()
                self.loop_thread_id = threading.get_ident()
                self.profile_started_at = time.perf_counter()
                self.phase = "before"
                self.started = True
                if self.cpu_detail:
                    self.profile.enable()
            elif operation in {
                "mixed-0",
                "mixed-12.5",
                "mixed-25",
                "mixed-37.5",
                "after",
            }:
                self.phase = operation
                if self.tasks_diagnostic is not None:
                    self.tasks_diagnostic.set_phase(operation)
            elif operation == "stop":
                self._finish()
                if self.tasks_diagnostic is not None:
                    extra = await self.tasks_diagnostic.finish()
                    report = json.loads(self.output_path.read_text())
                    report["tasks_noop_diagnostic"] = extra
                    self.output_path.write_text(json.dumps(report))
            else:
                raise AssertionError("unknown synthetic profile control")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        finance = scope["type"] == "http" and path.startswith(
            "/api/openclaw/v1/tools/finance-"
        )
        began_at = time.perf_counter()
        phase = self.phase
        tracked = self.started and not self.completed and scope["type"] == "http"
        try:
            await self.app(scope, receive, send)
        finally:
            if tracked:
                self.http_spans.append(
                    {
                        "route": path,
                        "phase": phase,
                        "started_ms": round(
                            (began_at - self.profile_started_at) * 1000, 3
                        ),
                        "finished_ms": round(
                            (time.perf_counter() - self.profile_started_at) * 1000, 3
                        ),
                    }
                )
        if finance and tracked:
            self.count += 1

    def _finish(self):
        import pstats

        self.profile.disable()
        self.completed = True
        # pstats exposes this public mapping at runtime; its stub omits it.
        statistics = (
            cast(Any, pstats.Stats(self.profile)).stats if self.cpu_detail else {}
        )

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
            wall_calls = {key: list(values) for key, values in self.wall_calls.items()}
            executor_spans = list(self.executor_spans)
            phase_wall_calls = {
                key: list(values) for key, values in self.phase_wall_calls.items()
            }
            phase_cpu_calls = {
                key: list(values) for key, values in self.phase_cpu_calls.items()
            }
        self.output_path.write_text(
            json.dumps(
                {
                    "diagnostic_only": True,
                    "profiled_finance_calls": self.count,
                    "cpu_profile_enabled": self.cpu_detail,
                    "independent_load_processes": True,
                    "final_authority_sql_stubbed": self.authority_control,
                    "acceptance_eligible": False,
                    "executor_spans": executor_spans,
                    "loop_callback_spans": list(self.loop_spans),
                    "cpu_time_semantics": (
                        "thread CPU excludes waits; wall minus CPU includes SQL I/O, "
                        "scheduling and possible GIL contention, not GIL time alone"
                    ),
                    "http_spans": list(self.http_spans),
                    "phase_thread_cpu_instrumented_ms": {
                        key: {
                            "calls": len(values),
                            "total": round(sum(values) * 1000, 3),
                            "p95": round(_p95(values) * 1000, 3),
                            "max": round(max(values) * 1000, 3),
                        }
                        for key, values in phase_cpu_calls.items()
                    },
                    "phase_wall_calls_instrumented_ms": {
                        key: {
                            "calls": len(values),
                            "total": round(sum(values) * 1000, 3),
                            "p95": round(_p95(values) * 1000, 3),
                            "max": round(max(values) * 1000, 3),
                        }
                        for key, values in phase_wall_calls.items()
                    },
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
    authority_control: bool = False,
    tasks_noop_mode: str | None = None,
) -> None:
    diagnostic = None
    diagnostic_context = None
    if tasks_noop_mode is not None:
        assert profile and not authority_control
        assert tasks_noop_mode in {"baseline", "control"}
        from tests.fixtures.finance_tasks_noop import TasksNoopDiagnostic

        diagnostic = TasksNoopDiagnostic(control=tasks_noop_mode == "control")
        diagnostic_context = diagnostic.install()
        diagnostic_context.__enter__()
    if authority_control:
        assert profile, "authority control cannot run in an acceptance server"
        from adminme.finance import disclosure

        original_authority = disclosure.validate_current_authority

        def synthetic_final_authority_control(runtime, session, expected_digest=None):
            # Deliberately unqualified TEST CONTROL: omit only the final SQL
            # read/handoff after receipt persistence. Initial/current session
            # queries, owner checks, hashes and all audit work still execute.
            # Never use this control as evidence of real authorization safety
            # or latency acceptance. The fixture has no authority mutations.
            if expected_digest is None or session.service_context is None:
                return original_authority(runtime, session, expected_digest)
            disclosure._check_current_owner(runtime, session)
            digest = disclosure._authority_digest(session, session.service_context)
            if digest != expected_digest:
                raise disclosure.FinanceDisclosureError(
                    "synthetic_control_digest_mismatch"
                )
            return digest

        disclosure.validate_current_authority = synthetic_final_authority_control
    app = create_application(_settings(root))
    app.add_middleware(_SyntheticServerTiming)
    if profile:
        app.add_middleware(
            _SyntheticCpuProfile,
            output_path=root / "finance-cpu-profile.json",
            authority_control=authority_control,
            tasks_diagnostic=diagnostic,
        )
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            runtime = application.state.runtime
            if diagnostic is not None:
                diagnostic.runtime = runtime
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
    try:
        uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(
            sockets=[listener]
        )
    finally:
        if diagnostic_context is not None:
            diagnostic_context.__exit__(None, None, None)


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
    url: str,
    actor: str,
    session: str,
    ready,
    stop,
    planning=None,
    max_calls: int = 800,
    first_call: int = 100,
) -> None:
    """Independent process/event loop: finance client work cannot delay core timing."""

    async def load():
        statuses: list[float] = []
        calculations: list[float] = []
        async with _client(url) as client:
            ready.send({"ready": True})
            start = time.perf_counter()
            for index in range(max_calls):
                if stop.is_set():
                    break
                calculation, elapsed = await _finance_call(
                    client, actor, session, index + first_call, planning
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


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_AUTHORITY_CONTROL") != "1",
    reason="Isolated synthetic counterfactual only; not authorization or latency acceptance",
)
@pytest.mark.parametrize("planning_workload", [False, True])
async def test_profile_final_authority_sql_control(tmp_path, planning_workload):
    await _profile_workload(
        tmp_path, planning_workload=planning_workload, authority_control=True
    )


async def _profile_workload(
    tmp_path, *, planning_workload, authority_control=False, tasks_noop_mode=None
):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    listener = socket.socket()
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = context.Process(
        target=_serve,
        args=(
            tmp_path / "profile-instance",
            listener,
            send,
            True,
            planning_workload,
            authority_control,
            tasks_noop_mode,
        ),
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

            for index in range(20):
                response = await core.get(
                    "/api/core/v1/what-now"
                    if index % 2 == 0
                    else "/api/core/v1/commitments/active",
                    headers=HEADERS,
                )
                assert response.status_code == 200
                await _finance_call(finance, actor, session, index + 1000, planning)

            diagnostic_core_samples: dict[str, list[float]] = {}
            current_phase = "before"

            async def core_calls():
                started = time.perf_counter()
                for index in range(100):
                    request_started = time.perf_counter()
                    response = await core.get(
                        "/api/core/v1/what-now"
                        if index % 2 == 0
                        else "/api/core/v1/commitments/active",
                        headers=HEADERS,
                    )
                    assert response.status_code == 200
                    if tasks_noop_mode is not None:
                        diagnostic_core_samples.setdefault(current_phase, []).append(
                            (time.perf_counter() - request_started) * 1000
                        )
                    await asyncio.sleep(
                        max(0, started + (index + 1) * 0.05 - time.perf_counter())
                    )

            await asyncio.sleep(0.25)  # Settle warmup audit work before diagnosis.
            assert (await core.get("/__synthetic/profile/start")).status_code == 200
            await core_calls()
            phases = (
                ("mixed-0", 0.0),
                ("mixed-12.5", 0.0125),
                ("mixed-25", 0.025),
                ("mixed-37.5", 0.0375),
            )
            for phase_index, (phase, delay) in enumerate(phases):
                current_phase = phase
                assert (
                    await core.get("/__synthetic/profile/" + phase)
                ).status_code == 200
                load_receive, load_send = context.Pipe(duplex=False)
                stop_load = context.Event()
                load = context.Process(
                    target=_finance_load,
                    args=(
                        f"http://127.0.0.1:{port}",
                        actor,
                        session,
                        load_send,
                        stop_load,
                        planning,
                        50,
                        100 + phase_index * 100,
                    ),
                )
                load.start()
                load_send.close()
                try:
                    assert await asyncio.to_thread(load_receive.poll, 15)
                    assert load_receive.recv() == {"ready": True}
                    await asyncio.sleep(delay)
                    await core_calls()
                    stop_load.set()
                    assert await asyncio.to_thread(load_receive.poll, 15)
                    measured = load_receive.recv()
                    assert "error" not in measured, measured
                    assert (
                        len(measured["statuses"]) + len(measured["calculations"]) == 50
                    )
                finally:
                    stop_load.set()
                    await asyncio.to_thread(load.join, 5)
                    if load.is_alive():
                        load.terminate()
                        await asyncio.to_thread(load.join, 5)
                    if load.is_alive():
                        load.kill()
                        await asyncio.to_thread(load.join, 5)
                    load_receive.close()
                assert load.exitcode == 0
            current_phase = "after"
            assert (await core.get("/__synthetic/profile/after")).status_code == 200
            await core_calls()
            assert (await core.get("/__synthetic/profile/stop")).status_code == 200
            report_path = tmp_path / "profile-instance" / "finance-cpu-profile.json"
            for _ in range(50):
                if report_path.exists():
                    break
                await asyncio.sleep(0.01)
            report = json.loads(report_path.read_text())
            if tasks_noop_mode is not None:
                report["tasks_noop_diagnostic"]["raw_core_samples_ms"] = (
                    diagnostic_core_samples
                )
            if planning is not None:
                report["planning_inventory"] = {
                    "bodies": planning["inventory_bodies"],
                    "references": planning["context_references"],
                    "queried_context_rows": len(planning["expected_events"]),
                }
            # Retain the raw diagnostic even when a validity check below fails.
            artifact_dir = os.getenv("FINANCE_PROFILE_ARTIFACT_DIR")
            if artifact_dir:
                target = Path(artifact_dir)
                target.mkdir(parents=True, exist_ok=True)
                (
                    target
                    / (
                        ("planning" if planning_workload else "light")
                        + ("-authority-control" if authority_control else "")
                        + ("-tasks-noop-" + tasks_noop_mode if tasks_noop_mode else "")
                        + "-profile.json"
                    )
                ).write_text(json.dumps(report, indent=2))
            assert (
                report["profiled_finance_calls"] == 200
                and report["independent_load_processes"]
                and (not report["cpu_profile_enabled"] or report["top_self"])
            )
            if planning is not None:
                assert report["planning_inventory"] == {
                    "bodies": 3307,
                    "references": 38382,
                    "queried_context_rows": 2818,
                }
            assert report["final_authority_sql_stubbed"] == authority_control
            assert report["acceptance_eligible"] is False
            assert report["loop_callback_spans"]
            assert all(
                span["thread_cpu_ms"] >= 0 and span["finished_ms"] >= span["started_ms"]
                for span in report["loop_callback_spans"] + report["executor_spans"]
            )
            assert any(
                "event_envelope_decode:http_loop" == label
                for label in report["wall_calls_instrumented_ms"]
            )
            # Pinned FastAPI uses direct Pydantic JSON bytes for typed
            # endpoints. JSONResponse.render is optional on that fast path.
            assert "response_serialize" in report["wall_calls_instrumented_ms"]
            label = (
                "SYNTHETIC_PLANNING_REQUEST_PROFILE"
                if planning_workload
                else "SYNTHETIC_FINANCE_REQUEST_PROFILE"
            )
            if authority_control:
                label += "_UNQUALIFIED_AUTHORITY_CONTROL"
            print(
                label
                + " "
                + json.dumps(
                    {
                        k: v
                        for k, v in report.items()
                        if k
                        not in {
                            "executor_spans",
                            "http_spans",
                            "loop_callback_spans",
                            "tasks_noop_diagnostic",
                        }
                    }
                ),
                flush=True,
            )
            for phase in ("before", *(name for name, _ in phases), "after"):
                assert (
                    sum(
                        item["phase"] == phase
                        and item["route"].startswith("/api/core/")
                        for item in report["http_spans"]
                    )
                    == 100
                )
            waits = report["wall_calls_instrumented_ms"]
            assert "executor_wait:adminme-projections:_call_many_query" in waits
            if tasks_noop_mode != "control":
                assert "executor_wait:adminme-projections:_apply_one_for:tasks" in waits
            else:
                assert (
                    "executor_wait:adminme-projections:_apply_one_for:tasks"
                    not in waits
                )
            assert not any(":timed" in key for key in waits)
            return report
    finally:
        receive.close()
        listener.close()
        server.terminate()
        await asyncio.to_thread(server.join, 5)
        if server.is_alive():
            server.kill()
            await asyncio.to_thread(server.join, 5)
