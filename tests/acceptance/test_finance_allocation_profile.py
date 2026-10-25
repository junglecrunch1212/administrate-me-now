"""One bounded synthetic attribution pass; diagnostic overhead is not acceptance."""

import asyncio
import json
import os
import time
import tracemalloc

from tests.acceptance.test_finance_activation import IMPORTS
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS
from tests.acceptance.test_finance_storage_measurement import (
    PhasedRun,
    cleanup_run,
    setup_runtime,
    settled,
)
from tests.fixtures.finance_allocation_diagnostics import AllocationProfile


class AllocationRun(PhasedRun):
    runtime_case = "allocation_lifetime"


async def test_bounded_three_import_allocation_profile(tmp_path):
    run = AllocationRun(tmp_path / "allocation", name="allocation_profile")
    report = run.record
    report.update(
        evaluation="allocation_diagnostic_not_acceptance",
        requests=[],
        imports=[],
        faults=[],
    )
    active = set()
    profile = None
    try:
        _, _, _, wire, next_wire, _, expected, approvals, state = await setup_runtime(
            run, active
        )
        authority = state["authority"]
        profile = AllocationProfile("controller_parent")
        profile.mark(
            "input_buffers_already_owned",
            roots=(("initial_wire", wire), ("successor_wire", next_wire)),
            top=True,
        )
        report["roles"] = {
            "supervisor": run.supervisor_pid,
            "controller_parent": os.getpid(),
            "main_runtime": run.runtime_pid,
        }
        for index, (name, approval, payload) in enumerate(
            (
                ("initial", approvals[0], wire),
                ("duplicate", approvals[0], wire),
                ("successor", approvals[1], next_wire),
            )
        ):
            profile.mark(name + ":before")
            row = {"name": name, "started": time.monotonic()}
            report["imports"].append(row)

            async def stream():
                for offset in range(0, len(payload), 65536):
                    yield payload[offset : offset + 65536]
                    await asyncio.sleep(0)

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
            row.update(finished=time.monotonic(), status=response.status_code)
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert (
                receipt["accounting"]["publication_id"]
                == expected[2 if index == 2 else 0]
            )
            assert receipt["dated"]["projection_id"] == expected[3 if index == 2 else 1]
            current = await run.call("inspect")
            assert settled(current) and current["authority"] == authority
            assert len(current["consumers"]) == 34
            row["durable"] = current
            profile.mark(name + ":settled", top=True)
        assert current["counts"]["finance_publications"] == 2
        assert current["counts"]["finance_dated_projections"] == 2
        assert len(current["audit"]) == 3
        assert all(a["payload"]["outcome"] == "accepted" for a in current["audit"])
        assert len(current["allocation_profiles"]) == 3
        assert all(
            p["role"] == "isolated_storage_executor"
            for p in current["allocation_profiles"]
        )
        assert all(p["records"] and p["groups"] for p in current["allocation_profiles"])
        assert all(
            lifetime["reaped"] and lifetime["exit_code"] == 0
            for lifetime in current["lifetimes"]
        )
        report["state"] = "allocation_diagnostic_complete"
    finally:
        await cleanup_run(run, active)
        if profile:
            profile.mark("controller_cleanup", top=True)
            report["controller_allocation_profile"] = profile.snapshot()
            tracemalloc.stop()
        (run.output / "allocation_profile.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert report["cleanup"]["runtime_exit"] == 0 and not report["cleanup"]["forced"]
    assert (
        not report["cleanup"]["remaining_owned"]
        and report["cleanup"]["scratch_removed"]
    )
