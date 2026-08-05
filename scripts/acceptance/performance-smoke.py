"""Credential-free Mac-mini-scale release performance smoke.

This measures production composition and real encrypted/projection/API seams.
It is a regression budget, not a synthetic throughput claim for live providers.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "member-benchmark"
HEADERS = {"Tailscale-User-Login": "benchmark.fixture.ts.net"}


def _p95(values: list[float]) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def _timed(call) -> tuple[Any, float]:
    started = time.perf_counter()
    value = call()
    return value, (time.perf_counter() - started) * 1000


def benchmark() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="adminme-gate8-performance-") as raw:
        instance = Path(raw) / "instance"
        instance.mkdir()
        settings = RuntimeSettings.credential_free(
            instance,
            encryption_key=b"p" * 32,
            repository_root=REPO_ROOT,
            member_identities={"benchmark.fixture.ts.net": MEMBER},
            member_roles={MEMBER: "principal"},
            openclaw_enabled=True,
            openclaw_shared_secret="gate8-performance-secret-32-bytes",
            openclaw_actor_identities={"fixture|fixture|benchmark": MEMBER},
            openclaw_gateway_token="gate8-performance-gateway-token",
            openclaw_model_configured=True,
            member_chat_enabled_members=frozenset({MEMBER}),
            deterministic_member_chat_fixture=True,
            deterministic_openclaw_fixture=True,
            fixture_member_id=MEMBER,
        )
        app = create_application(settings)
        tracemalloc.start()
        entered = time.perf_counter()
        with TestClient(app) as client:
            cold_start_ms = (time.perf_counter() - entered) * 1000
            runtime = app.state.runtime
            append_ms: list[float] = []
            for index in range(100):
                envelope = EventEnvelope(
                    event_at_ms=1_786_000_000_000 + index,
                    tenant_id=settings.instance.tenant_id,
                    type="task.created",
                    schema_version=1,
                    occurred_at="2026-08-04T12:00:00Z",
                    source_adapter="gate8-performance",
                    source_account_id="credential-free",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    idempotency_key=f"performance:task:{index}",
                    payload={
                        "task_id": f"task-performance-{index}",
                        "title": f"Performance task {index}",
                        "owner_member_id": MEMBER,
                    },
                )

                def append_and_project(envelope=envelope):
                    event_id = client.portal.call(runtime.events.write, envelope)
                    client.portal.call(runtime.wait_for_projection, "tasks", event_id)
                    return event_id

                _, duration = _timed(append_and_project)
                append_ms.append(duration)

            _, rebuild_ms = _timed(
                lambda: client.portal.call(runtime.projections.rebuild, "tasks")
            )

            dashboard_ms: list[float] = []
            for _ in range(50):
                response, duration = _timed(
                    lambda: client.get("/api/member/v1/surfaces/today", headers=HEADERS)
                )
                assert response.status_code == 200
                dashboard_ms.append(duration)

            before_chat = client.portal.call(runtime.event_log.count)
            chat_ms: list[float] = []
            for index in range(10):
                response, duration = _timed(
                    lambda index=index: client.post(
                        "/api/member/v1/chat/stream",
                        headers=HEADERS,
                        json={
                            "message": "Help me think through this afternoon.",
                            "request_id": f"performance-chat-{index}",
                        },
                    )
                )
                assert response.status_code == 200
                chat_ms.append(duration)
            after_chat = client.portal.call(runtime.event_log.count)
            assert after_chat == before_chat

            created = client.post(
                "/api/member/v1/temporary-surfaces",
                headers=HEADERS,
                json={
                    "title": "Performance lens",
                    "purpose": "Measure allowlisted temporary-view composition.",
                    "queries": [
                        {"name": "what_now"},
                        {"name": "tasks.open"},
                        {"name": "lists.open"},
                        {"name": "calendar.week"},
                        {"name": "finance.recent"},
                    ],
                    "action_verbs": [],
                    "required_capabilities": [],
                    "compatible_modes": ["compressed"],
                },
            )
            assert created.status_code == 201
            stable_id = created.json()["surface"]["stable_id"]
            before_views = client.portal.call(runtime.event_log.count)
            view_ms: list[float] = []
            for _ in range(50):
                response, duration = _timed(
                    lambda: client.get(
                        f"/api/member/v1/temporary-surfaces/{stable_id}/evaluate",
                        headers=HEADERS,
                    )
                )
                assert response.status_code == 200
                view_ms.append(duration)
            after_views = client.portal.call(runtime.event_log.count)
            assert after_views == before_views

            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
            event_storage_bytes = sum(
                path.stat().st_size
                for path in settings.instance.event_log_path.parent.glob(
                    f"{settings.instance.event_log_path.name}*"
                )
            )
        tracemalloc.stop()

    metrics = {
        "cold_start_ms": round(cold_start_ms, 2),
        "append_projection_p50_ms": round(sorted(append_ms)[len(append_ms) // 2], 2),
        "append_projection_p95_ms": round(_p95(append_ms), 2),
        "task_projection_rebuild_ms": round(rebuild_ms, 2),
        "today_query_p95_ms": round(_p95(dashboard_ms), 2),
        "member_chat_fixture_p95_ms": round(_p95(chat_ms), 2),
        "temporary_view_five_query_p95_ms": round(_p95(view_ms), 2),
        "tracemalloc_current_mib": round(current_bytes / 1024 / 1024, 2),
        "tracemalloc_peak_mib": round(peak_bytes / 1024 / 1024, 2),
        "encrypted_event_storage_bytes_after_100_tasks": event_storage_bytes,
        "ordinary_chat_event_delta": after_chat - before_chat,
        "temporary_view_evaluation_event_delta": after_views - before_views,
    }
    budgets = {
        "cold_start_ms": 30_000,
        "append_projection_p95_ms": 1_000,
        "task_projection_rebuild_ms": 15_000,
        "today_query_p95_ms": 1_000,
        "member_chat_fixture_p95_ms": 2_000,
        "temporary_view_five_query_p95_ms": 1_000,
        "tracemalloc_peak_mib": 256,
        "encrypted_event_storage_bytes_after_100_tasks": 10 * 1024 * 1024,
    }
    failures = [
        name
        for name, maximum in budgets.items()
        if float(metrics[name]) > float(maximum)
    ]
    return {
        "schema_version": 1,
        "boundary": "credential-free production composition; provider latency excluded",
        "workload": {
            "tasks": 100,
            "today_queries": 50,
            "ordinary_chat_streams": 10,
            "five-query_temporary_view_evaluations": 50,
        },
        "metrics": metrics,
        "budgets": budgets,
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark()
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
