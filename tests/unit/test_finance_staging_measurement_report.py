"""Bounded preflight for proposed measurement budgets and fail-closed receipts."""

from copy import deepcopy
import threading

import pytest

from tests.fixtures.finance_staging_measurement_report import (
    LIMITS,
    MIB,
    PHASE_CAPS,
    abort_pending,
    assess,
    close_completed_slots,
    deadline,
    slot_plan,
)
from tests.fixtures.finance_watchdog import (
    mode_budget,
    remove_scratch,
    start_cleanup_sampler,
)


def test_staged_limits_are_explicit_and_do_not_mutate_prior_modes():
    prior = mode_budget("full")
    staged = mode_budget("staged-bounded")
    assert (staged["work"], staged["cleanup"], staged["absolute"]) == (330, 30, 360)
    assert (staged["grace"], staged["term"], staged["kill"]) == (15, 5, 5)
    assert staged["limits"] == LIMITS
    assert (prior["work"], prior["cleanup"], prior["absolute"]) == (200, 30, 230)
    assert prior["limits"]["runtime_executor_cpu_seconds"] == 90
    assert prior["limits"]["whole_tree_cpu_seconds"] == 180
    assert mode_budget("full") == prior
    assert mode_budget("contract-hang")["absolute"] == 6
    assert mode_budget("staged-bounded-extra") == prior


def test_phase_deadline_cannot_borrow_unused_time_or_extend_absolute_work():
    assert deadline(10, 45, 0) == 55
    assert deadline(300, 60, 0) == 330
    assert deadline(329, 15, 0) == 330


@pytest.mark.parametrize("uncertain", [False, True])
def test_scratch_removal_requires_confirmed_empty_ownership(tmp_path, uncertain):
    scratch = tmp_path / "owned"
    scratch.mkdir()
    marker = scratch / "unpublished-evidence"
    marker.write_text("synthetic only")
    removed = remove_scratch(
        scratch,
        retain_unknown=True,
        remaining_owned=[{"pid": 123, "start_ticks": 456}] if uncertain else [],
    )
    assert removed is (not uncertain)
    assert marker.exists() is uncertain


@pytest.mark.parametrize("mode", ["sample", "failure", "legacy"])
def test_cleanup_sampler_stops_and_preserves_faults(mode):
    received = threading.Event()
    rows, faults = [], []

    def take_sample():
        if mode == "failure":
            received.set()
            raise OSError("synthetic")
        return {"synthetic_sample": True}

    def emit(row):
        rows.append(row)
        received.set()

    stop, thread = start_cleanup_sampler(
        "full" if mode == "legacy" else "staged-bounded", take_sample, emit, faults
    )
    try:
        if mode == "legacy":
            assert thread is None and not rows and not faults
        else:
            assert received.wait(1)
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert faults == (["cleanup_sampler:OSError"] if mode == "failure" else [])
    assert bool(rows) is (mode == "sample")


@pytest.mark.parametrize(
    "seconds,hz,count", [(5, 20, 100), (10, 20, 200), (10, 10, 100)]
)
def test_fixed_slot_windows_include_the_last_slot(seconds, hz, count):
    rows = slot_plan(
        "window", "core" if hz == 20 else "planning", hz, 100, 100 + seconds, seconds
    )
    assert len(rows) == count and len({r["id"] for r in rows}) == count
    assert rows[0]["scheduled"] == 100
    assert rows[-1]["scheduled"] == pytest.approx(100 + seconds - 1 / hz)
    assert rows[-1]["scheduled"] < 100 + seconds


def test_completed_operation_and_abort_preserve_distinct_request_evidence():
    rows = slot_plan("import", "core", 20, 0, 120)
    assert len(rows) == 2400
    rows[0].update(admission="admitted", error="inflight_limit")
    close_completed_slots(rows, 0.075)
    abort_pending(rows)
    assert rows[0]["error"] == "inflight_limit"
    assert rows[1]["admission"] == "not_started_after_abort"
    assert all(r["admission"] == "outside_completed_operation" for r in rows[2:])
    assert len(rows) == 2400


