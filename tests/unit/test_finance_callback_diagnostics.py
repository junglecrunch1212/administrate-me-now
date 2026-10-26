"""Proposed isolated observer tests; never a representative workload."""

import ast
import asyncio
import contextvars
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from tests.fixtures import finance_callback_diagnostics as diagnostic

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.getenv("ADMINME_SYNTHETIC_CALLBACK_PREFLIGHT") != "1",
    reason="exact callback diagnostic preflight admission required",
)


class Clock:
    def __init__(self):
        self.wall = 10.0
        self.cpu = 1.0

    def monotonic(self):
        self.wall += 0.000001
        return self.wall

    def thread_time(self):
        self.cpu += 0.0000001
        return self.cpu


@pytest.fixture
def harness(monkeypatch):
    # Read SQL literals only; no application import, SQL execution or native DB.
    source = Path(__file__).resolve().parents[2] / "adminme/finance/planning_store.py"
    literals = {
        hashlib.sha256(n.value.encode()).hexdigest(): n.value
        for n in ast.walk(ast.parse(source.read_text()))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    sql = [literals[digest] for _, digest in diagnostic.SQL]
    clock = Clock()
    other_hook = object()
    fake_gc = SimpleNamespace(callbacks=[other_hook])
    monkeypatch.setattr(diagnostic, "time", clock)
    monkeypatch.setattr(diagnostic, "gc", fake_gc)
    monkeypatch.setattr(
        diagnostic, "sys", SimpleNamespace(_is_gil_enabled=lambda: True)
    )
    request = contextvars.ContextVar("synthetic_callback_request", default=None)
    secret = "MUST_NOT_APPEAR_IN_OBSERVER_OUTPUT"
    metadata = json.dumps(
        {
            "accounting_revision_ids": [secret] * 8022,
            "contexts": [{"horizons": [None] * 5} for _ in range(24)],
        }
    )
    body = json.dumps({"private": secret})
    state = SimpleNamespace(
        problem=None,
        sql_change=False,
        extra_sql=False,
        extra_json=False,
        metadata_change=False,
        page_rows=51,
        callbacks=0,
        clock=clock,
        gc=fake_gc,
        other_hook=other_hook,
    )

    class Runner:
        def __init__(self):
            self._money_executor = SimpleNamespace(
                submit=lambda function, *args, **kwargs: (
                    lambda: function(*args, **kwargs)
                )
            )

        def _call_query(self, name, query):
            assert name == "money"
            state.callbacks += 1
            return query(self)

        def _execute_statement(self, name, query, parameters, many, read_only):
            if getattr(state, "sql_problem", None) is not None:
                raise state.sql_problem
            index = sql.index(query) if query in sql else 0
            values = (
                [metadata]
                if index == 1
                else [body] * state.page_rows
                if index == 3
                else ["head"]
            )
            return SimpleNamespace(_rows=[(value,) for value in values])

    runner_source = source.parents[1] / "projections/runner.py"
    runner_class = next(
        n
        for n in ast.parse(runner_source.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "ProjectionRunner"
    )
    method = next(
        n
        for n in runner_class.body
        if isinstance(n, ast.FunctionDef) and n.name == "_call_query"
    )
    # Only this synthetic callable adopts the verified real seam's qualifier.
    Runner._call_query.__qualname__ = f"{runner_class.name}.{method.name}"
    runner = Runner()
    planning = SimpleNamespace(json=json)

    def query(conn, session, **kwargs):
        if state.problem is not None:
            raise state.problem
        statements = []
        for index, text in enumerate(sql):
            statements.append(
                conn._execute_statement(
                    "money",
                    secret if state.sql_change and index == 0 else text,
                    (secret,),
                    False,
                    True,
                )
            )
        if state.extra_sql:
            conn._execute_statement("money", sql[0], (secret,), False, True)
        meta = planning.json.loads(
            "{}" if state.metadata_change else statements[1]._rows[0][0]
        )
        events = [planning.json.loads(row[0]) for row in statements[3]._rows[:50]]
        if state.extra_json:
            planning.json.loads(body)
        assert isinstance(meta, dict)
        return {"events": events, "next_offset": 100}

    planning.query_projection = query
    observer = diagnostic.CallbackDiagnostics()
    binding_patches = pytest.MonkeyPatch()
    originals = (
        runner._money_executor.submit,
        runner._execute_statement,
        planning.query_projection,
        planning.json,
    )
    observer.install(runner, planning, binding_patches, request)
    state.observer, state.runner, state.planning = observer, runner, planning
    state.original_query = query
    state.secret = secret
    state.binding_patches, state.originals = binding_patches, originals

    def fire(label, **override):
        selection = dict(
            book_id="book-synthetic",
            context_id="bank-current-base",
            report_id="planning_events",
            horizon_days=30,
            offset=50,
            limit=50,
        )
        selection.update(override)
        token = request.set(label)
        try:
            pending = runner._money_executor.submit(
                runner._call_query,
                "money",
                lambda conn: planning.query_projection(conn, None, **selection),
            )
        finally:
            request.reset(token)
        return contextvars.Context().run(pending)

    state.fire = fire
    observer.begin_request(diagnostic.FIRST)
    yield state
    observer.remove_gc()
    assert fake_gc.callbacks == [other_hook]
    binding_patches.undo()
    assert (
        runner._money_executor.submit,
        runner._execute_statement,
        planning.query_projection,
        planning.json,
    ) == originals


def finish(h):
    h.observer.end_request(diagnostic.LAST)
    result = h.observer.close()
    assert h.secret not in json.dumps(result)
    return result


def test_exact_five_calls_preserve_results_and_bounded_sql_json_evidence(harness):
    h = harness
    original_json_loads = json.loads
    for request in diagnostic.REQUESTS:
        result = h.fire(request)
        assert (
            len(result["events"]) == 50 and result["events"][0]["private"] == h.secret
        )
    value = finish(h)
    assert value["complete"] and value["removed"] and not value["faults"]
    assert json.loads is original_json_loads and h.planning.json is not json
    assert (
        len(json.dumps(value, separators=(",", ":")).encode())
        <= diagnostic.SECTION_BYTES
    )
    for row in value["callbacks"]:
        assert [s["rows"] for s in row["sql"]] == [1, 1, 1, 51]
        assert row["metadata_counts"] == [8022, 24, 120]
        assert [row["json"][k]["calls"] for k in ("metadata", "event_bodies")] == [
            1,
            50,
        ]
        assert len(row["json"]["event_bodies"]["spans"]) == 50
        assert row["thread_cpu_seconds"] > 0 and row["ended"] > row["started"]


@pytest.mark.parametrize(
    "problem",
    [ValueError("private exception"), asyncio.CancelledError("private cancellation")],
)
def test_original_exception_identity_and_thread_scope_are_preserved(harness, problem):
    h = harness
    h.problem = problem
    with pytest.raises(type(problem)) as caught:
        h.fire(diagnostic.REQUESTS[0])
    assert caught.value is problem and h.observer.local.active is None
    h.problem = None
    assert len(h.fire(diagnostic.REQUESTS[1])["events"]) == 50
    value = finish(h)
    assert not value["complete"] and "operation_error" in value["faults"]
    assert "private exception" not in json.dumps(
        value
    ) and "private cancellation" not in json.dumps(value)


def test_unselected_requests_and_operations_have_no_capture(harness):
    h = harness
    assert len(h.fire("unrelated-private-request")["events"]) == 50
    assert not h.observer.records
    assert h.runner._money_executor.submit(lambda: h.secret)() == h.secret
    assert not h.observer.records
    assert not finish(h)["complete"]


@pytest.mark.parametrize("stage", ["sql", "json"])
@pytest.mark.parametrize(
    "problem",
    [
        ValueError("private subspan error"),
        asyncio.CancelledError("private subspan cancellation"),
    ],
)
def test_subspan_exceptions_preserve_identity_and_cleanup(harness, stage, problem):
    h = harness
    if stage == "sql":
        h.sql_problem = problem
    else:

        def fail(*args, **kwargs):
            raise problem

        h.planning.json.original = SimpleNamespace(loads=fail)
    with pytest.raises(type(problem)) as caught:
        h.fire(diagnostic.REQUESTS[0])
    assert caught.value is problem and h.observer.local.active is None
    value = finish(h)
    assert not value["complete"] and "operation_error" in value["faults"]
    assert "private subspan" not in json.dumps(value)


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("sql_change", "sql_shape"),
        ("extra_sql", "sql_shape"),
        ("extra_json", "json_shape"),
        ("metadata_change", "json_shape"),
        ("selection", "selection"),
        ("page_rows", "sql_shape"),
    ],
)
def test_changed_selection_or_subspan_shape_cannot_claim_coverage(
    harness, fault, expected
):
    h = harness
    if fault != "selection":
        setattr(h, fault, 50 if fault == "page_rows" else True)
    for request in diagnostic.REQUESTS:
        assert (
            len(
                h.fire(
                    request, **({"horizon_days": 90} if fault == "selection" else {})
                )["events"]
            )
            == 50
        )
    value = finish(h)
    assert not value["complete"] and expected in value["faults"]
    assert all(
        len(row["sql"]) <= 4
        and sum(len(v["spans"]) for v in row["json"].values()) <= 51
        for row in value["callbacks"]
    )


