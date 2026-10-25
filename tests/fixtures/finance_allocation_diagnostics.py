"""Bounded synthetic allocation attribution, never a production performance verdict."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
import functools
import os
from pathlib import Path
import sys
import time
import tracemalloc

from tests.fixtures.finance_import_diagnostics import usage

ROOT = Path(__file__).resolve().parents[2]


def ownership(roots):
    """Count live selected objects once without retaining or exposing their values."""
    seen = set()
    result = {}
    for label, value in roots:
        pending = [value]
        total = count = 0
        while pending:
            item = pending.pop()
            identity = id(item)
            if identity in seen:
                continue
            seen.add(identity)
            count += 1
            total += sys.getsizeof(item)
            if len(seen) > 1_000_000:
                return {"truncated": True, "visited": len(seen)}
            if isinstance(item, Mapping):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (list, tuple, set, frozenset)):
                pending.extend(item)
            elif is_dataclass(item) and not isinstance(item, type):
                pending.extend(getattr(item, f.name) for f in fields(item))
        result[label] = {"exclusive_bytes_in_root_order": total, "objects": count}
    return {"truncated": False, "roots": result, "distinct_objects": len(seen)}


class AllocationProfile:
    def __init__(self, role):
        self.role = role
        self.pid = os.getpid()
        self.baseline = {"at": time.monotonic(), **usage()}
        self.records = []
        self.groups = {}
        self.top_snapshots = 0
        self.serialization_marks = 0
        self.dropped_records = 0
        self.overhead_seconds = 0.0
        tracemalloc.start(1)
        self.mark("tracing_started")

    def sample(self):
        current, peak = tracemalloc.get_traced_memory()
        return {
            **usage(),
            "traced_live_bytes": current,
            "traced_peak_bytes_since_start": peak,
            "tracer_internal_bytes": tracemalloc.get_tracemalloc_memory(),
        }

    def mark(self, name, *, roots=(), top=False):
        started = time.monotonic()
        if len(self.records) >= 48:
            self.dropped_records += 1
            return
        row = {"name": name, "at": started, **self.sample()}
        if roots:
            row["ownership"] = ownership(roots)
        if top and self.top_snapshots < 12:
            snapshot = tracemalloc.take_snapshot()
            stats = snapshot.statistics("lineno")[:8]
            row["top_live_locations"] = []
            for entry in stats:
                frame = entry.traceback[0]
                path = Path(frame.filename)
                label = (
                    str(path.relative_to(ROOT))
                    if path.is_relative_to(ROOT)
                    else path.name
                )
                row["top_live_locations"].append(
                    {
                        "file": label,
                        "line": frame.lineno,
                        "bytes": entry.size,
                        "count": entry.count,
                    }
                )
            del snapshot, stats
            self.top_snapshots += 1
        row["probe_finished_at"] = time.monotonic()
        row["probe_after_usage"] = self.sample()
        self.records.append(row)
        self.overhead_seconds += time.monotonic() - started

    @contextmanager
    def stage(self, name):
        started = time.monotonic()
        before = self.sample()
        try:
            yield
        finally:
            after = self.sample()
            group = self.groups.setdefault(
                name,
                {
                    "calls": 0,
                    "wall_seconds": 0.0,
                    "cpu_seconds": 0.0,
                    "max_before_rss_bytes": 0,
                    "max_after_rss_bytes": 0,
                    "max_after_live_bytes": 0,
                },
            )
            group["calls"] += 1
            group["wall_seconds"] += time.monotonic() - started
            group["cpu_seconds"] += after["cpu_seconds"] - before["cpu_seconds"]
            group["max_before_rss_bytes"] = max(
                group["max_before_rss_bytes"], before["rss_bytes"]
            )
            group["max_after_rss_bytes"] = max(
                group["max_after_rss_bytes"], after["rss_bytes"]
            )
            group["max_after_live_bytes"] = max(
                group["max_after_live_bytes"], after["traced_live_bytes"]
            )

    def snapshot(self):
        return {
            "role": self.role,
            "pid": self.pid,
            "baseline": self.baseline,
            "records": self.records,
            "groups": self.groups,
            "dropped_records": self.dropped_records,
            "top_snapshots": self.top_snapshots,
            "serialization_marks": self.serialization_marks,
            "explicit_probe_overhead_seconds": self.overhead_seconds,
            "limitations": [
                "Diagnostic overhead is not acceptance timing",
                "Baseline allocations predate tracing",
                "Reported peaks include probe overhead; exclude marked probe intervals for RSS comparisons",
                "RSS minus traced live is not solely allocator residency",
                "Nested stage times overlap; do not sum them",
            ],
        }

    def instrument_storage(self):
        from adminme.finance import closed_import, dated_projection, planning_store
        from adminme.finance import provenance_store, publication_provenance

        for module, name in (
            (closed_import, "_decode_packet"),
            (closed_import, "_validate_owned_projection"),
            (dated_projection, "_validate_owned_projection"),
            (planning_store, "validate_projection"),
            (planning_store, "_anchored"),
            (provenance_store, "verify_archived"),
            (publication_provenance, "verify_package"),
        ):
            original = getattr(module, name)
            label = module.__name__.rsplit(".", 1)[-1] + "." + name

            @functools.wraps(original)
            def wrapped(*args, _original=original, _name=name, _label=label, **kwargs):
                if _name == "_decode_packet":
                    self.mark(
                        "closed_packet",
                        roots=(("packet_components", args[0]),),
                        top=True,
                    )
                with self.stage(_label):
                    value = _original(*args, **kwargs)
                if _name == "_decode_packet":
                    self.mark(
                        "decoded_owned_components",
                        roots=(
                            ("publication", value[0]),
                            ("projection", value[1]),
                            ("proof_package", value[2]),
                        ),
                        top=True,
                    )
                elif _label == "planning_store.validate_projection":
                    self.mark(
                        "money_validated_projection",
                        roots=(
                            ("incoming_projection", args[0]),
                            ("validated_projection", value),
                        ),
                        top=True,
                    )
                elif _name == "verify_package":
                    self.mark(
                        "package_verified", roots=(("proof_result", value),), top=True
                    )
                return value

            setattr(module, name, wrapped)

        for module in (
            closed_import,
            dated_projection,
            planning_store,
            publication_provenance,
        ):
            original = module.canonical_bytes
            label = module.__name__.rsplit(".", 1)[-1]

            @functools.wraps(original)
            def serialized(value, _original=original, _label=label):
                raw = _original(value)
                if len(raw) > 1024 * 1024 and self.serialization_marks < 6:
                    self.serialization_marks += 1
                    self.mark(
                        "serialized:" + _label,
                        roots=(("input_object", value), ("encoded_bytes", raw)),
                    )
                return raw

            module.canonical_bytes = serialized
