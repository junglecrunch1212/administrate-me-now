"""Offline policy evidence fixtures; no process or native API execution."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import pytest

from scripts import finance_resource_acceptance as policy

IDENTITY = {"candidate": "a" * 40, "checkout": "b" * 40}


def evidence(*, workload="light", latency=6, outlier=None, cpu=0.2, growth=0, peak=0):
    summary = {
        "policy": policy.POLICY,
        "identity": IDENTITY,
        "environment": "synthetic-native-lab",
        "platform": "Darwin",
        "mode": "qualification",
        "smoke_only": False,
        "state": "completed",
        "errors": [],
        "workload": workload,
        "phases": list(policy.QUALIFICATION_PHASES),
    }
    raw: dict[str, Any] = {
        key: [] for key in ("core", "finance", "phases", "resources", "lifecycle")
    }
    members = [
        {"birth": target, "pid": index + 100, "target": target, "identity": IDENTITY}
        for index, target in enumerate(("runtime", "core", "finance"))
    ]
    for m in members:
        raw["lifecycle"].append({"kind": "registered", **m})
        raw["lifecycle"].append({"birth": m["birth"], "record": {"kind": "hello", **m}})
    clock, cpu_total, peak_total = 100.0, 0.0, 100 * policy.MIB
    last_rss = 100 * policy.MIB

    def metric(at, rss, peak_rss):
        return {
            "at": at,
            "cpu_seconds": cpu_total,
            "user_cpu_seconds": cpu_total,
            "system_cpu_seconds": 0.0,
            "rss_bytes": rss,
            "peak_rss_bytes": peak_rss,
            "mach_peak_rss_bytes": peak_rss,
        }

    for name, duration, core_rate, finance_rate in policy.QUALIFICATION_PHASES:
        loaded = name in {"normal", "burst", "stress"}
        start = clock + 0.25
        raw["phases"].append(
            {
                "name": name,
                "start": start,
                "duration": duration,
                "core_rate": core_rate,
                "finance_rate": finance_rate,
            }
        )
        for kind, rate in (("core", core_rate), ("finance", finance_rate)):
            count = math.ceil(duration * rate)
            for i in range(count):
                scheduled = start + i / rate
                delay = (latency if loaded else 2) if kind == "core" else 20
                if loaded and kind == "core" and outlier is not None and i % 50 == 0:
                    delay = outlier
                raw[kind].append(
                    {
                        "phase": name,
                        "index": i,
                        "scheduled_at": scheduled,
                        "started_at": scheduled,
                        "terminal_at": scheduled + delay / 1000,
                        "terminal_ms": delay + 0.001,
                        "latency_ms": delay,
                        "queue_ms": 0,
                        "outcome": "success",
                        "http_status": 200,
                        "operation": (
                            ("what_now", "commitments")
                            if kind == "core"
                            else (
                                ("planning_detail", "planning_summary")
                                if workload == "planning"
                                else ("calculate", "status")
                            )
                        )[i % 2],
                    }
                )
        rss = 100 * policy.MIB + (
            growth if name not in {"cold", "warmup", "baseline_before"} else 0
        )
        peak_total = max(peak_total, rss, 100 * policy.MIB + (peak if loaded else 0))
        for i in range(duration + 2):
            clock = start - 0.1 + i
            cpu_total += cpu if loaded else 0.01
            value = metric(clock, rss, peak_total)
            for m in members:
                raw["lifecycle"].append(
                    {
                        "birth": m["birth"],
                        "record": {"kind": "sample", "metrics": value},
                    }
                )
            raw["resources"].append(
                {
                    "at": clock,
                    "phase": name,
                    "errors": [],
                    "cpu_seconds": cpu_total,
                    "rss_bytes": rss,
                    "processes": [
                        {"birth": "runtime", "target": "runtime", "metrics": value}
                    ],
                }
            )
        last_rss = rss
    value = metric(clock + 0.1, last_rss, peak_total)
    for m in members:
        raw["lifecycle"].append(
            {
                "birth": m["birth"],
                "record": {
                    "kind": "terminal",
                    "birth": m["birth"],
                    "pid": m["pid"],
                    "metrics": value,
                    "faults": [],
                    "outcome": "returned",
                },
            }
        )
    summary["cleanup"] = {
        "all_registered_processes_stopped": True,
        "registered_coverage_complete": True,
        "errors": [],
        "members": [
            {
                "birth": m["birth"],
                "pid": m["pid"],
                "target": m["target"],
                "exitcode": 0,
                "samples": sum(
                    row.get("birth") == m["birth"]
                    and row.get("record", {}).get("kind") in {"sample", "terminal"}
                    for row in raw["lifecycle"]
                ),
            }
            for m in members
        ],
    }
    raw["lifecycle"].append({"kind": "cleanup", **summary["cleanup"]})
    return summary, raw


def evaluate(data):
    return policy.evaluate(*data, **IDENTITY, environment="synthetic-native-lab")


def test_versioned_budgets_and_complete_evidence():
    result = evaluate(evidence())
    assert result["state"] == "passed", result
    assert result["policy"] == "finance-resource-v2.2026-10-24"
    assert result["accepted"] and not result["authorizes_activation"]
    assert set(result["measurements"]) >= {
        "normal",
        "burst",
        "stress",
        "startup_sample",
    }
    assert result["limits"]["core_p95_ms"] == 25
    assert result["limits"]["added_core_p95_ms"] == 5
    assert result["limits"]["core_p99_ms"] == 75
    assert result["limits"]["added_core_p99_ms"] == 15


@pytest.mark.parametrize(
    "settings,error",
    [
        ({"latency": 26}, "core_p95_ms"),
        ({"latency": 8}, "added_core_p95_ms"),
        ({"outlier": 76}, "core_p99_ms"),
        ({"outlier": 18}, "added_core_p99_ms"),
        ({"cpu": 0.27}, "normal: added CPU"),
        ({"cpu": 1.02}, "stress: added CPU"),
        ({"peak": 65 * policy.MIB}, "added RSS"),
        ({"workload": "planning", "peak": 129 * policy.MIB}, "added RSS"),
        ({"growth": 9 * policy.MIB}, "retained RSS growth"),
    ],
)
def test_each_limit_fails_without_using_larger_after_memory_baseline(settings, error):
    result = evaluate(evidence(**settings))
    assert result["state"] == "failed", result
    assert any(error in reason for reason in result["errors"])
    assert not result["accepted"]


@pytest.mark.parametrize(
    "settings",
    [
        {"latency": 7, "outlier": 17},
        {"cpu": 0.26},
        {"peak": 64 * policy.MIB},
        {"workload": "planning", "peak": 128 * policy.MIB},
        {"growth": 8 * policy.MIB},
    ],
)
def test_inclusive_approved_boundaries(settings):
    result = evaluate(evidence(**settings))
    assert result["accepted"], result


@pytest.fixture(scope="module")
def valid():
    return evidence()


@pytest.mark.parametrize(
    "defect",
    [
        "smoke",
        "linux",
        "candidate",
        "checkout",
        "environment",
        "policy",
        "missing_request",
        "duplicate_request",
        "error",
        "missing_sample",
        "resource_gap",
        "missing_terminal",
        "exit",
        "phase_rate",
        "no_stress",
        "queue",
        "startup_peak",
        "retention",
        "lifecycle_count",
    ],
)
def test_incomplete_or_failed_evidence_never_passes(valid, defect):
    summary, raw = deepcopy(valid)
    if defect in {"candidate", "checkout"}:
        summary["identity"][defect] = "c" * 40
    elif defect == "smoke":
        summary["smoke_only"] = True
    elif defect == "linux":
        summary["platform"] = "Linux"
    elif defect in {"environment", "policy"}:
        summary[defect] = "old"
    elif defect == "missing_request":
        raw["core"].pop()
    elif defect == "duplicate_request":
        raw["core"][-1] = raw["core"][-2]
    elif defect == "error":
        raw["core"][-1]["outcome"] = "drain_cancelled"
    elif defect == "missing_sample":
        raw["resources"] = [r for r in raw["resources"] if r["phase"] != "normal"]
    elif defect == "resource_gap":
        raw["resources"][100]["errors"].append("sampling failed")
    elif defect == "missing_terminal":
        raw["lifecycle"] = [
            r for r in raw["lifecycle"] if r.get("record", {}).get("kind") != "terminal"
        ]
    elif defect == "exit":
        summary["cleanup"]["members"][0]["exitcode"] = -9
    elif defect == "phase_rate":
        raw["phases"][3]["core_rate"] = 1
    elif defect == "no_stress":
        summary["phases"] = summary["phases"][:-3]
    elif defect == "queue":
        raw["core"][100]["queue_ms"] = 999
    elif defect == "startup_peak":
        raw["lifecycle"][6]["record"]["metrics"]["peak_rss_bytes"] = None
    elif defect == "lifecycle_count":
        summary["cleanup"]["members"][0]["samples"] += 1
    elif defect == "retention":
        summary["errors"].append("retention failed")
    result = evaluate((summary, raw))
    assert not result["accepted"], result
    assert result["state"] in {"incomplete", "failed"}


def test_old_diagnostic_summary_is_not_retroactive_acceptance():
    result = evaluate(
        (
            {
                "contract": "synthetic-finance-sustained-resources-v1",
                "candidate_head": "c2fd0293afd576bfaa717132f8aacadea799bf90",
            },
            {},
        )
    )
    assert result["state"] == "incomplete" and not result["accepted"]
