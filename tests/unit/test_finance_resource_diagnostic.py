"""Diagnostic evidence must retain tails, failures, queueing and process scope."""

import asyncio
import json
import time
from pathlib import Path

import yaml
import httpx

import pytest

from tests.fixtures import finance_resource_diagnostic as diagnostic

from tests.fixtures.finance_resource_diagnostic import (
    FULL_PHASES,
    compare_phases,
    OwnedProcessTree,
    parse_stat,
    quantiles,
    run_phase,
    summarize_resources,
)


def _stat(pid, ppid, own=10, waited=0, start=100, rss=5):
    fields = ["0"] * 22
    fields[0] = "S"
    for index, value in {1: ppid, 11: own, 13: waited, 19: start, 21: rss}.items():
        fields[index] = str(value)
    return f"{pid} (name with ) spaces) " + " ".join(fields)


def _process(root, pid, ppid, **values):
    path = root / str(pid)
    path.mkdir(exist_ok=True)
    (path / "stat").write_text(_stat(pid, ppid, **values))
    task = path / "task" / str(pid)
    task.mkdir(parents=True, exist_ok=True)
    (task / "children").write_text("")
    return task


def test_quantiles_keep_outliers_and_report_low_tail_count():
    result = quantiles([1.0] * 98 + [50.0, 100.0])
    assert result["p95"] == 1
    assert result["p99"] == 50
    assert result["max"] == 100
    assert result["tail_caution"]
    assert quantiles([])["p99"] is None


def test_fixed_envelope_does_not_replace_existing_stress_gate():
    assert ("normal", 900, 2, 0.2) in FULL_PHASES
    assert ("burst", 30, 20, 10) in FULL_PHASES
    assert FULL_PHASES[2] == ("baseline_before", 60, 2, 0)
    assert FULL_PHASES[4] == ("baseline_after", 60, 2, 0)


def test_owned_tree_reads_threads_children_and_waited_cpu_only(tmp_path):
    task = _process(tmp_path, 100, 1, own=100, waited=20)
    other_thread = task.parent / "101"
    other_thread.mkdir()
    (other_thread / "children").write_text("200")
    _process(tmp_path, 200, 100, own=30, waited=10, rss=3)
    _process(tmp_path, 999, 1, own=9000, rss=9000)
    tree = OwnedProcessTree(100, proc=tmp_path, ticks=100, page_size=4096)
    result = tree.sample("normal")
    assert not result["errors"]
    assert {row["pid"] for row in result["processes"]} == {100, 200}
    assert result["cpu_seconds"] == pytest.approx(1.6)
    assert result["rss_bytes"] == 8 * 4096
    # A waited child disappears; its CPU is retained in the parent's OS counter.
    (other_thread / "children").write_text("")
    (tmp_path / "100/stat").write_text(_stat(100, 1, own=110, waited=60))
    assert tree.sample("normal")["cpu_seconds"] == pytest.approx(1.7)


@pytest.mark.parametrize("defect", ["pid_reuse", "parent_change", "missing"])
def test_tree_gaps_cannot_turn_into_zero_cpu_or_memory(tmp_path, defect):
    task = _process(tmp_path, 100, 1)
    (task / "children").write_text("200")
    _process(tmp_path, 200, 100)
    tree = OwnedProcessTree(100, proc=tmp_path, ticks=100, page_size=4096)
    assert not tree.sample("normal")["errors"]
    if defect == "missing":
        (tmp_path / "200/stat").unlink()
    else:
        (tmp_path / "200/stat").write_text(
            _stat(
                200,
                999 if defect == "parent_change" else 100,
                start=200 if defect == "pid_reuse" else 100,
            )
        )
    result = tree.sample("normal")
    assert result["errors"]
    assert result["cpu_seconds"] is None and result["rss_bytes"] is None


def test_resource_summary_keeps_counter_regression_and_normalizes_cpu():
    samples = [
        {
            "at": at,
            "phase": "normal",
            "cpu_seconds": cpu,
            "rss_bytes": rss,
            "errors": [],
        }
        for at, cpu, rss in [(1, 1, 10), (3, 2, 20), (4, 1, 30)]
    ]
    summary = summarize_resources(samples)
    assert summary["average_logical_core_equivalents"] == 0.5
    assert summary["rss_growth_bytes"] == 20
    assert summary["invalid_intervals"] == [
        {"at": 4, "reason": "counter_regression_or_clock"}
    ]
    assert parse_stat(_stat(100, 1), ticks=100, page_size=4096)["pid"] == 100