def fixture():
    projections = ["money", "parties", *[f"p{i}" for i in range(13)]]
    native = {
        "state": "read",
        "counts": {
            "finance_provenance_blobs": 99,
            "finance_publications": 1,
            "finance_dated_projections": 1,
            "money_flows": 0,
        },
        "events": {"count": 101},
        "money_checkpoint_verified": True,
        "money_lag": 0,
        "active_executor": False,
        "audit": [
            {
                "outcome": "accepted",
                "reason": "exact_approved_package",
                "provider_effects": False,
            }
        ],
        "projection_names": projections,
        "consumer_names": ["projection:" + n for n in projections],
        "lifetimes": [{"reaped": True, "exit_code": 0} for _ in range(2)],
        "stage_lifetimes": [{"reaped": True, "stage_removed": True}],
        "handoffs": [{"stage": "preparation_reaped_before_money", "events": 101}],
        "guards": ["before_transaction", "before_commit"],
    }
    clean = {
        "runtime_exit": 0,
        "forced": False,
        "remaining_owned": [],
        "scratch_removed": True,
    }
    comparisons = {
        "loaded_planning": {"overall_verdict": "passed"},
        "stored_query_cpu_within": True,
        "import_replay_diagnostic": {
            n: {"absolute_latency_within": True, "legacy_mean_cpu_within": True}
            for n in ("single_initial_import", "single_full_money_replay")
        },
    }
    alive = {"pid": 123, "start_ticks": 456, "state": "S"}
    windows = [
        (0, 0.5),
        (0.5, 6),
        (6, 16),
        (16, 16.5),
        (16.5, 17),
        (17, 17.5),
        (17.5, 27.5),
        (27.5, 37.5),
        (37.5, 47.5),
        (47.5, 48),
    ]
    phases = [
        {
            "name": name,
            "state": "completed",
            "started": start,
            "ended": end,
            "deadline": deadline(start, PHASE_CAPS[name], 0),
            "cap_seconds": PHASE_CAPS[name],
            "end_resources": {"runtime_executor_rss_bytes": 104 * MIB, "pids": [alive]},
        }
        for name, (start, end) in zip(PHASE_CAPS, windows)
    ]
    report = {
        "correctness": "passed",
        "faults": [],
        "unexecuted_phases": [],
        "final_durable_state": native,
        "cleanup": clean,
        "phases": phases,
        "measurement_comparisons": comparisons,
        "work_finished_at": 48.05,
        "warmup": {"started": 1, "ended": 6, "core_slots": 100},
    }
    watch = {
        "mode": "staged-bounded",
        "limits": dict(LIMITS),
        "work_cap_seconds": 330,
        "cleanup_cap_seconds": 30,
        "absolute_stop_seconds": 360,
        "controller_exit": 0,
        "forced": False,
        "faults": [],
        "remaining_owned": [],
        "scratch_removed": True,
        "absolute_bound_met": True,
        "started": 0,
        "finished": 49.05,
        "work_finished_at": 48.05,
        "cleanup_seconds": 1,
        "runtime_final_cpu_seconds": 5,
        "runtime_binding": alive,
    }
    samples = [
        {
            "at": i / 20,
            "owned_processes": 2,
            "runtime_executor_rss_bytes": (
                100 if i / 20 < 16 else 104 if i / 20 <= 48 else 0
            )
            * MIB,
            "runtime_executor_cpu_seconds": min(i / 200, 4.8),
            "whole_tree_rss_bytes": 200 * MIB,
            "whole_tree_cpu_seconds": i / 100,
            "scratch_bytes": MIB,
            "pids": [alive] if i / 20 <= 48 else [],
        }
        for i in range(982)
    ]
    watch["initial_sample"] = deepcopy(samples[0])
    watch["post_cleanup_sample"] = deepcopy(samples[-1])
    return report, watch, samples


@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "empty_consumers",
        "empty_lifetimes",
        "missing_samples",
        "unreaped",
        "late",
        "high_peak",
        "retained_growth",
        "unstable",
        "cpu_unreconciled",
        "baseline_only_trace",
        "missing_startup",
        "missing_workload",
        "missing_cleanup",
        "short_baseline",
        "phase_deadline_extension",
        "retained_runtime_exited",
    ],
)
def test_missing_failed_or_unstable_evidence_never_passes(mutation):
    report, watch, samples = deepcopy(fixture())
    expected = "failed"
    if mutation == "valid":
        expected = "passed"
    elif mutation == "empty_consumers":
        report["final_durable_state"]["consumer_names"] = []
    elif mutation == "empty_lifetimes":
        report["final_durable_state"]["lifetimes"] = []
    elif mutation == "missing_samples":
        samples = []
    elif mutation == "unreaped":
        watch["remaining_owned"] = [{"pid": 123, "start_ticks": 456}]
        watch["scratch_removed"] = False
    elif mutation == "late":
        watch["absolute_bound_met"] = False
    elif mutation == "high_peak":
        samples[325]["runtime_executor_rss_bytes"] = 240 * MIB
    elif mutation == "retained_growth":
        for row in samples:
            if 47 <= row["at"] <= 47.5:
                row["runtime_executor_rss_bytes"] = 110 * MIB
    elif mutation == "unstable":
        report["measurement_comparisons"]["loaded_planning"]["overall_verdict"] = (
            "inconclusive"
        )
        expected = "inconclusive"
    elif mutation == "cpu_unreconciled":
        watch["runtime_final_cpu_seconds"] = 1
        expected = "inconclusive"
    elif mutation == "baseline_only_trace":
        samples = [r for r in samples if 6 <= r["at"] <= 16]
        expected = "inconclusive"
    elif mutation == "missing_startup":
        samples = [r for r in samples if r["at"] >= 0.5]
        expected = "inconclusive"
    elif mutation == "missing_workload":
        samples = [r for r in samples if not 16 <= r["at"] <= 17.5]
        expected = "inconclusive"
    elif mutation == "missing_cleanup":
        samples = [r for r in samples if r["at"] <= 48]
        expected = "inconclusive"
    elif mutation == "short_baseline":
        report["phases"][2]["ended"] = 15.5
        expected = "inconclusive"
    elif mutation == "phase_deadline_extension":
        report["phases"][3]["deadline"] += 1
        expected = "inconclusive"
    elif mutation == "retained_runtime_exited":
        for row in samples:
            if 47.25 <= row["at"] <= 47.5:
                row["pids"] = []
        expected = "inconclusive"
    result = assess(report, watch, samples)
    assert result["measurement_verdict"] == expected
    assert (
        result["resource_qualified"] is False
        and result["production_qualified"] is False
    )
