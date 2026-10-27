"""Short hosted Darwin composition/lifecycle proof, never Mini qualification."""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.finance_native_resources import (
    EvidenceError,
    Registry,
    prepare_synthetic_mime_database,
)
from scripts.finance_resource_acceptance import (
    POLICY,
    QUALIFICATION_PHASES,
    evaluate,
    load_evidence,
)
from scripts.finance_performance_policy import POLICY as V3_POLICY
from tests.fixtures.finance_resource_diagnostic import (
    SMOKE_PHASES,
    append_record,
    summarize_resources,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Explicit hosted macOS synthetic correctness smoke only",
)


def identity():
    return {
        "candidate": os.environ["FINANCE_CANDIDATE_SHA"],
        "checkout": os.environ["GITHUB_SHA"],
    }


async def receive(member, timeout=30):
    if not await asyncio.to_thread(member.work.poll, timeout):
        raise EvidenceError("owned work channel timed out")
    return await asyncio.wait_for(asyncio.to_thread(member.work.recv), 5)


async def native_run(tmp_path, workload="light", *, qualification=False):
    selected_policy = os.environ.get("FINANCE_PERFORMANCE_POLICY", POLICY)
    assert selected_policy in {POLICY, V3_POLICY}, (
        "explicit known performance policy required"
    )
    phases = QUALIFICATION_PHASES if qualification else SMOKE_PHASES
    prepare_synthetic_mime_database()
    # The entire fixture instance and telemetry are fresh; only selected evidence
    # is copied to the artifact directory. No ambient service address is used.
    root = tmp_path / "native-fixture"
    root.mkdir(mode=0o700)
    output = Path(os.environ["FINANCE_NATIVE_ARTIFACT_DIR"]) / (
        workload + ("-qualification" if qualification else "-smoke")
    )
    output.mkdir(parents=True, exist_ok=True)
    listener = socket.socket()
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    registry = Registry(
        root, port, identity(), wall_seconds=1800 if qualification else 180
    )
    report: dict[str, Any] = {
        "contract": "native-finance-self-report-v1",
        "policy": selected_policy,
        "mode": "qualification" if qualification else "correctness_smoke",
        "workload": workload,
        "environment": os.environ.get(
            "FINANCE_NATIVE_ENVIRONMENT", "hosted-correctness-only"
        ),
        "identity": identity(),
        "diagnostic_only": True,
        "acceptance_eligible": False,
        "smoke_only": not qualification,
        "platform": "Darwin",
        "phases": list(phases),
        "results": [],
        "state": "incomplete",
        "errors": [],
        "limits": {
            "CPU_seconds_per_child": 60,
            "wall_seconds": registry.wall_seconds,
            "RSS_bytes_cooperative_per_child": 536870912,
            "OS_filesystem_network_sandbox": False,
            "whole_OS_process_tree_coverage": False,
        },
    }
    (output / "native-summary.json").write_text(json.dumps(report, indent=2))
    samples = []
    phase = "startup"

    async def monitor():
        while True:
            registry.drain()
            row = registry.snapshot(phase)
            samples.append(row)
            append_record(root / "resources.jsonl", row)
            await asyncio.sleep(0.1)

    server = registry.spawn(
        "runtime", listener=listener, planning=workload == "planning"
    )
    monitor_task = asyncio.create_task(monitor())
    try:
        binding = await receive(server, 120 if workload == "planning" else 60)
        actor, session = binding[:2]
        planning = binding[2] if workload == "planning" else None
        from tests.acceptance.test_finance_mixed_load import HEADERS, _client

        url = f"http://127.0.0.1:{port}"
        async with _client(url) as client:
            response = await client.post(
                "/api/member/v1/cos/delegations",
                headers=HEADERS,
                json={
                    "request_id": "synthetic-native-resource-grant",
                    "context_scope": "member_private",
                    "permissions": ["read_context"],
                },
            )
            assert response.status_code in {200, 201}, response.text
        clients = [
            registry.spawn(
                kind, url=url, actor=actor, session=session, planning=planning
            )
            for kind in ("core", "finance")
        ]
        assert await asyncio.gather(*(receive(client) for client in clients)) == [
            {"ready": True},
            {"ready": True},
        ]
        for name, duration, core_rate, finance_rate in phases:
            phase = name
            before = await registry.barrier(name + ":before")
            before["phase"] = name
            append_record(root / "resources.jsonl", before)
            start = time.monotonic() + 0.25
            append_record(
                root / "phases.jsonl",
                {
                    "name": name,
                    "start": start,
                    "duration": duration,
                    "core_rate": core_rate,
                    "finance_rate": finance_rate,
                },
            )
            for client, rate in zip(clients, (core_rate, finance_rate)):
                client.work.send(
                    {"phase": name, "start": start, "duration": duration, "rate": rate}
                )
            results = await asyncio.gather(
                *(receive(client, duration + 20) for client in clients)
            )
            after = await registry.barrier(name + ":after")
            after["phase"] = name
            append_record(root / "resources.jsonl", after)
            between = [
                row
                for row in samples
                if row["phase"] == name and before["at"] < row["at"] < after["at"]
            ]
            summary = summarize_resources([before, *between, after])
            report["results"].append(
                {
                    "phase": name,
                    "core": results[0],
                    "finance": results[1],
                    "resources": summary,
                    "before": before,
                    "after": after,
                }
            )
            assert results[0]["expected_count"] == math.ceil(duration * core_rate)
            assert results[1]["expected_count"] == math.ceil(duration * finance_rate)
            assert all(set(result["outcomes"]) <= {"success"} for result in results)
            assert not summary["invalid_intervals"] and not summary["collection_errors"]
        report["state"] = "completed"
    except BaseException as exc:
        report["errors"].append(type(exc).__name__ + ": " + str(exc)[:200])
        raise
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            report["errors"].append("monitor: " + str(exc)[:200])
        report["cleanup"] = await registry.close()
        listener.close()
        if not report["cleanup"]["registered_coverage_complete"] or report["errors"]:
            report["state"] = "incomplete"
        # Explicit filenames only: no database, configuration or arbitrary tree export.
        for name in [
            "lifecycle.jsonl",
            "resources.jsonl",
            "phases.jsonl",
            "core-requests.jsonl",
            "finance-requests.jsonl",
        ]:
            source = root / name
            if source.exists():
                (output / name).write_bytes(source.read_bytes())
        (output / "native-summary.json").write_text(json.dumps(report, indent=2))
    assert report["state"] == "completed", report
    assert report["cleanup"]["all_registered_processes_stopped"]
    assert all(
        member["startup_sample"]["peak_rss_bytes"] > 0
        for member in report["cleanup"]["members"]
    )

    return output


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_NATIVE_SMOKE") != "1",
    reason="explicit hosted native smoke",
)
@pytest.mark.parametrize("workload", ["light", "planning"])
async def test_native_production_composition_resource_smoke(tmp_path, workload):
    # Verify both persisted-control startup paths before any sustained lab:
    # planning includes the exact approved closed-package import used by its
    # synthetic query workload. This remains short correctness-only evidence.
    output = await native_run(tmp_path, workload)
    summary, raw = load_evidence(output)
    verdict = evaluate(
        summary,
        raw,
        **identity(),
        environment="hosted-correctness-only",
        policy=os.environ.get("FINANCE_PERFORMANCE_POLICY", POLICY),
    )
    assert verdict["state"] == "incomplete" and not verdict["accepted"]


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_NATIVE_QUALIFICATION") != "1",
    reason="separate approved native qualification scope required",
)
@pytest.mark.parametrize("workload", ["light", "planning"])
async def test_native_resource_qualification(tmp_path, workload):
    assert os.environ.get("FINANCE_NATIVE_ENVIRONMENT") not in {
        None,
        "",
        "hosted-correctness-only",
    }
    output = await asyncio.wait_for(
        native_run(tmp_path, workload, qualification=True), 1800
    )
    summary, raw = load_evidence(output)
    verdict = evaluate(
        summary,
        raw,
        **identity(),
        environment=os.environ["FINANCE_NATIVE_ENVIRONMENT"],
        policy=os.environ.get("FINANCE_PERFORMANCE_POLICY", POLICY),
    )
    (output / "policy-verdict.json").write_text(json.dumps(verdict, indent=2))
    assert verdict["accepted"], verdict


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_NATIVE_SMOKE") != "1",
    reason="explicit hosted native smoke",
)
async def test_native_registered_worker_exit_and_forced_cancellation(tmp_path):
    root = tmp_path / "owned-workers"
    root.mkdir()
    registry = Registry(root, 49152, identity())
    worker = registry.spawn("worker")
    try:
        assert await receive(worker) == {"worker_ready": True}
        await registry.barrier("worker-ready")
        worker.stop.set()
        for _ in range(100):
            registry.drain()
            if worker.terminal:
                break
            await asyncio.sleep(0.05)
        await asyncio.to_thread(worker.process.join, 3)
        assert worker.verdict()["complete"]
        retained_cpu = registry.snapshot("after-exit")["cpu_seconds"]
        assert retained_cpu == worker.terminal["metrics"]["cpu_seconds"]
        assert registry.snapshot("after-exit")["rss_bytes"] == 0
        killed = registry.spawn("worker")
        assert await receive(killed) == {"worker_ready": True}
        await registry.barrier("before-forced-stop")
        killed.process.kill()  # Only the exact process handle created above.
        await asyncio.to_thread(killed.process.join, 3)
    finally:
        result = await registry.close()
        output = Path(os.environ["FINANCE_NATIVE_ARTIFACT_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "worker-lifecycle.jsonl").write_bytes(
            (root / "lifecycle.jsonl").read_bytes()
        )
        (output / "worker-cleanup.json").write_text(json.dumps(result, indent=2))
    assert result["all_registered_processes_stopped"]
    assert not result["registered_coverage_complete"]
    assert any("missing" in error for error in result["members"][1]["errors"])


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_NATIVE_SMOKE") != "1",
    reason="explicit hosted native smoke",
)
async def test_native_target_failure_retains_terminal_and_cleanup(tmp_path):
    root = tmp_path / "failed-worker"
    root.mkdir()
    registry = Registry(root, 49152, identity())
    failed = registry.spawn("failure")
    try:
        for _ in range(200):
            registry.drain()
            if failed.terminal:
                break
            await asyncio.sleep(0.05)
        await asyncio.to_thread(failed.process.join, 3)
    finally:
        result = await registry.close()
        output = Path(os.environ["FINANCE_NATIVE_ARTIFACT_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "failure-lifecycle.jsonl").write_bytes(
            (root / "lifecycle.jsonl").read_bytes()
        )
        (output / "failure-cleanup.json").write_text(json.dumps(result, indent=2))
    assert result["all_registered_processes_stopped"]
    assert not result["registered_coverage_complete"]
    assert failed.terminal["outcome"] == "failed"
    assert any(
        "synthetic target failure" in fault for fault in failed.terminal["faults"]
    )


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_NATIVE_SMOKE") != "1",
    reason="explicit hosted native smoke",
)
async def test_native_parent_stop_channel_closure(tmp_path):
    root = tmp_path / "parent-channel-closure"
    root.mkdir()
    registry = Registry(root, 49152, identity())
    worker = registry.spawn("worker")
    try:
        assert await receive(worker) == {"worker_ready": True}
        await registry.barrier("before-parent-closure")
        worker.stop.close()
        for _ in range(100):
            registry.drain()
            if worker.terminal:
                break
            await asyncio.sleep(0.05)
        await asyncio.to_thread(worker.process.join, 3)
    finally:
        result = await registry.close()
        output = Path(os.environ["FINANCE_NATIVE_ARTIFACT_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "parent-closure-lifecycle.jsonl").write_bytes(
            (root / "lifecycle.jsonl").read_bytes()
        )
        (output / "parent-closure-cleanup.json").write_text(
            json.dumps(result, indent=2)
        )
    assert result["all_registered_processes_stopped"]
    assert result["registered_coverage_complete"]
