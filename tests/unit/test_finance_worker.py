"""Bounded child protocol and parent-owned transaction/cleanup contracts."""

import io
import math
import sqlite3
import struct
from types import SimpleNamespace

import pytest

from adminme.finance import worker, worker_protocol
from adminme.finance.publication import PublicationError
from adminme.finance.worker_protocol import FRAME_LIMIT, read_frame, write_json


@pytest.mark.parametrize(
    "wire",
    [
        b"",
        b"\x00\x00",
        struct.pack("!I", 10) + b"short",
        struct.pack("!I", FRAME_LIMIT + 1),
    ],
)
def test_pipe_rejects_truncation_and_oversize_before_decoding(wire):
    with pytest.raises(ValueError):
        read_frame(io.BytesIO(wire))


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1, True, "1"])
def test_nonfinite_or_nonnumeric_child_usage_is_rejected(bad):
    pipe = io.BytesIO()
    write_json(
        pipe,
        {
            "kind": "done",
            "cpu_seconds": bad,
            "user_cpu_seconds": 0,
            "system_cpu_seconds": 0,
            "rss_bytes": 1,
            "peak_rss_bytes": 1,
        },
    )
    pipe.seek(0)
    child = object.__new__(worker.FinanceWorker)
    child.process = SimpleNamespace(stdout=pipe)
    child.done = False
    with pytest.raises(PublicationError, match="usage_invalid"):
        child.receive()
    assert not child.done


@pytest.mark.parametrize(
    "sql",
    [
        "COMMIT",
        "ROLLBACK",
        "BEGIN",
        "SAVEPOINT escape",
        "PRAGMA journal_mode=OFF",
        "ATTACH ':memory:' AS escaped",
        "DROP TABLE finance_publications",
        "CREATE TABLE escaped(value)",
        "SELECT value FROM authority",
        "SELECT randomblob(100000000)",
        "DELETE FROM finance_publications",
    ],
)
def test_child_cannot_escape_parent_transaction_or_money_scope(sql):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE finance_publications(value)")
    conn.execute("CREATE TABLE authority(value)")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO finance_publications VALUES ('pending')")
    try:
        with pytest.raises((sqlite3.DatabaseError, PublicationError)):
            worker._sql(
                conn,
                {"kind": "sql", "sql": sql, "parameters": []},
                read_only=False,
                scope=("tenant", "owner", "book"),
            )
        assert conn.in_transaction
        conn.execute("ROLLBACK")
        assert conn.execute("SELECT * FROM finance_publications").fetchall() == []
    finally:
        conn.close()


