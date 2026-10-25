"""Only two proven response observation clocks may vary between core windows."""

from copy import deepcopy
import hashlib
import json

import pytest

from tests.acceptance.test_finance_staging_measurement import comparisons
from tests.fixtures.finance_staging_measurement_report import core_response_fingerprint


def response():
    clock = "2026-01-01T00:00:00.000001+00:00"
    return {
        "surface": "reminders",
        "member_id": "synthetic-member",
        "state": "ready",
        "permission": {"read": True},
        "cockpit": {
            "version": "deterministic-reminders-cockpit-v2",
            "generated_at": clock,
            "items": [
                {"id": "synthetic-item", "amount": "1.00", "due_at": "2026-01-02"}
            ],
        },
        "freshness": {
            "contract": "surface-freshness-v1",
            "generated_at": clock,
            "state": "fresh",
            "latest_source_at": "2025-12-31T23:00:00+00:00",
            "checkpoint_event_id": "synthetic-event",
        },
        "extra": {"generated_at": "must-remain-semantic"},
    }


def encode(value):
    return json.dumps(value, separators=(",", ":")).encode()


def test_only_validated_observation_clocks_are_normalized():
    first = response()
    second = deepcopy(first)
    for path in ("cockpit", "freshness"):
        second[path]["generated_at"] = "2026-01-01T00:00:01.000002+00:00"
    a, b = (core_response_fingerprint(encode(v)) for v in (first, second))
    assert a["semantic_sha256"] == b["semantic_sha256"]
    assert a["body_sha256"] != b["body_sha256"]
    assert a["body_sha256"] == hashlib.sha256(encode(first)).hexdigest()
    assert b["observed_clocks"] == {
        path + ".generated_at": second[path]["generated_at"]
        for path in ("cockpit", "freshness")
    }


@pytest.mark.parametrize(
    "field",
    [
        "member",
        "permission",
        "state",
        "amount",
        "due",
        "source_clock",
        "freshness",
        "checkpoint",
        "other_generated_at",
    ],
)
def test_semantic_and_other_timestamp_changes_remain_visible(field):
    original, changed = response(), response()
    if field == "member":
        changed["member_id"] = "another-synthetic-member"
    elif field == "permission":
        changed["permission"]["read"] = False
    elif field == "state":
        changed["state"] = "degraded"
    elif field == "amount":
        changed["cockpit"]["items"][0]["amount"] = "2.00"
    elif field == "due":
        changed["cockpit"]["items"][0]["due_at"] = "2026-01-03"
    elif field == "source_clock":
        changed["freshness"]["latest_source_at"] = "2026-01-01T00:00:00+00:00"
    elif field == "freshness":
        changed["freshness"]["state"] = "stale"
    elif field == "checkpoint":
        changed["freshness"]["checkpoint_event_id"] = "another-event"
    else:
        changed["extra"]["generated_at"] = "changed"
    assert (
        core_response_fingerprint(encode(original))["semantic_sha256"]
        != core_response_fingerprint(encode(changed))["semantic_sha256"]
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "null",
        "number",
        "invalid",
        "naive",
        "non_utc",
        "mismatch",
        "version",
        "duplicate",
        "nonfinite",
    ],
)
def test_unvalidated_clocks_or_ambiguous_bodies_cannot_compare(fault):
    value = response()
    if fault == "missing":
        del value["freshness"]["generated_at"]
    elif fault in {"null", "number", "invalid", "naive", "non_utc"}:
        value["freshness"]["generated_at"] = {
            "null": None,
            "number": 1,
            "invalid": "no-clock",
            "naive": "2026-01-01T00:00:00",
            "non_utc": "2026-01-01T00:00:00+01:00",
        }[fault]
    elif fault == "mismatch":
        value["freshness"]["generated_at"] = "2026-01-01T00:00:02+00:00"
    elif fault == "version":
        value["cockpit"]["version"] = "unknown"
    raw = encode(value)
    if fault == "duplicate":
        raw = raw.replace(
            b'"surface":"reminders"', b'"surface":"reminders","surface":"reminders"'
        )
    elif fault == "nonfinite":
        raw = raw[:-1] + b',"unknown":NaN}'
    with pytest.raises(ValueError):
        core_response_fingerprint(raw)


