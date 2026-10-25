"""Focused supervisor contracts; selected only beneath the owned watchdog."""

from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import time

import pytest

from tests.fixtures import finance_watchdog as watchdog
from tests.fixtures.finance_startup import startup_instruction
from tests.acceptance.test_finance_storage_isolation import LIMITS
from tests.acceptance.test_finance_storage_measurement import (
    PhasedRun,
    setup_runtime,
    cleanup_run,
    settled,
)


def frame(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return io.BytesIO(struct.pack("!I", len(raw)) + raw)


@pytest.mark.parametrize("value", [{"launch": 1}, {"stop": 1}])
def test_fixed_startup_instruction(value):
    assert startup_instruction(frame(value)) == next(iter(value))


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"launch": True},
        {"launch": 1, "path": "/foreign"},
        {"launch": 1, "stop": 1},
        {"test_key": "not-a-key"},
        {"database": "/foreign"},
        b'{"launch":1,"launch":1}',
        b"{",
    ],
)
def test_launch_gate_rejects_malformed_or_external_inputs(value):
    with pytest.raises(ValueError):
        startup_instruction(frame(value))


def test_oversized_launch_frame_is_rejected_before_body():
    with pytest.raises(ValueError, match="frame_limit"):
        startup_instruction(io.BytesIO(struct.pack("!I", 2049)))


def test_exact_containment_bounds_and_watchdog_accounting():
    assert watchdog.LIMITS == {
        k: v for k, v in LIMITS.items() if k not in {"work_seconds", "cleanup_seconds"}
    }
    row = {k: v for k, v in watchdog.LIMITS.items()}
    assert watchdog.violations(row) == []
    for key in row:
        assert watchdog.violations({**row, key: row[key] + 1}) == [key]
    root = int(os.environ["FINANCE_WATCHDOG_PID"])
    observed = watchdog.sample(root, None, os.getpid(), [])
    by_pid = {r["pid"]: r for r in observed["pids"]}
    assert root in by_pid and os.getpid() in by_pid
    assert observed["owned_processes"] == 2
    assert by_pid[root]["cpu_seconds"] > 0
    assert observed["whole_tree_rss_bytes"] == sum(
        r["rss_bytes"] for r in by_pid.values()
    )
    assert observed["whole_tree_cpu_seconds"] == sum(
        r["cpu_seconds"] + r["reaped_cpu_seconds"] for r in by_pid.values()
    )
    assert observed["runtime_executor_cpu_seconds"] == 0
    assert float(os.environ["FINANCE_WATCHDOG_STARTED"]) < time.monotonic()


async def test_phased_setup_under_watchdog(tmp_path):
    mode = os.environ["FINANCE_WATCHDOG_MODE"]
    boundary = {
        "setup-stop-before": "before_coordinates",
        "setup-stop-packets": "packets_ready",
        "setup-stop-released": "runtime_released",
    }.get(mode)
    run = PhasedRun(tmp_path / "phased", name="phased_setup")
    report = run.record
    report.update(
        faults=[], phases=[], imports=[], requests=[], evaluation="setup_only"
    )
    active = set()
    reached = False

    async def cancel(name):
        nonlocal reached
        if name == boundary:
            reached = True
            raise asyncio.CancelledError("planned phased startup cancellation")

    if boundary:
        run.setup_hook = cancel
    try:
        if boundary:
            with pytest.raises(asyncio.CancelledError, match="planned phased"):
                await setup_runtime(run, active)
            assert reached
        else:
            result = await setup_runtime(run, active)
            assert len(result[-2]) == 2 and settled(result[-1])
            assert len(result[-1]["consumers"]) == 34
            assert report["setup"]["finished"] <= run.started + 30
            marks = {r["stage"]: r["at"] for r in report["setup_stages"]}
            assert marks["fixture.ready_before_release"] < run.started + 15
            assert marks["runtime.released"] >= run.started + 15
            assert not report["imports"] and not report["requests"]
            del result
        report["state"] = "setup_contract_passed"
    finally:
        await cleanup_run(run, active)
    assert report["cleanup"]["runtime_exit"] == 0
    assert not report["cleanup"]["forced"] and not report["cleanup"]["remaining_owned"]
    assert report["cleanup"]["scratch_removed"]
    if run.runtime_released:
        assert settled(report["cleanup_shutdown"])
        assert not report["cleanup_shutdown"]["finance_events"]
        assert not report["cleanup_shutdown"]["lifetimes"]
    else:
        assert report["cleanup_shutdown"]["stopped"]
    assert report["maxima"]["owned_processes"] == 3


def test_request_manifest_preserves_all_slots_when_aborted():
    from tests.acceptance.test_finance_storage_measurement import (
        planned_requests,
        complete_request_evidence,
    )

    planned = planned_requests(123.0)
    assert sum(r["kind"] == "core" for r in planned) == 3060
    assert sum(r["kind"] == "planning" for r in planned) == 380
    assert len({r["id"] for r in planned}) == 3440
    first = {**planned[0], "started": 153.0, "status": 200, "milliseconds": 1.0}
    report = {"planned_requests": planned, "requests": [first]}
    complete_request_evidence(report)
    assert report["request_evidence"][0] == first
    assert len(report["request_evidence"]) == 3440
    assert all(
        r["error"] == "not_started_after_abort" for r in report["request_evidence"][1:]
    )
