"""Focused integrity equivalence and Python-allocation diagnostics."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import resource
import secrets
import sys
import time
import tracemalloc

import pytest

from adminme.events.log import EventLog
from tests.fixtures.finance_staging import append_staged
from tests.fixtures.finance_staging_store import CHUNK, StageError
from tests.unit.test_finance_encrypted_staging import event_count, sealed
from tests.unit.test_finance_encrypted_staging import stage as stage

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_SYNTHETIC_STAGING_CORRECTNESS") != "1"
    or sys.platform != "linux",
    reason="exact admitted hosted integrity cohort only",
)


@pytest.mark.parametrize("kind", ["component", "event"])
@pytest.mark.parametrize("size", [1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK + 17])
def test_body_and_nonmaterializing_verification_are_equivalent(stage, kind, size):
    raw, metadata = b"x" * size, {"type": "synthetic", "ordinal": 0}
    stage.put(kind, "one", raw, metadata)
    assert stage.read(kind, "one") == (raw, metadata)
    assert stage.verify(kind, "one") == metadata
    before = stage.headers(stage.conn)
    assert stage.inventory(stage.conn) == before
    assert before[0][2:4] == [size, hashlib.sha256(raw).hexdigest()]


@pytest.mark.parametrize(
    "fault",
    [
        "first",
        "middle",
        "last",
        "truncated",
        "ordinal",
        "extra",
        "metadata",
        "mac",
        "context",
    ],
)
def test_verification_preserves_read_rejection_semantics(stage, fault):
    stage.put("component", "one", b"x" * (3 * CHUNK + 17), {"type": "synthetic"})
    if fault in {"first", "middle", "last", "truncated"}:
        ordinal = {"first": 0, "middle": 1, "last": 3, "truncated": 3}[fault]
        length = 16 if fault == "truncated" else 17 if ordinal == 3 else CHUNK
        stage.conn.execute(
            "UPDATE chunks SET body=zeroblob(?) WHERE ordinal=?", (length, ordinal)
        ).close()
    elif fault == "ordinal":
        stage.conn.execute("UPDATE chunks SET ordinal=8 WHERE ordinal=1").close()
    elif fault == "extra":
        stage.conn.execute(
            "INSERT INTO chunks VALUES('component','one',4,?)", (b"x",)
        ).close()
    elif fault == "metadata":
        # Authenticated but malformed metadata must still reach json.loads.
        header = stage.headers(stage.conn)[0]
        header[4] = "{"
        stage.conn.execute(
            "UPDATE objects SET metadata=?,mac=?", (header[4], stage.mac(header[:5]))
        ).close()
    elif fault == "mac":
        stage.conn.execute("UPDATE objects SET mac=?", ("0" * 64,)).close()
    else:
        stage.binding = "0" * 64
    failures = []
    for method in (stage.read, stage.verify):
        with pytest.raises((StageError, json.JSONDecodeError)) as error:
            method("component", "one")
        failures.append((type(error.value), str(error.value)))
    assert failures[0] == failures[1]
    with pytest.raises((StageError, json.JSONDecodeError)):
        stage.inventory(stage.conn)


@pytest.mark.parametrize("method", ["read", "verify"])
@pytest.mark.parametrize("error_type", [OSError, asyncio.CancelledError])
def test_chunk_cursor_closes_on_error_or_cancellation(stage, method, error_type):
    raw = b"x" * (2 * CHUNK + 17)
    stage.put("component", "one", raw)
    observed = []

    class InterruptedCursor:
        def __init__(self, cursor):
            self.cursor, self.closed = cursor, False
            observed.append(self)

        def __iter__(self):
            yield next(iter(self.cursor))
            raise error_type("synthetic chunk interruption")

        def close(self):
            self.cursor.close()
            self.closed = True

    class Connection:
        def execute(self, sql, parameters=()):
            cursor = stage.conn.execute(sql, parameters)
            return (
                InterruptedCursor(cursor)
                if sql.startswith("SELECT ordinal,")
                else cursor
            )

    with pytest.raises(error_type):
        getattr(stage, method)("component", "one", Connection())
    assert len(observed) == 1 and observed[0].closed
    assert stage.read("component", "one") == (raw, {})


@pytest.mark.parametrize("fault", ["io", "corruption"])
async def test_streaming_final_integrity_failure_rolls_back(stage, monkeypatch, fault):
    events = sealed(stage)
    log = EventLog(stage.path.parent / "streaming-events.db", secrets.token_bytes(32))
    check, verify = stage.check_seal, stage.verify
    passes, guards, inserted_before_failure = [], [], []

    def checking(conn):
        passes.append(len(passes) + 1)
        return check(conn)

    def verifying(kind, name, conn=None):
        if len(passes) == 3 and kind == "component":
            # Complete staged validation, then pre-BEGIN check, then final check.
            # The final integrity check runs on the EventLog connection thread.
            inserted_before_failure.append(event_count(log))
            if fault == "io":
                raise OSError("synthetic final integrity I/O")
            writer = stage.connect()
            try:
                writer.execute(
                    "UPDATE chunks SET body=zeroblob(length(body)) WHERE kind='component'"
                ).close()
            finally:
                writer.close()
        return verify(kind, name, conn)

    monkeypatch.setattr(stage, "check_seal", checking)
    monkeypatch.setattr(stage, "verify", verifying)
    try:
        with pytest.raises((OSError, StageError)):
            await append_staged(
                log,
                ("synthetic-tenant", "synthetic-owner", "synthetic-book"),
                events,
                lambda: guards.append(len(guards) + 1),
                None,
            )
        assert passes == [1, 2, 3] and inserted_before_failure == [2]
        assert guards == [1]  # Integrity fails before the final authority guard.
        assert await log._run_on_connection_thread(event_count, log) == 0
        assert (
            await log._run_on_connection_thread(lambda: log._conn.in_transaction)
            is False
        )
    finally:
        await log.close()


def process_usage():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "rss_bytes": int(Path("/proc/self/statm").read_text().split()[1])
        * os.sysconf("SC_PAGE_SIZE"),
        "process_lifetime_peak_rss_bytes": usage.ru_maxrss * 1024,
        "process_cpu_seconds": usage.ru_utime + usage.ru_stime,
    }


def allocated_call(function):
    assert not tracemalloc.is_tracing()
    before = process_usage()
    tracemalloc.start(1)
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        started = time.monotonic()
        result = function()
        elapsed = time.monotonic() - started
        current, peak = tracemalloc.get_traced_memory()
        tracer_bytes = tracemalloc.get_tracemalloc_memory()
        after = process_usage()
        return result, {
            "wall_seconds_with_tracer": elapsed,
            "traced_baseline_bytes": baseline,
            "traced_live_after_bytes": current,
            "traced_peak_bytes": peak,
            "traced_peak_above_baseline_bytes": peak - baseline,
            "tracer_internal_bytes_after": tracer_bytes,
            "process_before": before,
            "process_after": after,
        }
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("size", [CHUNK + 17, 4 * 1024 * 1024 + 17])
def test_inventory_python_allocation_is_bounded(stage, monkeypatch, size):
    stage.put("component", "payload", b"x" * size)
    expected = stage.headers(stage.conn)

    def materializing_control():
        # Prior inventory behavior on this same authenticated immutable stage.
        rows = stage.headers(stage.conn)
        cursor = stage.conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM chunks LIMIT 2247)"
        )
        try:
            assert cursor.fetchone()[0] == sum(
                (r[2] + CHUNK - 1) // CHUNK for r in rows
            )
        finally:
            cursor.close()
        for row in rows:
            stage.read(row[0], row[1], stage.conn)
        return rows

    control_result, control = allocated_call(materializing_control)

    def no_materialization(*args, **kwargs):
        raise AssertionError("integrity-only inventory requested a body")

    monkeypatch.setattr(stage, "read", no_materialization)
    stream_result, streaming = allocated_call(lambda: stage.inventory(stage.conn))
    result = {
        "candidate": os.environ["GITHUB_SHA"],
        "pid": os.getpid(),
        "role": "isolated unit-test process; no application runtime or executor child",
        "body_bytes": size,
        "chunk_bytes": CHUNK,
        "control": control,
        "streaming": streaming,
        "equal_authenticated_inventory": control_result == stream_result == expected,
        "integrity_python_peak_bound_bytes": 8 * CHUNK,
        "limitations": [
            "Traced Python allocations are not RSS, SQLCipher/native allocation, or runtime acceptance.",
            "Inputs/imports precede tracing; operations execute once in fixed control-then-streaming order.",
            "RSS endpoints and lifetime peak include prior process activity; no RSS improvement claim.",
            "Tracing changes timing; no forced GC, allocator trim, repeated trial or allocation-stack attribution.",
            "The 512KiB diagnostic buffer bound does not replace the 128MiB/8MiB Finance thresholds.",
        ],
    }
    output = Path(os.environ["FINANCE_STORAGE_ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    (output / f"streaming-allocation-{size}.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    assert result["equal_authenticated_inventory"]
    assert streaming["traced_peak_above_baseline_bytes"] <= 8 * CHUNK
    assert control["traced_peak_above_baseline_bytes"] >= size
