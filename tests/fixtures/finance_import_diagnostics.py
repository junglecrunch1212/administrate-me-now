"""Bounded scalar-only attribution for the disposable storage executor."""

from contextlib import contextmanager
import functools
import os
from pathlib import Path
import resource
import time


def usage():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "rss_bytes": int(Path("/proc/self/statm").read_text().split()[1])
        * os.sysconf("SC_PAGE_SIZE"),
        "lifetime_peak_bytes": r.ru_maxrss * 1024,
        "cpu_seconds": r.ru_utime + r.ru_stime,
    }


class PreparationDiagnostics:
    def __init__(self):
        self.stages = []
        self.events = 0
        self.encoded_event_bytes = 0
        self.largest_event_bytes = 0
        self.event_peak_bytes = 0

    @contextmanager
    def stage(self, name):
        if len(self.stages) >= 32:
            raise RuntimeError("synthetic_attribution_limit")
        row = {"name": name, "started": time.monotonic(), "before": usage()}
        self.stages.append(row)
        try:
            yield
        finally:
            row.update(finished=time.monotonic(), after=usage())

    def emitted(self, size):
        self.events += 1
        self.encoded_event_bytes += size
        self.largest_event_bytes = max(self.largest_event_bytes, size)
        self.event_peak_bytes = max(self.event_peak_bytes, usage()["rss_bytes"])
        if self.events > 131:
            raise RuntimeError("synthetic_attribution_event_limit")

    def snapshot(self):
        return {**self.__dict__, "prepared_usage": usage()}

    @contextmanager
    def instrument(self):
        from adminme.finance import closed_import, dated_projection
        from adminme.finance import provenance_store, publication_provenance

        originals = []
        for module, name in (
            (closed_import, "_decode_packet"),
            (dated_projection, "_validate_owned_projection"),
            (closed_import, "_validate_owned_projection"),
            (provenance_store, "verify_archived"),
            (publication_provenance, "verify_package"),
        ):
            original = getattr(module, name)
            originals.append((module, name, original))

            @functools.wraps(original)
            def wrapped(*args, _original=original, _name=name, **kwargs):
                with self.stage(_name):
                    return _original(*args, **kwargs)

            setattr(module, name, wrapped)
        try:
            yield self
        finally:
            for module, name, original in reversed(originals):
                setattr(module, name, original)