async def test_open_loop_keeps_errors_queue_rejections_and_cancellations(tmp_path):
    async def call(client, index):
        if index == 0:
            raise TimeoutError("synthetic timeout")
        await asyncio.sleep(1)

    path = tmp_path / "requests.jsonl"
    result = await run_phase(
        None,
        call,
        phase="stress",
        start=time.monotonic(),
        duration=0.05,
        rate=1000,
        operation=lambda index: "fixture",
        output=path,
        concurrency=1,
        queue_limit=1,
        drain_seconds=0.01,
    )
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == result["expected_count"] == 50
    assert {row["index"] for row in rows} == set(range(50))
    assert result["outcomes"]["error"] == 1
    assert result["outcomes"]["client_queue_rejected"] > 0
    assert result["outcomes"]["drain_cancelled"] == 1
    assert result["outcomes"]["drain_not_dispatched"] == 1
    assert result["attempt_latency_ms_including_errors"]["count"] == 2
    assert result["success_latency_ms"]["count"] == 0
    assert all(row["terminal_ms"] >= 0 for row in rows)


def test_phase_comparison_uses_both_baselines_and_never_adopts_budget():
    rows = []
    for name, latency, cpu, rss in [
        ("baseline_before", 2, 0.1, 10),
        ("normal", 4, 0.4, 25),
        ("baseline_after", 3, 0.2, 20),
    ]:
        rows.append(
            {
                "name": name,
                "core": {
                    "attempt_latency_ms_including_errors": {
                        "p95": latency,
                        "p99": latency + 1,
                    }
                },
                "resources": {
                    "average_logical_core_equivalents": cpu,
                    "rss_first_bytes": rss,
                    "rss_bytes": {"max": rss},
                },
            }
        )
    result = compare_phases(rows)["normal"]
    assert result["added_core_p95_ms"] == 1
    assert result["added_core_p99_ms"] == 1
    assert result["added_cpu_logical_core_equivalents"] == pytest.approx(0.2)
    assert result["added_peak_rss_bytes"] == 5
    assert result["acceptance_eligible"] is False


async def test_raw_retention_failure_cannot_produce_success(tmp_path, monkeypatch):
    def fail_write(*args):
        raise OSError("synthetic artifact storage failure")

    monkeypatch.setattr(diagnostic, "append_record", fail_write)

    async def call(client, index):
        return None

    with pytest.raises(AssertionError, match="evidence is incomplete"):
        await run_phase(
            None,
            call,
            phase="cold",
            start=time.monotonic(),
            duration=0.01,
            rate=100,
            operation=lambda index: "fixture",
            output=tmp_path / "absent.jsonl",
        )


def test_diagnostic_runner_paths_are_only_resolved_inside_steps():
    workflow = yaml.load(
        (
            Path(__file__).resolve().parents[2]
            / ".github/workflows/finance-development.yml"
        ).read_text(),
        Loader=yaml.BaseLoader,
    )
    job = workflow["jobs"]["resource-diagnostic"]
    assert "runner." not in json.dumps(job.get("env", {}))
    execution_steps = [
        step
        for step in job["steps"]
        if "ADMINME_FINANCE_RESOURCE_SMOKE" in step.get("env", {})
        or "ADMINME_FINANCE_RESOURCE_DIAGNOSTIC" in step.get("env", {})
    ]
    assert len(execution_steps) == 2
    assert all(
        step["env"]["FINANCE_RESOURCE_ARTIFACT_DIR"]
        == "${{ runner.temp }}/finance-resources"
        for step in execution_steps
    )


async def test_empty_read_error_retains_reset_cause_and_is_not_retried(tmp_path):
    calls = []

    async def call(client, index):
        calls.append(index)
        try:
            raise ConnectionResetError(54, "Connection reset by peer")
        except ConnectionResetError as cause:
            raise httpx.ReadError("") from cause

    path = tmp_path / "requests.jsonl"
    result = await run_phase(
        None,
        call,
        phase="normal",
        start=time.monotonic(),
        duration=0.01,
        rate=100,
        operation=lambda index: "status",
        output=path,
    )
    [row] = [json.loads(line) for line in path.read_text().splitlines()]
    assert calls == [0]
    assert result["outcomes"] == {"error": 1}
    assert row["error_type"] == "ReadError" and row["error"] == ""
    assert row["exception_chain"] == [
        {"type": "ReadError", "message": "", "errno": None},
        {
            "type": "ConnectionResetError",
            "message": "[Errno 54] Connection reset by peer",
            "errno": 54,
        },
    ]
    assert result["attempt_latency_ms_including_errors"]["count"] == 1
    assert result["success_latency_ms"]["count"] == 0
