"""Bounded, opt-in hosted Linux diagnostics; never imported by production.

Allocator trim is a post-measurement causal probe, not a runtime policy. Timing
records contain operation names and coordinates only, never SQL or payloads.
"""

from __future__ import annotations

import ctypes
from contextvars import ContextVar
import json
import threading
import time
import xml.etree.ElementTree as ET


request_kind = ContextVar("finance_diagnostic_request", default="other")


class AllocatorProbe:
    def __init__(self, directory):
        self.directory = directory
        self.libc = ctypes.CDLL(None)
        self.libc.gnu_get_libc_version.restype = ctypes.c_char_p
        self.version = self.libc.gnu_get_libc_version().decode("ascii")
        self.libc.open_memstream.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.libc.open_memstream.restype = ctypes.c_void_p
        self.libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
        self.libc.malloc_info.restype = ctypes.c_int
        self.libc.fclose.argtypes = [ctypes.c_void_p]
        self.libc.fclose.restype = ctypes.c_int
        self.libc.free.argtypes = [ctypes.c_void_p]
        self.libc.free.restype = None
        self.libc.malloc_trim.argtypes = [ctypes.c_size_t]
        self.libc.malloc_trim.restype = ctypes.c_int
        self.trimmed = False

    def snapshot(self, label):
        # malloc_info covers all glibc arenas; mallinfo's main-arena-only
        # coverage is insufficient for the runtime's existing owner threads.
        pointer = ctypes.c_void_p()
        length = ctypes.c_size_t()
        stream = self.libc.open_memstream(ctypes.byref(pointer), ctypes.byref(length))
        if not stream:
            raise RuntimeError("allocator diagnostic stream unavailable")
        try:
            result = self.libc.malloc_info(0, stream)
            closed = self.libc.fclose(stream)
            stream = None
            assert result == closed == 0 and length.value <= 1024 * 1024
            raw = ctypes.string_at(pointer, length.value)
        finally:
            if stream:
                self.libc.fclose(stream)
            self.libc.free(pointer)
        (self.directory / ("allocator-" + label + ".xml")).write_bytes(raw)
        root = ET.fromstring(raw)
        totals = {
            v.attrib["type"]: int(v.attrib["size"]) for v in root.findall("total")
        }
        system = {
            v.attrib["type"]: int(v.attrib["size"]) for v in root.findall("system")
        }
        free = totals["fast"] + totals["rest"]
        return {
            "glibc": self.version,
            "arenas": len(root.findall("heap")),
            "arena_system_bytes": system["current"],
            "arena_free_bytes": free,
            "mmap_bytes": totals["mmap"],
            "arena_in_use_including_metadata_bytes": system["current"] - free,
            "coverage": "all glibc arenas; not a count of live Python objects or RSS",
            "raw_xml": "allocator-" + label + ".xml",
        }

    def trim_once(self):
        assert not self.trimmed
        self.trimmed = True
        return self.libc.malloc_trim(0)


