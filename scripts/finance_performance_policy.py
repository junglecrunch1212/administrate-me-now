"""Prospective Finance v3 budgets; never rewrite an earlier evidence verdict."""

from __future__ import annotations

from collections import Counter
import math
from typing import Any

POLICY = "finance-resource-v3.2026-10-26"
LEGACY_POLICY = "finance-resource-v2.2026-10-24"
MIB = 1024**2
LIMITS = {
    "core_p95_ms": 25,
    "added_core_p95_ms": 12,
    "core_p99_ms": 75,
    "added_core_p99_ms": 30,
    "normal_added_cpu_cores": 0.25,
    "burst_added_cpu_cores": 1,
    "stress_added_cpu_cores": 1,
    "light_added_rss_bytes": 64 * MIB,
    "planning_added_rss_bytes": 160 * MIB,
    "retained_growth_bytes": 32 * MIB,
}
BASELINE = {
    "source": "71972213dd828b4a11f9be76d52647cd89028049",
    "diagnostic": "82fcf9aab2fa11fcc0b5198eea15f7722df66693",
    "tree": "94f6513132ef292e4151e401072493cac68ace68",
    "run": 40292362979,
    "job": 121470180704,
    "attempt": 1,
    "artifact": 12637160248,
    "artifact_sha256": "eb4f252f2968cb79a47d49dad543e3abed588502b2ac67eea2afcf95000b328f",
    "original_verdict": "failed; remains failed under the original policy",
    "workload": "8022 units / 98 blobs; owner import, full Money replay, 20 core and 10 planning RPS",
    "added_core_p95_ms": 8.959314999998469,
    "added_core_p99_ms": 23.602958999987322,
    "added_peak_bytes": 147632128,
    "retained_growth_bytes": 26275840,
    "planning_dispatched_valid": 99,
    "planning_scheduled": 100,
    "planning_inflight_refusals": 1,
    "native_mini_qualification": False,
}
PLANNING_CONSEQUENCE = (
    "One of 100 scheduled planning refreshes may be undispatched under this "
    "load. It has no new result and is never counted as a successful response. "
    "This collector refusal does not prove a production UI busy indication. "
    "Dispatched HTTP errors, incorrect data and any core refusal still fail."
)


def _finite(value: Any) -> bool:
    return type(value) in (float, int) and math.isfinite(value)


def owner_requests(rows: list[dict], schedule: list[dict]) -> dict[str, Any]:
    """Keep every slot; permit only one explicit undispatched planning refusal."""
    errors = []
    scheduled: Counter[tuple[str, str, int]] = Counter()
    terminal: Counter[tuple[str, str, int]] = Counter()
    due = {}
    refused = []
    requests = []
    controls = 0
    planning_valid = 0
    try:
        for row in schedule:
            key = (row["phase"], row["request_kind"], row["index"])
            if row["request_kind"] not in {"core", "planning"} or not _finite(
                row["due"]
            ):
                raise ValueError("invalid schedule")
            scheduled[key] += 1
            due[key] = row["due"]
        for row in rows:
            kind = row["request_kind"]
            if kind == "control":
                controls += 1
                if row.get("slot") is not None:
                    errors.append("control has workload slot")
            else:
                slot = row["slot"]
                key = (row["phase"], kind, slot["index"])
                terminal[key] += 1
                if (
                    key not in due
                    or slot["due"] != due[key]
                    or row["scheduled"] != due[key]
                ):
                    errors.append("slot identity or due mismatch")
            allowed_refusal = (
                kind == "planning"
                and row["phase"] == "loaded_planning_probe"
                and row.get("error_type") == "inflight_limit"
                and row.get("validated") is False
                and row.get("started") is None
                and row.get("milliseconds") is None
                and "status" not in row
                and "request" not in row
                and _finite(row.get("finished"))
                and row["finished"] >= row["scheduled"]
            )
            if allowed_refusal:
                refused.append(row)
                continue
            if (
                row.get("validated") is not True
                or row.get("status") != 200
                or "error_type" in row
                or not all(
                    _finite(row.get(k)) for k in ("started", "finished", "milliseconds")
                )
                or not row["finished"] >= row["started"] >= row["scheduled"]
                or row["milliseconds"] < 0
            ):
                errors.append("dispatched request failed or evidence invalid")
            requests.append(row["request"])
            planning_valid += kind == "planning"
        if scheduled != terminal or any(n != 1 for n in scheduled.values()):
            errors.append("missing, duplicate or extra terminal slot")
        if controls != 15 or len(requests) != len(set(requests)):
            errors.append("control count or dispatched identity mismatch")
        for phase in (
            "preimport_core_baseline",
            "loaded_core_baseline_before",
            "loaded_planning_probe",
            "loaded_core_baseline_after",
        ):
            if (
                sum(
                    n
                    for (p, k, _), n in scheduled.items()
                    if p == phase and k == "core"
                )
                != 200
            ):
                errors.append("fixed core slot count")
        planning_count = sum(n for (_, k, _), n in scheduled.items() if k == "planning")
        if (
            planning_count != 100
            or planning_valid + len(refused) != 100
            or len(refused) > 1
        ):
            errors.append("planning availability budget")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append("incomplete request evidence: " + type(exc).__name__)
    return {
        "passed": not errors,
        "errors": errors,
        "all_requests_succeeded": not errors and not refused,
        "scheduled_slots": sum(scheduled.values()),
        "terminal_slots": sum(terminal.values()),
        "planning_dispatched": planning_valid,
        "planning_refused": len(refused),
        "refused_rows": refused,
        "maximum_planning_refusals_per_100_slots": 1,
        "user_consequence": PLANNING_CONSEQUENCE,
    }


def owner_verdict(
    legacy: dict, rows: list[dict], schedule: list[dict], *, captured_policy: str
) -> dict:
    """Evaluate only a fresh run whose fixture captured v3 before work started."""
    requests = owner_requests(rows, schedule)
    checks = {
        "prospective_policy_binding": captured_policy == POLICY,
        "stable_baselines": legacy.get("stable") is True,
        "same_authorized_readback": legacy.get("comparable") is True,
        "absolute_core_latency": legacy.get("absolute_latency") is True,
        "request_accounting_and_availability": requests["passed"],
    }
    against = legacy.get("added_against_each", {})
    checks["added_latency_cpu"] = set(against) == {"left", "right"} and all(
        _finite(delta.get(field)) and delta[field] <= limit
        for delta in against.values()
        for field, limit in (
            ("p95_ms", LIMITS["added_core_p95_ms"]),
            ("observed_p99_ms", LIMITS["added_core_p99_ms"]),
            ("cpu_cores", LIMITS["burst_added_cpu_cores"]),
        )
    )
    memory = legacy.get("memory", {})
    checks["memory"] = memory.get("complete") is True and all(
        _finite(memory.get(field)) and memory[field] <= limit
        for field, limit in (
            ("added_peak_bytes", LIMITS["planning_added_rss_bytes"]),
            ("retained_growth_bytes", LIMITS["retained_growth_bytes"]),
        )
    )
    checks["planning_p95"] = (
        _finite(legacy.get("planning_p95_ms")) and 0 <= legacy["planning_p95_ms"] <= 500
    )
    return {
        "policy": POLICY,
        "passed": all(checks.values()),
        "qualification": False,
        "scope": "one owner import/full replay; native Mini and release acceptance remain separate",
        "limits": LIMITS,
        "checks": checks,
        "requests": requests,
        "baseline_reference": BASELINE,
        "legacy_policy": LEGACY_POLICY,
        "legacy_verdict": legacy,
        "final_cleanup_required": True,
        "authorizes_activation": False,
    }
