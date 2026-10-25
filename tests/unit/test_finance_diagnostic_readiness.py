"""Only the new first-inspect gate; existing observer proofs are reused."""

import hashlib
import json

import pytest

from tests.fixtures.finance_diagnostic_readiness import startup_evidence


def rows():
    values = [
        {"kind": "runtime.ready"},
        {"kind": "loop.delay"},
        {"kind": "handler.start", "route": "core"},
        {"kind": "handler.end", "route": "core"},
        *[
            {"kind": "native.queue", "executor": name}
            for name in ("event_log", "event_metadata", "money_read")
        ],
        {"kind": "control.start", "operation": "inspect"},
        *[
            {
                "kind": kind,
                "span": name,
                "rss_bytes": 1024,
                "process_cpu_seconds": 1,
                "sampled_at": 10,
            }
            for name in ("metadata_snapshot", "event_digest", "heads")
            for kind in ("readback.start", "readback.end")
        ],
        {"kind": "control.end", "operation": "inspect"},
    ]
    return [dict(row, n=index + 1, at=10) for index, row in enumerate(values)]


@pytest.mark.parametrize(
    "fault",
    [None, "ready", "tick", "handler", "executor", "readback", "resource", "boundary"],
)
def test_first_inspect_requires_actual_observer_evidence(tmp_path, fault):
    values = rows()
    if fault == "ready":
        values[0]["kind"] = "unrelated"
    elif fault == "tick":
        values[1]["at"] = 9
    elif fault == "handler":
        values[3]["route"] = "setup"
    elif fault == "executor":
        values[4]["executor"] = "unrelated"
    elif fault == "readback":
        values[-2]["kind"] = "unrelated"
    elif fault == "resource":
        values[-2]["rss_bytes"] = 0
    elif fault == "boundary":
        values[-1]["operation"] = "unrelated"
    path = tmp_path / "trace.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in values))
    result = startup_evidence(path, 10.1)
    assert result["complete"] is (fault is None)
    assert bool(result["faults"]) is (fault is not None)
    assert result["rows"] == len(values)


@pytest.mark.parametrize("tail", ["future_tick", "partial_row"])
def test_gate_uses_only_completed_first_inspect_prefix(tmp_path, tail):
    values = rows()
    prefix = "".join(json.dumps(row) + "\n" for row in values).encode()
    suffix = (
        json.dumps({"n": len(values) + 1, "at": 11, "kind": "loop.delay"}).encode()
        + b"\n"
        if tail == "future_tick"
        else b'{"n":'
    )
    path = tmp_path / "trace.jsonl"
    path.write_bytes(prefix + suffix)
    result = startup_evidence(path, 10.1)
    assert result["complete"] and result["last_tick_at"] == 10
    assert result["rows"] == len(values) and result["bytes"] == len(prefix)
    assert result["sha256"] == hashlib.sha256(prefix).hexdigest()
    assert result["completed_prefix_boundary"] == {
        "n": len(values),
        "at": 10,
        "operation": "inspect",
    }


@pytest.mark.parametrize("fault", ["missing", "partial", "oversize"])
def test_unavailable_or_unbounded_trace_cannot_pass(tmp_path, fault):
    path = tmp_path / "trace.jsonl"
    if fault != "missing":
        path.write_bytes(b"{" if fault == "partial" else b"x" * 65537)
    result = startup_evidence(path, 10.1)
    assert not result["complete"] and result["faults"]
