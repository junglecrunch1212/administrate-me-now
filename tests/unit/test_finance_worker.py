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
        pipe, {"kind": "done", "cpu_seconds": bad, "rss_bytes": 1, "peak_rss_bytes": 1}
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
                conn, {"kind": "sql", "sql": sql, "parameters": []}, read_only=False
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
