"""One hosted Linux synthetic storage prototype evaluation, never a live switch."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import gc
import json
import math
import os
import resource
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import pytest

from adminme.finance.worker_protocol import read_json, write_json
from tests.acceptance.test_finance_activation import (
    CONTROL,
    APPROVALS,
    IMPORTS,
    SURFACE,
    packet_for,
)
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, PRINCIPAL
from tests.fixtures.finance_import_load import sized_package
from tests.fixtures.finance_planning import seal
from tests.fixtures.finance_provenance import seal_publication
from tests.fixtures.finance_storage_cases import CASES

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_SYNTHETIC_STORAGE_EVALUATION") != "1" or sys.platform != "linux",
    reason="one explicitly selected hosted Linux synthetic prototype only",
)

MIB = 1024 * 1024
LIMITS = {
    "work_seconds": 180,
    "cleanup_seconds": 30,
    "owned_processes": 4,
    "runtime_executor_rss_bytes": 512 * MIB,
    "runtime_executor_cpu_seconds": 90,
    "whole_tree_rss_bytes": 1024 * MIB,
    "whole_tree_cpu_seconds": 180,
    "scratch_bytes": 512 * MIB,
}


def process_row(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rindex(")") + 2 :].split()
        hz = os.sysconf("SC_CLK_TCK")
        return {
            "pid": pid,
            "state": fields[0],
            "ppid": int(fields[1]),
            "cpu_seconds": (int(fields[11]) + int(fields[12])) / hz,
            "reaped_cpu_seconds": (int(fields[13]) + int(fields[14])) / hz,
            "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
            "start_ticks": int(fields[19]),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


class OwnedRun:
    def __init__(self, name, root):
        resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        self.name, self.root = name, root
        root.mkdir()
        self.output = Path(os.environ["FINANCE_STORAGE_ARTIFACT_DIR"])
        self.output.mkdir(parents=True, exist_ok=True)
        self.record = {
            "case": name,
            "candidate": os.environ["GITHUB_SHA"],
            "synthetic_only": True,
            "production_or_native_qualification": False,
            "containment_not_acceptance": True,
            "limits": LIMITS,
            "cpu_enforcement": "per-process RLIMIT_CPU plus aggregate owned-tree stop",
            "ram_disk_enforcement": "cooperative sampled stop, not an OS sandbox",
            "samples": [],
            "violations": [],
            "state": "incomplete",
        }
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.known = {}
        self.process = None
        self.error_file = (self.output / (name + ".stderr.txt")).open("wb")
        before = process_row(os.getpid())
        self.cpu_before = before["cpu_seconds"] + before["reaped_cpu_seconds"]
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                str(
                    Path(__file__).resolve().parents[1]
                    / "fixtures/finance_storage_cases.py"
                ),
                name,
                str(root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.error_file,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "ADMINME_TEST_MODE": "1"},
            cwd="/",
            close_fds=True,
            start_new_session=True,
        )
        self.runtime_pid = self.process.pid
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

    def sample(self):
        rows = []
        pending = [os.getpid()]
        visited = set()
        while pending:
            pid = pending.pop()
            if pid in visited:
                continue
            visited.add(pid)
            row = process_row(pid)
            if row is None:
                continue
            rows.append(row)
            self.known[pid] = row["start_ticks"]
            # Linux records children on the creating thread. Storage is
            # spawned from an executor thread, so inspect every owned TID.
            for task in Path(f"/proc/{pid}/task").glob("*"):
                try:
                    children = (task / "children").read_text().split()
                except FileNotFoundError:
                    continue
                pending.extend(int(v) for v in children)
        runtime = [row for row in rows if row["pid"] != os.getpid()]
        scratch = 0
        for path in [*self.root.rglob("*"), *self.output.rglob("*")]:
            try:
                if path.is_file():
                    scratch += path.stat().st_size
            except FileNotFoundError:
                pass  # A settled WAL may disappear between listing and stat.
        sample = {
            "at": time.monotonic(),
            "pids": rows,
            "scratch_bytes": scratch,
            "runtime_executor_rss_bytes": sum(r["rss_bytes"] for r in runtime),
            "whole_tree_rss_bytes": sum(r["rss_bytes"] for r in rows),
            "runtime_executor_cpu_seconds": sum(
                r["cpu_seconds"] + r["reaped_cpu_seconds"] for r in runtime
            ),
            "whole_tree_cpu_seconds": sum(
                r["cpu_seconds"] + r["reaped_cpu_seconds"] for r in rows
            )
            - self.cpu_before,
            "owned_processes": len(rows),
            "work_seconds": time.monotonic() - self.started,
        }
        self.record["samples"].append(sample)
        return sample

    def _monitor(self):
        try:
            while not self.stop.wait(0.05):
                sample = self.sample()
                violations = [
                    key
                    for key, value in LIMITS.items()
                    if key in sample and sample[key] > value
                ]
                if violations:
                    self.record["violations"].extend(violations)
                    self.kill()
                    return
        except BaseException as exc:
            self.record["violations"].append("monitor:" + type(exc).__name__)
            self.kill()

    def kill(self):
        try:
            os.killpg(self.runtime_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def receive(self):
        return read_json(self.process.stdout, 65536)

    def command(self, op):
        write_json(self.process.stdin, {"op": op})
        return self.receive()

    def close(self):
        start = time.monotonic()
        forced = False
        try:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                forced = True
                self.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.runtime_pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
            self.stop.set()
            self.monitor.join(timeout=2)
            assert not self.monitor.is_alive()
            living = []
            for pid, birth in self.known.items():
                if pid == os.getpid():
                    continue
                row = process_row(pid)
                if row is not None and row["start_ticks"] == birth:
                    living.append(row)
            if living:
                forced = True
                self.kill()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and any(
                    process_row(row["pid"]) is not None for row in living
                ):
                    time.sleep(0.05)
                try:
                    os.killpg(self.runtime_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # Retain the original un-reaped ownership failure even if the
                # emergency group stop removes it; do not call that a pass.
            self.record["cleanup"] = {
                "runtime_exit": self.process.returncode,
                "forced": forced,
                "remaining_owned": living,
                "seconds": time.monotonic() - start,
            }
            self.process.stdin.close()
            self.process.stdout.close()
            self.error_file.close()
            for trace in self.root.glob("measurement-*.jsonl"):
                shutil.copyfile(trace, self.output / trace.name)
            shutil.rmtree(self.root)
            self.record["cleanup"]["scratch_removed"] = not self.root.exists()
            self.record["maxima"] = {
                key: max((s[key] for s in self.record["samples"]), default=0)
                for key in LIMITS
                if key != "cleanup_seconds"
            }
        finally:
            (self.output / (self.name + ".json")).write_text(
                json.dumps(self.record, indent=2) + "\n"
            )
        assert not forced and not living and self.process.returncode == 0, self.record[
            "cleanup"
        ]
        assert not self.record["violations"], self.record["violations"]
        assert self.record["cleanup"]["seconds"] < 30


@pytest.mark.parametrize("name", CASES)
def test_synthetic_storage_contracts(tmp_path, name):
    run = OwnedRun(name, tmp_path / name)
    try:
        result = run.receive()
        run.record["result"] = result
        assert result["state"] == "passed", result
        run.record["state"] = "correctness_passed"
    finally:
        run.close()


def percentiles(rows):
    values = sorted(row["milliseconds"] for row in rows)
    return {
        "count": len(values),
        "successes": sum(row.get("status") == 200 for row in rows),
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "observed_p99_ms": values[math.ceil(len(values) * 0.99) - 1]
        if values
        else None,
        "max_ms": max(values, default=None),
    }


async def test_representative_import_duplicate_successor_replay_one_parent(tmp_path):
    run = OwnedRun("large_lifetime", tmp_path / "large_lifetime")
    report = run.record
    report["core"] = []
    report["queries"] = []
    report["retained"] = []
    stop = asyncio.Event()
    core_task = None
    phase = "before"
    try:
        ready = await asyncio.to_thread(run.receive)
        assert ready["ready"]
        p, d, package = await asyncio.to_thread(
            sized_package, ready["tenant"], PRINCIPAL
        )
        manifest, packet = packet_for(p, d, package)
        wire = json.dumps(packet, separators=(",", ":")).encode()
        later = deepcopy(p)
        later["prior_publication_id"] = p["publication_id"]
        seal_publication(later)
        later_d = deepcopy(d)
        later_d["prior_projection_id"] = d["projection_id"]
        later_d["projection_id"] = "projection-synthetic-successor"
        later_d["accounting_publication_id"] = later["publication_id"]
        later_d["accounting_content_sha256"] = later["content_sha256"]
        # The projection contract's accounting anchor is retained explicitly.
        if "publication_id" in later_d:
            later_d["publication_id"] = later["publication_id"]
        later_d = seal(later_d)
        successor_manifest, successor_packet = packet_for(later, later_d, package)
        successor_wire = json.dumps(successor_packet, separators=(",", ":")).encode()
        report["geometry"] = {
            "accepted_units": len(p["accepted_journal_revision_ids"]),
            "proof_blobs": len(package.blobs),
            "shared_variants": len(d["shared_event_inventory"]),
            "context_references": sum(
                len(c["record_ids"]) for c in d["detail_manifest"]
            ),
            "wire_bytes": len(wire),
        }
        assert report["geometry"]["accepted_units"] == 8022
        assert report["geometry"]["proof_blobs"] == 98
        book = p["book_id"]
        context = d["contexts"][0]["context_id"]
        expected = [
            p["publication_id"],
            d["projection_id"],
            later["publication_id"],
            later_d["projection_id"],
        ]
        del p, d, later, later_d, package, packet, successor_packet
        gc.collect()  # Controller fixture buffers only; never trim a runtime allocator.
        report["baseline"] = run.sample()
        async with httpx.AsyncClient(
            base_url=ready["url"], trust_env=False, timeout=120
        ) as client:
            control = await client.put(
                CONTROL,
                headers=DEMO_HEADERS,
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": 0,
                    "binding": manifest["binding"],
                    "books_enabled": True,
                    "cos_analysis_enabled": False,
                },
            )
            assert control.status_code == 200, control.text

            async def approve_manifest(value):
                reply = await client.post(
                    APPROVALS,
                    headers=DEMO_HEADERS,
                    json={
                        "request_id": str(uuid4()),
                        "expected_policy_revision": 1,
                        "manifest": value,
                    },
                )
                assert reply.status_code == 200, reply.text
                return reply.json()["approval_event_id"]

            approval = await approve_manifest(manifest)
            successor_approval = await approve_manifest(successor_manifest)

            async def core():
                while not stop.is_set():
                    began = time.monotonic()
                    row = {"at": began, "phase": phase}
                    try:
                        response = await client.get(
                            "/api/member/v1/surfaces/reminders",
                            headers=DEMO_HEADERS,
                            timeout=2,
                        )
                        row["status"] = response.status_code
                    except Exception as exc:
                        row["error"] = type(exc).__name__
                    row["milliseconds"] = (time.monotonic() - began) * 1000
                    report["core"].append(row)
                    await asyncio.sleep(max(0, 0.25 - (time.monotonic() - began)))

            core_task = asyncio.create_task(core())
            await asyncio.sleep(3)
            for phase, target, payload in (
                ("initial", approval, wire),
                ("duplicate", approval, wire),
                ("successor", successor_approval, successor_wire),
            ):

                async def streamed():
                    for offset in range(0, len(payload), 65536):
                        yield payload[offset : offset + 65536]
                        await asyncio.sleep(0)

                began = time.monotonic()
                reply = await client.post(
                    IMPORTS + target,
                    headers={**DEMO_HEADERS, "Content-Type": "application/json"},
                    content=streamed(),
                )
                report[phase] = {
                    "status": reply.status_code,
                    "body": reply.json(),
                    "seconds": time.monotonic() - began,
                }
                assert reply.status_code == 200, reply.text
                assert (
                    reply.json()["accounting"]["publication_id"]
                    == expected[2 if phase == "successor" else 0]
                )
                assert (
                    reply.json()["dated"]["projection_id"]
                    == expected[3 if phase == "successor" else 1]
                )
                if phase == "duplicate":
                    assert (
                        reply.json()["accounting"]["event_id"]
                        == report["initial"]["body"]["accounting"]["event_id"]
                    )
                status = await asyncio.to_thread(run.command, "inspect")
                report[phase]["durable"] = status
                assert status["active_executor"] is False
                report["retained"].append({"phase": phase, **run.sample()})
            phase = "replay"
            before = report["successor"]["durable"]
            after = await asyncio.to_thread(run.command, "replay")
            assert after["counts"] == before["counts"]
            assert after["finance_events"] == before["finance_events"]
            report["replay"] = after
            report["retained"].append({"phase": phase, **run.sample()})
            phase = "stored_state_queries"
            for selection in range(30):
                began = time.monotonic()
                result = await client.get(
                    "/api/member/v1/finance/planning",
                    headers=DEMO_HEADERS,
                    params={
                        "book_id": book,
                        "context_id": context,
                        "report_id": "planning_events"
                        if selection % 2
                        else "planning_projection",
                        "horizon_days": (7, 30, 90, 365)[selection % 4],
                        "offset": 50 * (selection % 5),
                    },
                )
                report["queries"].append(
                    {
                        "status": result.status_code,
                        "milliseconds": (time.monotonic() - began) * 1000,
                    }
                )
                assert result.status_code == 200, result.text
                await asyncio.sleep(0.1)
            surface = await client.get(SURFACE, headers=DEMO_HEADERS)
            assert surface.status_code == 200, surface.text
            assert (
                surface.json()["cockpit"]["accepted_books"]["publication_id"]
                == expected[2]
            )
            report["retained"].append({"phase": phase, **run.sample()})
            phase = "after"
            await asyncio.sleep(3)
            stop.set()
            await core_task
            core_task = None
            report["terminal"] = await asyncio.to_thread(run.command, "shutdown")
            assert report["terminal"]["counts"]["finance_publications"] == 2
            assert report["terminal"]["counts"]["finance_dated_projections"] == 2
            assert len(report["terminal"]["audit"]) == 3
            assert all(
                r["payload"]["outcome"] == "accepted"
                for r in report["terminal"]["audit"]
            )
            assert all(
                r["exit_code"] == 0 and r["reaped"]
                for r in report["terminal"]["lifetimes"]
            )
            assert all(r.get("status") == 200 for r in report["core"])
            report["state"] = "correctness_passed"
    finally:
        stop.set()
        if core_task is not None:
            await core_task
        report["core_summary"] = {
            phase_name: percentiles(
                [r for r in report["core"] if r["phase"] == phase_name]
            )
            for phase_name in sorted({r["phase"] for r in report["core"]})
        }
        report["query_summary"] = percentiles(report["queries"])
        if report.get("baseline") and report["retained"]:
            baseline_rss = report["baseline"]["runtime_executor_rss_bytes"]
            report["retained_growth_bytes"] = max(
                r["runtime_executor_rss_bytes"] - baseline_rss
                for r in report["retained"]
            )
            report["added_combined_peak_bytes"] = (
                max(r["runtime_executor_rss_bytes"] for r in report["samples"])
                - baseline_rss
            )
            before = report["core_summary"].get("before", {})
            during = percentiles(
                [
                    r
                    for r in report["core"]
                    if r["phase"]
                    in {
                        "initial",
                        "duplicate",
                        "successor",
                        "replay",
                        "stored_state_queries",
                    }
                ]
            )
            report["acceptance_comparison"] = {
                "retained_8_mib": report["retained_growth_bytes"] <= 8 * MIB,
                "planning_added_peak_128_mib": report["added_combined_peak_bytes"]
                <= 128 * MIB,
                "core_p95_25_ms": during["p95_ms"] <= 25,
                "core_p99_75_ms": during["observed_p99_ms"] <= 75,
                "added_p95_5_ms": during["p95_ms"] - before["p95_ms"] <= 5,
                "added_p99_15_ms": during["observed_p99_ms"] - before["observed_p99_ms"]
                <= 15,
            }
            report["foreground_during"] = during
            report["cpu_phase_means"] = {}
            for name in (
                "initial",
                "duplicate",
                "successor",
                "replay",
                "stored_state_queries",
            ):
                requests = [r for r in report["core"] if r["phase"] == name]
                if len(requests) < 2:
                    continue
                samples = [
                    r
                    for r in report["samples"]
                    if requests[0]["at"] <= r["at"] <= requests[-1]["at"]
                ]
                if len(samples) > 1:
                    cpu = (
                        samples[-1]["runtime_executor_cpu_seconds"]
                        - samples[0]["runtime_executor_cpu_seconds"]
                    ) / (samples[-1]["at"] - samples[0]["at"])
                    ceiling = 0.25 if name == "stored_state_queries" else 1.0
                    report["cpu_phase_means"][name] = {
                        "observed_cores": cpu,
                        "comparison_limit": ceiling,
                        "within_mean_comparison": cpu <= ceiling,
                    }
            report["acceptance_note"] = (
                "Unchanged comparisons, not sustained native qualification; small-sample observed percentiles retained."
            )
        run.close()
