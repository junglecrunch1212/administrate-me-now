"""Synthetic Linux allocator observation, never application or release policy.

All observations perturb the process. The one explicit trim happens only after
original timing/retention windows; it cannot qualify a release or replace RSS.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import time

from tests.fixtures.finance_import_attribution import AllocatorProbe

BOUNDARIES = ("preimport", "postimport", "postreplay", "postload", "posttrim")
GC_LIMIT = 2048


class Observations:
    def __init__(self, root):
        self.root = root
        self.allocator = AllocatorProbe(root)
        self.next_boundary = 0
        self.gc_count = 0
        self.gc_dropped = 0
        self.gc_errors = 0
        self.gc_stream = (root / "native-gc.jsonl").open("wb", buffering=0)
        self.callback = self.on_gc
        gc.callbacks.append(self.callback)

    def on_gc(self, phase, info):
        # Passive scalar records only: no object traversal, tracing, forced GC,
        # thresholds or collection scheduling changes. No retained record list.
        if self.gc_count >= GC_LIMIT:
            self.gc_dropped += 1
            return
        self.gc_count += 1
        try:
            row = {
                "edge": phase,
                "at": time.monotonic(),
                "cpu_seconds": time.process_time(),
                "boundary_index": self.next_boundary,
                "generation": info.get("generation"),
                "collected": info.get("collected"),
                "uncollectable": info.get("uncollectable"),
            }
            raw = (json.dumps(row, separators=(",", ":")) + "\n").encode()
            assert len(raw) <= 512
            assert self.gc_stream.write(raw) == len(raw)
        except Exception:
            self.gc_errors += 1

    def close(self):
        if self.callback in gc.callbacks:
            gc.callbacks.remove(self.callback)
        if not self.gc_stream.closed:
            self.gc_stream.close()

    def capture(self, boundary):
        assert self.next_boundary < len(BOUNDARIES)
        assert boundary == BOUNDARIES[self.next_boundary], "boundary order"
        began = time.monotonic()
        cpu = time.process_time()
        trim = None
        if boundary == "posttrim":
            # The controller has ended every original measured phase. Never
            # overwrite the retained RSS or substitute this probe for a fix.
            trim = self.allocator.trim_once()
            self.close()
        allocator = self.allocator.snapshot(boundary)
        self.next_boundary += 1
        names = [allocator["raw_xml"]]
        if boundary == "posttrim":
            names.append("native-gc.jsonl")
        files = []
        for name in names:
            raw = (self.root / name).read_bytes()
            assert len(raw) <= 1024 * 1024
            files.append(
                {
                    "name": name,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        return {
            "boundary": boundary,
            "pid": os.getpid(),
            "acceptance_eligible": False,
            "qualification": False,
            "started": began,
            "finished": time.monotonic(),
            "observer_cpu_seconds": time.process_time() - cpu,
            "allocator": allocator,
            "malloc_trim_result": trim,
            "gc_records": self.gc_count,
            "gc_dropped": self.gc_dropped,
            "gc_errors": self.gc_errors,
            "complete": self.gc_dropped == self.gc_errors == 0,
            "files": files,
        }