class CommitAttribution:
    def __init__(self, path):
        self.stream = path.open("w")
        self.lock = threading.Lock()
        self.enabled = False
        self.count = 0
        self.counts = {}
        self.dropped = 0

    def record(self, kind, began, *, cpu=None, context=None):
        ended = time.monotonic()
        row = {
            "kind": kind,
            "start": began,
            "end": ended,
            "wall_seconds": ended - began,
            "thread_cpu_seconds": None if cpu is None else time.thread_time() - cpu,
            "thread": threading.get_ident(),
            "context": request_kind.get() if context is None else context,
        }
        # Write after taking both clocks. Async spans intentionally omit CPU:
        # other tasks can execute while that coroutine is suspended.
        with self.lock:
            if self.count >= 20000:
                self.dropped += 1
                return
            self.stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            self.count += 1
            self.counts[kind] = self.counts.get(kind, 0) + 1

    def sync(self, kind, function, *args, context=None, **kwargs):
        if not self.enabled:
            return function(*args, **kwargs)
        began, cpu = time.monotonic(), time.thread_time()
        try:
            return function(*args, **kwargs)
        finally:
            self.record(kind, began, cpu=cpu, context=context)

    async def async_call(self, kind, function, *args, **kwargs):
        if not self.enabled:
            return await function(*args, **kwargs)
        began = time.monotonic()
        try:
            return await function(*args, **kwargs)
        finally:
            self.record(kind, began)

    def install(self, runtime):
        from adminme.finance.worker import ValidatedFinanceEvent

        probe = self
        log = runtime.event_log
        writer = runtime.events

        class TimedLock:
            def __init__(self, original, label):
                self.original, self.label = original, label

            def locked(self):
                return self.original.locked()

            async def acquire(self):
                return await probe.async_call(self.label, self.original.acquire)

            def release(self):
                return self.original.release()

            async def __aenter__(self):
                await self.acquire()
                return self

            async def __aexit__(self, *args):
                self.release()

        class TimedCursor:
            def __init__(self, original):
                self.original = original

            def execute(self, sql, *args):
                kind = "sql_" + sql.split(None, 1)[0].lower()
                probe.sync(
                    kind, self.original.execute, sql, *args, context="finance_commit"
                )
                return self

            def __getattr__(self, name):
                return getattr(self.original, name)

        class TimedConnection:
            def __init__(self, original):
                self.original = original

            def cursor(self):
                return TimedCursor(self.original.cursor())

            def __getattr__(self, name):
                return getattr(self.original, name)

        original_insert = log._insert_rows

        def insert(rows, commit_guard=None):
            if not probe.enabled or commit_guard is None:
                return original_insert(rows, commit_guard)
            connection = log._conn
            checks = 0

            def guard():
                nonlocal checks
                checks += 1
                return probe.sync(
                    "native_authority_check_" + str(checks),
                    commit_guard,
                    context="finance_commit",
                )

            # The existing connection stays on its owner thread. No alternate
            # database, transaction, guard, SQL or result is substituted.
            log._conn = TimedConnection(connection)
            try:
                return probe.sync(
                    "native_insert_rows",
                    original_insert,
                    rows,
                    guard,
                    context="finance_commit",
                )
            finally:
                log._conn = connection

        log._insert_rows = insert
        original_thread = log._run_on_connection_thread

        async def on_thread(function, *args):
            if not probe.enabled:
                return await original_thread(function, *args)
            began = time.monotonic()
            context = request_kind.get()
            name = getattr(function, "__name__", "callback")

            def run():
                probe.record("connection_queue:" + name, began, context=context)
                return probe.sync(
                    "connection_execute:" + name, function, *args, context=context
                )

            return await original_thread(run)

        log._run_on_connection_thread = on_thread
        log._lock = TimedLock(log._lock, "event_log_lock_wait")
        writer._write_lock = TimedLock(writer._write_lock, "event_writer_lock_wait")
        original_write = writer._write_finance_batch

        async def write(*args, before_commit, **kwargs):
            token = request_kind.set("finance_commit")
            try:

                async def authorize():
                    return await probe.async_call(
                        "authority_after_writer_wait", before_commit
                    )

                return await probe.async_call(
                    "finance_write",
                    original_write,
                    *args,
                    before_commit=authorize,
                    **kwargs,
                )
            finally:
                request_kind.reset(token)

        writer._write_finance_batch = write
        original_verify = ValidatedFinanceEvent.verify

        def verify(event):
            return probe.sync("receipt_verification", original_verify, event)

        ValidatedFinanceEvent.verify = verify
        original_row = log._row_with_payload

        def row(*args):
            return probe.sync("envelope_row", original_row, *args)

        log._row_with_payload = row

    def finish(self):
        self.enabled = False
        with self.lock:
            self.stream.flush()
        assert self.dropped == 0, "attribution record budget exceeded"
        for kind in (
            "sql_begin",
            "sql_commit",
            "native_authority_check_1",
            "native_authority_check_2",
        ):
            assert self.counts.get(kind) == 1, (kind, self.counts)
        return {
            "diagnostic_only": True,
            "records": self.count,
            "counts": self.counts,
            "dropped": self.dropped,
            "async_cpu": "omitted; async wall spans include waits",
            "sync_cpu": "current native thread only, spans may be nested",
        }

    def close(self):
        self.enabled = False
        self.stream.close()
