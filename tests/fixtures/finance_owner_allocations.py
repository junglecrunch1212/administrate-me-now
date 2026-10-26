"""Three bounded runtime-only allocation observations; never qualification.

Allocation sites are not object ownership proofs. RSS and tracemalloc describe
different quantities; their difference must remain unexplained by this probe.
No object values, heap dumps, collection, allocator changes or application hooks.
"""

import os
from pathlib import Path
import sys
import time
import tracemalloc

REPO = Path(__file__).resolve().parents[2]
BOUNDARIES = ("preimport", "postimport", "postreplay")
TOP = 15
MAX_TRACES = 1_000_000
MAX_SITES = 8192


def location(filename):
    path = Path(filename)
    for root, prefix in (
        (REPO, "repo/"),
        (Path(sys.prefix), "python-env/"),
        (Path(sys.base_prefix), "python-base/"),
    ):
        if path.is_relative_to(root):
            result = prefix + str(path.relative_to(root))
            break
    else:
        result = "external/" + path.name
    if len(result) > 256:
        raise ValueError("allocation_source_location_bound")
    return result


def totals(sites):
    return {
        "bytes": sum(v[0] for v in sites.values()),
        "count": sum(v[1] for v in sites.values()),
    }


def delta(current, previous):
    rows = []
    for site in current.keys() | previous.keys():
        now, before = current.get(site, (0, 0)), previous.get(site, (0, 0))
        if now != before:
            rows.append(
                {
                    "source": site[0],
                    "line": site[1],
                    "bytes_delta": now[0] - before[0],
                    "count_delta": now[1] - before[1],
                }
            )

    def order(row):
        return (row["bytes_delta"], row["count_delta"], row["source"], row["line"])

    now, before = totals(current), totals(previous)
    return {
        "bytes_delta": now["bytes"] - before["bytes"],
        "count_delta": now["count"] - before["count"],
        "changed_sites": len(rows),
        "largest_increases": sorted(
            (r for r in rows if r["bytes_delta"] > 0), key=order, reverse=True
        )[:TOP],
        "largest_decreases": sorted(
            (r for r in rows if r["bytes_delta"] < 0), key=order
        )[:TOP],
    }


class Observations:
    def __init__(self):
        if tracemalloc.is_tracing():
            raise ValueError("unexpected_existing_tracer")
        self.index = 0
        self.baseline = self.previous = None
        self.started = time.monotonic()
        # Only this fixture runtime starts tracing, before application creation.
        # Shipping worker subprocess environments and source remain untouched.
        tracemalloc.start(1)

    def capture(self, boundary):
        if self.index >= len(BOUNDARIES) or boundary != BOUNDARIES[self.index]:
            raise ValueError("allocation_boundary_order")
        started, cpu_started = time.monotonic(), time.process_time()
        current, peak = tracemalloc.get_traced_memory()
        metadata_before = tracemalloc.get_tracemalloc_memory()
        snapshot = tracemalloc.take_snapshot()
        trace_count = len(snapshot.traces)
        if trace_count > MAX_TRACES:
            raise ValueError("allocation_trace_count_bound")
        statistics = snapshot.statistics("lineno")
        if len(statistics) > MAX_SITES:
            raise ValueError("allocation_source_count_bound")
        sites, observer = {}, {"bytes": 0, "count": 0}
        for stat in statistics:
            frame = stat.traceback[0]
            source = location(frame.filename)
            if source in {
                "repo/tests/fixtures/finance_owner_allocations.py",
                "repo/tests/fixtures/finance_owner_production.py",
            } or source.endswith("/tracemalloc.py"):
                observer["bytes"] += stat.size
                observer["count"] += stat.count
            else:
                key = (source, frame.lineno)
                old = sites.get(key, (0, 0))
                sites[key] = (old[0] + stat.size, old[1] + stat.count)
        del snapshot, statistics  # No snapshot survives this boundary; no forced GC.
        row = {
            "schema": "finance-owner-allocation-observation-v1",
            "qualification": False,
            "boundary": boundary,
            "pid": os.getpid(),
            "trace_started_at": self.started,
            "trace_frames": tracemalloc.get_traceback_limit(),
            "started_at": started,
            "tracked_live_before_snapshot_bytes": current,
            "tracked_peak_bytes": peak,
            "tracer_metadata_before_bytes": metadata_before,
            "snapshot_trace_count": trace_count,
            "snapshot_source_sites": len(sites),
            "snapshot_live_excluding_observer": totals(sites),
            "snapshot_observer_allocations": observer,
            "from_preimport": delta(sites, self.baseline)
            if self.baseline is not None
            else None,
            "from_previous": delta(sites, self.previous)
            if self.previous is not None
            else None,
            "interpretation": "Tracked live allocations since tracing started; source sites are not owners. Untracked allocations and RSS differences remain unexplained. Traced durations do not qualify latency; total tracing overhead has no untraced control.",
        }
        if self.baseline is None:
            self.baseline = sites
        self.previous = sites
        self.index += 1
        row.update(
            tracer_metadata_after_bytes=tracemalloc.get_tracemalloc_memory(),
            tracked_live_after_observation_bytes=tracemalloc.get_traced_memory()[0],
            finished_at=time.monotonic(),
            observation_cpu_seconds=time.process_time() - cpu_started,
        )
        row["observation_wall_seconds"] = row["finished_at"] - started
        return row