def test_parent_rpc_bounds_rows_and_output_bytes_before_json_encoding():
    sql = "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?"
    request = {"kind": "sql", "sql": sql, "parameters": ["tenant", "owner", "book"]}

    class Cursor:
        description = [("publication_id",)]

        def __init__(self, rows):
            self.rows, self.fetched = iter(rows), 0

        def fetchone(self):
            self.fetched += 1
            return next(self.rows, None)

        def close(self):
            pass

    class Connection:
        def __init__(self, rows):
            self.cursor = Cursor(rows)

        def execute(self, *_):
            return self.cursor

        def set_authorizer(self, _):
            pass

    conn = Connection(([x] for x in range(100000)))
    with pytest.raises(PublicationError, match="query_limit"):
        worker._sql(conn, request, read_only=True, scope=("tenant", "owner", "book"))
    assert conn.cursor.fetched == 1025
    conn = Connection([["x" * (FRAME_LIMIT // 6)]])
    with pytest.raises(PublicationError, match="query_limit"):
        worker._sql(conn, request, read_only=True, scope=("tenant", "owner", "book"))
    conn = Connection([["bounded"]])
    assert worker._sql(
        conn, request, read_only=True, scope=("tenant", "owner", "book")
    )["rows"] == [["bounded"]]
    with pytest.raises(PublicationError, match="scope_denied"):
        worker._sql(
            conn, request, read_only=True, scope=("tenant", "different-owner", "book")
        )


@pytest.fixture
def timer_references(monkeypatch):
    """Finished Timer double retains its callback, as threading.Timer does."""
    import subprocess
    import weakref

    timers, owners, cancellations = [], [], []
    state = SimpleNamespace(start_failure=None)

    class Timer:
        def __init__(self, delay, callback):
            self.function = callback
            timers.append(weakref.ref(self))
            owners.append(weakref.ref(callback.__self__))

        def start(self):
            if state.start_failure is not None:
                raise state.start_failure

        def cancel(self):
            cancellations.append(True)

    class Process:
        pid = 123
        returncode = None
        can_reap = True
        terminated = False

        def __init__(self):
            self.stdin, self.stdout, self.stderr = (
                io.BytesIO(),
                io.BytesIO(),
                io.BytesIO(),
            )

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout):
            if not self.can_reap:
                raise subprocess.TimeoutExpired("synthetic-owned-child", timeout)
            self.returncode = -15 if self.terminated else 0
            return self.returncode

    process = Process()
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(worker.threading, "Timer", Timer)
    yield SimpleNamespace(
        process=process,
        timers=timers,
        owners=owners,
        cancellations=cancellations,
        state=state,
    )
    # On an assertion failure, still release synthetic admission without hiding it.
    process.can_reap = True
    state.start_failure = None
    for ref in owners:
        if (child := ref()) is not None and not child.closed:
            child.close()


def test_closed_worker_releases_cancelled_timer_without_gc(timer_references):
    probe = timer_references
    child = worker.FinanceWorker("reference-normal", timeout=10)
    assert probe.timers[0]() is not None
    child.done = True
    child.close()
    # Keep the worker alive: GC cannot collect a timer still owned by it.
    assert probe.owners[0]() is child and child.closed
    assert probe.timers[0]() is None
    assert len(probe.cancellations) == 1 and probe.process.returncode == 0
    assert all(
        p.closed
        for p in (probe.process.stdin, probe.process.stdout, probe.process.stderr)
    )
    assert worker.worker_usage()["active"] is None and not worker._admission.locked()
    child.close()
    assert len(probe.cancellations) == 1


def test_timer_start_failure_preserves_error_and_reaps_child(timer_references):
    probe = timer_references
    original = RuntimeError("synthetic timer start failure")
    probe.state.start_failure = original
    with pytest.raises(RuntimeError) as caught:
        worker.FinanceWorker("reference-start-failure", timeout=10)
    assert caught.value is original
    # The retained exception intentionally owns its traceback; test owner release,
    # not disappearance of objects still legitimately referenced by that traceback.
    child = probe.owners[0]()
    assert child.closed and child.timer is None and child._retry_timer is None
    assert probe.process.returncode == -15 and len(probe.cancellations) == 1
    assert all(
        p.closed
        for p in (probe.process.stdin, probe.process.stdout, probe.process.stderr)
    )
    assert worker.worker_usage()["active"] is None and not worker._admission.locked()


def test_retry_callback_releases_timer_ownership_without_self_join(timer_references):
    probe = timer_references
    child = worker.FinanceWorker("reference-retry", timeout=10)
    probe.process.can_reap = False
    with pytest.raises(PublicationError, match="reap_uncertain"):
        child.close()
    assert child.timer is None
    assert worker._admission.locked() and not child.closed
    assert worker.worker_usage()["active"]["reap_uncertain"]
    # Like Timer.run, this callable retains the worker through its own reference.
    callback = probe.timers[1]().function
    probe.process.can_reap = True
    callback()
    assert child.closed and probe.timers[1]() is None
    assert callback.__self__ is child and len(probe.cancellations) == 2
    assert not worker._admission.locked() and worker.worker_usage()["active"] is None
    assert not worker.worker_usage()["lifetimes"][-1]["complete"]


def test_timeout_abort_overlaps_close_reference_release(timer_references, monkeypatch):
    import threading

    probe = timer_references
    before = worker.worker_usage()
    child = worker.FinanceWorker("reference-overlap", timeout=10)
    entered, release, cancelled = (threading.Event() for _ in range(3))
    errors = []
    original_wait = probe.process.wait

    def blocked_wait(timeout):
        # abort owns the process lock before entering this synthetic child wait.
        entered.set()
        assert release.wait(5), "test did not release the timeout callback"
        return original_wait(timeout)

    def cancel(timer):
        assert child.timer is None
        probe.cancellations.append(True)
        cancelled.set()

    def invoke(function):
        try:
            function()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(probe.process, "wait", blocked_wait)
    monkeypatch.setattr(type(probe.timers[0]()), "cancel", cancel)
    # Like an entered Timer.run, the thread keeps its callback alive independently
    # of the worker's timer field. Only the test threads use bounded joins.
    timeout_thread = threading.Thread(
        target=invoke, args=(probe.timers[0]().function,), daemon=True
    )
    close_thread = threading.Thread(target=invoke, args=(child.close,), daemon=True)
    started = []
    try:
        timeout_thread.start()
        started.append(timeout_thread)
        assert entered.wait(2), "timeout callback did not enter abort"
        assert child.timed_out and child._process_lock.locked()
        close_thread.start()
        started.append(close_thread)
        assert cancelled.wait(2), "close did not clear and cancel the timer"
        assert child.timer is None and child._retry_timer is None
        assert child._close_lock.locked() and close_thread.is_alive()
        assert timeout_thread.is_alive() and not child.closed
        assert worker._admission.locked() and probe.process.returncode is None
        assert worker.worker_usage()["active"]["operation"] == "reference-overlap"
        assert not child._quarantined
    finally:
        # Release and join even when an ordering assertion fails; the fixture then
        # closes any unfinished synthetic child without hiding the original error.
        release.set()
        for thread in started:
            thread.join(2)
        assert all(not thread.is_alive() for thread in started)
    assert not errors, errors
    assert child.closed and child._reap_attempts == 1 and not child._quarantined
    assert probe.timers[0]() is None and len(probe.cancellations) == 1
    assert child.timer is None and child._retry_timer is None
    assert probe.process.returncode == -15
    assert all(
        p.closed
        for p in (probe.process.stdin, probe.process.stdout, probe.process.stderr)
    )
    assert not worker._admission.locked()
    after = worker.worker_usage()
    assert after["active"] is None
    assert after["completed_count"] == before["completed_count"] + 1
    assert after["failed_count"] == before["failed_count"] + 1
    assert after["completed_cpu_seconds"] == before["completed_cpu_seconds"]
    assert after["lifetimes"][-1]["timed_out"]
    assert not after["lifetimes"][-1]["complete"]
    child.close()
    assert worker.worker_usage() == after and len(probe.cancellations) == 1


@pytest.mark.parametrize("failure", ["unknown_operation", "oversize", "timeout"])
def test_owned_child_failure_closes_process_and_releases_admission(failure):
    child = worker.FinanceWorker("test", timeout=0.05 if failure == "timeout" else 10)
    try:
        with pytest.raises(PublicationError, match="worker_busy"):
            worker.FinanceWorker("second", timeout=1)
        if failure == "unknown_operation":
            child.send({"operation": "unsupported"})
        elif failure == "oversize":
            child.process.stdin.write(struct.pack("!I", 65537))
            child.process.stdin.flush()
        with pytest.raises((PublicationError, ValueError)):
            child.receive()
    finally:
        child.close()
    assert child.process.poll() is not None and child.closed
    assert worker.worker_usage()["active"] is None
    assert not worker._admission.locked()
    # The next admitted lifetime also has an owned, observable terminal exit.
    next_child = worker.FinanceWorker("test", timeout=10)
    try:
        next_child.send({"operation": "unsupported"})
        with pytest.raises(PublicationError):
            next_child.receive()
    finally:
        next_child.close()
    assert next_child.process.poll() is not None


def test_untrusted_receipt_never_reaches_the_log():
    from dataclasses import replace
    import hashlib

    forged = worker.ValidatedFinanceEvent(
        "tenant",
        "owner",
        "book",
        "finance.publication_accepted",
        "accepted_books",
        "key",
        "{}",
        hashlib.sha256(b"{}").hexdigest(),
        b"forged",
    )
    with pytest.raises(PublicationError, match="receipt_invalid"):
        forged.verify()
    valid = replace(forged, _receipt=forged._signature())
    valid.verify()
    for changed in (
        replace(valid, owner_id="another-owner"),
        replace(valid, type="finance.provenance_archived"),
        replace(valid, payload_json='{"changed":true}'),
    ):
        with pytest.raises(PublicationError, match="receipt_invalid"):
            changed.verify()


@pytest.mark.parametrize("failure", ["terminate", "kill", "wait"])
@pytest.mark.parametrize("exhaust", [False, True])
def test_uncertain_reap_keeps_admission_quarantined_until_confirmed(
    monkeypatch, failure, exhaust
):
    import subprocess

    timers = []

    class Timer:
        def __init__(self, delay, callback):
            self.callback = callback
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    class Process:
        pid = 123
        returncode = None
        reaped = False
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def poll(self):
            if self.reaped:
                self.returncode = -15
            return self.returncode

        def terminate(self):
            if failure == "terminate":
                raise OSError("synthetic signal failure")

        def kill(self):
            if failure == "kill":
                raise OSError("synthetic kill failure")

        def wait(self, timeout):
            if not self.reaped:
                raise subprocess.TimeoutExpired("owned synthetic child", timeout)
            self.returncode = -15
            return -15

    process = Process()
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(worker.threading, "Timer", Timer)
    child = worker.FinanceWorker("synthetic-reap", timeout=10)
    try:
        with pytest.raises(PublicationError, match="reap_uncertain"):
            child.close()
        assert not child.closed and worker._admission.locked()
        assert worker.worker_usage()["active"]["reap_uncertain"]
        with pytest.raises(PublicationError, match="worker_busy"):
            worker.FinanceWorker("must-not-launch", timeout=10)
        if exhaust:
            for _ in range(3):
                timers[-1].callback()
            assert child._reap_attempts == 4 and len(timers) == 4
            assert worker._admission.locked() and not child.closed
        process.reaped = True
        # A bounded retry or later explicit cleanup confirms the owned exit.
        timers[-1].callback() if not exhaust else child.close()
        assert child.closed and not worker._admission.locked()
        assert worker.worker_usage()["active"] is None
        assert not worker.worker_usage()["lifetimes"][-1]["complete"]
    finally:
        process.reaped = True
        child.close()


@pytest.mark.parametrize(
    "event,args",
    [
        ("open", ("/private/household/state.sqlite", "r", 0)),
        ("open", ("/Library/Keychains/System.keychain", "r", 0)),
        ("open", (__file__, "w", 1)),
        ("socket.connect", (None, ("127.0.0.1", 49152))),
        ("subprocess.Popen", ("anything",)),
        ("os.fork", ()),
        ("os.remove", ("anything",)),
        ("os.putenv", ("HOME", "anything")),
    ],
)
def test_calculation_guard_denies_private_io_network_and_descendants(event, args):
    from adminme.finance.worker_guard import CalculationGuard

    guard = CalculationGuard()
    with pytest.raises(PermissionError, match="worker_io_denied"):
        guard(event, args)  # Validation only; never perform the forbidden action.
    assert guard.fault_count == 1


def test_calculation_guard_allows_only_readonly_trusted_code():
    from adminme.finance.worker_guard import CalculationGuard

    guard = CalculationGuard()
    guard("open", (__file__, "r", 0))
    assert guard.fault_count == 0


@pytest.mark.parametrize("mode", ["blocked_input", "blocked_output"])
def test_owned_child_exits_after_abrupt_parent_death_in_hosted_linux(mode, request):
    import json
    from pathlib import Path
    import subprocess
    import sys

    if sys.platform != "linux":
        pytest.skip("owned Linux subreaper proof; not Darwin crash qualification")
    script = Path(__file__).resolve().parents[1] / "fixtures" / "finance_parent_exit.py"
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(script), "supervisor", mode],
        capture_output=True,
        timeout=18,
        check=True,
    )
    receipt = json.loads(result.stdout)
    request.node.user_properties.append(
        ("owned_parent_exit_receipt", json.dumps(receipt, sort_keys=True))
    )
    if mode == "blocked_output":
        proof = receipt["parent_evidence"]
        assert (
            proof["declared_frame_bytes"]
            > proof["pipe_capacity_bytes"] + io.DEFAULT_BUFFER_SIZE
        )
        assert proof["unread_pipe_bytes"] > 0
    assert receipt["mode"] == mode and receipt["reaped"]
    assert receipt["seconds_after_parent_exit"] < 5


@pytest.mark.parametrize(
    "text",
    ["", "a" * 200_000, "aé🙂" * 70_000],
    ids=["empty", "ascii-multiple-chunks", "unicode-multiple-chunks"],
)
def test_chunked_text_frame_and_hash_preserve_every_utf8_byte(text):
    import hashlib

    class RecordingPipe(io.BytesIO):
        def __init__(self):
            super().__init__()
            self.largest_write = 0

        def write(self, data):
            self.largest_write = max(self.largest_write, len(data))
            return super().write(data)

    pipe = RecordingPipe()
    child = object.__new__(worker.FinanceWorker)
    child.process = SimpleNamespace(stdin=pipe)
    child.send_text(text)
    assert pipe.largest_write <= worker_protocol.UPLOAD_CHUNK
    pipe.seek(0)
    assert read_frame(pipe) == text.encode("utf-8")
    assert (
        worker_protocol.text_sha256(text) == hashlib.sha256(text.encode()).hexdigest()
    )


@pytest.mark.parametrize(
    "text",
    ["a" * 65, "🙂" * 17, "a" + chr(0xD800)],
    ids=["ascii-oversize", "unicode-oversize", "invalid-surrogate"],
)
def test_text_frame_rejects_invalid_or_oversize_before_writing_header(
    monkeypatch, text
):
    monkeypatch.setattr(worker_protocol, "FRAME_LIMIT", 64)
    pipe = io.BytesIO()
    with pytest.raises((ValueError, UnicodeEncodeError)):
        worker_protocol.write_text_frame(pipe, text)
    assert pipe.getvalue() == b""


def test_receipt_hash_checks_changes_after_multiple_utf8_chunks():
    from dataclasses import replace
    import hashlib

    payload = '{"value":"' + "aé🙂" * 70_000 + '"}'
    event = worker.ValidatedFinanceEvent(
        "tenant",
        "owner",
        "book",
        "finance.publication_accepted",
        "accepted_books",
        "key",
        payload,
        hashlib.sha256(payload.encode()).hexdigest(),
        b"",
    )
    event = replace(event, _receipt=event._signature())
    event.verify()
    changed = replace(event, payload_json=payload[:-3] + 'changed"}')
    with pytest.raises(PublicationError, match="receipt_invalid"):
        changed.verify()