def test_duplicate_and_missing_callbacks_fail_without_more_records(harness):
    h = harness
    for request in (*diagnostic.REQUESTS, diagnostic.REQUESTS[0]):
        h.fire(request)
    value = finish(h)
    assert h.callbacks == 6 and len(value["callbacks"]) == 5
    assert not value["complete"] and "callback_coverage" in value["faults"]


@pytest.mark.parametrize(
    "mode", ["paired", "orphan_stop", "unclosed", "mismatched", "overflow"]
)
def test_gc_capture_is_bounded_and_protocol_checked(harness, mode):
    h = harness
    for request in diagnostic.REQUESTS:
        h.fire(request)
    if mode == "orphan_stop":
        h.observer.gc_event("stop", {"generation": 0})
    else:
        for _ in range(65 if mode == "overflow" else 1):
            h.observer.gc_event("start", {"generation": 0})
            if mode != "unclosed":
                h.observer.gc_event(
                    "stop", {"generation": 1 if mode == "mismatched" else 0}
                )
    value = finish(h)
    assert len(value["gc_events"]) <= 128
    assert value["complete"] is (mode == "paired")
    if mode == "overflow":
        assert "gc_overflow" in value["faults"] and h.observer.gc_count == 128
    elif mode != "paired":
        assert "gc_protocol" in value["faults"]


