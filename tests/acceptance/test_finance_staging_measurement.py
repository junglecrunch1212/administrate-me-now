"""One separately admitted synthetic staged import; never a native qualification."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import sys
import time
from uuid import uuid4

import httpx
import pytest

from adminme.finance.publication import canonical_bytes
from tests.acceptance.test_finance_activation import (
    APPROVALS,
    CONTROL,
    IMPORTS,
    packet_for,
)
from tests.acceptance.test_finance_storage_measurement import (
    CORE_PATH,
    PLANNING_PATH,
    PhasedRun,
    cleanup_run,
    compare_pair,
    window_summary,
)
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, PRINCIPAL
from tests.fixtures.finance_import_load import sized_package
from tests.fixtures.finance_diagnostic_readiness import startup_evidence
from tests.fixtures.finance_staging_measurement_report import (
    abort_pending,
    close_completed_slots,
    core_response_fingerprint,
    deadline,
    slot_plan,
)

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_SYNTHETIC_STAGED_MEASUREMENT") != "1"
    or os.getenv("FINANCE_WATCHDOG_MODE") != "staged-bounded"
    or sys.platform != "linux",
    reason="one exact admitted staged watchdog invocation required",
)

MIB = 1024 * 1024
PHASES = {
    "synthetic_fixture_build": 45,
    "runtime_startup": 30,
    "preimport_core_baseline": 10,
    "single_initial_import": 120,
    "settle_and_native_readback": 15,
    "single_full_money_replay": 60,
    "loaded_core_baseline_before": 10,
    "loaded_planning_probe": 10,
    "loaded_core_baseline_after": 10,
    "shutdown_receipt": 15,
}


class StagedRun(PhasedRun):
    runtime_script = "finance_staging_measurement.py"
    runtime_case = "staged_lifetime"
    setup_deadline = 45
    setup_complete_deadline = 75
    limits = {
        **PhasedRun.limits,
        "work_seconds": 330,
        "runtime_executor_cpu_seconds": 180,
        "whole_tree_cpu_seconds": 240,
    }


def native_identity(state):
    return {
        key: state[key]
        for key in (
            "counts",
            "events",
            "heads",
            "money_checkpoint",
            "audit",
            "authority",
        )
    }


def require_native(state):
    assert state["state"] == "read" and state["money_checkpoint_verified"]
    assert state["counts"] == {
        "finance_provenance_blobs": 99,
        "finance_publications": 1,
        "finance_dated_projections": 1,
        "money_flows": 0,
    }
    assert (
        state["events"]["count"] == 101
        and len(state["events"]["identity_payload_sha256"]) == 64
    )
    assert state["audit"] == [
        {
            "outcome": "accepted",
            "reason": "exact_approved_package",
            "provider_effects": False,
        }
    ]
    assert state["money_lag"] == 0 and not state["active_executor"]
    assert len(state["projection_names"]) == 15
    assert {"projection:" + name for name in state["projection_names"]} <= set(
        state["consumer_names"]
    )
    assert {"money", "parties"} <= set(state["projection_names"])
    assert len(state["lifetimes"]) >= 2 and len(state["stage_lifetimes"]) == 1
    assert len(state["starts"]) == len(state["lifetimes"])
    assert all(row["exit_code"] == 0 for row in state["lifetimes"])
    assert state["handoffs"] == [
        {"stage": "preparation_reaped_before_money", "events": 101}
    ]
    assert state["guards"] == ["before_transaction", "before_commit"]
    assert (
        all(len(rows) == 1 for rows in state["heads"].values())
        and len(state["heads"]) == 2
    )
    assert all(row["reaped"] for row in state["lifetimes"])
    assert all(
        row["reaped"] and row["stage_removed"] for row in state["stage_lifetimes"]
    )
    assert all(row["predecessor_reaped"] for row in state["starts"])
    assert len({row["birth"] for row in state["starts"]}) == len(state["starts"])


def make_packet(run, tenant):
    with run.setup_stage("fixture.generate"):
        publication, dated, package = sized_package(tenant, PRINCIPAL)
    with run.setup_stage("fixture.packet"):
        manifest, packet = packet_for(publication, dated, package)
        wire = json.dumps(packet, separators=(",", ":")).encode()
        geometry = {
            "accepted_native_units": len(publication["accepted_journal_revision_ids"]),
            "proof_blobs": len(package.blobs),
            "proof_plus_index_bytes": len(package.index)
            + sum(map(len, package.blobs.values())),
            "dated_json_bytes": len(canonical_bytes(dated)),
            "shared_variants": len(dated["shared_event_inventory"]),
            "unique_event_ids": len(
                {r["event_id"] for r in dated["shared_event_inventory"]}
            ),
            "context_references": sum(
                len(c["record_ids"]) for c in dated["detail_manifest"]
            ),
            "largest_blob_bytes": max(map(len, package.blobs.values())),
            "wire_bytes": len(wire),
        }
        assert (
            geometry["accepted_native_units"] == 8022 and geometry["proof_blobs"] == 98
        )
        assert 18 * MIB <= geometry["proof_plus_index_bytes"] < 18 * MIB + 2 * 8022
        assert 6020614 <= geometry["dated_json_bytes"] <= 8 * MIB
        assert (
            geometry["shared_variants"],
            geometry["unique_event_ids"],
            geometry["context_references"],
        ) == (3307, 3215, 38382)
        assert geometry["largest_blob_bytes"] <= MIB and len(wire) <= 44 * MIB
        run.record["geometry"] = geometry
        run.record["packet_pins"] = {
            "manifest": manifest,
            "wire_sha256": hashlib.sha256(wire).hexdigest(),
            "wire_bytes": len(wire),
        }
        selection = {
            "book_id": publication["book_id"],
            "context_id": dated["contexts"][0]["context_id"],
        }
        expected = publication["publication_id"], dated["projection_id"]
        del publication, dated, package, packet
        gc.collect()  # Controller construction cost remains in the first phase.
        return manifest, wire, selection, expected


async def test_one_staged_import_replay_and_loaded_queries(tmp_path):
    run = StagedRun(tmp_path / "staged", name="staged_bounded")
    report, active = run.record, set()
    report.update(
        phase_caps=PHASES,
        phases=[],
        requests=[],
        controls=[],
        faults=[],
        final_durable_state={"state": "unavailable"},
        correctness="incomplete",
        measurement_qualification=False,
        normal_cpu_qualification="not_measured",
        repeated_import_retention="not_measured",
        native_qualification=False,
        construction_gc={
            "process": "controller_only",
            "calls": 1,
            "phase": "synthetic_fixture_build",
            "cost_included": True,
            "runtime_executor_forced_gc_or_trim": False,
        },
        authority_scope="synthetic only; live grants unknown",
        request_policy={
            "core_hz": 20,
            "planning_hz": 10,
            "core_inflight": 4,
            "planning_inflight": 1,
            "timeout_seconds": 2,
            "retries": 0,
            "maximum_core_slots": 4500,
            "planning_slots": 100,
        },
    )
    client = None
    selection = expected = approval = None
    shutdown_sent = False
    failure = None

    async def phase(name, function, *, fixed=False):
        started = time.monotonic()
        end = deadline(started, PHASES[name], run.started)
        row = {
            "name": name,
            "started": started,
            "deadline": end,
            "cap_seconds": PHASES[name],
            "state": "incomplete",
            "start_resources": run.sample(),
        }
        report["phases"].append(row)
        task = asyncio.create_task(function(end))
        active.add(task)
        try:
            done, _ = await asyncio.wait({task}, timeout=max(0, end - time.monotonic()))
            if not done:
                task.cancel()
                raise TimeoutError("synthetic_phase_deadline")
            result = task.result()
            if time.monotonic() > end:
                raise TimeoutError("synthetic_phase_deadline")
            # Fixed traffic has finished all planned requests before the cap.
            # Idle remainder is measured; only bookkeeping can follow the clock edge.
            if fixed:
                await asyncio.sleep(max(0, end - time.monotonic()))
            row.update(
                state="completed",
                ended=end if fixed else time.monotonic(),
                returned_at=time.monotonic(),
                end_resources=run.sample(),
            )
            return result
        except BaseException as exc:
            row.update(state="failed", error=type(exc).__name__, ended=time.monotonic())
            raise
        finally:
            if task.done():
                active.discard(task)

    async def call(op):
        row = {"operation": op or "startup_frame", "started": time.monotonic()}
        report["controls"].append(row)
        try:
            result = await run.call(op)
            row["state"] = "received"
            return result
        except BaseException as exc:
            row.update(state="failed", error=type(exc).__name__)
            raise
        finally:
            row["finished"] = time.monotonic()

    async def http_control(method, path, body, deadline):
        row = {
            "operation": "setup_control" if path == CONTROL else "approval",
            "started": time.monotonic(),
        }
        report["controls"].append(row)
        try:
            async with asyncio.timeout_at(deadline):
                response = await client.request(
                    method,
                    path,
                    headers=DEMO_HEADERS,
                    json=body,
                    timeout=min(2, max(0.001, deadline - time.monotonic())),
                )
            row["status"] = response.status_code
            assert response.status_code == 200
            return response.json()
        except BaseException as exc:
            row["error"] = type(exc).__name__
            raise
        finally:
            row["finished"] = time.monotonic()

    async def load(name, end, *, seconds=None, operation=None, planning=False):
        start = time.monotonic()
        stop = start + seconds if seconds is not None else end
        stop = min(stop, end)
        op_task = asyncio.create_task(operation()) if operation is not None else None
        op_done_at = None
        op_succeeded = False
        if op_task is not None:
            active.add(op_task)

            def completed(task):
                nonlocal op_done_at, op_succeeded
                op_done_at = time.monotonic()
                op_succeeded = not task.cancelled() and task.exception() is None

            op_task.add_done_callback(completed)
        admitted = []
        tasks = set()

        async def request(row):
            row["started"] = time.monotonic()
            try:
                params = None
                if row["kind"] == "planning":
                    slot = row["slot"]
                    params = {
                        **selection,
                        "report_id": "planning_events"
                        if slot % 2
                        else "planning_projection",
                        "horizon_days": (7, 30, 90, 365)[slot % 4],
                        "offset": 50 * (slot % 5),
                    }
                path = PLANNING_PATH if row["kind"] == "planning" else CORE_PATH
                row["parameters"] = params
                async with asyncio.timeout_at(min(stop, row["started"] + 2)):
                    async with client.stream(
                        "GET",
                        path,
                        params=params,
                        headers={**DEMO_HEADERS, "X-Measurement-Id": row["id"]},
                        timeout=2,
                    ) as response:
                        row.update(
                            status=response.status_code, headers_at=time.monotonic()
                        )
                        body = await response.aread()
                        row["last_byte_at"] = time.monotonic()
                        row["body_sha256"] = hashlib.sha256(body).hexdigest()
                        if response.status_code != 200:
                            row["error"] = "http_status"
                        elif row["kind"] == "core":
                            row.update(core_response_fingerprint(body))
                        elif row["kind"] == "planning":
                            value = json.loads(body)
                            row["qualified"] = (
                                value.get("state") == "qualified"
                                and value.get("projection", {}).get("id") == expected[1]
                                and value.get("publication", {}).get("publication_id")
                                == expected[0]
                            )
                            if not row["qualified"]:
                                row["error"] = "planning_result_coordinates"
            except BaseException as exc:
                row["error"] = type(exc).__name__
                if isinstance(exc, asyncio.CancelledError):
                    raise
            finally:
                row["finished"] = time.monotonic()
                row["milliseconds"] = (row["finished"] - row["started"]) * 1000

        async def arrivals(kind, hz, capacity):
            own = set()
            # Explicit maximum ledger: future slots after normal operation completion
            # are outside admission, distinct from missing admitted/aborted slots.
            rows = slot_plan(name, kind, hz, start, stop, seconds)
            report["requests"].extend(rows)
            for row in rows:
                await asyncio.sleep(max(0, row["scheduled"] - time.monotonic()))
                if op_done_at is not None and row["scheduled"] >= op_done_at:
                    if op_succeeded:
                        close_completed_slots(rows, op_done_at)
                    break
                row["admission"] = "admitted"
                admitted.append(row)
                if time.monotonic() - row["scheduled"] >= 1 / hz:
                    row["error"] = "missed_arrival_slot"
                    continue
                if len(own) >= capacity:
                    row["error"] = "inflight_limit"
                    continue
                task = asyncio.create_task(request(row))
                own.add(task)
                tasks.add(task)
                active.add(task)
                task.add_done_callback(own.discard)
                task.add_done_callback(active.discard)

        arrivals_tasks = [asyncio.create_task(arrivals("core", 20, 4))]
        if planning:
            arrivals_tasks.append(asyncio.create_task(arrivals("planning", 10, 1)))
        active.update(arrivals_tasks)
        try:
            await asyncio.gather(*arrivals_tasks)
            if tasks:
                await asyncio.gather(*tasks)
            value = await op_task if op_task is not None else None
            report.setdefault("admission_windows", []).append(
                {
                    "phase": name,
                    "started": start,
                    "closed_at": op_done_at if op_done_at is not None else stop,
                    "fixed_seconds": seconds,
                    "admitted": len(admitted),
                }
            )
            assert all(r.get("status") == 200 and "error" not in r for r in admitted)
            return value
        finally:
            for task in [*arrivals_tasks, *tasks, *([op_task] if op_task else [])]:
                if not task.done():
                    task.cancel()
                else:
                    active.discard(task)

    try:

        async def build(end):
            run.setup_deadline = end - run.started
            coordinates = await call(None)
            return await run.thread(make_packet, run, coordinates["tenant"])

        manifest, wire, selection, expected = await phase(
            "synthetic_fixture_build", build
        )

        async def startup(end):
            nonlocal client, approval
            ready_end = end - 5
            async with asyncio.timeout_at(ready_end):
                await run.thread(run.release_runtime)
                ready = await call(None)
                client = run.client = httpx.AsyncClient(
                    base_url=ready["url"],
                    trust_env=False,
                    timeout=2,
                    limits=httpx.Limits(
                        max_connections=6,
                        max_keepalive_connections=6,
                        keepalive_expiry=4,
                    ),
                )
                # Fixed250ms readiness slots, max100, all retained. First success ends polling.
                probe_start = time.monotonic()
                for slot in range(100):
                    await asyncio.sleep(
                        max(0, probe_start + slot * 0.25 - time.monotonic())
                    )
                    if time.monotonic() >= ready_end:
                        raise TimeoutError("synthetic_readiness_deadline")
                    row = {
                        "operation": "http_readiness",
                        "slot": slot,
                        "scheduled": probe_start + slot * 0.25,
                        "started": time.monotonic(),
                    }
                    report["controls"].append(row)
                    try:
                        response = await client.get(
                            CORE_PATH,
                            headers=DEMO_HEADERS,
                            timeout=min(0.25, max(0.001, ready_end - time.monotonic())),
                        )
                        row["status"] = response.status_code
                    except httpx.HTTPError as exc:
                        row["error"] = type(exc).__name__
                    finally:
                        row["finished"] = time.monotonic()
                    if row.get("status") == 200:
                        break
                else:
                    raise TimeoutError("synthetic_readiness_slots_exhausted")
                await http_control(
                    "PUT",
                    CONTROL,
                    {
                        "request_id": str(uuid4()),
                        "expected_revision": 0,
                        "binding": manifest["binding"],
                        "books_enabled": True,
                        "cos_analysis_enabled": False,
                    },
                    ready_end,
                )
                accepted = await http_control(
                    "POST",
                    APPROVALS,
                    {
                        "request_id": str(uuid4()),
                        "expected_policy_revision": 1,
                        "manifest": manifest,
                    },
                    ready_end,
                )
                approval = accepted["approval_event_id"]
                report["empty_state"] = await call("inspect")
                # The existing inspect flushes its trace before replying. Keep
                # this gate inside startup; no extra request, wait or retry.
                report["diagnostic_startup"] = startup_evidence(
                    run.root / "measurement-trace.jsonl", time.monotonic()
                )
                assert report["diagnostic_startup"]["complete"], (
                    "synthetic_diagnostic_startup_incomplete"
                )
            warm_start = time.monotonic()
            report["warmup"] = {"started": warm_start, "seconds": 5, "core_slots": 100}
            await load("warm_core", min(end, warm_start + 5), seconds=5)
            await asyncio.sleep(max(0, warm_start + 5 - time.monotonic()))
            report["warmup"]["ended"] = warm_start + 5

        await phase("runtime_startup", startup)
        await phase(
            "preimport_core_baseline",
            lambda end: load("preimport_core_baseline", end, seconds=10),
            fixed=True,
        )

        async def initial():
            row = {"operation": "initial_import", "started": time.monotonic()}
            report["controls"].append(row)

            async def stream():
                for offset in range(0, len(wire), 65536):
                    yield wire[offset : offset + 65536]
                    await asyncio.sleep(0)

            try:
                response = await client.post(
                    IMPORTS + approval,
                    headers={
                        **DEMO_HEADERS,
                        "Content-Type": "application/json",
                        "X-Measurement-Id": "initial",
                    },
                    content=stream(),
                    timeout=120,
                )
                row["status"] = response.status_code
                assert response.status_code == 200
                value = response.json()
                assert value["accounting"]["publication_id"] == expected[0]
                assert value["dated"]["projection_id"] == expected[1]
                row["state"] = "accepted"
            finally:
                row["finished"] = time.monotonic()

        await phase(
            "single_initial_import",
            lambda end: load("single_initial_import", end, operation=initial),
        )
        wire = b""
        before = await phase("settle_and_native_readback", lambda end: call("inspect"))
        require_native(before)
        report["after_import"] = before
        after = await phase(
            "single_full_money_replay",
            lambda end: load(
                "single_full_money_replay", end, operation=lambda: call("replay")
            ),
        )
        require_native(after)
        assert native_identity(before) == native_identity(after)
        report["after_replay"] = after
        for name in (
            "loaded_core_baseline_before",
            "loaded_planning_probe",
            "loaded_core_baseline_after",
        ):
            await phase(
                name,
                lambda end, name=name: load(
                    name, end, seconds=10, planning=name == "loaded_planning_probe"
                ),
                fixed=True,
            )
        final = await phase("shutdown_receipt", lambda end: call("shutdown"))
        shutdown_sent = True
        require_native(final)
        assert native_identity(before) == native_identity(final)
        report["final_durable_state"] = final
        report["correctness"] = "passed"
    except BaseException as exc:
        failure = exc
        report["faults"].append(
            {
                "kind": type(exc).__name__,
                "phase": report["phases"][-1]["name"]
                if report["phases"]
                else "constructor",
            }
        )
    finally:
        abort_pending(report["requests"])
        report["unexecuted_phases"] = [
            name for name in PHASES if name not in {r["name"] for r in report["phases"]}
        ]
        try:
            await cleanup_run(run, active, shutdown_sent=shutdown_sent)
        except BaseException as exc:
            report["cleanup_failure"] = {"kind": type(exc).__name__}
            failure = failure or exc
        if (
            report["final_durable_state"]["state"] == "unavailable"
            and isinstance(report.get("cleanup_shutdown"), dict)
            and report["cleanup_shutdown"].get("state") == "read"
        ):
            report["final_durable_state"] = report["cleanup_shutdown"]
        report["measurement_comparisons"] = comparisons(report)
        (run.output / "staged_bounded.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    if failure is not None:
        raise failure


def comparisons(report):
    phases = {
        row["name"]: row for row in report["phases"] if row["state"] == "completed"
    }
    summaries = {}
    for name, phase in phases.items():
        if name not in {
            "preimport_core_baseline",
            "single_initial_import",
            "single_full_money_replay",
            "loaded_core_baseline_before",
            "loaded_planning_probe",
            "loaded_core_baseline_after",
        }:
            continue
        rows = [
            r
            for r in report["requests"]
            if r["phase"] == name
            and r["kind"] == "core"
            and r["admission"] != "outside_completed_operation"
        ]
        fixed_window = name in {
            "preimport_core_baseline",
            "loaded_core_baseline_before",
            "loaded_planning_probe",
            "loaded_core_baseline_after",
        }
        summary = window_summary(rows, 200 if fixed_window else len(rows))
        seconds = phase["ended"] - phase["started"]
        summary["cpu_cores"] = (
            phase["end_resources"]["runtime_executor_cpu_seconds"]
            - phase["start_resources"]["runtime_executor_cpu_seconds"]
        ) / seconds
        summary["body_hashes"] = sorted(
            {r["body_sha256"] for r in rows if "body_sha256" in r}
        )
        summary["semantic_hashes"] = sorted(
            {r["semantic_sha256"] for r in rows if "semantic_sha256" in r}
        )
        summary["validated_core_responses"] = sum(
            all(
                key in r
                for key in ("body_sha256", "semantic_sha256", "observed_clocks")
            )
            for r in rows
        )
        summary["valid"] = summary["valid"] and bool(rows)
        summary["valid"] = summary["valid"] and summary[
            "validated_core_responses"
        ] == len(rows)
        summaries[name] = summary
    loaded_names = (
        "loaded_core_baseline_before",
        "loaded_core_baseline_after",
        "loaded_planning_probe",
    )
    loaded = {"overall_verdict": "inconclusive", "reason": "missing_windows"}
    if all(name in summaries for name in loaded_names):
        states = (
            report.get("after_import"),
            report.get("after_replay"),
            report.get("final_durable_state"),
        )
        state_equal = all(s and s.get("state") == "read" for s in states) and all(
            native_identity(s) == native_identity(states[0]) for s in states[1:]
        )
        hashes = [summaries[n]["semantic_hashes"] for n in loaded_names]
        loaded = compare_pair(
            *(summaries[n] for n in loaded_names),
            comparable=bool(
                state_equal
                and all(summaries[n]["valid"] for n in loaded_names)
                and len(hashes[0]) == 1
                and hashes[0] == hashes[1] == hashes[2]
            ),
        )
    baseline = summaries.get("preimport_core_baseline", {})
    diagnostic = {}
    for name in ("single_initial_import", "single_full_money_replay"):
        row = summaries.get(name, {})
        diagnostic[name] = {
            "classification": "diagnostic_changing_state_not_comparable",
            "added": {
                key: row[key] - baseline[key]
                if row.get(key) is not None and baseline.get(key) is not None
                else None
                for key in ("p95_ms", "observed_p99_ms", "cpu_cores")
            },
            "absolute_latency_within": row.get("p95_ms") is not None
            and row["p95_ms"] <= 25
            and row["observed_p99_ms"] <= 75,
            "legacy_mean_cpu_within": row.get("cpu_cores") is not None
            and row["cpu_cores"] <= 1,
        }
    return {
        "core_windows": summaries,
        "loaded_planning": loaded,
        "import_replay_diagnostic": diagnostic,
        "stored_query_cpu_within": all(
            summaries.get(n, {}).get("cpu_cores", float("inf")) <= 0.25
            for n in loaded_names
        ),
        "normal_native_cpu": "not_measured",
        "repeated_import_growth": "not_measured",
    }
