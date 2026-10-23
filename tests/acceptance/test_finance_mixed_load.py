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


def _serve(root: Path, listener: socket.socket, ready) -> None:
    app = create_application(_settings(root))
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


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_BENCHMARK") != "1",
    reason="Opt-in isolated hosted HTTP benchmark; not the family runtime",
)
async def test_light_finance_mixed_load_p95_gate(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    process = context.Process(
        target=_serve, args=(tmp_path / "instance", listener, send)
    )
    process.start()
    send.close()
    try:
        assert await asyncio.to_thread(receive.poll, 45), (
            "synthetic HTTP server did not start"
        )
        actor, session = receive.recv()
        async with (
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=10
            ) as core_client,
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=10
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
                calculation = calls % 2 == 0
                payload: dict[str, Any] = {
                    "provenance": {
                        "actor_id": actor,
                        "agent_id": member_agent_id(FIXTURE_MEMBER_ID),
                        "session_key": session,
                        "session_id": "fixture-session",
                        "channel_id": "administrateme-portal",
                        "account_id": "member-portal",
                        "message_id": f"finance-run:fixture-{calls}",
                        "correlation_id": f"fixture-{calls}",
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
                response = await finance_client.post(
                    "/api/openclaw/v1/tools/finance-"
                    + ("calculate" if calculation else "status"),
                    headers={"Authorization": f"Bearer {SECRET}"},
                    json=payload,
                )
                elapsed = time.perf_counter() - started
                assert response.status_code == 200, response.text
                if calculation:
                    assert (
                        response.json()["values"]["reference_months_covered"]
                        == "4.000000"
                    ), response.text
                    calculations.append(elapsed)
                else:
                    assert "publication" in response.json(), response.text
                    statuses.append(elapsed)
                return elapsed

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
                return elapsed

            cold_status = await finance_call()
            for index in range(30):
                await core_call(index)
                await finance_call()
            baseline = [await core_call(i) for i in range(SAMPLES)]
            mixed = []
            finance = []
            for index in range(SAMPLES):
                # Alternate launch order; neither caller gets an artificial head start.
                if index % 2:
                    f, c = await asyncio.gather(finance_call(), core_call(index))
                else:
                    c, f = await asyncio.gather(core_call(index), finance_call())
                mixed.append(c)
                finance.append(f)
            after = [await core_call(i) for i in range(SAMPLES)]
            baseline_p95 = max(_p95(baseline), _p95(after))
            report = {
                "contract": "synthetic-light-finance-http-mixed-load-v2",
                "python": platform.python_version(),
                "samples_per_phase": SAMPLES,
                "cold_status_ms": round(cold_status * 1000, 3),
                "baseline_before_p95_ms": round(_p95(baseline) * 1000, 3),
                "baseline_after_p95_ms": round(_p95(after) * 1000, 3),
                "mixed_core_p95_ms": round(_p95(mixed) * 1000, 3),
                "regression_ratio": _p95(mixed) / baseline_p95,
                "warm_status_p95_ms": round(_p95(statuses[1:]) * 1000, 3),
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
            assert _p95(statuses[1:]) < 0.100, report
            assert _p95(calculations) < 0.500, report
    finally:
        receive.close()
        listener.close()
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)
