"""Bounded child protocol and parent-owned transaction/cleanup contracts."""

import io
import math
import sqlite3
import struct
from types import SimpleNamespace

import pytest

from adminme.finance import worker
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
