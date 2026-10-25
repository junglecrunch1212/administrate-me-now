"""One-at-a-time, short-lived Finance workers with parent-owned SQL and keys.

Preparation returns immutable encoded events after full closed-proof/schema
validation. Projection workers read actual committed event bytes and use the
existing Money transaction through bounded SQL RPC. No child opens a database.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
import hashlib
import hmac
import secrets
import json
import math
import sqlcipher3
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

from adminme.finance.import_work import await_settled
from adminme.finance.publication import PublicationError
from adminme.finance.worker_statements import MONEY_STATEMENTS
from adminme.finance.worker_protocol import (
    FINANCE_TYPES,
    FRAME_LIMIT,
    UPLOAD_CHUNK,
    read_frame,
    read_json,
    write_frame,
    write_json,
)

_admission = threading.Lock()
_usage_lock = threading.Lock()
_active: dict[str, Any] | None = None
_completed_cpu = 0.0
_completed_count = 0
_failed_count = 0
_peak_child_rss = 0
_lifetimes: list[dict[str, Any]] = []
_RECEIPT_KEY = secrets.token_bytes(32)
PREPARE_SECONDS = 30


@dataclass(frozen=True)
class ValidatedFinanceEvent:
    """Internal immutable receipt; HTTP callers cannot manufacture this value."""

    tenant_id: str
    owner_id: str
    book_id: str
    type: str
    source_adapter: str
    idempotency_key: str
    payload_json: str
    sha256: str
    _receipt: bytes

    def verify(self) -> None:
        if (
            not hmac.compare_digest(self._receipt, self._signature())
            or self.type not in FINANCE_TYPES
            or hashlib.sha256(self.payload_json.encode()).hexdigest() != self.sha256
            or self.source_adapter
            != (
                "accepted_books_projection"
                if self.type == "finance.projection_verified"
                else "accepted_books"
            )
            or len(self.idempotency_key) > 240
        ):
            raise PublicationError("finance_worker_receipt_invalid")

    def _signature(self) -> bytes:
        coordinates = [
            self.tenant_id,
            self.owner_id,
            self.book_id,
            self.type,
            1,
            self.source_adapter,
            self.idempotency_key,
            self.sha256,
        ]
        return hmac.digest(
            _RECEIPT_KEY,
            json.dumps(coordinates, separators=(",", ":")).encode(),
            "sha256",
        )


@dataclass
class PreparedFinanceImport:
    metadata: dict[str, Any]
    events: list[ValidatedFinanceEvent]


def worker_usage() -> dict[str, Any]:
    """Owned-child observations, including completed lifetimes, never other PIDs."""
    with _usage_lock:
        active = dict(_active) if _active is not None else None
        return {
            "active": active,
            "completed_cpu_seconds": _completed_cpu,
            "completed_count": _completed_count,
            "failed_count": _failed_count,
            "peak_child_rss_bytes": _peak_child_rss,
            "lifetimes": [dict(row) for row in _lifetimes],
        }


class FinanceWorker:
    def __init__(self, operation: str, *, timeout: float):
        global _active
        if not _admission.acquire(blocking=False):
            raise PublicationError("finance_worker_busy")
        self.process: subprocess.Popen[bytes] | None = None
        self.done = False
        self.closed = False
        self.timed_out = False
        self.stats: dict[str, Any] = {}
        self._process_lock = threading.Lock()
        self.timer: threading.Timer | None = None
        try:
            self.process = subprocess.Popen(
                [
                    str(Path(sys.executable).absolute()),
                    "-I",
                    "-B",
                    str(Path(__file__).with_name("worker_child.py").resolve()),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                close_fds=True,
            )
            with _usage_lock:
                _active = {
                    "pid": self.process.pid,
                    "operation": operation,
                    "rss_bytes": 0,
                    "cpu_seconds": 0.0,
                    "started_at": time.monotonic(),
                }
            self.timer = threading.Timer(timeout, self._timeout)
            self.timer.daemon = True
            self.timer.start()
        except BaseException:
            self.close()
            raise

    def _timeout(self):
        self.timed_out = True
        self.abort()

    def abort(self):
        with self._process_lock:
            if self.process is not None and self.process.poll() is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)

    def send(self, value):
        assert self.process is not None and self.process.stdin is not None
        write_json(self.process.stdin, value)

    def send_bytes(self, value: bytes):
        assert self.process is not None and self.process.stdin is not None
        write_frame(self.process.stdin, value)

    def receive(self):
        assert self.process is not None and self.process.stdout is not None
        while True:
            message = read_json(self.process.stdout, FRAME_LIMIT)
            if not isinstance(message, dict):
                raise PublicationError("finance_worker_message_invalid")
            kind = message.get("kind")
            if kind in {"usage", "done", "error"}:
                self.stats = {
                    k: message[k]
                    for k in ("cpu_seconds", "rss_bytes", "peak_rss_bytes")
                }
                if any(
                    type(v) not in (float, int)
                    or not math.isfinite(v)
                    or not 0 <= v <= 2**53
                    for v in self.stats.values()
                ):
                    raise PublicationError("finance_worker_usage_invalid")
                with _usage_lock:
                    if _active is not None:
                        _active.update(self.stats)
                if kind == "usage":
                    continue
                if kind == "error":
                    raise PublicationError(
                        str(message.get("reason", "finance_worker_failed"))[:120]
                    )
                self.done = True
            return message

    def raw(self):
        assert self.process is not None and self.process.stdout is not None
        return read_frame(self.process.stdout)

    def close(self):
        global _active, _completed_count, _failed_count, _completed_cpu, _peak_child_rss
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.timer.cancel()
        good = False
        try:
            process = self.process
            if process is not None:
                if self.done:
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.abort()
                else:
                    self.abort()
                good = self.done and process.returncode == 0 and not self.timed_out
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()
        finally:
            with _usage_lock:
                _completed_count += 1
                _failed_count += int(not good)
                _completed_cpu += self.stats.get("cpu_seconds", 0.0)
                _peak_child_rss = max(
                    _peak_child_rss, self.stats.get("peak_rss_bytes", 0)
                )
                _lifetimes.append(
                    {
                        **(_active or {}),
                        **self.stats,
                        "exitcode": self.process.returncode
                        if self.process is not None
                        else None,
                        "terminal_received": self.done,
                        "timed_out": self.timed_out,
                        "complete": good,
                        "finished_at": time.monotonic(),
                    }
                )
                del _lifetimes[:-128]
                _active = None
            _admission.release()
        if self.done and not good:
            raise PublicationError("finance_worker_exit_failed")


async def _owned_call(worker: FinanceWorker, function, *args):
    pending = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(pending)
    except BaseException:
        # Kill only this owned calculation child. Accepted parent SQL settles
        # separately on its owner before any lock or admission is released.
        await await_settled(asyncio.to_thread(worker.abort))
        with suppress(BaseException):
            await await_settled(pending)
        raise


_MONEY_TABLES = frozenset(
    {
        "finance_provenance_blobs",
        "finance_publications",
        "finance_publication_heads",
        "finance_publication_metrics",
        "finance_dated_projections",
        "finance_dated_projection_heads",
        "finance_dated_projection_refs",
        "finance_dated_shared_events",
        "finance_dated_event_bindings",
        "finance_dated_projection_events",
    }
)


def _sql(conn, message, *, read_only, scope):
    kind = message.get("kind")
    sql = message.get("sql")
    parameters = message.get("parameters")
    if (
        kind not in {"sql", "sql_many"}
        or not isinstance(sql, str)
        or len(sql) > 8192
        or not isinstance(parameters, list)
    ):
        raise PublicationError("finance_worker_query_invalid")
    if sql not in MONEY_STATEMENTS:
        raise PublicationError("finance_worker_statement_denied")
    parameter_rows = parameters if kind == "sql_many" else [parameters]
    if any(
        not isinstance(row, (list, tuple)) or tuple(row[:3]) != scope
        for row in parameter_rows
    ):
        raise PublicationError("finance_worker_scope_denied")
    if read_only and (kind != "sql" or not sql.lstrip().upper().startswith("SELECT ")):
        raise PublicationError("finance_worker_read_only")
    if kind == "sql_many" and len(parameters) > 128:
        raise PublicationError("finance_worker_query_limit")

    def authorizer(action, arg1, arg2, database, source):
        del arg2, source
        if action == getattr(sqlcipher3, "SQLITE_SELECT"):
            return getattr(sqlcipher3, "SQLITE_OK")
        actions = {getattr(sqlcipher3, "SQLITE_READ")}
        if not read_only:
            actions.update(
                {
                    getattr(sqlcipher3, "SQLITE_INSERT"),
                    getattr(sqlcipher3, "SQLITE_UPDATE"),
                }
            )
        if database == "main" and action in actions and arg1 in _MONEY_TABLES:
            return getattr(sqlcipher3, "SQLITE_OK")
        # Transactions, functions, extensions, PRAGMAs, ATTACH, DDL and other
        # projection/authority tables are never child capabilities.
        return getattr(sqlcipher3, "SQLITE_DENY")

    conn.set_authorizer(authorizer)
    cursor = None
    try:
        cursor = (
            conn.executemany(sql, parameters)
            if kind == "sql_many"
            else conn.execute(sql, parameters)
        )
        rows = []
        upper_bound = 128
        if cursor.description:
            while (row := cursor.fetchone()) is not None:
                if len(rows) >= 1024:
                    raise PublicationError("finance_worker_query_limit")
                # Bound serialized output before constructing its JSON frame.
                # Six bytes/codepoint covers every JSON string escape.
                for value in row:
                    if isinstance(value, str):
                        upper_bound += 6 * len(value) + 4
                    elif value is None or type(value) in (bool, int, float):
                        upper_bound += 32
                    else:
                        raise PublicationError("finance_worker_query_type")
                if upper_bound > FRAME_LIMIT - 4096:
                    raise PublicationError("finance_worker_query_limit")
                rows.append(list(row))
        return {"kind": "rows", "rows": rows}
    finally:
        if cursor is not None:
            cursor.close()
        conn.set_authorizer(None)


async def prepare_import(request, runtime, session, manifest) -> PreparedFinanceImport:
    worker = FinanceWorker("prepare", timeout=60)
    try:
        await _owned_call(
            worker,
            worker.send,
            {
                "operation": "prepare",
                "manifest": manifest.model_dump(),
                "owner_id": session.auth_member_id,
                "tenant_id": session.tenant_id,
            },
        )
        total = 0
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                total += len(chunk)
                if total > manifest.encoded_body_limit():
                    raise PublicationError("finance_import_body_limit")
                for start in range(0, len(chunk), UPLOAD_CHUNK):
                    await _owned_call(
                        worker, worker.send_bytes, chunk[start : start + UPLOAD_CHUNK]
                    )
        await _owned_call(worker, worker.send_bytes, b"")
        events: list[ValidatedFinanceEvent] = []
        metadata = None
        byte_count = 0
        async with asyncio.timeout(PREPARE_SECONDS):
            while True:
                message = await _owned_call(worker, worker.receive)
                kind = message.get("kind")
                if kind == "sql":
                    result = await runtime.projections._read_native(
                        "money",
                        lambda conn: _sql(
                            conn,
                            message,
                            read_only=True,
                            scope=(
                                session.tenant_id,
                                session.auth_member_id,
                                manifest.binding.book_id,
                            ),
                        ),
                    )
                    await _owned_call(worker, worker.send, result)
                elif kind == "prepared" and metadata is None:
                    metadata = message
                elif kind == "event" and metadata is not None:
                    raw = await _owned_call(worker, worker.raw)
                    byte_count += len(raw)
                    if (
                        len(events) >= 131
                        or byte_count > 4 * manifest.encoded_body_limit()
                    ):
                        raise PublicationError("finance_worker_output_limit")
                    if (
                        len(raw) != message["bytes"]
                        or hashlib.sha256(raw).hexdigest() != message["sha256"]
                    ):
                        raise PublicationError("finance_worker_receipt_invalid")
                    event = ValidatedFinanceEvent(
                        session.tenant_id,
                        session.auth_member_id,
                        manifest.binding.book_id,
                        message["type"],
                        message["source_adapter"],
                        message["idempotency_key"],
                        raw.decode(),
                        message["sha256"],
                        b"",
                    )
                    event = replace(event, _receipt=event._signature())
                    event.verify()
                    events.append(event)
                elif kind == "done":
                    break
                else:
                    raise PublicationError("finance_worker_message_invalid")
        expected_types = ["finance.provenance_archived"] * (len(manifest.blobs) + 1) + [
            "finance.publication_accepted"
        ]
        if (
            manifest.projection is not None
            and metadata is not None
            and not metadata.get("dated_error")
        ):
            expected_types.append("finance.projection_verified")
        if (
            not metadata
            or [e.type for e in events] != expected_types
            or metadata["book_id"] != manifest.binding.book_id
        ):
            raise PublicationError("finance_worker_output_invalid")
        return PreparedFinanceImport(metadata, events)
    except (BrokenPipeError, EOFError, OSError, ValueError, KeyError) as exc:
        raise PublicationError("finance_worker_failed") from exc
    finally:
        await await_settled(asyncio.to_thread(worker.close))


def apply_committed(envelope: dict[str, Any], conn: Any) -> None:
    """Called within the existing Money transaction on its lifetime owner."""
    worker = FinanceWorker("apply", timeout=30)
    try:
        metadata = {
            k: v for k, v in envelope.items() if k not in {"payload", "_payload_json"}
        }
        raw = envelope.get("_payload_json")
        if raw is None:
            raw = json.dumps(envelope["payload"], separators=(",", ":"))
        worker.send({"operation": "apply", "envelope": metadata})
        worker.send_bytes(raw.encode())
        while True:
            message = worker.receive()
            if message.get("kind") in {"sql", "sql_many"}:
                worker.send(
                    _sql(
                        conn,
                        message,
                        read_only=False,
                        scope=(
                            envelope["tenant_id"],
                            envelope["owner_scope"].removeprefix("private:"),
                            envelope["source_account_id"],
                        ),
                    )
                )
            elif message.get("kind") == "done":
                return
            else:
                raise PublicationError("finance_worker_message_invalid")
    except (BrokenPipeError, EOFError, OSError, ValueError, KeyError) as exc:
        raise PublicationError("finance_worker_failed") from exc
    finally:
        worker.close()