def test_gc_callback_returns_while_both_trace_locks_are_held(harness, tmp_path):
    # Manual callback invocation with the actual locks held; no forced GC,
    # process scheduling claim or real runtime is involved in this unit proof.
    from tests.fixtures.finance_staging_measurement import DiagnosticTrace

    trace = DiagnosticTrace(tmp_path)
    errors = []

    def interrupted_trace():
        try:
            with trace.lock, trace.cost_lock:
                harness.observer.gc_event("start", {"generation": 2})
                harness.observer.gc_event("stop", {"generation": 2})
        except BaseException as exc:
            errors.append(type(exc).__name__)

    worker = threading.Thread(target=interrupted_trace, daemon=True)
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive() and not errors
    assert harness.observer.gc_count == 2 and trace.sequence == 0
    trace.close()


@pytest.mark.parametrize("mode", ["window", "callback", "section"])
def test_time_and_byte_overflow_remain_incomplete(harness, monkeypatch, mode):
    h = harness
    for request in diagnostic.REQUESTS:
        h.fire(request)
    if mode == "window":
        h.clock.wall += 13
        h.observer.gc_event("start", {"generation": 0})
    elif mode == "callback":
        # Check coverage independently of operation outcome or observer output.
        h.observer.records[0]["ended"] = h.observer.records[0]["started"] + 3
    else:
        monkeypatch.setattr(diagnostic, "SECTION_BYTES", 64)
    value = finish(h)
    assert not value["complete"]
    assert value["removed"]


