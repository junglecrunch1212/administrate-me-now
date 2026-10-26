"""Opt-in isolated Linux reader-memory measurements, never household execution."""

from __future__ import annotations

import asyncio
import gc
import json
import multiprocessing
import os
import platform
import threading
from pathlib import Path

import pytest

from tests.acceptance.test_finance_authority_reader import start_runner
from tests.unit.test_finance_delegation_snapshot import approve, grant, resolve


def _memory_snapshot():
    # Only the spawned hosted process reads its own accounting. VmSize includes
    # reserved stacks/address space and must not be reported as resident memory.
    selected = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        name, _, value = line.partition(":")
        if name in {"VmRSS", "VmHWM", "VmSize"}:
            selected[name + "_bytes"] = int(value.split()[0]) * 1024
        elif name == "Threads":
            selected["kernel_threads"] = int(value.strip())
    selected["authority_reader_threads"] = sum(
        thread.name.startswith("adminme-authority-reader")
        for thread in threading.enumerate()
    )
    return selected


def _measure_reader_memory(root, output):
    async def measure():
        runner, log = await start_runner(root)
        try:
            writer = runner._unsafe_maintenance_connection("parties")
            grant(writer)
            approve(writer)
            # Warm the same SQL/decoder on the writer before measuring the
            # incremental dedicated reader. This is a synthetic native control,
            # not a request-path permission bypass or a production alternative.
            for _ in range(50):
                await runner._read_native(
                    "parties", lambda conn: resolve(conn).audit_payload()
                )
            assert runner._parties_reader_thread_id is None
            gc.collect()
            snapshots = {"before_reader": _memory_snapshot()}
            expected = await runner.read_authority(
                lambda conn: resolve(conn).audit_payload()
            )
            snapshots["first_read"] = _memory_snapshot()
            for _ in range(1000):
                assert (
                    await runner.read_authority(
                        lambda conn: resolve(conn).audit_payload()
                    )
                    == expected
                )
            gc.collect()
            snapshots["after_1000_reads"] = _memory_snapshot()
            await runner.rebuild("parties")
            assert (
                await runner.read_authority(
                    lambda conn: conn.execute(
                        "SELECT COUNT(*) FROM service_delegation_grants"
                    ).fetchone()[0],
                )
                == 0
            )
            snapshots["after_rebuild_reopen"] = _memory_snapshot()
        finally:
            await runner.stop()
            await log.close()
        gc.collect()
        snapshots["after_shutdown"] = _memory_snapshot()
        assert snapshots["before_reader"]["authority_reader_threads"] == 0
        assert snapshots["first_read"]["authority_reader_threads"] == 1
        assert snapshots["after_1000_reads"]["authority_reader_threads"] == 1
        assert snapshots["after_shutdown"]["authority_reader_threads"] == 0
        baseline = snapshots["before_reader"]["VmRSS_bytes"]
        return {
            "snapshots": snapshots,
            "resident_delta_bytes": {
                phase: values["VmRSS_bytes"] - baseline
                for phase, values in snapshots.items()
            },
        }

    try:
        output.send({"measurement": asyncio.run(measure())})
    except Exception as exc:
        output.send({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        output.close()


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_READER_MEMORY") != "1" or platform.system() != "Linux",
    reason="Opt-in isolated hosted Linux memory diagnosis; not the family runtime",
)
def test_authority_reader_incremental_memory_and_thread_shutdown(tmp_path):
    context = multiprocessing.get_context("spawn")
    trials = []
    for trial in range(3):
        receive, send = context.Pipe(duplex=False)
        process = context.Process(
            target=_measure_reader_memory, args=(tmp_path / str(trial), send)
        )
        process.start()
        send.close()
        try:
            assert receive.poll(45), (
                "synthetic reader memory measurement did not settle"
            )
            result = receive.recv()
            assert "error" not in result, result
            trials.append(result["measurement"])
        finally:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            receive.close()
        assert process.exitcode == 0
    report = {
        "diagnostic_only": True,
        "platform": platform.system(),
        "python": platform.python_version(),
        "read_operations_per_trial": 1001,
        "trials": trials,
        "deployment_hardware_validated": False,
        "heavy_worker_memory_validated": False,
        "page_cache_target_is_rss_limit": False,
    }
    print("SYNTHETIC_AUTHORITY_READER_MEMORY " + json.dumps(report), flush=True)
