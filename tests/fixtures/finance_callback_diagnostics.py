"""Opt-in synthetic Money attribution; never installs into production code."""

from __future__ import annotations

import functools
import gc
import hashlib
import json
import math
import sys
import threading
import time

REQUESTS = tuple(f"loaded_planning_probe:planning:{n}" for n in (1, 21, 41, 61, 81))
FIRST, LAST = "loaded_planning_probe:planning:0", "loaded_planning_probe:planning:99"
GC_RECORDS = 128
SECTION_BYTES = 48 * 1024
WINDOW_SECONDS = 12
CALLBACK_SECONDS = 2
SQL = (
    (
        "projection_head",
        "be078d69f7827a5f7dc35ded4c920548cd27a25c860cb6bd7027c7c50d19b5dd",
    ),
    ("metadata", "32c9e60f77375a83545e5c55103aa59d8c285dd44e300b9a88dc210005fd702d"),
    (
        "accounting_head",
        "c7f04044555cb194ab7ae423014483882f50c9942852fd600466a8033b4b51a6",
    ),
    ("events_page", "37af18f51012f84ed6383954393b35e0c319d7c716ac5f49e796351f4268a992"),
)
FAULTS = (
    "installation",
    "window",
    "callback_coverage",
    "selection",
    "sql_shape",
    "json_shape",
    "operation_error",
    "clock",
    "gc_overflow",
    "gc_protocol",
    "section_bytes",
    "observer_error",
)


class JSONProbe:
    """Module-local proxy: the process-wide json module is never patched."""

    def __init__(self, original, observer):
        self.original, self.observer = original, observer

    def __getattr__(self, name):
        return getattr(self.original, name)

    def loads(self, value, *args, **kwargs):
        return self.observer.decode(self.original.loads, value, *args, **kwargs)


