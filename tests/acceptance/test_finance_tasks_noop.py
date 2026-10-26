"""One opt-in hosted diagnostic, never production or resource acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
from collections import Counter
from pathlib import Path

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.projections.context_fabric import ContextFabricProjection
from adminme.projections.runner import ProjectionRunner
from adminme.projections.tasks import TasksProjection
from tests.acceptance.test_finance_mixed_load import _profile_workload
from tests.fixtures.finance_tasks_noop import TasksNoopDiagnostic, projection_snapshot
from tests.unit.test_event_bus import _wait_for

# Held advisor contracts only; the owner-release suite uses the unmodified ceiling.
pytestmark = [
    pytest.mark.usefixtures("unreleased_finance_advisor"),
    pytest.mark.skipif(
        os.getenv("ADMINME_FINANCE_TASKS_NOOP_DIAGNOSTIC") != "1"
        or platform.system() != "Linux",
        reason="One explicitly selected hosted synthetic diagnostic only",
    ),
]
KEY = b"n" * 32
STAMP = "2030-01-01T00:00:00Z"
PRINCIPAL = "service-principal:chief-of-staff"
SCOPE = "service-private:" + PRINCIPAL


def _corpus(tenant):
    def envelope(number, event_type, payload, version=1, service=False):
        return EventEnvelope(
            event_id=f"ev_noop_diagnostic_{number:04d}",
            event_at_ms=1893456000000 + number,
            tenant_id=tenant,
            type=event_type,
            schema_version=version,
            occurred_at=STAMP,
            recorded_at=STAMP,
            source_adapter="openclaw-plugin"
            if "tool_call" in event_type
            else "fixture",
            source_account_id=PRINCIPAL if service else "fixture-account",
            owner_scope=SCOPE if service else "private:fixture-member",
            visibility_scope=SCOPE if service else "private:fixture-member",
            idempotency_key=f"fixed-noop-diagnostic:{number}",
            payload=payload,
        )

    v1 = {
        "operation": "finance.status",
        "actor_id": "fixture-actor",
        "member_id": "fixture-member",
        "channel_id": "fixture-channel",
        "correlation_id": "fixture-call",
        "call_kind": "tool",
        "openclaw_version": "fixture-version",
        "openclaw_commit": "fixture-commit",
    }
    v2 = {
        "operation": "finance.calculate",
        "actor_principal_id": PRINCIPAL,
        "operator_principal_id": "service-operator:fixture",
        "represented_service_principal_id": PRINCIPAL,
        "context_scope": "member_private",
        "approval_quorum": "member_owner",
        "openclaw_actor_id": "fixture-actor",
        "channel_id": "fixture-channel",
        "request_correlation_id": "fixture-call",
        "call_kind": "tool",
        "openclaw_version": "fixture-version",
        "openclaw_commit": "fixture-commit",
    }
    source = {
        "source_event_id": "fixture-source",
        "authority_kind": "service_self",
        "context_scope": "service_private",
        "owner_party_id": "service-party:chief-of-staff",
        "source_owner_scope": SCOPE,
        "source_visibility_scope": SCOPE,
        "source_sensitivity": "privileged",
        "source_adapter": "fixture",
        "source_account_id": "fixture-account",
        "source_policy_key": "fixture-policy",
        "source_policy_owner_id": PRINCIPAL,
        "read_mode": "metadata_only",
        "restriction": "internal_processing_only",
    }
    v3 = {
        **v2,
        "operation": "tools.service-context",
        "context_scope": "service_private",
        "approval_quorum": "service_self",
        "source_read_session_digest": "a" * 64,
        "source_read_authorities": [source],
    }
    return [
        envelope(1, "openclaw.tool_call.recorded", v1),
        envelope(2, "openclaw.tool_call.recorded", v2, version=2),
        envelope(
            3,
            "task.created",
            {
                "task_id": "fixture-task",
                "title": "Fixture task",
                "owner_member_id": "fixture-member",
            },
        ),
        envelope(4, "openclaw.tool_call.recorded", v3, version=3, service=True),
        envelope(
            5,
            "finance.disclosure_recorded",
            {
                "kind": "session",
                "member_id": "fixture-member",
                "session_sha256": "b" * 64,
                "authority_sha256": "c" * 64,
                "issued_at": STAMP,
            },
        ),
        envelope(
            6,
            "openclaw.tool_call.recorded",
            {
                **v3,
                "source_read_authorities": [
                    {**source, "read_mode": "typed_facts_safe_snippet"}
                ],
            },
            version=3,
            service=True,
        ),
        envelope(7, "openclaw.tool_call.recorded", v3, version=3, service=True),
        envelope(
            8,
            "task.created",
            {
                "task_id": "fixture-task-two",
                "title": "Second fixture task",
                "owner_member_id": "fixture-member",
            },
        ),
        envelope(
            9,
            "openclaw.tool_call.recorded",
            {**v1, "correlation_id": "fixture-cancelled"},
        ),
    ]


async def _open(root):
    config = load_instance_config(root)
    log = EventLog(config, KEY)
    bus = EventBus(log, config.bus_checkpoint_path, failure_threshold=1)
    runner = ProjectionRunner(bus, log, config, encryption_key=KEY)
    runner.register(TasksProjection())
    runner.register(ContextFabricProjection())
    await runner.start()
    return config, log, bus, runner


async def _settle(bus, expected):
    await _wait_for(
        lambda: all(
            state.checkpoint == expected and not state.degraded
            for state in bus._subscribers.values()
        ),
        timeout=10,
    )
    # The durable checkpoint write follows the in-memory update.
    for _ in range(100):
        async with bus._ckpt_lock:
            rows = await asyncio.to_thread(
                lambda: list(
                    bus._ckpt_conn.execute(
                        "SELECT checkpoint_event_id FROM bus_consumer_offsets"
                    )
                )
            )
        if all(row[0] == expected for row in rows):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("durable checkpoints did not settle")


async def _snapshot(log, bus, runner):
    return {
        "events": [event async for event in log.read_since()],
        "projections": {
            name: await runner.read(name, projection_snapshot)
            for name in ("tasks", "context_fabric")
        },
        "checkpoints": {
            name: {
                key: value
                for key, value in (await bus.subscriber_status(name)).items()
                if key
                in {
                    "checkpoint_event_id",
                    "lag_count",
                    "degraded",
                    "poison_event_id",
                    "consecutive_failures",
                }
            }
            for name in bus._subscribers
        },
    }


async def _fixed_corpus_trial(root, control):
    diagnostic = TasksNoopDiagnostic(control=control)
    diagnostic.active = True
    with diagnostic.install():
        config, log, bus, runner = await _open(root)
        events = _corpus(config.tenant_id)
        try:
            projection = runner._projections["tasks"]
            apply = projection.apply
            fail = True

            def fail_middle(event, connection):
                if fail and event["event_id"] == events[2].event_id:
                    raise RuntimeError("synthetic ordered failure")
                return apply(event, connection)

            setattr(projection, "apply", fail_middle)
            await EventWriter(log, bus).write_batch(events[:8])
            state = bus._subscribers["projection:tasks"]
            await _wait_for(lambda: state.degraded, timeout=10)
            assert state.checkpoint == events[1].event_id
            assert state.poison_event_id == events[2].event_id
            assert (await bus.subscriber_status("projection:tasks"))["lag_count"] == 5
            fail = False
            await bus.reset_subscriber("projection:tasks")
            await _settle(bus, events[7].event_id)
            # Positive version 3 processing must upgrade, never lower, read mode.
            assert (
                await runner.read(
                    "tasks",
                    lambda c: c.execute(
                        "SELECT read_rank FROM cos_source_read_receipts"
                    ).fetchone()[0],
                )
                == 2
            )

            # Unknown versions do not enter the fast path. This is a direct
            # callback probe, NOT an unregistered schema appended to the log.
            before = len(diagnostic.applications)
            unknown = events[3].model_dump()
            unknown["schema_version"] = 99
            await state.callback(unknown)
            assert len(diagnostic.applications) == before + 1
            assert diagnostic.applications[-1]["version"] == 99

            # Cancellation while waiting for the existing Tasks lock must not
            # acknowledge an event or release the lock owned by another caller.
            lock = runner._write_locks["tasks"]
            entered = asyncio.Event()
            callback = state.callback

            async def entering(event):
                entered.set()
                return await callback(event)

            state.callback = entering
            await lock.acquire()
            try:
                await EventWriter(log, bus).write(events[8])
                await asyncio.wait_for(entered.wait(), 10)
                task = bus._tasks["projection:tasks"]
                task.cancel()
                await task
                assert lock.locked()
                assert state.checkpoint == events[7].event_id
                assert await log.get(events[8].event_id) is not None
            finally:
                lock.release()
            state.callback = callback
            await bus.reset_subscriber("projection:tasks")
            await _settle(bus, events[8].event_id)
            live = await _snapshot(log, bus, runner)
            for name in ("tasks", "context_fabric"):
                await runner.rebuild(name)
            assert await _snapshot(log, bus, runner) == live
        finally:
            await runner.stop()
            await log.close()
        _, log, bus, runner = await _open(root)
        try:
            await _settle(bus, events[8].event_id)
            restarted = await _snapshot(log, bus, runner)
            assert restarted == live
        finally:
            await runner.stop()
            await log.close()
    return live, diagnostic


async def test_fixed_corpus_order_projection_checkpoint_replay_restart_and_cancellation(
    tmp_path,
):
    baseline, baseline_diagnostic = await _fixed_corpus_trial(
        tmp_path / "baseline" / "instance", False
    )
    control, control_diagnostic = await _fixed_corpus_trial(
        tmp_path / "control" / "instance", True
    )
    assert baseline == control
    assert not any(row["skipped"] for row in baseline_diagnostic.attempts)
    skipped = [row for row in control_diagnostic.attempts if row["skipped"]]
    assert skipped and all(
        row["version"] in (1, 2) and row["projection"] == "tasks" for row in skipped
    )
    assert any(row["version"] == 3 for row in control_diagnostic.applications)
    assert any(row["version"] == 99 for row in control_diagnostic.applications)
    print(
        "SYNTHETIC_TASKS_NOOP_CORRECTNESS "
        + json.dumps(
            {
                "acceptance_eligible": False,
                "identical_complete_events_projections_checkpoints": True,
                "replay_restart_cancellation_successful_prefix": True,
                "sha256": hashlib.sha256(
                    json.dumps(baseline, sort_keys=True).encode()
                ).hexdigest(),
            }
        ),
        flush=True,
    )


def _p95(values):
    return (
        round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3) if values else None
    )


def _summarize(report):
    extra = report["tasks_noop_diagnostic"]
    phases = {}
    for phase in ("before", "mixed-0", "mixed-12.5", "mixed-25", "mixed-37.5", "after"):
        workers = [row for row in report["executor_spans"] if row["phase"] == phase]
        loops = [row for row in report["loop_callback_spans"] if row["phase"] == phase]
        parts = {
            key: [] for key in ("http", "queue", "worker_wall", "worker_cpu", "overlap")
        }
        calls = [
            row
            for row in report["http_spans"]
            if row["phase"] == phase and row["route"] == "/api/core/v1/what-now"
        ]
        for call in calls:
            start, end = call["started_ms"], call["finished_ms"]
            matches = [
                row
                for row in workers
                if row["operation"] == "adminme-projections:_call_many_query"
                and start <= row["queued_ms"] <= row["finished_ms"] <= end
            ]
            assert len(matches) == 1
            work = matches[0]
            parts["http"].append(end - start)
            parts["queue"].append(work["started_ms"] - work["queued_ms"])
            parts["worker_wall"].append(work["finished_ms"] - work["started_ms"])
            parts["worker_cpu"].append(work["thread_cpu_ms"])
            parts["overlap"].append(
                sum(
                    max(
                        0,
                        min(row["finished_ms"], work["finished_ms"])
                        - max(row["started_ms"], work["started_ms"]),
                    )
                    for row in loops
                )
            )
        applies = [row for row in extra["applications"] if row["phase"] == phase]
        phases[phase] = {
            "what_now_samples": len(calls),
            "what_now_p95_ms": {key: _p95(values) for key, values in parts.items()},
            "raw_core_client_p95_ms": _p95(extra["raw_core_samples_ms"][phase]),
            "projection_worker_cost": {
                operation: {
                    "count": len(rows),
                    "queue_p95_ms": _p95(
                        [row["started_ms"] - row["queued_ms"] for row in rows]
                    ),
                    "cpu_total_ms": round(sum(row["thread_cpu_ms"] for row in rows), 3),
                    "wall_total_ms": round(
                        sum(row["finished_ms"] - row["started_ms"] for row in rows), 3
                    ),
                }
                for operation in sorted(
                    {
                        row["operation"]
                        for row in workers
                        if "_apply_one_for" in row["operation"]
                    }
                )
                for rows in [[row for row in workers if row["operation"] == operation]]
            },
            "worker_submissions": dict(Counter(row["operation"] for row in workers)),
            "projection_transactions": {
                verb: sum(row["transactions"].get(verb, 0) for row in applies)
                for verb in ("BEGIN", "COMMIT", "ROLLBACK")
            },
            "projection_worker_cpu_total_ms": round(
                sum(row["thread_cpu_ms"] for row in applies), 3
            ),
            "skipped_tasks_dispatches": sum(
                row["phase"] == phase and row["skipped"] for row in extra["attempts"]
            ),
        }
    return {
        "phases": phases,
        "process_memory": extra["process_memory"],
        "memory_scope": extra["memory_scope"],
    }


@pytest.mark.parametrize("planning_workload", [False, True])
async def test_paired_tasks_noop_diagnostic(tmp_path, planning_workload):
    reports = {}
    errors = {}
    for mode in ("baseline", "control"):
        try:
            reports[mode] = await _profile_workload(
                tmp_path / mode,
                planning_workload=planning_workload,
                tasks_noop_mode=mode,
            )
        except Exception as exc:
            errors[mode] = f"{type(exc).__name__}: {exc}"
    result = {
        "acceptance_eligible": False,
        "diagnostic_only": True,
        "production_source_changed": False,
        "workload": "planning" if planning_workload else "light",
        "errors": errors,
        "modes": {mode: _summarize(report) for mode, report in reports.items()},
    }
    artifact_dir = os.getenv("FINANCE_PROFILE_ARTIFACT_DIR")
    if artifact_dir:
        Path(artifact_dir).mkdir(parents=True, exist_ok=True)
        (
            Path(artifact_dir) / (result["workload"] + "-tasks-noop-comparison.json")
        ).write_text(json.dumps(result, indent=2))
    print("SYNTHETIC_TASKS_NOOP_COMPARISON " + json.dumps(result), flush=True)
    assert not errors, errors
    baseline = reports["baseline"]["tasks_noop_diagnostic"]
    control = reports["control"]["tasks_noop_diagnostic"]
    assert baseline["measured_event_sequence"] == control["measured_event_sequence"]
    assert len(baseline["measured_event_sequence"]) == 400
    for mode, extra in (("baseline", baseline), ("control", control)):
        attempts = [
            row
            for row in extra["attempts"]
            if row["phase"].startswith("mixed") and row["projection"] == "tasks"
        ]
        assert len(attempts) == 200
        assert all(row["skipped"] == (mode == "control") for row in attempts)
        applies = [
            row
            for row in extra["applications"]
            if row["phase"].startswith("mixed") and row["projection"] == "tasks"
        ]
        assert len(applies) == (200 if mode == "baseline" else 0)
        assert all(
            row["changed_rows"] == 0
            and row["transactions"] == {"BEGIN": 1, "COMMIT": 1}
            for row in applies
        )
