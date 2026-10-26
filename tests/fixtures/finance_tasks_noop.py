"""Test-only known-no-op control; never imported by production code."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

from adminme.projections.runner import ProjectionRunner


class TasksNoopDiagnostic:
    def __init__(self, *, control: bool):
        self.control = control
        self.active = False
        self.phase = "warmup"
        self.attempts: list[dict[str, Any]] = []
        self.applications: list[dict[str, Any]] = []
        self.memory: dict[str, dict[str, int]] = {}
        self.lock = threading.Lock()
        self.runtime: Any = None
        self.start_cursor: str | None = None

    @staticmethod
    def eligible(name, event):
        return (
            name == "tasks"
            and event["type"] == "openclaw.tool_call.recorded"
            and type(event["schema_version"]) is int
            and event["schema_version"] in (1, 2)
        )

    @contextmanager
    def install(self):
        original_run = ProjectionRunner._run_on_worker
        original_apply = ProjectionRunner._apply_one_for
        diagnostic = self

        @wraps(original_run)
        async def run(runner, function, *args):
            if getattr(function, "__name__", None) == "_apply_one_for":
                name, _, event = args
                skip = diagnostic.control and diagnostic.eligible(name, event)
                # The real _make_callback has already acquired this lock.
                assert runner._write_locks[name].locked()
                if diagnostic.active:
                    diagnostic.attempts.append(
                        {
                            "phase": diagnostic.phase,
                            "projection": name,
                            "type": event["type"],
                            "version": event["schema_version"],
                            "skipped": skip,
                        }
                    )
                if skip:
                    return None
            return await original_run(runner, function, *args)

        @wraps(original_apply)
        def apply(runner, name, projection, event):
            if not diagnostic.active:
                return original_apply(runner, name, projection, event)
            phase = diagnostic.phase
            connection = runner._require_connection(name)
            statements: Counter[str] = Counter()

            def trace(statement):
                # Retain only transaction verbs, never SQL values or payloads.
                verb = statement.lstrip().split(" ", 1)[0].upper()
                if verb in {"BEGIN", "COMMIT", "ROLLBACK"}:
                    statements[verb] += 1

            changed_before = connection.total_changes
            started = time.perf_counter()
            cpu_started = time.thread_time()
            connection.set_trace_callback(trace)
            try:
                return original_apply(runner, name, projection, event)
            finally:
                connection.set_trace_callback(None)
                row = {
                    "phase": phase,
                    "projection": name,
                    "type": event["type"],
                    "version": event["schema_version"],
                    "transactions": dict(statements),
                    "changed_rows": connection.total_changes - changed_before,
                    "wall_ms": (time.perf_counter() - started) * 1000,
                    "thread_cpu_ms": (time.thread_time() - cpu_started) * 1000,
                }
                with diagnostic.lock:
                    diagnostic.applications.append(row)

        setattr(ProjectionRunner, "_run_on_worker", run)
        setattr(ProjectionRunner, "_apply_one_for", apply)
        try:
            yield self
        finally:
            ProjectionRunner._run_on_worker = original_run
            ProjectionRunner._apply_one_for = original_apply

    def set_phase(self, phase):
        self.phase = phase
        values = {}
        for line in Path("/proc/self/status").read_text().splitlines():
            name, _, value = line.partition(":")
            if name in {"VmRSS", "VmHWM", "VmSize"}:
                values[name + "_bytes"] = int(value.split()[0]) * 1024
            elif name == "Threads":
                values["threads"] = int(value.strip())
        self.memory[phase] = values

    async def start(self):
        self.start_cursor = await self.runtime.event_log.latest_event_id()
        self.set_phase("before")
        self.active = True

    async def finish(self):
        self.active = False
        self.set_phase("after_measurement")
        events = [
            event
            async for event in self.runtime.event_log.read_since(
                self.start_cursor,
                types=["openclaw.tool_call.recorded", "finance.disclosure_recorded"],
            )
        ]
        # Compare measured logical ordering without pretending independently
        # minted timestamps/event IDs are byte-identical. Exact complete event,
        # projection and checkpoint equality uses the fixed-corpus test.
        sequence = []
        for event in events:
            payload = event["payload"]
            sequence.append(
                {
                    "type": event["type"],
                    "version": event["schema_version"],
                    "operation": payload.get("operation"),
                    "request": payload.get(
                        "request_correlation_id", payload.get("correlation_id")
                    ),
                    "kind": payload.get("kind"),
                    "run_sha256": payload.get("run_sha256"),
                    "slot": payload.get("slot"),
                }
            )
        return {
            "control": self.control,
            "acceptance_eligible": False,
            "attempts": self.attempts,
            "applications": self.applications,
            "process_memory": self.memory,
            "memory_scope": "instrumented synthetic server, including trace buffers",
            "measured_event_sequence": sequence,
        }


def projection_snapshot(connection):
    """Exact logical rows of every user table; no wall-clock normalization."""
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        name: sorted(
            [
                list(row)
                for row in connection.execute(
                    'SELECT * FROM "' + name.replace('"', '""') + '"'
                )
            ],
            key=lambda row: json.dumps(row, sort_keys=True),
        )
        for name in tables
    }
