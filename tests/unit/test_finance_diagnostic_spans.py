"""Bounded synthetic observer buffers and spans; no representative workload."""

import asyncio
import contextvars
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import tracemalloc
from types import SimpleNamespace

import pytest

from tests.fixtures import finance_staging_measurement as diagnostic
from tests.unit.test_finance_diagnostic_observers import (
    exact_reviewed_v2_sources as exact_reviewed_v2_sources,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux"
    or os.getenv("ADMINME_SYNTHETIC_DIAGNOSTIC_PREFLIGHT") != "1",
    reason="exact admitted synthetic diagnostic preflight only",
)


def evidence(name, **fields):
    output = Path(os.environ["FINANCE_STORAGE_ARTIFACT_DIR"])
    output.mkdir(exist_ok=True)
    (output / (name + ".json")).write_text(
        json.dumps(
            {
                "candidate": os.environ["GITHUB_SHA"],
                "role": "isolated synthetic unit-test process; no application runtime",
                "resource_qualified": False,
                **fields,
            },
            indent=2,
        )
        + "\n"
    )


def trace_rows(root):
    return [
        json.loads(line)
        for line in (root / "measurement-trace.jsonl").read_text().splitlines()
    ]


def problem_for(outcome):
    return (
        OSError("synthetic work error")
        if outcome == "error"
        else asyncio.CancelledError("synthetic cancellation")
    )


def test_trace_streaming_buffer_does_not_retain_prior_rows(tmp_path):
    trace = diagnostic.DiagnosticTrace(tmp_path)
    assert not tracemalloc.is_tracing()
    tracemalloc.start(1)
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        for index in range(4096):
            trace.mark("buffer.test", index=index, value="x" * 512)
        trace.flush()
        current, peak = tracemalloc.get_traced_memory()
        internal = tracemalloc.get_tracemalloc_memory()
    finally:
        tracemalloc.stop()
        trace.close()
    path = tmp_path / "measurement-trace.jsonl"
    retained_bytes = path.stat().st_size
    with path.open() as stream:
        for index, line in enumerate(stream):
            row = json.loads(line)
            assert row["index"] == index and row["value"] == "x" * 512
        assert index == 4095
    receipt = json.loads((tmp_path / "diagnostic-observer.json").read_text())
    evidence(
        "trace-buffer",
        retained_bytes=retained_bytes,
        retained_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        retained_rows=4096,
        attempted_rows=trace.sequence,
        attempted_bytes=trace.bytes,
        traced_peak_above_baseline_bytes=peak - baseline,
        traced_live_bytes=current,
        tracer_internal_bytes=internal,
        python_diagnostic_bound_bytes=524288,
        observer_receipt=receipt,
        limitations="One Python-buffer diagnostic, not RSS or native allocation; tracing affects timing; no forced GC/trim.",
    )
    assert trace.sequence == 4096 and trace.bytes == retained_bytes
    assert retained_bytes > 2 * 1024 * 1024 and retained_bytes <= 16 * 1024 * 1024
    assert peak - baseline <= 524288
    assert receipt["costs"]["trace_mark"]["calls"] == 4096
    assert (
        receipt["costs"]["trace_flush"]["calls"]
        == receipt["costs"]["trace_close"]["calls"]
        == 1
    )