class CallbackDiagnostics:
    def __init__(self):
        self.local = threading.local()
        self.records = []
        self.fault_mask = 0
        self.window_start = self.window_end = None
        self.installed = self.closed = False
        self.gc_records = [None] * GC_RECORDS
        self.gc_count = 0
        self.gc_hook = self.gc_event

    def fault(self, name):
        self.fault_mask |= 1 << FAULTS.index(name)

    def gc_event(self, phase, info):
        # GC may interrupt Trace.mark/account while either non-reentrant lock
        # is held. No trace/file/JSON calls, logging or locks are allowed here.
        # CPython's GIL-enabled collector serializes this callback. Free-threaded
        # runtimes are explicitly unsupported, rather than assuming atomicity.
        if self.closed or self.window_start is None or self.window_end is not None:
            return
        if self.gc_count >= GC_RECORDS:
            self.fault_mask |= 1 << 8
            return
        now = time.monotonic()
        if now - self.window_start > WINDOW_SECONDS:
            self.fault_mask |= 1 << 1
            return
        code = 0 if phase == "start" else 1 if phase == "stop" else -1
        generation = info.get("generation")
        if code < 0 or type(generation) is not int or generation not in (0, 1, 2):
            self.fault_mask |= 1 << 9
            return
        self.gc_records[self.gc_count] = (
            code,
            generation,
            threading.get_native_id(),
            now,
            time.thread_time(),
        )
        self.gc_count += 1

    def begin_request(self, request):
        if request == FIRST:
            if not self.installed or self.window_start is not None:
                self.fault("window")
                return
            self.window_start = time.monotonic()
            gc.callbacks.append(self.gc_hook)

    def end_request(self, request):
        if request == LAST:
            if self.window_start is None or self.window_end is not None:
                self.fault("window")
            self.window_end = time.monotonic()
            self.remove_gc()

    def remove_gc(self):
        if self.gc_hook in gc.callbacks:
            gc.callbacks.remove(self.gc_hook)

    def install(self, runner, planning, patches, request_context):
        if self.installed or not getattr(sys, "_is_gil_enabled", lambda: False)():
            self.fault("installation")
            return
        submit = runner._money_executor.submit
        statement = runner._execute_statement
        query = planning.query_projection

        def observed_submit(function, *args, **kwargs):
            request = request_context.get()
            if (
                request not in REQUESTS
                or function != runner._call_query
                or getattr(function, "__qualname__", "")
                != "ProjectionRunner._call_query"
                or not args
                or args[0] != "money"
            ):
                return submit(function, *args, **kwargs)

            @functools.wraps(function)
            def call(*inner_args, **inner_kwargs):
                return self.callback(request, function, *inner_args, **inner_kwargs)

            return submit(call, *args, **kwargs)

        @functools.wraps(query)
        def observed_query(conn, session, **kwargs):
            active = getattr(self.local, "active", None)
            if active is not None:
                active["queries"] += 1
                expected = dict(
                    book_id="book-synthetic",
                    context_id="bank-current-base",
                    report_id="planning_events",
                    horizon_days=30,
                    offset=50,
                    limit=50,
                )
                if (
                    any(kwargs.get(k) != v for k, v in expected.items())
                    or any(
                        kwargs.get(k) is not None
                        for k in ("projection_id", "reference_id", "gap_group_id")
                    )
                    or kwargs.get("view", "all") != "all"
                    or kwargs.get("selector")
                ):
                    self.fault("selection")
            return query(conn, session, **kwargs)

        @functools.wraps(statement)
        def observed_statement(name, sql, parameters, many, read_only):
            active = getattr(self.local, "active", None)
            if active is None:
                return statement(name, sql, parameters, many, read_only)
            index = len(active["sql"])
            if index >= 4:
                self.fault("sql_shape")
                return statement(name, sql, parameters, many, read_only)
            label, digest = SQL[index]
            if (
                name != "money"
                or many
                or not read_only
                or hashlib.sha256(sql.encode()).hexdigest() != digest
            ):
                self.fault("sql_shape")
            started, cpu = time.monotonic(), time.thread_time()
            try:
                result = statement(name, sql, parameters, many, read_only)
            except BaseException:
                self.fault("operation_error")
                raise
            finally:
                ended, used = time.monotonic(), time.thread_time()
            # Metadata only. Never consume the cursor or retain SQL/parameters.
            rows = getattr(result, "_rows", None)
            count = len(rows) if isinstance(rows, list) else -1
            active["sql"].append(
                dict(
                    label=label,
                    started=started,
                    ended=ended,
                    thread_cpu_seconds=used - cpu,
                    rows=count,
                )
            )
            return result

        patches.setattr(runner._money_executor, "submit", observed_submit)
        patches.setattr(runner, "_execute_statement", observed_statement)
        patches.setattr(planning, "query_projection", observed_query)
        patches.setattr(planning, "json", JSONProbe(planning.json, self))
        self.installed = True

    def decode(self, original, value, *args, **kwargs):
        active = getattr(self.local, "active", None)
        if active is None:
            return original(value, *args, **kwargs)
        index = active["json_calls"]
        active["json_calls"] += 1
        if index >= 51:
            self.fault("json_shape")
            return original(value, *args, **kwargs)
        started, cpu = time.monotonic(), time.thread_time()
        try:
            result = original(value, *args, **kwargs)
        except BaseException:
            self.fault("operation_error")
            raise
        finally:
            ended, used = time.monotonic(), time.thread_time()
        label = "metadata" if index == 0 else "event_bodies"
        metric = active["json"][label]
        metric["calls"] += 1
        metric["wall_seconds"] += ended - started
        metric["thread_cpu_seconds"] += used - cpu
        metric["input_characters"] += len(value) if isinstance(value, str) else 0
        metric["spans"].append([started, ended, used - cpu])
        if index == 0:
            try:
                active["metadata_counts"] = [
                    len(result["accounting_revision_ids"]),
                    len(result["contexts"]),
                    sum(len(c["horizons"]) for c in result["contexts"]),
                ]
            except (KeyError, TypeError):
                self.fault("json_shape")
        return result

    def callback(self, request, function, *args, **kwargs):
        now = time.monotonic()
        if (
            self.window_start is None
            or self.window_end is not None
            or not 0 <= now - self.window_start <= WINDOW_SECONDS
        ):
            self.fault("window")
            return function(*args, **kwargs)
        if (
            self.closed
            or getattr(self.local, "active", None) is not None
            or len(self.records) >= 5
            or any(r["request"] == request for r in self.records)
        ):
            self.fault("callback_coverage")
            return function(*args, **kwargs)
        row = dict(
            request=request,
            native_thread=threading.get_native_id(),
            queries=0,
            sql=[],
            json_calls=0,
            metadata_counts=[],
            json={
                k: dict(
                    calls=0,
                    wall_seconds=0.0,
                    thread_cpu_seconds=0.0,
                    input_characters=0,
                    spans=[],
                )
                for k in ("metadata", "event_bodies")
            },
            outcome="error",
        )
        self.records.append(row)
        self.local.active = row
        row["started"], cpu = time.monotonic(), time.thread_time()
        try:
            result = function(*args, **kwargs)
            row["outcome"] = "returned"
            row["events"] = (
                len(result.get("events", [])) if isinstance(result, dict) else -1
            )
            offset = result.get("next_offset") if isinstance(result, dict) else None
            row["next_offset_matches"] = type(offset) is int and offset == 100
            return result
        except BaseException:
            self.fault("operation_error")
            raise
        finally:
            row["ended"], used = time.monotonic(), time.thread_time()
            row["thread_cpu_seconds"] = used - cpu
            self.local.active = None
            if not 0 <= row["ended"] - row["started"] <= CALLBACK_SECONDS or used < cpu:
                self.fault("clock")

    def close(self):
        self.remove_gc()
        self.closed = True
        if (
            not self.installed
            or self.window_start is None
            or self.window_end is None
            or not 0 <= self.window_end - self.window_start <= WINDOW_SECONDS
        ):
            self.fault("window")
        if [r["request"] for r in self.records] != list(REQUESTS):
            self.fault("callback_coverage")
        for row in self.records:
            if (
                row.get("outcome") != "returned"
                or row.get("queries") != 1
                or row.get("events") != 50
                or row.get("next_offset_matches") is not True
            ):
                self.fault("selection")
            if [r["rows"] for r in row["sql"]] != [1, 1, 1, 51]:
                self.fault("sql_shape")
            if row["json_calls"] != 51 or row["metadata_counts"] != [8022, 24, 120]:
                self.fault("json_shape")
            spans = [
                (r["started"], r["ended"], r["thread_cpu_seconds"]) for r in row["sql"]
            ]
            spans += [
                tuple(s) for group in row["json"].values() for s in group["spans"]
            ]
            if any(
                not all(math.isfinite(v) for v in s)
                or not row["started"] <= s[0] <= s[1] <= row.get("ended", -1)
                or s[2] < 0
                for s in spans
            ):
                self.fault("clock")
            if (
                self.window_end is None
                or self.window_start is None
                or not self.window_start
                <= row["started"]
                <= row.get("ended", -1)
                <= self.window_end
            ):
                self.fault("window")
        events = self.gc_records[: self.gc_count]
        pending = None
        for event in events:
            phase, generation, tid, at, cpu = event
            if (
                not math.isfinite(at)
                or not math.isfinite(cpu)
                or self.window_start is None
                or self.window_end is None
                or not self.window_start <= at <= self.window_end
            ):
                self.fault("gc_protocol")
            if phase == 0 and pending is None:
                pending = event
            elif (
                phase == 1
                and pending is not None
                and (generation, tid) == pending[1:3]
                and at >= pending[3]
                and cpu >= pending[4]
            ):
                pending = None
            else:
                self.fault("gc_protocol")
        if pending is not None:
            self.fault("gc_protocol")
        if len({r["native_thread"] for r in self.records}) != 1:
            self.fault("callback_coverage")
        result = dict(
            complete=not self.fault_mask,
            faults=[v for i, v in enumerate(FAULTS) if self.fault_mask & (1 << i)],
            installed=self.installed,
            removed=self.gc_hook not in gc.callbacks,
            window_start=self.window_start,
            window_end=self.window_end,
            callbacks=self.records,
            gc_events=events,
            gc_event_fields=[
                "phase_0_start_1_stop",
                "generation",
                "native_thread",
                "at",
                "thread_cpu",
            ],
            limits=dict(
                callbacks=5,
                sql_per_callback=4,
                json_per_callback=51,
                gc_events=GC_RECORDS,
                section_bytes=SECTION_BYTES,
                window_seconds=WINDOW_SECONDS,
                callback_seconds=CALLBACK_SECONDS,
            ),
            limitations="Synthetic diagnostics only. Wall includes contention and GC; CPU is thread-wide. SQL includes execute/fetch/detach. JSON spans time each decode; assembly/local cleanup remain inside callback. Observer overhead stays in full-run totals; no cost subtraction. No GC event is not evidence GC is impossible.",
        )
        if len(json.dumps(result, separators=(",", ":")).encode()) > SECTION_BYTES:
            self.fault("section_bytes")
            return dict(
                complete=False, faults=["section_bytes"], removed=result["removed"]
            )
        return result
