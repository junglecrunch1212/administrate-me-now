"""Offline policy contracts, not runtime, native or historical qualification."""

from copy import deepcopy

import pytest

from scripts import finance_performance_policy as policy


def fixture(*, refusals=1):
    rows, schedule = [], []
    groups = [
        (name, "core", 200)
        for name in (
            "preimport_core_baseline",
            "loaded_core_baseline_before",
            "loaded_planning_probe",
            "loaded_core_baseline_after",
        )
    ] + [("loaded_planning_probe", "planning", 100)]
    for phase, kind, count in groups:
        for index in range(count):
            due = 100.0 + index
            slot = {"index": index, "due": due}
            schedule.append({"phase": phase, "request_kind": kind, **slot})
            row = {
                "phase": phase,
                "request_kind": kind,
                "slot": slot,
                "scheduled": due,
                "started": due + 0.001,
                "finished": due + 0.02,
                "milliseconds": 20,
                "validated": True,
                "status": 200,
                "request": len(rows) + 1,
            }
            if kind == "planning" and index < refusals:
                row.update(
                    started=None,
                    milliseconds=None,
                    validated=False,
                    error_type="inflight_limit",
                )
                del row["status"], row["request"]
            rows.append(row)
    for index in range(15):
        rows.append(
            {
                "phase": "runtime_startup",
                "request_kind": "control",
                "slot": None,
                "scheduled": 0,
                "started": 0,
                "finished": 1,
                "milliseconds": 1000,
                "validated": True,
                "status": 200,
                "request": 1000 + index,
            }
        )
    legacy = {
        "passed": False,
        "stable": True,
        "comparable": True,
        "absolute_latency": True,
        "added_against_each": {
            side: {"p95_ms": 9, "observed_p99_ms": 24, "cpu_cores": 0.15}
            for side in ("left", "right")
        },
        "memory": {
            "complete": True,
            "added_peak_bytes": 141 * policy.MIB,
            "retained_growth_bytes": 26 * policy.MIB,
        },
        "planning_p95_ms": 22,
        "all_requests_passed": False,
    }
    return legacy, rows, schedule


def evaluate(value, *, captured_policy=policy.POLICY):
    return policy.owner_verdict(*value, captured_policy=captured_policy)


def test_prospective_budget_preserves_failed_legacy_and_every_refusal():
    value = fixture()
    original = deepcopy(value)
    result = evaluate(value)
    assert result["passed"] and not result["qualification"]
    assert not result["authorizes_activation"]
    assert value == original
    assert result["legacy_verdict"]["passed"] is False
    assert result["requests"]["all_requests_succeeded"] is False
    assert result["requests"]["planning_refused"] == 1
    assert result["requests"]["planning_dispatched"] == 99
    assert (
        result["requests"]["scheduled_slots"]
        == result["requests"]["terminal_slots"]
        == 900
    )
    assert result["requests"]["refused_rows"][0]["error_type"] == "inflight_limit"
    assert "no new result" in result["requests"]["user_consequence"]


@pytest.mark.parametrize("captured", [None, "", policy.LEGACY_POLICY, "unknown"])
def test_missing_or_old_policy_cannot_be_relabelled(captured):
    assert not evaluate(fixture(), captured_policy=captured)["passed"]


@pytest.mark.parametrize(
    "field,limit",
    [
        ("added_peak_bytes", 160 * policy.MIB),
        ("retained_growth_bytes", 32 * policy.MIB),
    ],
)
def test_memory_boundaries(field, limit):
    value = fixture()
    value[0]["memory"][field] = limit
    assert evaluate(value)["passed"]
    value[0]["memory"][field] += 1
    assert not evaluate(value)["passed"]


@pytest.mark.parametrize(
    "field,limit", [("p95_ms", 12), ("observed_p99_ms", 30), ("cpu_cores", 1)]
)
def test_added_bounds_apply_against_both_baselines(field, limit):
    value = fixture()
    value[0]["added_against_each"]["left"][field] = limit
    assert evaluate(value)["passed"]
    value[0]["added_against_each"]["right"][field] = limit + 0.001
    assert not evaluate(value)["passed"]


@pytest.mark.parametrize(
    "defect",
    [
        "two_refusals",
        "missing",
        "duplicate",
        "extra",
        "wrong_due",
        "core_refusal",
        "http_error",
        "fake_refusal",
        "control_missing",
        "invalid_control",
        "nan",
        "readback",
        "stability",
        "absolute",
        "slow_planning",
    ],
)
def test_correctness_accounting_and_finite_regression_guards(defect):
    value = fixture(refusals=2 if defect == "two_refusals" else 1)
    legacy, rows, schedule = value
    if defect == "missing":
        rows.pop(0)
    elif defect == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif defect == "extra":
        schedule.append(
            {"phase": "extra", "request_kind": "core", "index": 0, "due": 1}
        )
    elif defect == "wrong_due":
        rows[0]["scheduled"] = 99
    elif defect == "core_refusal":
        rows[0].update(validated=False, error_type="inflight_limit", started=None)
    elif defect == "http_error":
        rows[0]["status"] = 500
    elif defect == "fake_refusal":
        rows[800]["status"] = 200
    elif defect == "control_missing":
        rows.pop()
    elif defect == "invalid_control":
        rows[-1]["validated"] = False
    elif defect == "nan":
        legacy["memory"]["retained_growth_bytes"] = float("nan")
    elif defect == "readback":
        legacy["comparable"] = False
    elif defect == "stability":
        legacy["stable"] = False
    elif defect == "absolute":
        legacy["absolute_latency"] = False
    elif defect == "slow_planning":
        legacy["planning_p95_ms"] = 500.001
    assert not evaluate(value)["passed"], defect
