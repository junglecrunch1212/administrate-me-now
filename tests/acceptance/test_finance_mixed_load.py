"""Opt-in synthetic light-finance benchmark through the production root.

Run in hosted CI/a disposable lab with ADMINME_FINANCE_BENCHMARK=1. This is a
preliminary light-work gate, not heavy-worker/reminder/deployment acceptance.
"""

from __future__ import annotations

import json
import math
import os
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from adminme.demo import FIXTURE_MEMBER_ID, canonical_seed_events
from adminme.openclaw_topology import member_agent_id
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = 200
SECRET = "synthetic-finance-benchmark-loopback-secret"
HEADERS = {"Tailscale-User-Login": "fixture-benchmark.ts.net"}


def _p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_BENCHMARK") != "1",
    reason="Opt-in hosted-CI/disposable-lab finance benchmark; not run on family runtime",
)
def test_light_finance_mixed_load_p95_gate(tmp_path: Path) -> None:
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=b"b" * 32,
        repository_root=REPO_ROOT,
        member_identities={"fixture-benchmark.ts.net": FIXTURE_MEMBER_ID},
        member_roles={FIXTURE_MEMBER_ID: "principal"},
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "discord|fixture-account|fixture-actor": FIXTURE_MEMBER_ID
        },
    )
    settings = replace(
        settings,
        finance_publications_enabled=True,
        finance_model_members=frozenset({FIXTURE_MEMBER_ID}),
        member_chat_enabled_members=frozenset({FIXTURE_MEMBER_ID}),
    )
    app = create_application(settings)
    phase = ["warmup"]
    server_core_latencies: dict[str, list[float]] = {}

    class CoreTimingMiddleware:
        """Test-only server timing; raw client latency remains the release gate."""

        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http" or scope.get("path") not in {
                "/api/core/v1/what-now",
                "/api/core/v1/commitments/active",
            }:
                await self.app(scope, receive, send)
                return
            measured_phase = phase[0]
            started = time.perf_counter()

            async def measured_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    server_core_latencies.setdefault(measured_phase, []).append(
                        time.perf_counter() - started
                    )
                await send(message)

            await self.app(scope, receive, measured_send)

    app.add_middleware(CoreTimingMiddleware)
    with TestClient(app) as client:
        runtime = app.state.runtime
        finance_actor, finance_session = runtime.member_chat_binding(FIXTURE_MEMBER_ID)
        assert client.portal is not None

        async def seed() -> None:
            ids = await runtime.events.write_batch(
                canonical_seed_events(settings.instance.tenant_id)
            )
            await runtime.wait_for_projection("parties", ids[5])
            await runtime.wait_for_projection("tasks", ids[-1])

        client.portal.call(seed)
        grant = client.post(
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
        status_latencies: list[float] = []
        calculation_latencies: list[float] = []

        def finance_call() -> float:
            nonlocal calls
            calls += 1
            provenance = {
                "actor_id": finance_actor,
                "agent_id": member_agent_id(FIXTURE_MEMBER_ID),
                "session_key": finance_session,
                "session_id": "fixture-session",
                "channel_id": "administrateme-portal",
                "account_id": "member-portal",
                "message_id": f"finance-run:fixture-message-{calls}",
                "correlation_id": f"fixture-call-{calls}",
                "call_kind": "tool",
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
            }
            calculation = calls % 2 == 0
            payload: dict[str, Any] = {"provenance": provenance}
            if calculation:
                payload.update(
                    {
                        "calculator_id": "reserve_coverage",
                        "version": "1",
                        "hypothetical": True,
                        "inputs": {
                            "currency": "USD",
                            "liquid_cash_minor": 120000,
                            "monthly_reference_spending_minor": 30000,
                            "chosen_reserve_floor_minor": 60000,
                            "liquidity_confirmed": True,
                            "reference_basis": "chosen_essential_spending",
                        },
                    }
                )
            started = time.perf_counter()
            response = client.post(
                "/api/openclaw/v1/tools/finance-calculate"
                if calculation
                else "/api/openclaw/v1/tools/finance-status",
                headers={"Authorization": f"Bearer {SECRET}"},
                json=payload,
            )
            elapsed = time.perf_counter() - started
            assert response.status_code == 200, response.text
            if calculation:
                assert (
                    response.json()["values"]["reference_months_covered"] == "4.000000"
                ), response.text
                calculation_latencies.append(elapsed)
            else:
                # A real scoped database status read; missing publication is honest.
                assert "publication" in response.json(), response.text
                status_latencies.append(elapsed)
            return elapsed

        def core_call(index: int) -> float:
            path = (
                "/api/core/v1/what-now"
                if index % 2 == 0
                else "/api/core/v1/commitments/active"
            )
            started = time.perf_counter()
            response = client.get(path, headers=HEADERS)
            elapsed = time.perf_counter() - started
            assert response.status_code == 200, response.text
            return elapsed

        cold_status = finance_call()
        for index in range(20):
            core_call(index)
            finance_call()
        phase[0] = "baseline_before"
        baseline = [core_call(index) for index in range(SAMPLES)]
        mixed: list[float] = []
        finance: list[float] = []
        contention_control: list[float] = []
        phase[0] = "finance_mixed"
        with ThreadPoolExecutor(max_workers=2) as pool:
            for index in range(SAMPLES):
                barrier = threading.Barrier(2)

                def parallel_finance() -> float:
                    barrier.wait(timeout=10)
                    return finance_call()

                def parallel_core() -> float:
                    barrier.wait(timeout=10)
                    return core_call(index)

                finance_future = pool.submit(parallel_finance)
                core_future = pool.submit(parallel_core)
                mixed.append(core_future.result(timeout=10))
                finance.append(finance_future.result(timeout=10))
            # Diagnostic only: identical concurrent TestClient dispatch with a
            # cheap existing HTTP request instead of finance. This measures
            # shared-client/portal contention plus that lightweight route;
            # it does NOT replace or relax the sequential baseline gate.
            phase[0] = "contention_control"
            for index in range(SAMPLES):
                barrier = threading.Barrier(2)

                def parallel_control() -> None:
                    barrier.wait(timeout=10)
                    response = client.get("/api/system/live")
                    assert response.status_code == 200, response.text

                def parallel_core() -> float:
                    barrier.wait(timeout=10)
                    return core_call(index)

                control_future = pool.submit(parallel_control)
                core_future = pool.submit(parallel_core)
                contention_control.append(core_future.result(timeout=10))
                control_future.result(timeout=10)
        phase[0] = "baseline_after"
        after = [core_call(index) for index in range(SAMPLES)]
        # Use the slower bracketing baseline to avoid claiming a regression
        # merely because the runner warmed up or drifted during the measurement.
        baseline_p95 = max(_p95(baseline), _p95(after))
        mixed_p95 = _p95(mixed)
        server_baseline_p95 = max(
            _p95(server_core_latencies["baseline_before"]),
            _p95(server_core_latencies["baseline_after"]),
        )
        server_mixed_p95 = _p95(server_core_latencies["finance_mixed"])
        report: dict[str, Any] = {
            "contract": "synthetic-light-finance-mixed-load-v1",
            "python": platform.python_version(),
            "samples_per_phase": SAMPLES,
            "cold_status_ms": round(cold_status * 1000, 3),
            "warm_status_p95_ms": round(_p95(status_latencies[1:]) * 1000, 3),
            "calculation_p95_ms": round(_p95(calculation_latencies) * 1000, 3),
            "mixed_finance_p95_ms": round(_p95(finance) * 1000, 3),
            "baseline_before_p95_ms": round(_p95(baseline) * 1000, 3),
            "baseline_after_p95_ms": round(_p95(after) * 1000, 3),
            "mixed_core_p95_ms": round(mixed_p95 * 1000, 3),
            "regression_ratio": mixed_p95 / baseline_p95,
            "diagnostic_control_core_p95_ms": round(_p95(contention_control) * 1000, 3),
            "diagnostic_control_regression_ratio": _p95(contention_control)
            / baseline_p95,
            "diagnostic_server_baseline_core_p95_ms": round(
                server_baseline_p95 * 1000, 3
            ),
            "diagnostic_server_mixed_core_p95_ms": round(server_mixed_p95 * 1000, 3),
            "diagnostic_server_regression_ratio": server_mixed_p95
            / server_baseline_p95,
            "heavy_worker_validated": False,
            "reminder_delivery_validated": False,
            "large_ledger_validated": False,
            "deployment_hardware_validated": False,
        }
        (tmp_path / "finance-light-load-results.json").write_text(
            json.dumps(report, indent=2)
        )
        print(json.dumps(report, sort_keys=True))
        assert mixed_p95 <= baseline_p95 * 1.10, report
        assert _p95(status_latencies[1:]) < 0.100, report
        assert _p95(calculation_latencies) < 0.500, report
