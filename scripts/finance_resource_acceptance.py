"""Versioned offline native evidence gate. Does not grant execution or activation.

Inputs must come from one reviewed immutable native fixture artifact. This checks
its content and exact expected bindings; it does not attest artifact provenance.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from tests.fixtures.finance_native_resources import EvidenceError, Member
from tests.fixtures.finance_resource_diagnostic import quantiles
from scripts.finance_performance_policy import POLICY as V3_POLICY, LIMITS as V3_LIMITS

POLICY = "finance-resource-v2.2026-10-24"
MIB = 1024 * 1024
# Normal sustained use, short burst, and sustained stress are distinct workloads.
QUALIFICATION_PHASES = (
    ("cold", 1, 1, 1),
    ("warmup", 30, 2, 1),
    ("baseline_before", 60, 2, 0),
    ("normal", 900, 2, 0.2),
    ("baseline_after", 60, 2, 0),
    ("burst_baseline_before", 25, 20, 0),
    ("burst", 30, 20, 10),
    ("burst_baseline_after", 25, 20, 0),
    ("stress_baseline_before", 120, 20, 0),
    ("stress", 120, 20, 10),
    ("stress_baseline_after", 120, 20, 0),
)
LIMITS = {
    "core_p95_ms": 25,
    "added_core_p95_ms": 5,
    "core_p99_ms": 75,
    "added_core_p99_ms": 15,
    "normal_added_cpu_cores": 0.25,
    "burst_added_cpu_cores": 1,
    "stress_added_cpu_cores": 1,
    "light_added_rss_bytes": 64 * MIB,
    "planning_added_rss_bytes": 128 * MIB,
    "retained_growth_bytes": 8 * MIB,
}


def require(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def number(value):
    require(
        type(value) in {int, float} and math.isfinite(value) and value >= 0,
        "missing/invalid numeric evidence",
    )
    return value


def lifecycle(summary, rows, expected):
    """Replay ownership/counter checks; never inspect any process on this host."""
    members = {}
    cleanup_rows = [r for r in rows if r.get("kind") == "cleanup"]
    require(
        len(cleanup_rows) == 1
        and cleanup_rows[0] == {"kind": "cleanup", **summary["cleanup"]},
        "missing or inconsistent cleanup receipt",
    )
    exits = {item["birth"]: item for item in summary["cleanup"]["members"]}
    require(len(exits) == len(summary["cleanup"]["members"]), "duplicate exit birth")
    for row in rows:
        if row.get("kind") == "registered":
            birth = row["birth"]
            require(
                birth not in members and row["identity"] == expected,
                "duplicate birth or stale lifecycle identity",
            )
            require(birth in exits, "missing process exit")
            members[birth] = Member(
                birth,
                row["target"],
                row["pid"],
                expected,
                SimpleNamespace(exitcode=exits[birth]["exitcode"]),
                cast(Any, None),
                cast(Any, None),
                None,
            )
        elif "record" in row:
            require(row["birth"] in members, "unregistered lifecycle sample")
            members[row["birth"]].accept(row["record"])
        else:
            require(row.get("kind") == "cleanup", "unknown lifecycle evidence")
    require(set(members) == set(exits), "unaccounted lifecycle")
    require(
        sorted(m.target for m in members.values()) == ["core", "finance", "runtime"],
        "unexpected registered fixture topology",
    )
    require(
        all(
            m.verdict()["samples"] == exits[birth]["samples"]
            and m.pid == exits[birth]["pid"]
            and m.target == exits[birth]["target"]
            for birth, m in members.items()
        ),
        "retained lifecycle count/ownership mismatch",
    )
    require(
        all(m.verdict()["complete"] for m in members.values()),
        "incomplete self-report lifecycle",
    )
    require(
        summary["cleanup"]["all_registered_processes_stopped"] is True
        and summary["cleanup"]["registered_coverage_complete"] is True
        and not summary["cleanup"]["errors"],
        "incomplete owned cleanup",
    )
    return next(m for m in members.values() if m.target == "runtime")


def evaluate(summary, raw, *, candidate, checkout, environment, policy=POLICY):
    result = {
        "policy": policy,
        "identity": {"candidate": candidate, "checkout": checkout},
        "environment": environment,
        "state": "incomplete",
        "accepted": False,
        "limits": dict(V3_LIMITS if policy == V3_POLICY else LIMITS),
        "errors": [],
        "measurements": {},
        "authorizes_activation": False,
    }
    try:
        require(policy in {POLICY, V3_POLICY}, "unknown performance policy")
        _evaluate(summary, raw, result)
    except (EvidenceError, KeyError, TypeError, ValueError, IndexError) as exc:
        result["errors"].append(str(exc)[:200])
    return result


def _evaluate(summary, raw, result):
    expected = result["identity"]
    limits = result["limits"]
    require(
        all(
            isinstance(v, str)
            and len(v) == 40
            and all(c in "0123456789abcdef" for c in v)
            for v in expected.values()
        ),
        "exact expected SHA required",
    )
    require(
        bool(result["environment"]) and summary["environment"] == result["environment"],
        "native environment binding mismatch",
    )
    require(
        summary["policy"] == result["policy"] and summary["identity"] == expected,
        "stale policy/candidate/checkout",
    )
    require(
        summary["mode"] == "qualification"
        and not summary["smoke_only"]
        and summary["platform"] == "Darwin",
        "native full qualification required",
    )
    require(summary["state"] == "completed" and not summary["errors"], "incomplete run")
    require(summary["workload"] in {"light", "planning"}, "unknown workload")
    phases = [tuple(p) for p in summary["phases"]]
    require(
        phases == list(QUALIFICATION_PHASES),
        "phase plan mismatch: normal/burst/stress required",
    )
    require(
        [p["name"] for p in raw["phases"]] == [p[0] for p in phases],
        "missing/duplicate phase",
    )
    runtime = lifecycle(summary, raw["lifecycle"], expected)
    known_samples = {json.dumps(row, sort_keys=True) for row in runtime.samples}
    resources = raw["resources"]
    require(resources, "missing raw resources")
    for row in resources:
        # Startup rows before the first sample are retained, not used as zeros.
        if row["phase"] == "startup" and not row["processes"][0]["metrics"]:
            continue
        require(
            not row["errors"] and len(row["processes"]) == 1, "resource collection gap"
        )
        process = row["processes"][0]
        require(
            process["birth"] == runtime.birth
            and process["target"] == "runtime"
            and json.dumps(process["metrics"], sort_keys=True) in known_samples,
            "resource snapshot not bound to owned self sample",
        )
        metric = process["metrics"]
        require(
            row["cpu_seconds"] == metric["cpu_seconds"]
            and row["rss_bytes"] == metric["rss_bytes"],
            "aggregate counter mismatch",
        )
    grouped: dict[str, Any] = {}
    failures = []
    for name, duration, core_rate, finance_rate in phases:
        definition = next(p for p in raw["phases"] if p["name"] == name)
        require(
            (
                definition["duration"],
                definition["core_rate"],
                definition["finance_rate"],
            )
            == (duration, core_rate, finance_rate),
            "baseline/workload rate mismatch",
        )
        start = number(definition["start"])
        grouped[name] = {}
        for kind, rate in (("core", core_rate), ("finance", finance_rate)):
            rows = [r for r in raw[kind] if r["phase"] == name]
            count = math.ceil(duration * rate)
            require(
                len(rows) == count and {r["index"] for r in rows} == set(range(count)),
                f"{name}/{kind}: missing or duplicate scheduled request",
            )
            for row in rows:
                require(
                    math.isclose(
                        number(row["scheduled_at"]),
                        start + row["index"] / rate,
                        rel_tol=0,
                        abs_tol=0.000001,
                    ),
                    "scheduled request binding mismatch",
                )
                operations = (
                    ("what_now", "commitments")
                    if kind == "core"
                    else (
                        ("planning_detail", "planning_summary")
                        if summary["workload"] == "planning"
                        else ("calculate", "status")
                    )
                )
                require(
                    row["operation"] == operations[row["index"] % 2],
                    "operation coverage mismatch",
                )
                number(row["terminal_at"])
                if row["outcome"] != "success":
                    failures.append(name + "/" + kind + ": " + row["outcome"])
                    continue
                require(
                    row["http_status"] == 200, "successful receipt without HTTP success"
                )
                require(
                    row["terminal_at"]
                    >= number(row["started_at"])
                    >= row["scheduled_at"],
                    "invalid request lifecycle times",
                )
                require(
                    math.isclose(
                        number(row["queue_ms"]),
                        (row["started_at"] - row["scheduled_at"]) * 1000,
                        abs_tol=0.01,
                    ),
                    "queue timing mismatch",
                )
                number(row["latency_ms"])
                require(
                    number(row["terminal_ms"]) >= row["latency_ms"],
                    "terminal latency missing",
                )
            grouped[name][kind] = quantiles(
                [r["latency_ms"] for r in rows if r["outcome"] == "success"]
            )
            grouped[name][kind + "_rows"] = rows
        samples = sorted(
            [r for r in resources if r["phase"] == name], key=lambda r: r["at"]
        )
        require(
            len(samples) >= 2
            and samples[0]["at"] <= start
            and samples[-1]["at"] >= start + duration,
            "missing phase boundary coverage",
        )
        for before, after in zip(samples, samples[1:]):
            require(
                0 < after["at"] - before["at"] <= 2
                and after["cpu_seconds"] >= before["cpu_seconds"],
                "resource gap/regression",
            )
        grouped[name]["cpu"] = (
            samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"]
        ) / (samples[-1]["at"] - samples[0]["at"])
        grouped[name]["rss_first"] = samples[0]["rss_bytes"]
        grouped[name]["rss_last"] = samples[-1]["rss_bytes"]
        grouped[name]["rss_peak"] = max(r["rss_bytes"] for r in samples)
        grouped[name]["lifetime_peak"] = max(
            r["processes"][0]["metrics"]["peak_rss_bytes"] for r in samples
        )
    allowed_phases = {p[0] for p in phases}
    require(
        all(
            r["phase"] in allowed_phases
            for kind in ("core", "finance")
            for r in raw[kind]
        ),
        "extra request outside fixed plan",
    )
    for name, before, after in (
        ("normal", "baseline_before", "baseline_after"),
        ("burst", "burst_baseline_before", "burst_baseline_after"),
        ("stress", "stress_baseline_before", "stress_baseline_after"),
    ):
        measured = {}
        for q in ("p95", "p99"):
            actual = grouped[name]["core"][q]
            baseline = max(grouped[before]["core"][q], grouped[after]["core"][q])
            measured["core_" + q + "_ms"] = actual
            measured["added_core_" + q + "_ms"] = actual - baseline
        measured["added_cpu_cores"] = grouped[name]["cpu"] - max(
            grouped[before]["cpu"], grouped[after]["cpu"]
        )
        # Only the pre-load baseline is subtracted: the after-baseline may have
        # absorbed retained growth and must never erase its cost.
        measured["added_rss_bytes"] = (
            max(grouped[name]["rss_peak"], grouped[name]["lifetime_peak"])
            - grouped["baseline_before"]["rss_first"]
        )
        result["measurements"][name] = measured
        for key in (
            "core_p95_ms",
            "added_core_p95_ms",
            "core_p99_ms",
            "added_core_p99_ms",
        ):
            if measured[key] > limits[key]:
                failures.append(name + ": " + key)
        if measured["added_cpu_cores"] - limits[name + "_added_cpu_cores"] > 1e-9:
            failures.append(name + ": added CPU")
        if (
            measured["added_rss_bytes"]
            > limits[summary["workload"] + "_added_rss_bytes"]
        ):
            failures.append(name + ": added RSS")
        for operation in {r["operation"] for r in grouped[name]["finance_rows"]}:
            values = [
                r["latency_ms"]
                for r in grouped[name]["finance_rows"]
                if r["operation"] == operation and r["outcome"] == "success"
            ]
            if values and quantiles(values)["p95"] >= (
                100 if operation == "status" else 500
            ):
                failures.append(name + ": finance p95 " + operation)
    warm = grouped["baseline_before"]["rss_first"]
    retained = (
        max(
            grouped[name]["rss_last"]
            for name in (
                "normal",
                "baseline_after",
                "burst",
                "burst_baseline_after",
                "stress",
                "stress_baseline_after",
            )
        )
        - warm
    )
    result["measurements"]["retained_growth_bytes"] = retained
    result["measurements"]["startup_sample"] = runtime.samples[0]
    result["measurements"]["lifetime_peak_bytes"] = max(
        r["peak_rss_bytes"] for r in runtime.samples
    )
    result["measurements"]["tail_counts"] = {
        name: {kind: grouped[name][kind]["count"] for kind in ("core", "finance")}
        for name in allowed_phases
    }
    if retained > limits["retained_growth_bytes"]:
        failures.append("retained RSS growth")
    result.update(
        state="failed" if failures else "passed", accepted=not failures, errors=failures
    )


def load_evidence(directory):
    return json.loads((directory / "native-summary.json").read_text()), {
        key: [
            json.loads(line) for line in (directory / filename).read_text().splitlines()
        ]
        for key, filename in {
            "core": "core-requests.jsonl",
            "finance": "finance-requests.jsonl",
            "resources": "resources.jsonl",
            "phases": "phases.jsonl",
            "lifecycle": "lifecycle.jsonl",
        }.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--policy", choices=(POLICY, V3_POLICY), default=POLICY)
    for name in ("candidate", "checkout", "environment"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        summary, raw = load_evidence(args.directory)
        result = evaluate(
            summary,
            raw,
            candidate=args.candidate,
            checkout=args.checkout,
            environment=args.environment,
            policy=args.policy,
        )
    except (OSError, ValueError) as exc:
        result = {
            "policy": args.policy,
            "accepted": False,
            "state": "incomplete",
            "errors": [str(exc)],
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