def comparison_report():
    names = (
        "loaded_core_baseline_before",
        "loaded_planning_probe",
        "loaded_core_baseline_after",
    )
    phases, requests = [], []
    for index, name in enumerate(names):
        start = 1 + index * 10
        phases.append(
            {
                "name": name,
                "state": "completed",
                "started": start,
                "ended": start + 10,
                "start_resources": {"runtime_executor_cpu_seconds": index},
                "end_resources": {"runtime_executor_cpu_seconds": index + 1},
            }
        )
        for slot in range(200):
            value = response()
            clock = f"2026-01-01T00:00:00.{index * 200 + slot:06d}+00:00"
            for path in ("cockpit", "freshness"):
                value[path]["generated_at"] = clock
            requests.append(
                {
                    "phase": name,
                    "kind": "core",
                    "admission": "admitted",
                    "slot": slot,
                    "started": start + slot / 20,
                    "milliseconds": 5.0,
                    "status": 200,
                    **core_response_fingerprint(encode(value)),
                }
            )
    native = {
        "state": "read",
        "counts": {"finance_publications": 1},
        "events": {"count": 101},
        "heads": {"synthetic": "one"},
        "money_checkpoint": "synthetic-event",
        "audit": [{"provider_effects": False}],
        "authority": {"owner": "synthetic-member"},
    }
    return {
        "phases": phases,
        "requests": requests,
        **{
            name: deepcopy(native)
            for name in ("after_import", "after_replay", "final_durable_state")
        },
    }


@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "missing_semantic",
        "missing_clocks",
        "missing_raw",
        "multiple_hashes",
        "missing_request",
        "missing_window",
        "native_change",
        "baseline_drift",
    ],
)
def test_comparisons_require_complete_single_semantic_loaded_windows(fault):
    report = comparison_report()
    if fault in {"missing_semantic", "missing_clocks", "missing_raw"}:
        key = {
            "missing_semantic": "semantic_sha256",
            "missing_clocks": "observed_clocks",
            "missing_raw": "body_sha256",
        }[fault]
        del report["requests"][0][key]
    elif fault == "multiple_hashes":
        # Identical sets across all windows still represent unstable bodies.
        for row in report["requests"]:
            if row["slot"] == 0:
                row["semantic_sha256"] = "f" * 64
    elif fault == "missing_request":
        report["requests"].pop(0)
    elif fault == "missing_window":
        report["phases"].pop()
    elif fault == "native_change":
        report["final_durable_state"]["authority"]["owner"] = "changed"
    elif fault == "baseline_drift":
        for row in report["requests"]:
            if row["phase"] == "loaded_core_baseline_after":
                row["milliseconds"] = 9.0
    result = comparisons(report)
    loaded = result["loaded_planning"]
    assert loaded["overall_verdict"] == (
        "passed" if fault == "valid" else "inconclusive"
    )
    if fault != "missing_window":
        assert loaded["comparable"] is (fault in {"valid", "baseline_drift"})
        assert loaded["stable"] is (fault == "valid")
        assert set(loaded["against_each"]) == {"left", "right"}
    for name, summary in result["core_windows"].items():
        rows = [r for r in report["requests"] if r["phase"] == name]
        assert summary["scheduled"] == 200
        assert summary["recorded"] == len(rows)
        assert summary["body_hashes"] == sorted(
            {r["body_sha256"] for r in rows if "body_sha256" in r}
        )
        if fault == "valid":
            assert summary["recorded"] == summary["validated_core_responses"] == 200
            assert (
                len(summary["body_hashes"]) == 200
                and len(summary["semantic_hashes"]) == 1
            )
