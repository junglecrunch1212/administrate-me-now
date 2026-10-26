"""Bounded synthetic attribution of existing buffered reads and import stages.

No data values, SQL text, heap traversal, forced GC or allocator trim. Diagnostic
observers perturb timing; these records cannot qualify a release.
"""

from __future__ import annotations

from collections import Counter
import json
import os
import threading
import time

from tests.fixtures.finance_import_attribution import AllocatorProbe

BOUNDARIES = ("preimport", "postimport", "postreplay", "postload")
STAGE_LIMIT = 16
READ_LIMIT = 50_000
READ_RECORD_LIMIT = 256
READ_FILE_LIMIT = READ_LIMIT * READ_RECORD_LIMIT
STAGE_FILE_LIMIT = 65_536


def evidence_names():
    return [
        *(f"allocator-stage-{n:04d}.xml" for n in range(1, STAGE_LIMIT + 1)),
        "allocation-stages.jsonl",
        "frame-reads.jsonl",
    ]


class ReadObserver:
    def __init__(self, observer, stream, frame_id, kind):
        self.observer = observer
        self.stream = stream
        self.frame_id = frame_id
        self.kind = kind
        self.calls = 0

    def read(self, requested):
        # Exactly the original blocking buffered read, same argument and
        # returned bytes. Do not force fragmentation or copy the result.
        result = self.stream.read(requested)
        observer = self.observer
        began = time.monotonic()
        cpu = time.thread_time()
        self.calls += 1
        with observer.lock:
            if observer.read_count >= READ_LIMIT:
                observer.dropped += 1
                return result
            observer.read_count += 1
            returned = len(result)
            edge = "header" if self.calls == 1 else "payload"
            if 0 < returned < requested:
                if edge == "header":
                    observer.partial_header += 1
                else:
                    observer.partial_payload += 1
            row = {
                "seq": observer.read_count,
                "at": began,
                "thread": threading.get_native_id(),
                "frame": self.frame_id,
                "kind": self.kind,
                "stream": type(self.stream).__name__,
                "edge": edge,
                "requested": requested,
                "returned": returned,
            }
            raw = (json.dumps(row, separators=(",", ":")) + "\n").encode()
            if len(raw) > READ_RECORD_LIMIT:
                observer.errors += 1
            elif observer.read_stream.write(raw) != len(raw):
                observer.errors += 1
            observer.observer_seconds += time.monotonic() - began
            observer.observer_cpu_seconds += time.thread_time() - cpu
        return result


class Observations:
    def __init__(self, root):
        self.root = root
        self.allocator = AllocatorProbe(root)
        self.lock = threading.Lock()
        self.next_boundary = 0
        self.stages = Counter()
        self.stage_count = 0
        self.frame_count = 0
        self.read_count = 0
        self.partial_header = 0
        self.partial_payload = 0
        self.dropped = 0
        self.errors = 0
        self.observer_seconds = 0.0
        self.observer_cpu_seconds = 0.0
        self.originals = []
        self.stage_stream = (root / "allocation-stages.jsonl").open("wb", buffering=0)
        self.read_stream = (root / "frame-reads.jsonl").open("wb", buffering=0)

    def mark(self, label):
        began = time.monotonic()
        cpu = time.thread_time()
        with self.lock:
            self.stages[label] += 1
            if self.stage_count >= STAGE_LIMIT:
                self.dropped += 1
                return
            self.stage_count += 1
            allocator = self.allocator.snapshot(f"stage-{self.stage_count:04d}")
            row = {
                "sequence": self.stage_count,
                "stage": label,
                "at": began,
                "finished": time.monotonic(),
                "observer_thread_cpu_seconds": time.thread_time() - cpu,
                "thread_id": threading.get_ident(),
                "native_thread_id": threading.get_native_id(),
                "allocator": allocator,
                "frame_count": self.frame_count,
                "read_count": self.read_count,
                "qualification": False,
            }
            raw = (json.dumps(row, separators=(",", ":")) + "\n").encode()
            assert len(raw) <= 4096
            assert self.stage_stream.write(raw) == len(raw)
            self.observer_seconds += time.monotonic() - began
            self.observer_cpu_seconds += time.thread_time() - cpu

    def frame(self, original, stream, limit, kind):
        with self.lock:
            self.frame_count += 1
            frame_id = self.frame_count
        return original(ReadObserver(self, stream, frame_id, kind), limit)

    def replace(self, target, name, replacement):
        self.originals.append((target, name, getattr(target, name)))
        setattr(target, name, replacement)

    def install(self):
        from adminme.finance import worker, worker_protocol
        from adminme.products.member import finance_import
        from adminme.events.log import EventLog

        original_frame = worker_protocol.read_frame
        original_prepare = finance_import.prepare_import
        original_append = EventLog._append_finance_batch
        original_settle = finance_import._settled_approved_batch
        original_apply = worker.MoneyCalculation.apply

        def raw_frame(stream, limit=worker_protocol.FRAME_LIMIT):
            return self.frame(original_frame, stream, limit, "raw_event")

        def json_frame(stream, limit=worker_protocol.FRAME_LIMIT):
            return self.frame(original_frame, stream, limit, "json_message")

        async def prepare(*args, **kwargs):
            result = await original_prepare(*args, **kwargs)
            self.mark("prepare_return")
            return result

        async def append(log, *args, **kwargs):
            result = await original_append(log, *args, **kwargs)
            # Before returning to EventWriter, which owns the later bus notify.
            self.mark("append_return_before_notify")
            return result

        async def settle(*args, **kwargs):
            result = await original_settle(*args, **kwargs)
            self.mark("settled_return")
            return result

        def apply(calculation, envelope, conn):
            dated = envelope["type"] == "finance.projection_verified"
            if dated:
                self.mark("dated_apply_begin")
            result = original_apply(calculation, envelope, conn)
            if dated:
                self.mark("dated_apply_end")
            return result

        self.replace(worker, "read_frame", raw_frame)
        self.replace(worker_protocol, "read_frame", json_frame)
        self.replace(finance_import, "prepare_import", prepare)
        self.replace(EventLog, "_append_finance_batch", append)
        self.replace(finance_import, "_settled_approved_batch", settle)
        self.replace(worker.MoneyCalculation, "apply", apply)

    def close(self):
        for target, name, original in reversed(self.originals):
            setattr(target, name, original)
        self.originals.clear()
        for stream in (self.stage_stream, self.read_stream):
            if not stream.closed:
                stream.close()

    def capture(self, boundary):
        assert self.next_boundary < len(BOUNDARIES)
        assert boundary == BOUNDARIES[self.next_boundary], "boundary order"
        self.mark(boundary)
        self.next_boundary += 1
        if boundary == "postload":
            self.close()
            expected = Counter({name: 1 for name in BOUNDARIES})
            expected.update(
                {
                    "prepare_return": 1,
                    "append_return_before_notify": 1,
                    "settled_return": 1,
                    "dated_apply_begin": 2,
                    "dated_apply_end": 2,
                }
            )
            assert self.stages == expected, "stage_count_mismatch"
        return {
            "boundary": boundary,
            "pid": os.getpid(),
            "acceptance_eligible": False,
            "qualification": False,
            "stage_count": self.stage_count,
            "frame_count": self.frame_count,
            "read_count": self.read_count,
            "partial_header": self.partial_header,
            "partial_payload": self.partial_payload,
            "observer_sum_wall_seconds": self.observer_seconds,
            "observer_sum_thread_cpu_seconds": self.observer_cpu_seconds,
            "dropped": self.dropped,
            "errors": self.errors,
            "complete": self.dropped == self.errors == 0,
        }