async def test_trace_overflow_is_diagnostic_failure_before_operation_or_write(tmp_path):
    trace = diagnostic.DiagnosticTrace(tmp_path)
    trace.mark("retained.before.overflow")
    trace.flush()
    path = tmp_path / "measurement-trace.jsonl"
    before = path.read_bytes()
    # Seed the existing budget counter at its cap; do not allocate a16MiB row.
    trace.bytes = 16 * 1024 * 1024
    called = []

    async def operation():
        called.append(True)
        return "must-not-run"

    try:
        with pytest.raises(RuntimeError, match="synthetic_trace_limit"):
            await diagnostic.diagnostic_readback(trace, "event_digest", operation)
    finally:
        trace.close()
    receipt = json.loads((tmp_path / "diagnostic-observer.json").read_text())
    evidence(
        "trace-overflow",
        attempted_rows=trace.sequence,
        attempted_bytes=trace.bytes,
        retained_rows=1,
        retained_bytes=path.stat().st_size,
        counter_seed_bytes=16 * 1024 * 1024,
        observer_receipt=receipt,
        diagnostic_failure="synthetic_trace_limit",
        product_operation_executed=bool(called),
    )
    assert not called and path.read_bytes() == before
    assert trace.sequence == 2 and trace.bytes > 16 * 1024 * 1024
    assert path.stat().st_size < 16 * 1024 * 1024
    assert receipt["diagnostic_complete"] is False
    assert receipt["costs"]["trace_mark"]["calls"] == 2
    assert receipt["costs"]["resource_sample"]["calls"] == 1


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_readback_spans_preserve_digest_result_errors_and_cursor_cleanup(
    tmp_path, monkeypatch, outcome
):
    trace = diagnostic.DiagnosticTrace(tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE events(tenant_id,event_id,type,owner_scope,source_account_id,payload)"
    )
    conn.executemany(
        "INSERT INTO events VALUES(?,?,?,?,?,?)",
        [
            (
                "synthetic",
                "event-2",
                "finance.projection_verified",
                "private:synthetic",
                "book-synthetic",
                '{"amount":"1.00"}',
            ),
            (
                "synthetic",
                "event-1",
                "finance.publication_accepted",
                "private:synthetic",
                "book-synthetic",
                '{"text":"café"}',
            ),
            (
                "excluded",
                "event-0",
                "finance.publication_accepted",
                "private:synthetic",
                "book-synthetic",
                "{}",
            ),
        ],
    )
    for table in ("finance_publication_heads", "finance_dated_projection_heads"):
        conn.execute("CREATE TABLE " + table + "(value)")
        conn.execute("INSERT INTO " + table + " VALUES('head')")
    projections = {cls.name for cls in diagnostic.DEFAULT_PROJECTION_CLASSES}
    state = {
        "counts": {"synthetic": 2},
        "finance_events": ["event-1", "event-2"],
        "audit": [],
        "consumers": {"projection:" + name: {"lag": 0} for name in projections},
        "active_executor": False,
        "money_checkpoint": "event-2",
        "authority": {"synthetic": True},
        "lifetimes": [],
        "guards": [],
        "boundaries": [],
    }
    problem = problem_for(outcome)
    cursors, bodies = [], []

    class Cursor:
        def __init__(self, cursor):
            self.cursor, self.closed = cursor, False
            cursors.append(self)

        def __iter__(self):
            bodies.append(("digest.enter", time.monotonic()))
            if outcome != "success":
                raise problem
            yield from self.cursor
            bodies.append(("digest.exit", time.monotonic()))

        def close(self):
            self.cursor.close()
            self.closed = True

    class Connection:
        def execute(self, sql, parameters=()):
            return Cursor(conn.execute(sql, parameters))

    async def metadata(runtime, storage):
        bodies.append(("metadata", time.monotonic()))
        return state

    async def execute(function):
        return function()

    async def heads(name, function):
        assert name == "money"
        bodies.append(("heads", time.monotonic()))
        return function(conn)

    monkeypatch.setattr(diagnostic, "snapshot", metadata)
    runtime = SimpleNamespace(
        event_log=SimpleNamespace(
            _conn=Connection(), _run_on_connection_thread=execute
        ),
        projections=SimpleNamespace(read=heads, projection_names=lambda: projections),
        settings=SimpleNamespace(instance=SimpleNamespace(tenant_id="synthetic")),
    )
    started = time.monotonic()
    result = None
    try:
        if outcome == "success":
            result = await diagnostic.durable_snapshot(
                runtime, SimpleNamespace(trace=trace, stage_lifetimes=[])
            )
        else:
            with pytest.raises(type(problem)) as caught:
                await diagnostic.durable_snapshot(
                    runtime, SimpleNamespace(trace=trace, stage_lifetimes=[])
                )
            assert caught.value is problem
        finished = time.monotonic()
    finally:
        conn.close()
        trace.close()
    rows = trace_rows(tmp_path)
    names = (
        ["metadata_snapshot", "event_digest", "heads"]
        if outcome == "success"
        else ["metadata_snapshot", "event_digest"]
    )
    assert [(r["span"], r["kind"]) for r in rows] == [
        (name, kind) for name in names for kind in ("readback.start", "readback.end")
    ]
    for a, b in zip(rows[::2], rows[1::2]):
        assert (
            started
            <= a["sampled_at"]
            <= a["at"]
            <= b["sampled_at"]
            <= b["at"]
            <= finished
        )
        assert a["rss_bytes"] > 0 and b["rss_bytes"] > 0
        assert a["process_cpu_seconds"] <= b["process_cpu_seconds"]
    assert len(cursors) == 1 and cursors[0].closed
    assert rows[0]["at"] <= dict(bodies)["metadata"] <= rows[1]["at"]
    assert rows[2]["at"] <= dict(bodies)["digest.enter"] <= rows[3]["at"]
    if outcome == "success":
        assert rows[4]["at"] <= dict(bodies)["heads"] <= rows[5]["at"]
        assert result["events"] == {
            "count": 2,
            "identity_payload_sha256": "fbdc88fcf906aba6773a8a09bd925f3a754f1bff0a30051b8944c946a62b0585",
        }
        assert result["heads"] == {
            name: [["head"]]
            for name in ("finance_publication_heads", "finance_dated_projection_heads")
        }
        assert (
            result["counts"] is state["counts"]
            and result["authority"] is state["authority"]
        )
    accounted = sum(
        trace.costs[k]["wall_seconds"] for k in ("trace_mark", "resource_sample")
    )
    assert 0 <= accounted <= finished - started
    assert trace.costs["resource_sample"]["calls"] == len(rows)
    evidence(
        "readback-" + outcome,
        rows=rows,
        body_timestamps=bodies,
        costs=trace.costs,
        observed_wall_seconds=finished - started,
        accounted_observer_wall_seconds=accounted,
        cost_subtracted=False,
        cursor_closed=True,
        original_result=result,
        error_preserved=outcome != "success",
    )


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_route_spans_preserve_request_context_results_and_errors(
    tmp_path, outcome
):
    trace = diagnostic.DiagnosticTrace(tmp_path)
    sent = []
    value = object()
    problem = problem_for(outcome)
    scope = {
        "type": "http",
        "path": "/api/member/v1/finance/planning",
        "headers": [(b"x-measurement-id", b"span_test")],
    }

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    async def application(found, reader, writer):
        assert found is scope and reader is receive
        assert diagnostic.REQUEST.get() == "span_test"
        if outcome != "success":
            raise problem
        await writer({"type": "http.response.start", "status": 200})
        await writer({"type": "http.response.body", "body": b"synthetic"})
        return value

    app = diagnostic.TimedASGI(
        diagnostic.DiagnosticHandler(application, "planning", trace), trace
    )
    token = diagnostic.REQUEST.set("outer")
    started = time.monotonic()
    try:
        if outcome == "success":
            assert await app(scope, receive, send) is value
        else:
            with pytest.raises(type(problem)) as caught:
                await app(scope, receive, send)
            assert caught.value is problem
        finished = time.monotonic()
        assert diagnostic.REQUEST.get() == "outer"
    finally:
        diagnostic.REQUEST.reset(token)
        trace.close()
    rows = trace_rows(tmp_path)
    expected = (
        [
            "http.start",
            "handler.start",
            "http.headers",
            "http.last_byte",
            "handler.end",
            "http.finished",
        ]
        if outcome == "success"
        else ["http.start", "handler.start", "handler.end", "http.finished"]
    )
    assert [r["kind"] for r in rows] == expected
    assert all(r["request"] == "span_test" and r["route"] == "planning" for r in rows)
    assert started <= rows[0]["at"] <= rows[-1]["at"] <= finished
    assert all(a["at"] <= b["at"] for a, b in zip(rows, rows[1:]))
    assert trace.costs["trace_mark"]["wall_seconds"] <= finished - started
    assert sent == (
        [
            {"type": "http.response.start", "status": 200},
            {"type": "http.response.body", "body": b"synthetic"},
        ]
        if outcome == "success"
        else []
    )
    evidence(
        "route-" + outcome,
        rows=rows,
        costs=trace.costs,
        observed_wall_seconds=finished - started,
        cost_subtracted=False,
        context_restored=True,
        result_or_error_preserved=True,
    )


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_executor_spans_keep_submit_start_finish_order_and_original_outcome(
    tmp_path, monkeypatch, outcome
):
    trace = diagnostic.DiagnosticTrace(tmp_path)
    pending, body = [], []
    ticket, value = object(), object()
    problem = problem_for(outcome)

    def submit(function):
        pending.append(function)
        return ticket

    executor = SimpleNamespace(submit=submit)
    diagnostic.instrument_executor(executor, "money_read", monkeypatch, trace)

    def work(number, *, tag):
        assert (number, tag) == (7, "synthetic")
        body.append(time.monotonic())
        if outcome != "success":
            raise problem
        return value

    started = time.monotonic()
    token = diagnostic.REQUEST.set("queue_test")
    try:
        assert executor.submit(work, 7, tag="synthetic") is ticket
    finally:
        diagnostic.REQUEST.reset(token)
    assert len(pending) == 1 and trace.sequence == 0
    try:
        if outcome == "success":
            assert contextvars.Context().run(pending[0]) is value
        else:
            with pytest.raises(type(problem)) as caught:
                contextvars.Context().run(pending[0])
            assert caught.value is problem
        finished = time.monotonic()
    finally:
        trace.close()
    rows = trace_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert (
        row["kind"] == "native.queue"
        and row["executor"] == "money_read"
        and row["request"] == "queue_test"
    )
    assert (
        started
        <= row["submitted"]
        <= row["started"]
        <= body[0]
        <= row["finished"]
        <= row["at"]
        <= finished
    )
    assert trace.costs["trace_mark"]["wall_seconds"] <= finished - started
    evidence(
        "queue-" + outcome,
        rows=rows,
        costs=trace.costs,
        observed_wall_seconds=finished - started,
        cost_subtracted=False,
        result_or_error_preserved=True,
        limitation="Deferred submit stub executes in an empty context; no real worker scheduling claim.",
    )
