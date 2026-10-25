"""Bounded first-inspect diagnostics inside the existing startup budget."""

import hashlib
import json
import math


def startup_evidence(path, inspected_at):
    result = {
        "complete": False,
        "bytes": 0,
        "rows": 0,
        "faults": [],
        "last_tick_at": None,
        "last_tick_age_seconds": None,
    }
    digest = hashlib.sha256()
    ready, tick, boundary = None, None, None
    spans, handlers, executors = [], [], set()
    try:
        with path.open("rb") as stream:
            while raw := stream.readline(65537):
                result["bytes"] += len(raw)
                if result["bytes"] > 16 * 1024 * 1024 or len(raw) > 65536:
                    raise ValueError("trace_limit")
                digest.update(raw)
                if not raw.endswith(b"\n"):
                    raise ValueError("partial_trace_row")
                row = json.loads(raw)
                result["rows"] += 1
                if row["n"] != result["rows"] or not math.isfinite(row["at"]):
                    raise ValueError("trace_coordinates")
                kind = row["kind"]
                if kind == "runtime.ready":
                    if ready is not None:
                        raise ValueError("duplicate_ready")
                    ready = row["at"]
                elif kind == "loop.delay":
                    tick = row["at"]
                    if not math.isfinite(inspected_at):
                        raise ValueError("inspection_coordinates")
                    result.update(
                        last_tick_at=tick,
                        last_tick_age_seconds=inspected_at - tick,
                    )
                    if tick > inspected_at:
                        raise ValueError("loop_tick_coordinates")
                elif kind in {"readback.start", "readback.end"}:
                    if not (
                        row["rss_bytes"] > 0
                        and math.isfinite(row["process_cpu_seconds"])
                        and row["process_cpu_seconds"] >= 0
                        and math.isfinite(row["sampled_at"])
                        and row["sampled_at"] <= row["at"]
                    ):
                        raise ValueError("resource_coordinates")
                    spans.append([row["span"], kind])
                elif kind in {"handler.start", "handler.end"}:
                    handlers.append([row["route"], kind])
                elif kind == "native.queue":
                    executors.add(row["executor"])
                elif kind == "control.end" and row.get("operation") == "inspect":
                    # Inspect flushes this exact completed prefix before replying.
                    # Background callbacks can append later; never read their tail.
                    boundary = {"n": row["n"], "at": row["at"], "operation": "inspect"}
                    break
        result.update(
            sha256=digest.hexdigest(),
            ready_at=ready,
            last_tick_at=tick,
            inspected_at=inspected_at,
            readback_spans=spans,
            handler_spans=handlers,
            observed_executor_callbacks=sorted(executors),
            completed_prefix_boundary=boundary,
        )
        if boundary is None or not boundary["at"] <= inspected_at:
            result["faults"].append("completed_inspect_prefix_missing_or_invalid")
        if ready is None or not math.isfinite(inspected_at) or ready > inspected_at:
            result["faults"].append("ready_missing_or_invalid")
        # No early tick is required and its age is diagnostic only. The unchanged
        # terminal observer receipt requires ticks and a complete probe lifetime.
        expected = [
            [name, kind]
            for name in ("metadata_snapshot", "event_digest", "heads")
            for kind in ("readback.start", "readback.end")
        ]
        if spans != expected:
            result["faults"].append("first_readback_incomplete")
        if not all(
            ["core", kind] in handlers for kind in ("handler.start", "handler.end")
        ):
            result["faults"].append("core_handler_incomplete")
        if not {"event_log", "event_metadata", "money_read"} <= executors:
            result["faults"].append("readback_executor_callbacks_incomplete")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result["faults"].append(type(exc).__name__)
    result["complete"] = not result["faults"]
    return result
