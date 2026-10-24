"""Predeclared diagnostic-only sustained load; never a replacement release gate."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import platform
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance.test_finance_mixed_load import HEADERS, _client, _serve
from tests.fixtures.finance_resource_diagnostic import (
    FULL_PHASES,
    SMOKE_PHASES,
    OwnedProcessTree,
    append_record,
    compare_phases,
    load_client,
    summarize_resources,
)


async def _receive(connection, timeout):
    assert await asyncio.to_thread(connection.poll, timeout), (
        "synthetic client/server timed out"
    )
    return connection.recv()


async def _stop(process):
    if process.is_alive():
        process.terminate()
    await asyncio.to_thread(process.join, 10)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join, 5)
    assert not process.is_alive(), "owned synthetic process did not stop"


async def _diagnostic(tmp_path: Path, workload: str, *, smoke: bool) -> dict:
    phases = SMOKE_PHASES if smoke else FULL_PHASES
    output = Path(
        os.environ.get("FINANCE_RESOURCE_ARTIFACT_DIR", str(tmp_path / "evidence"))
    ) / (workload + ("-smoke" if smoke else "-sustained"))
    output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "contract": "synthetic-finance-sustained-resources-v1",
        "diagnostic_only": True,
        "acceptance_eligible": False,
        "existing_10_percent_gates_changed": False,
        "smoke_only": smoke,
        "workload": workload,
        "candidate_head": os.getenv("FINANCE_CANDIDATE_SHA"),
        "evaluated_checkout": os.getenv("GITHUB_SHA"),
        "python": platform.python_version(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "deployment_hardware_validated": False,
        "runtime_fixture": "credential_free_synthetic",
        "latency_method": "client operation including response validation; raw server header timing retained separately",
        "phases": [
            {
                "name": name,
                "duration_seconds": duration,
                "core_requests_per_second": core,
                "finance_requests_per_second": finance,
            }
            for name, duration, core, finance in phases
        ],
        "resource_method": {
            "scope": "explicitly_spawned_runtime_and_descendants_only",
            "cpu": "proc_stat_user_system_plus_waited_children_plus_live_descendants",
            "unit": "CPU seconds / wall seconds = logical-core equivalents",
            "threads_included": True,
            "load_generators_included": False,
            "sampling_seconds": 0.25,
            "atomic_tree_snapshot": False,
            "rss": "sum resident pages, shared pages may be counted more than once",
            "limits": [
                "sub-sample RSS spikes may be missed",
                "unwaited exited/orphaned children can leave gaps",
                "not production headroom or cgroup peak",
            ],
        },
        "results": [],
        "errors": [],
        "state": "running",
    }
    report_path = output / "summary.json"
    report_path.write_text(json.dumps(report, indent=2))
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    listener = socket.socket()
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = context.Process(
        target=_serve,
        args=(tmp_path / "instance", listener, send, False, workload == "planning"),
    )
    server.start()
    send.close()
    assert server.pid is not None
    report["runtime_pid"] = server.pid
    tree = OwnedProcessTree(server.pid)
    current_phase = "startup"
    samples = []
    clients = []
    connections = []

    async def monitor():
        while True:
            row = tree.sample(current_phase)
            samples.append(row)
            append_record(output / "resources.jsonl", row)
            await asyncio.sleep(0.25)

    monitor_task = asyncio.create_task(monitor())
    try:
        binding = await _receive(receive, 120 if workload == "planning" else 45)
        actor, session = binding[:2]
        planning = binding[2] if workload == "planning" else None
        if planning:
            assert planning["inventory_bodies"] == 3307
            assert planning["context_references"] == 38382
            report["planning_inventory_bodies"] = planning["inventory_bodies"]
            report["planning_context_references"] = planning["context_references"]
        async with _client(url) as client:
            response = await client.post(
                "/api/member/v1/cos/delegations",
                headers=HEADERS,
                json={
                    "request_id": "synthetic-resource-grant",
                    "context_scope": "member_private",
                    "permissions": ["read_context"],
                },
            )
            assert response.status_code in {200, 201}, response.text
        for kind in ("core", "finance"):
            parent, child = context.Pipe()
            process = context.Process(
                target=load_client,
                args=(
                    url,
                    kind,
                    actor,
                    session,
                    planning,
                    child,
                    output / f"{kind}-requests.jsonl",
                ),
            )
            process.start()
            child.close()
            clients.append(process)
            connections.append(parent)
        assert await asyncio.gather(
            *(_receive(connection, 30) for connection in connections)
        ) == [{"ready": True}, {"ready": True}]
        report["load_generator_pids_excluded_from_resources"] = [
            process.pid for process in clients
        ]
        for name, duration, core_rate, finance_rate in phases:
            current_phase = name
            start = time.monotonic() + 0.5
            phase_result: dict[str, Any] = {
                "name": name,
                "start_at": start,
                "end_at": start + duration,
                "duration_seconds": duration,
                "core_rate": core_rate,
                "finance_rate": finance_rate,
            }
            append_record(output / "phases.jsonl", phase_result)
            for connection, rate in zip(connections, (core_rate, finance_rate)):
                connection.send(
                    {"phase": name, "start": start, "duration": duration, "rate": rate}
                )
            results = await asyncio.gather(
                *(_receive(connection, duration + 35) for connection in connections)
            )
            phase_result.update(core=results[0], finance=results[1])
            phase_samples = [
                row
                for row in samples
                if row["phase"] == name and start <= row["at"] <= start + duration
            ]
            phase_result["resources"] = summarize_resources(phase_samples)
            report["results"].append(phase_result)
            for kind, result in zip(("core", "finance"), results):
                if any(
                    outcome != "success" and count
                    for outcome, count in result["outcomes"].items()
                ):
                    report["errors"].append(
                        {"phase": name, "client": kind, "outcomes": result["outcomes"]}
                    )
            if (
                phase_result["resources"]["samples"] < 2
                or phase_result["resources"]["collection_errors"]
                or phase_result["resources"]["invalid_intervals"]
            ):
                report["errors"].append(
                    {"phase": name, "resource_measurement_gap": True}
                )
            report_path.write_text(json.dumps(report, indent=2))
        report["comparisons"] = compare_phases(report["results"])
        report["state"] = "completed"
    except BaseException as exc:
        report["state"] = "incomplete"
        report["errors"].append(
            {"error_type": type(exc).__name__, "error": str(exc)[:500]}
        )
        raise
    finally:
        current_phase = "shutdown"
        for connection in connections:
            try:
                connection.send(None)
            except (OSError, EOFError):
                pass
        for process in clients:
            await asyncio.to_thread(process.join, 3)
            await _stop(process)
        for connection in connections:
            connection.close()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            report["errors"].append({"collector_error": type(exc).__name__})
        await _stop(server)
        receive.close()
        listener.close()
        report["cleanup"] = {
            "runtime_exitcode": server.exitcode,
            "client_exitcodes": [process.exitcode for process in clients],
            "all_owned_processes_stopped": not any(
                process.is_alive() for process in [server, *clients]
            ),
        }
        report["resource_samples"] = len(samples)
        report["resource_collection_errors_all_phases"] = sum(
            len(row["errors"]) for row in samples
        )
        report["observed_processes"] = [
            {"pid": pid, "start_ticks": stamp} for pid, stamp in tree.identities.items()
        ]
        report_path.write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "contract": report["contract"],
                    "workload": workload,
                    "state": report["state"],
                    "errors": report["errors"],
                    "acceptance_eligible": False,
                },
                sort_keys=True,
            )
        )
    assert not report["errors"], report["errors"]
    return report


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_RESOURCE_DIAGNOSTIC") != "1"
    or platform.system() != "Linux",
    reason="Explicit hosted synthetic diagnostic only; no local lab authorized",
)
@pytest.mark.parametrize("workload", ["light", "planning"])
async def test_sustained_resource_diagnostic(tmp_path, workload):
    await _diagnostic(tmp_path, workload, smoke=False)


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_RESOURCE_SMOKE") != "1" or platform.system() != "Linux",
    reason="Hosted diagnostic harness correctness smoke only",
)
async def test_resource_diagnostic_smoke(tmp_path):
    report = await _diagnostic(tmp_path, "light", smoke=True)
    assert report["state"] == "completed"
    assert all(row["core"]["scheduled_count"] == 4 for row in report["results"])
    assert all(row["resources"]["samples"] >= 2 for row in report["results"])
    assert report["cleanup"]["all_owned_processes_stopped"]
