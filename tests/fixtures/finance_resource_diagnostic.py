"""Synthetic-only open-loop load and owned-process resource diagnostics.

No production imports, process-name searches, ambient service discovery or
acceptance thresholds. Linux procfs reads are rooted at the explicitly spawned
runtime PID and its OS-reported descendants; load generators are excluded.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import httpx


FULL_PHASES = (
    ("cold", 1, 1, 1),
    ("warmup", 30, 2, 1),
    ("baseline_before", 60, 2, 0),
    ("normal", 900, 2, 0.2),
    ("baseline_after", 60, 2, 0),
    ("burst_baseline_before", 25, 20, 0),
    ("burst", 30, 20, 10),
    ("burst_baseline_after", 25, 20, 0),
)
SMOKE_PHASES = tuple(
    (name, 1, 4, 2 if finance else 0) for name, _, _, finance in FULL_PHASES
)
TRACE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "synthetic_http_trace", default=None
)


def append_record(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()


def exception_chain(exc):
    result = []
    seen = set()
    while exc is not None and id(exc) not in seen and len(result) < 4:
        seen.add(id(exc))
        result.append(
            {
                "type": type(exc).__name__,
                "message": str(exc)[:250],
                "errno": getattr(exc, "errno", None),
            }
        )
        exc = exc.__cause__ or exc.__context__
    return result


def quantiles(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
        "p99": ordered[math.ceil(len(ordered) * 0.99) - 1] if ordered else None,
        "max": ordered[-1] if ordered else None,
        "method": "nearest_rank",
        "tail_caution": len(ordered) < 1000,
    }


def summarize_requests(records: list[dict]) -> dict:
    return {
        "outcomes": dict(Counter(row["outcome"] for row in records)),
        "scheduled_count": len(records),
        "success_latency_ms": quantiles(
            [row["latency_ms"] for row in records if row["outcome"] == "success"]
        ),
        "attempt_latency_ms_including_errors": quantiles(
            [row["latency_ms"] for row in records if row.get("latency_ms") is not None]
        ),
        "queue_ms": quantiles(
            [row["queue_ms"] for row in records if row.get("queue_ms") is not None]
        ),
        "scheduled_to_terminal_ms_including_rejections": quantiles(
            [row["terminal_ms"] for row in records]
        ),
        "by_operation": {
            operation: {
                "outcomes": dict(Counter(row["outcome"] for row in rows)),
                "latency_ms_including_errors": quantiles(
                    [
                        row["latency_ms"]
                        for row in rows
                        if row.get("latency_ms") is not None
                    ]
                ),
            }
            for operation in sorted({row["operation"] for row in records})
            for rows in [[row for row in records if row["operation"] == operation]]
        },
    }


async def response_trace(response: httpx.Response) -> None:
    trace = TRACE.get()
    if trace is not None:
        trace["http_status"] = response.status_code
        value = response.headers.get("x-synthetic-server-us")
        trace["server_ms"] = int(value) / 1000 if value is not None else None


async def run_phase(
    client,
    call,
    *,
    phase: str,
    start: float,
    duration: float,
    rate: float,
    operation,
    output: Path,
    concurrency: int = 4,
    queue_limit: int = 64,
    drain_seconds: float = 15,
) -> dict:
    """Fixed arrival times; queue/overload/errors are retained, never retried."""
    expected = math.ceil(duration * rate)
    queue: asyncio.Queue = asyncio.Queue(maxsize=queue_limit)
    records: dict[int, dict] = {}

    def finish(row, outcome, **extra):
        if row["index"] in records:
            return
        now = time.monotonic()
        row = {
            **row,
            **extra,
            "outcome": outcome,
            "terminal_at": now,
            "terminal_ms": (now - row["scheduled_at"]) * 1000,
        }
        append_record(output, row)
        records[row["index"]] = row

    async def worker():
        while True:
            row = await queue.get()
            dispatched = time.monotonic()
            trace = {
                **row,
                "started_at": dispatched,
                "queue_ms": (dispatched - row["scheduled_at"]) * 1000,
            }
            token = TRACE.set(trace)
            try:
                await call(client, row["index"])
                finish(
                    trace, "success", latency_ms=(time.monotonic() - dispatched) * 1000
                )
            except asyncio.CancelledError:
                finish(
                    trace,
                    "drain_cancelled",
                    latency_ms=(time.monotonic() - dispatched) * 1000,
                )
                raise
            except Exception as exc:
                finish(
                    trace,
                    "error",
                    latency_ms=(time.monotonic() - dispatched) * 1000,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                    exception_chain=exception_chain(exc),
                )
            finally:
                TRACE.reset(token)
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    try:
        for index in range(expected):
            scheduled = start + index / rate
            await asyncio.sleep(max(0, scheduled - time.monotonic()))
            row = {
                "phase": phase,
                "index": index,
                "scheduled_at": scheduled,
                "operation": operation(index),
                "latency_ms": None,
                "queue_ms": None,
            }
            try:
                queue.put_nowait(row)
            except asyncio.QueueFull:
                finish(row, "client_queue_rejected")
        await asyncio.sleep(max(0, start + duration - time.monotonic()))
        try:
            await asyncio.wait_for(queue.join(), drain_seconds)
        except TimeoutError:
            pass
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        while not queue.empty():
            finish(queue.get_nowait(), "drain_not_dispatched")
            queue.task_done()
    assert len(records) == expected, "scheduled request evidence is incomplete"
    return {
        "phase": phase,
        "expected_count": expected,
        "finished_at": time.monotonic(),
        **summarize_requests(list(records.values())),
    }


def load_client(
    url: str, kind: str, actor: str, session: str, planning, control, output: Path
) -> None:
    """Separate core/finance generators, each persistent across fixed phases."""
    from tests.acceptance.test_finance_mixed_load import HEADERS, _client, _finance_call

    async def main():
        async with _client(url) as client:
            client.event_hooks["response"] = [response_trace]
            control.send({"ready": True})
            phase_number = 0
            while True:
                command = await asyncio.to_thread(control.recv)
                if command is None:
                    return
                phase_number += 1

                async def call(connection, index):
                    if kind == "finance":
                        await _finance_call(
                            connection,
                            actor,
                            session,
                            phase_number * 100000 + index,
                            planning,
                        )
                    else:
                        route = (
                            "/api/core/v1/what-now"
                            if index % 2 == 0
                            else "/api/core/v1/commitments/active"
                        )
                        response = await connection.get(route, headers=HEADERS)
                        assert response.status_code == 200, response.text
                        assert isinstance(response.json(), (dict, list))

                def operation(index):
                    if kind == "core":
                        return "what_now" if index % 2 == 0 else "commitments"
                    if planning is not None:
                        return (
                            "planning_detail" if index % 2 == 0 else "planning_summary"
                        )
                    return "calculate" if index % 2 == 0 else "status"

                control.send(
                    await run_phase(
                        client,
                        call,
                        operation=operation,
                        output=output,
                        concurrency=4 if kind == "core" else 2,
                        **command,
                    )
                )

    try:
        asyncio.run(main())
    except BaseException as exc:
        append_record(
            output.parent / f"{kind}-fatal.jsonl",
            {"error_type": type(exc).__name__, "error": str(exc)[:500]},
        )
        raise
    finally:
        control.close()


def parse_stat(text: str, *, ticks: int, page_size: int) -> dict:
    # comm can contain spaces and ')'; fields after the last ')' begin at #3.
    values = text[text.rindex(")") + 2 :].split()
    return {
        "pid": int(text.split(" ", 1)[0]),
        "ppid": int(values[1]),
        "start_ticks": int(values[19]),
        "self_cpu_seconds": (int(values[11]) + int(values[12])) / ticks,
        "waited_child_cpu_seconds": (int(values[13]) + int(values[14])) / ticks,
        "rss_bytes": int(values[21]) * page_size,
    }


class OwnedProcessTree:
    """Read only a passed child PID and child lists beneath its threads.

    CPU includes all threads, plus already waited-for descendants, and live
    descendants' own/waited CPU. Samples are not atomic. PID reuse, disappearing
    members and counter regressions are recorded as gaps, never silently zeroed.
    Summed RSS double-counts shared pages; it is not PSS or a cgroup peak.
    """

    def __init__(
        self, pid: int, *, proc: Path = Path("/proc"), ticks=None, page_size=None
    ):
        self.pid = pid
        self.proc = proc
        self.ticks = ticks or os.sysconf("SC_CLK_TCK")
        self.page_size = page_size or os.sysconf("SC_PAGE_SIZE")
        self.identities: dict[int, int] = {}

    def _read(self, pid):
        row = parse_stat(
            (self.proc / str(pid) / "stat").read_text(),
            ticks=self.ticks,
            page_size=self.page_size,
        )
        if row["pid"] != pid:
            raise ValueError("proc PID mismatch")
        previous = self.identities.setdefault(pid, row["start_ticks"])
        if previous != row["start_ticks"]:
            raise ValueError("owned process PID was reused")
        return row

    def sample(self, phase: str) -> dict:
        rows: dict[int, dict] = {}
        errors = []
        queue: list[tuple[int, int | None]] = [(self.pid, None)]
        while queue:
            pid, parent = queue.pop()
            if pid in rows:
                continue
            try:
                row = self._read(pid)
                if parent is not None and row["ppid"] != parent:
                    raise ValueError("descendant parent changed")
                rows[pid] = row
                tasks = self.proc / str(pid) / "task"
                for task in tasks.iterdir():
                    for child in (task / "children").read_text().split():
                        queue.append((int(child), pid))
            except (OSError, ValueError) as exc:
                errors.append(
                    {
                        "pid": pid,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:200],
                    }
                )
        return {
            "at": time.monotonic(),
            "phase": phase,
            "root_pid": self.pid,
            "processes": list(rows.values()),
            "errors": errors,
            "cpu_seconds": sum(
                row["self_cpu_seconds"] + row["waited_child_cpu_seconds"]
                for row in rows.values()
            )
            if not errors
            else None,
            "rss_bytes": sum(row["rss_bytes"] for row in rows.values())
            if not errors
            else None,
        }


def summarize_resources(samples: list[dict]) -> dict:
    intervals = []
    invalid = []
    for before, after in zip(samples, samples[1:]):
        if before["phase"] != after["phase"]:
            continue
        if before["cpu_seconds"] is None or after["cpu_seconds"] is None:
            invalid.append({"at": after["at"], "reason": "collection_gap"})
            continue
        seconds = after["at"] - before["at"]
        cpu = after["cpu_seconds"] - before["cpu_seconds"]
        if seconds <= 0 or cpu < 0:
            invalid.append({"at": after["at"], "reason": "counter_regression_or_clock"})
            continue
        intervals.append(
            {
                "at": after["at"],
                "seconds": seconds,
                "cpu_seconds": cpu,
                "logical_core_equivalents": cpu / seconds,
            }
        )
    rss = [row["rss_bytes"] for row in samples if row["rss_bytes"] is not None]
    duration = sum(row["seconds"] for row in intervals)
    cpu = sum(row["cpu_seconds"] for row in intervals)
    return {
        "samples": len(samples),
        "invalid_intervals": invalid,
        "collection_errors": sum(len(row["errors"]) for row in samples),
        "measured_seconds": duration,
        "cpu_seconds": cpu,
        "average_logical_core_equivalents": cpu / duration if duration else None,
        "rss_bytes": quantiles(rss),
        "rss_first_bytes": rss[0] if rss else None,
        "rss_last_bytes": rss[-1] if rss else None,
        "rss_growth_bytes": rss[-1] - rss[0] if rss else None,
        "intervals": intervals,
    }


def compare_phases(results: list[dict]) -> dict:
    """Absolute and baseline deltas only; no proposed budget becomes a gate."""
    phases = {row["name"]: row for row in results}
    comparisons = {}
    for name, before, after in (
        ("normal", "baseline_before", "baseline_after"),
        ("burst", "burst_baseline_before", "burst_baseline_after"),
    ):
        if not all(key in phases for key in (name, before, after)):
            continue
        loaded = phases[name]
        baselines = [phases[before], phases[after]]
        comparison = {
            "acceptance_eligible": False,
            "baseline_rule": "larger_bracketing_baseline",
        }
        for quantile in ("p95", "p99"):
            values = [
                row["core"]["attempt_latency_ms_including_errors"][quantile]
                for row in baselines
            ]
            actual = loaded["core"]["attempt_latency_ms_including_errors"][quantile]
            comparison["core_" + quantile + "_ms"] = actual
            comparison["added_core_" + quantile + "_ms"] = (
                actual - max(values)
                if actual is not None and all(value is not None for value in values)
                else None
            )
        for field, label in (
            ("average_logical_core_equivalents", "cpu_logical_core_equivalents"),
            ("rss_first_bytes", "rss_first_bytes"),
        ):
            values = [row["resources"][field] for row in baselines]
            actual = loaded["resources"][field]
            comparison["added_" + label] = (
                actual - max(values)
                if actual is not None and all(value is not None for value in values)
                else None
            )
        peaks = [row["resources"]["rss_bytes"]["max"] for row in baselines]
        peak = loaded["resources"]["rss_bytes"]["max"]
        comparison["added_peak_rss_bytes"] = (
            peak - max(peaks)
            if peak is not None and all(value is not None for value in peaks)
            else None
        )
        comparisons[name] = comparison
    return comparisons
