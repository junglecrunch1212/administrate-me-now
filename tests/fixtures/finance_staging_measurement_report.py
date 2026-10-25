"""Pure bounded schedule/report helpers; no runtime, provider or key imports."""

from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
import sys

MIB = 1024 * 1024
LIMITS = {
    "owned_processes": 4,
    "runtime_executor_rss_bytes": 512 * MIB,
    "runtime_executor_cpu_seconds": 180,
    "whole_tree_rss_bytes": 1024 * MIB,
    "whole_tree_cpu_seconds": 240,
    "scratch_bytes": 512 * MIB,
}
PHASE_CAPS = {
    "synthetic_fixture_build": 45,
    "runtime_startup": 30,
    "preimport_core_baseline": 10,
    "single_initial_import": 120,
    "settle_and_native_readback": 15,
    "single_full_money_replay": 60,
    "loaded_core_baseline_before": 10,
    "loaded_planning_probe": 10,
    "loaded_core_baseline_after": 10,
    "shutdown_receipt": 15,
}
FIXED_PHASES = {
    "preimport_core_baseline",
    "loaded_core_baseline_before",
    "loaded_planning_probe",
    "loaded_core_baseline_after",
}


def deadline(start, cap, work_start):
    return min(start + cap, work_start + 330)


def slot_plan(name, kind, hz, start, end, seconds=None):
    count = (
        round(seconds * hz) if seconds is not None else math.ceil((end - start) * hz)
    )
    if count < 0 or count > 2400 or kind not in {"core", "planning"}:
        raise ValueError("synthetic_slot_budget")
    return [
        {
            "id": f"{name}:{kind}:{slot}",
            "phase": name,
            "kind": kind,
            "slot": slot,
            "scheduled": start + slot / hz,
            "started": None,
            "milliseconds": None,
            "admission": "pending",
        }
        for slot in range(count)
    ]


def close_completed_slots(rows, at):
    for row in rows:
        if row["admission"] == "pending" and row["scheduled"] >= at:
            row["admission"] = "outside_completed_operation"


def abort_pending(rows):
    for row in rows:
        if row["admission"] == "pending":
            row.update(
                admission="not_started_after_abort", error="not_started_after_abort"
            )


def trace_coverage(report, watch, samples):
    reasons, observed = [], {}
    try:
        started, finished, work_end = (
            watch["started"],
            watch["finished"],
            watch["work_finished_at"],
        )
        assert all(math.isfinite(v) for v in (started, finished, work_end))
        assert started <= work_end <= started + 330 and work_end <= finished <= min(
            started + 360, work_end + 30
        )
        assert report["work_finished_at"] == work_end
        rows = report["phases"]
        assert [r["name"] for r in rows] == list(PHASE_CAPS)
        if not samples or not (
            0 <= samples[0]["at"] - started <= 0.2
            and 0 <= finished - samples[-1]["at"] <= 0.2
        ):
            reasons.append("startup_or_finish_not_covered")
        if any(
            not math.isfinite(r["at"]) or not started <= r["at"] <= finished
            for r in samples
        ):
            reasons.append("sample_outside_owned_lifetime")
        if (
            watch.get("initial_sample") not in samples
            or watch.get("post_cleanup_sample") not in samples
        ):
            reasons.append("initial_or_post_cleanup_receipt_not_in_trace")
        previous = started
        for row in rows:
            name = row["name"]
            begin, end = row["started"], row["ended"]
            shape = (
                row["state"] == "completed"
                and math.isfinite(begin)
                and math.isfinite(end)
                and previous <= begin <= end <= work_end
                and row["cap_seconds"] == PHASE_CAPS[name]
                and math.isclose(
                    row["deadline"],
                    deadline(begin, PHASE_CAPS[name], started),
                    abs_tol=1e-6,
                )
                and end <= row["deadline"]
            )
            duration = end - begin
            if name in FIXED_PHASES:
                shape = shape and math.isclose(duration, 10, rel_tol=0, abs_tol=1e-6)
            inside = [s for s in samples if begin <= s["at"] <= end]
            covered = bool(
                inside
                and inside[0]["at"] - begin <= 0.2
                and end - inside[-1]["at"] <= 0.2
            )
            observed[name] = {
                "duration_seconds": duration,
                "boundaries_valid": shape,
                "trace_samples": len(inside),
                "covered": covered,
            }
            if not shape or not covered:
                reasons.append("phase_coverage:" + name)
            previous = end
        warm = report["warmup"]
        startup = next(r for r in rows if r["name"] == "runtime_startup")
        if not (
            startup["started"] <= warm["started"] < warm["ended"] <= startup["ended"]
            and math.isclose(
                warm["ended"] - warm["started"], 5, rel_tol=0, abs_tol=1e-6
            )
            and warm["core_slots"] == 100
        ):
            reasons.append("warmup_boundary")
        cleanup = [s for s in samples if work_end <= s["at"] <= finished]
        if (
            not cleanup
            or cleanup[0]["at"] - work_end > 0.2
            or finished - cleanup[-1]["at"] > 0.2
        ):
            reasons.append("cleanup_not_covered")
    except (AssertionError, KeyError, TypeError, ValueError, StopIteration):
        reasons.append("malformed_lifetime_or_phase_boundaries")
    return {"valid": not reasons, "reasons": reasons, "phases": observed}


