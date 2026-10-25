"""Focused import/cancellation evidence, never the fixed-schedule benchmark."""

import asyncio
import json
import time

from tests.acceptance.test_finance_activation import IMPORTS
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS
from tests.acceptance.test_finance_storage_measurement import (
    PhasedRun,
    PLANNING_PATH,
    cleanup_run,
    coordinates,
    setup_runtime,
    settled,
)


class FocusedRun(PhasedRun):
    runtime_case = "focused_lifetime"


async def test_focused_import_cancellation_replay_and_late_shutdown(tmp_path):
    run = FocusedRun(tmp_path / "focused", name="focused_import")
    report = run.record
    report.update(
        evaluation="focused_correctness_only", requests=[], imports=[], faults=[]
    )
    active = set()
    try:
        (
            _,
            _,
            _,
            wire,
            successor_wire,
            selection,
            expected,
            approvals,
            state,
        ) = await setup_runtime(run, active)
        initial_authority = state["authority"]
        report["warmed_before_import"] = run.sample()

        async def post(name, approval, data):
            async def stream():
                for offset in range(0, len(data), 65536):
                    yield data[offset : offset + 65536]
                    await asyncio.sleep(0)

            row = {"name": name, "started": time.monotonic()}
            report["imports"].append(row)
            response = await run.client.post(
                IMPORTS + approval,
                headers={
                    **DEMO_HEADERS,
                    "Content-Type": "application/json",
                    "X-Measurement-Id": name,
                },
                content=stream(),
                timeout=35,
            )
            row.update(
                finished=time.monotonic(),
                status=response.status_code,
                receipt=response.json(),
            )
            assert response.status_code == 200, response.text
            return row

        initial = await post("initial", approvals[0], wire)
        state = await run.call("inspect")
        assert settled(state) and state["authority"] == initial_authority
        duplicate = await post("duplicate", approvals[0], wire)
        assert (
            duplicate["receipt"]["accounting"]["event_id"]
            == initial["receipt"]["accounting"]["event_id"]
        )
        assert (
            duplicate["receipt"]["dated"]["event_id"]
            == initial["receipt"]["dated"]["event_id"]
        )
        assert duplicate["receipt"]["accounting"]["idempotent"]
        duplicate_state = await run.call("inspect")
        assert duplicate_state["counts"] == state["counts"] and settled(duplicate_state)

        successor = asyncio.create_task(post("successor", approvals[1], successor_wire))
        active.add(successor)
        assert (await run.call("wait_successor_committed"))["committed"]
        successor.cancel()
        cancelled = await asyncio.gather(successor, return_exceptions=True)
        assert isinstance(cancelled[0], asyncio.CancelledError)
        active.discard(successor)
        # The HTTP waiter is cancelled, but admitted atomic work keeps ownership.
        state = await run.call("settled_import")
        assert state["counts"]["finance_publications"] == 2
        assert state["counts"]["finance_dated_projections"] == 2
        assert len(state["audit"]) == 3
        assert all(row["payload"]["outcome"] == "accepted" for row in state["audit"])
        assert state["authority"] == initial_authority
        assert settled(state) and len(state["consumers"]) == 34
        assert len(state["lifetimes"]) == 3
        assert all(
            row["exit_code"] == 0 and row["reaped"] for row in state["lifetimes"]
        )
        report["cancelled_successor_durable"] = state
        replay = await run.call("replay")
        assert coordinates(replay) == coordinates(state) and settled(replay)
        report["replay"] = replay
        response = await run.client.get(
            PLANNING_PATH,
            headers=DEMO_HEADERS,
            params={
                **selection,
                "report_id": "planning_projection",
                "horizon_days": 30,
            },
        )
        assert response.status_code == 200
        value = response.json()
        assert value["state"] == "qualified"
        assert value["publication"]["publication_id"] == expected[2]
        assert value["projection"]["id"] == expected[3]
        report["focused_query"] = {
            "state": value["state"],
            "publication_id": expected[2],
            "projection_id": expected[3],
        }
        report["expected_successor"] = expected[2:]
        report["state"] = "focused_correctness_passed"
    finally:
        await cleanup_run(run, active)
        (run.output / "focused_import.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert report["cleanup_request_error"] == "TimeoutError"
    terminal = report["cleanup_shutdown"]
    assert coordinates(terminal) == coordinates(report["replay"])
    assert settled(terminal) and len(terminal["consumers"]) == 34
    assert all(row["exit_code"] == 0 and row["reaped"] for row in terminal["lifetimes"])
    assert report["cleanup"]["runtime_exit"] == 0 and not report["cleanup"]["forced"]
    assert (
        not report["cleanup"]["remaining_owned"]
        and report["cleanup"]["scratch_removed"]
    )
    assert report["cleanup_shutdown_received_at"] > report["work_finished_at"] + 4
    assert report["total_cleanup_seconds"] < 30