@pytest.mark.parametrize("mode", ["complete", "missing", "oversize"])
def test_terminal_receipt_requires_callback_coverage_and_is_bounded(
    harness, tmp_path, mode
):
    from tests.fixtures.finance_staging_measurement import DiagnosticTrace

    h = harness
    if mode != "missing":
        for request in diagnostic.REQUESTS:
            h.fire(request)
        h.observer.end_request(diagnostic.LAST)
    trace = DiagnosticTrace(tmp_path)
    trace.callback_diagnostics = h.observer
    trace.probe["complete"] = True
    trace.executors = [
        "event_log",
        "event_metadata",
        "projection",
        "money_read",
        "authority",
    ]
    if mode == "oversize":
        trace.probe["test_oversize"] = "x" * 65536
    trace.close()
    raw = (tmp_path / "diagnostic-observer.json").read_bytes()
    assert len(raw) <= 65536 and h.gc.callbacks == [h.other_hook]
    receipt = json.loads(raw)
    assert receipt["diagnostic_complete"] is (mode == "complete")
    assert h.secret.encode() not in raw


def test_early_close_removes_only_owned_gc_hook_before_io_failure(harness, tmp_path):
    from tests.fixtures.finance_staging_measurement import DiagnosticTrace

    trace = DiagnosticTrace(tmp_path)
    trace.callback_diagnostics = harness.observer
    trace.file.close()
    problem = OSError("synthetic close failure")

    def fail():
        raise problem

    trace.file = SimpleNamespace(close=fail)
    with pytest.raises(OSError) as caught:
        trace.close()
    assert caught.value is problem
    assert harness.gc.callbacks == [harness.other_hook]


def test_installation_undo_restores_exact_module_and_executor_bindings(harness):
    h = harness
    for request in diagnostic.REQUESTS:
        h.fire(request)
    assert finish(h)["complete"]
    h.binding_patches.undo()
    assert (
        h.runner._money_executor.submit,
        h.runner._execute_statement,
        h.planning.query_projection,
        h.planning.json,
    ) == h.originals


def test_observer_off_receipt_preserves_baseline_shape_and_encoding(tmp_path):
    from tests.fixtures.finance_staging_measurement import DiagnosticTrace

    trace = DiagnosticTrace(tmp_path)
    assert trace.callback_diagnostics is None
    trace.probe["complete"] = True
    trace.executors = [
        "event_log",
        "event_metadata",
        "projection",
        "money_read",
        "authority",
    ]
    trace.mark("synthetic.off")
    trace.close()
    raw = (tmp_path / "diagnostic-observer.json").read_text()
    value = json.loads(raw)
    assert set(value) == {
        "diagnostic_complete",
        "loop_probe",
        "executors_instrumented",
        "costs",
        "trace_rows",
        "trace_encoded_bytes",
        "trace_buffer_bytes",
        "trace_limit_bytes",
        "limitations",
    }
    assert value["diagnostic_complete"] is True and value["trace_rows"] == 1
    assert (
        value["trace_buffer_bytes"] == 65536 and value["trace_limit_bytes"] == 16777216
    )
    assert (
        value["trace_encoded_bytes"]
        == (tmp_path / "measurement-trace.jsonl").stat().st_size
    )
    assert raw == json.dumps(value, indent=2) + "\n"
    assert (
        value["costs"]["trace_mark"]["calls"]
        == value["costs"]["trace_close"]["calls"]
        == 1
    )