def assess(report, watch, samples):
    """Missing, failed and unstable evidence never becomes a successful trial."""
    faults = []
    expected_counts = {
        "finance_provenance_blobs": 99,
        "finance_publications": 1,
        "finance_dated_projections": 1,
        "money_flows": 0,
    }
    native = report.get("final_durable_state", {})
    correctness = (
        report.get("correctness") == "passed"
        and not report.get("faults")
        and not report.get("cleanup_failure")
        and not report.get("unexecuted_phases")
        and native.get("state") == "read"
        and native.get("counts") == expected_counts
        and native.get("events", {}).get("count") == 101
        and native.get("money_checkpoint_verified") is True
        and native.get("money_lag") == 0
        and native.get("active_executor") is False
        and len(native.get("projection_names", [])) == 15
        and {"money", "parties"} <= set(native.get("projection_names", []))
        and {"projection:" + n for n in native.get("projection_names", [])}
        <= set(native.get("consumer_names", []))
        and len(native.get("lifetimes", [])) >= 2
        and all(
            r.get("reaped") is True and r.get("exit_code") == 0
            for r in native.get("lifetimes", [])
        )
        and len(native.get("stage_lifetimes", [])) == 1
        and all(
            r.get("reaped") is True and r.get("stage_removed") is True
            for r in native.get("stage_lifetimes", [])
        )
        and native.get("handoffs")
        == [{"stage": "preparation_reaped_before_money", "events": 101}]
        and native.get("guards") == ["before_transaction", "before_commit"]
        and native.get("audit")
        == [
            {
                "outcome": "accepted",
                "reason": "exact_approved_package",
                "provider_effects": False,
            }
        ]
    )
    containment = (
        watch.get("mode") == "staged-bounded"
        and watch.get("limits") == LIMITS
        and watch.get("work_cap_seconds") == 330
        and watch.get("cleanup_cap_seconds") == 30
        and watch.get("absolute_stop_seconds") == 360
        and watch.get("controller_exit") == 0
        and watch.get("forced") is False
        and not watch.get("faults")
        and watch.get("remaining_owned") == []
        and watch.get("scratch_removed") is True
        and watch.get("absolute_bound_met") is True
        and watch.get("work_finished_at") is not None
        and watch.get("cleanup_seconds") is not None
        and 0 <= watch["cleanup_seconds"] < 30
        and report.get("cleanup", {}).get("runtime_exit") == 0
        and report.get("cleanup", {}).get("forced") is False
        and report.get("cleanup", {}).get("remaining_owned") == []
        and report.get("cleanup", {}).get("scratch_removed") is True
        and not report.get("violations")
        and not report.get("unsettled_owned_calls")
    )
    if not correctness:
        faults.append("workload_or_durable_proof")
    if not containment:
        faults.append("containment_or_cleanup")
    samples = sorted(samples, key=lambda r: r["at"])
    coverage = trace_coverage(report, watch, samples)
    if not samples:
        faults.append("missing_continuous_samples")
    gaps = [b["at"] - a["at"] for a, b in zip(samples, samples[1:])]
    maxima = {k: max((r[k] for r in samples), default=0) for k in LIMITS}
    violations = [k for k in LIMITS if maxima[k] > LIMITS[k]]
    if violations:
        faults.append("sample_containment")
    phases = {
        r["name"]: r for r in report.get("phases", []) if r.get("state") == "completed"
    }
    binding = watch.get("runtime_binding", {})

    def runtime_alive(row):
        return bool(
            binding
            and any(
                p.get("pid") == binding.get("pid")
                and p.get("start_ticks") == binding.get("start_ticks")
                and p.get("state") != "Z"
                for p in row.get("pids", [])
            )
        )

    base = phases.get("preimport_core_baseline")
    baseline = [
        r
        for r in samples
        if base and base["started"] <= r["at"] <= base["ended"] and runtime_alive(r)
    ]
    baseline_rss = (
        statistics.mean(r["runtime_executor_rss_bytes"] for r in baseline)
        if baseline
        and coverage["phases"]
        .get("preimport_core_baseline", {})
        .get("boundaries_valid")
        else None
    )
    sample_quality = bool(
        samples
        and len(baseline) >= 160
        and gaps
        and max(gaps) <= 0.2
        and watch.get("post_cleanup_sample") is not None
        and coverage["valid"]
    )
    peak_added = (
        maxima["runtime_executor_rss_bytes"] - baseline_rss
        if baseline_rss is not None
        else None
    )
    retained = {}
    retained_samples = {}
    for phase in (
        "settle_and_native_readback",
        "single_full_money_replay",
        "loaded_core_baseline_after",
    ):
        row = phases.get(phase)
        boundary = [
            s
            for s in samples
            if row
            and max(row["started"], row["ended"] - 0.2) <= s["at"] <= row["ended"]
            and runtime_alive(s)
        ]
        sample = boundary[-1] if boundary else None
        retained_samples[phase] = sample["at"] if sample else None
        retained[phase] = (
            sample["runtime_executor_rss_bytes"] - baseline_rss
            if sample and baseline_rss is not None
            else None
        )
    rss = {
        "baseline_mean_bytes": baseline_rss,
        "baseline_samples": len(baseline),
        "absolute_peak_bytes": maxima["runtime_executor_rss_bytes"],
        "added_peak_bytes": peak_added,
        "added_peak_limit_bytes": 128 * MIB,
        "retained_growth_bytes": retained,
        "retained_limit_bytes": 8 * MIB,
        "retained_sample_times": retained_samples,
        "startup_overlap_cleanup_included": coverage["valid"],
        "continuous_trace_coverage": coverage,
        "sample_resolution_ms": 50,
        "maximum_observed_gap_seconds": max(gaps) if gaps else None,
        "sampling_quality_valid": sample_quality,
    }
    memory_failed = (
        peak_added is not None
        and peak_added > 128 * MIB
        or any(v is not None and v > 8 * MIB for v in retained.values())
    )
    memory_complete = (
        sample_quality
        and peak_added is not None
        and all(v is not None for v in retained.values())
    )
    rss["verdict"] = (
        "failed" if memory_failed else "passed" if memory_complete else "inconclusive"
    )
    comparison = report.get("measurement_comparisons", {})
    loaded = comparison.get("loaded_planning", {}).get(
        "overall_verdict", "inconclusive"
    )
    diagnostic = comparison.get("import_replay_diagnostic", {})
    absolute_cpu = all(
        diagnostic.get(n, {}).get("absolute_latency_within") is True
        and diagnostic.get(n, {}).get("legacy_mean_cpu_within") is True
        for n in ("single_initial_import", "single_full_money_replay")
    )
    stored_cpu = comparison.get("stored_query_cpu_within") is True
    final_cpu = watch.get("runtime_final_cpu_seconds")
    cpu_reconciled = (
        isinstance(final_cpu, (int, float))
        and final_cpu + 0.05 >= maxima["runtime_executor_cpu_seconds"]
    )
    cpu = {
        "sampled_runtime_executor_seconds": maxima["runtime_executor_cpu_seconds"],
        "sampled_whole_tree_seconds": maxima["whole_tree_cpu_seconds"],
        "final_wait_runtime_seconds": final_cpu,
        "quantization_allowance_seconds": 0.05,
        "final_wait_covers_sampled_runtime": cpu_reconciled,
        "normal_native_qualification": "not_measured",
    }
    verdicts = [
        rss["verdict"],
        loaded,
        "passed" if absolute_cpu and stored_cpu else "failed",
        "passed" if cpu_reconciled else "inconclusive",
    ]
    verdict = (
        "failed"
        if faults or "failed" in verdicts
        else "inconclusive"
        if "inconclusive" in verdicts
        else "passed"
    )
    return {
        "measurement_verdict": verdict,
        "workload_correct": correctness,
        "contained_and_clean": containment,
        "faults": faults,
        "sample_violations": violations,
        "sample_count": len(samples),
        "maxima": maxima,
        "memory": rss,
        "cpu": cpu,
        "loaded_planning_verdict": loaded,
        "changing_state_added_latency": "diagnostic_only",
        "comparison_details": comparison,
        "resource_qualified": False,
        "production_qualified": False,
        "native_qualified": False,
        "repeated_import_retention": "not_measured",
        "new_live_authority": False,
    }


def main(directory):
    root = Path(directory)
    report = (
        json.loads((root / "staged_bounded.json").read_text())
        if (root / "staged_bounded.json").is_file()
        else {}
    )
    watch = (
        json.loads((root / "watchdog.json").read_text())
        if (root / "watchdog.json").is_file()
        else {}
    )
    samples = []
    trace_error = None
    trace = root / "watchdog-trace.jsonl"
    if trace.is_file():
        with trace.open() as stream:
            for line in stream:
                if len(samples) >= 10000:
                    trace_error = "synthetic_sample_limit"
                    break
                try:
                    samples.append(json.loads(line))
                except (ValueError, TypeError):
                    trace_error = "synthetic_sample_parse_error"
                    break
    result = assess(report, watch, samples)
    if trace_error is not None:
        result["faults"].append(trace_error)
        result["measurement_verdict"] = "failed"
    result["candidate"] = watch.get("candidate")
    (root / "measurement-verdict.json").write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["measurement_verdict"] == "passed" else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("one retained synthetic artifact directory required")
    raise SystemExit(main(sys.argv[1]))
