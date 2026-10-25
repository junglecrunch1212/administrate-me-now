"""Small API/lifecycle cases in a disposable process, never the large fixture."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import resource
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adminme.finance.publication import PublicationError
from adminme.finance.worker_protocol import write_json
from tests.fixtures import finance_storage_cases as cases
from tests.fixtures.finance_staging import StagedStorage, fresh_controller_recovery
from tests.fixtures.finance_staging_evidence import (
    durable_receipt,
    failure,
    require_expiry,
    wire_failure,
)


class RecordedStorage(StagedStorage):
    instances = []
    upload_fault = None
    expires = None

    def __init__(self, root, **kwargs):
        super().__init__(root, **kwargs)
        self.instances.append(self)
        self.executor_errors = []
        self.receipt_failures = []
        self.final_durable_state = {"state": "unavailable"}
        self.runtime_shutdown_completed = False
        self.response = None

    def _receive(self):
        message = super()._receive()
        if message.get("kind") == "error":
            assert len(self.executor_errors) < 8
            self.executor_errors.append(wire_failure(message))
        return message

    def install(self, app, patches):
        super().install(app, patches)
        stop = self.runtime.stop

        async def stopped():
            try:
                # This runs on TestClient exit, including failed assertions,
                # while the EventLog and Money read lane are still available.
                async with asyncio.timeout(15):
                    while (
                        self.imports
                        or self.runtime.settings_command_locks[
                            "finance-closed-package-import"
                        ].locked()
                    ):
                        await asyncio.sleep(0.01)
                await self.close()
                self.final_durable_state = durable_receipt(
                    await cases.inspect(self.runtime, self)
                )
            except BaseException as exc:
                self.receipt_failures.append(failure(exc, "durable_readback"))
            try:
                await stop()
            except BaseException as exc:
                # Retained below as a failing terminal result, independently of
                # a body/readback failure. Never stringify cleanup exceptions.
                self.receipt_failures.append(failure(exc, "runtime_shutdown"))
            else:
                self.runtime_shutdown_completed = True

        patches.setattr(self.runtime, "stop", stopped)

    async def prepare(self, request, *args):
        original = request.stream
        upload_fault = self.upload_fault
        if upload_fault:

            async def stream():
                async for chunk in original():
                    yield chunk
                if upload_fault == "cancel_upload":
                    await self.checkpoint("upload_pause")
                elif upload_fault == "revoke_upload":
                    # Trigger the existing real policy-revocation hook after
                    # body frames, before the end frame and child recheck.
                    await self.checkpoint("prepared")
                else:
                    assert self.expires is not None
                    remaining = (
                        self.expires - datetime.now(timezone.utc)
                    ).total_seconds()
                    assert 0 < remaining <= 15, "expiry was not initially fresh"
                    await asyncio.sleep(remaining + 0.1)
                self.boundaries.append(
                    {"stage": "upload_authority_changed", "fault": upload_fault}
                )

            request.stream = stream
        return await super().prepare(request, *args)


def upload_interruption(name, root):
    store = RecordedStorage(root)
    settings = store.settings()
    p, d, package = cases.fixture(settings.instance.tenant_id, total_events=24)
    manifest, packet = cases.packet_for(p, d, package)
    patches = pytest.MonkeyPatch()
    app = cases.create_application(settings)
    store.install(app, patches)
    try:
        with cases.TestClient(app) as client:
            cases.control(client, manifest["binding"])
            if name == "expiry_upload":
                from adminme.products.member import finance_import

                clock = finance_import.datetime

                class ShortClock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        RecordedStorage.expires = clock.now(tz) + timedelta(seconds=15)
                        return RecordedStorage.expires - timedelta(minutes=30)

                finance_import.datetime = ShortClock
                try:
                    approval = cases.approve(client, manifest)
                finally:
                    finance_import.datetime = clock
            else:
                approval = cases.approve(client, manifest)

            async def exercise():
                entered, release = asyncio.Event(), asyncio.Event()
                store.response = {
                    "cancellation_observed": False,
                    "same_request_task": False,
                    "cancellation_at_upload": False,
                    "operation_lock_held": False,
                    "storage_lock_held": False,
                    "import_owned": False,
                    "executor_and_stage_owned": False,
                    "preparation_returned": False,
                }

                async def hook(stage):
                    if stage == "upload_pause":
                        store.response["same_request_task"] = (
                            asyncio.current_task() is task
                        )
                        entered.set()
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            store.response.update(
                                cancellation_at_upload=True,
                                operation_lock_held=app.state.runtime.settings_command_locks[
                                    "finance-closed-package-import"
                                ].locked(),
                                storage_lock_held=store.lock.locked(),
                                import_owned=store.imports == 1,
                                executor_and_stage_owned=(
                                    store.process is not None
                                    and store.stage_descriptor is not None
                                ),
                                preparation_returned=store.preparation_complete,
                            )
                            raise

                store.hook = hook
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://synthetic"
                ) as inner:
                    task = asyncio.create_task(
                        inner.post(
                            cases.IMPORTS + approval,
                            headers=cases.DEMO_HEADERS,
                            json=packet,
                        )
                    )
                    try:
                        await asyncio.wait_for(entered.wait(), 15)
                        assert (
                            store.process is not None
                            and store.stage_descriptor is not None
                        )
                        assert (
                            store.imports
                            and app.state.runtime.settings_command_locks[
                                "finance-closed-package-import"
                            ].locked()
                        )
                        assert store.response["same_request_task"]
                        task.cancel()
                        cancelled = False
                        try:
                            await task
                        except asyncio.CancelledError:
                            cancelled = True
                        store.response["cancellation_observed"] = cancelled
                        assert cancelled
                        assert all(
                            value
                            for key, value in store.response.items()
                            if key != "preparation_returned"
                        )
                        assert not store.response["preparation_returned"]
                        assert (
                            not store.refs
                            and not store.preparation_diagnostics
                            and not store.guards
                        )
                    finally:
                        release.set()
                        if not task.done():
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                    async with asyncio.timeout(15):
                        while (
                            store.imports
                            or app.state.runtime.settings_command_locks[
                                "finance-closed-package-import"
                            ].locked()
                        ):
                            await asyncio.sleep(0.01)
                    await store.close()
                store.hook = None
                result = await cases.inspect(app.state.runtime, store)
                assert (
                    not result["finance_events"]
                    and result["counts"]["finance_publications"] == 0
                )
                return result

            if name == "expiry_upload":
                response = cases.upload(client, approval, packet)
                store.response = {
                    "status": response.status_code,
                    "state": "unexpected",
                    "reason": "synthetic_unclassified_error",
                }
                detail = response.json().get("detail", {})
                store.response = {
                    "status": response.status_code,
                    "state": "rejected"
                    if detail.get("state") == "rejected"
                    else "unexpected",
                    "reason": wire_failure(detail)["reason"],
                }
                result = client.portal.call(cases.inspect, app.state.runtime, store)
            else:
                result = client.portal.call(exercise)
        if name == "expiry_upload":
            require_expiry(
                store.response, store.executor_errors, store.final_durable_state
            )
        else:
            durable = store.final_durable_state
            assert durable["state"] == "read" and durable["finance_event_count"] == 0
            assert all(value == 0 for value in durable["counts"].values())
            assert durable["audit"] == [
                {
                    "outcome": "rejected",
                    "reason": "finance_import_request_cancelled",
                    "provider_effects_false": True,
                }
            ]
            assert not store.executor_errors
            assert len(store.lifetimes) == 1
        return {
            "case": name,
            "state": "passed",
            "result": result,
            "lifetimes": store.lifetimes,
        }
    finally:
        patches.undo()


def small_staged_case(name, root):
    if name not in (*cases.CASES, "revoke_upload", "expiry_upload", "cancel_upload"):
        raise ValueError("fixed staged correctness case only")
    cases.SyntheticStorage = RecordedStorage
    RecordedStorage.upload_fault = (
        name if name in {"revoke_upload", "expiry_upload", "cancel_upload"} else None
    )
    actual_case = "revoke_prepared" if name == "revoke_upload" else name
    result = (
        upload_interruption(name, root)
        if name in {"expiry_upload", "cancel_upload"}
        else cases.small_case(actual_case, root)
    )
    result["case"] = name
    store = RecordedStorage.instances[-1]
    assert store.process is None and store.stage_descriptor is None
    if name in {"revoke_upload", "expiry_upload"}:
        assert any(r["stage"] == "upload_authority_changed" for r in store.boundaries)
        assert not result["result"]["finance_events"]
    if name == "atomic_proof":
        # A successful real API preparation crossed reference construction and
        # the EventLog thread, then committed and replayed the full small proof.
        assert store.refs and all(r.sha256 and r.operation for r in store.refs)
    assert not list(store.root.glob("unpublished-*"))
    assert all(r["reaped"] and r["stage_removed"] for r in store.stage_lifetimes)
    starts = [r for r in store.boundaries if r["stage"] == "executor_spawned"]
    assert all(r["predecessor_reaped"] for r in starts)
    assert len({r["birth"] for r in starts}) == len(starts)
    handoffs = [
        r for r in store.boundaries if r["stage"] == "preparation_reaped_before_money"
    ]
    if result["result"]["finance_events"]:
        assert handoffs
    if name == "event_after_commit":
        # Same surviving controller already proved exact lost-child-ack recovery
        # in small_case. A lost controller has no such refs/keys/anchor.
        store.refs.clear()
        store.operation = None
        try:
            fresh_controller_recovery()
        except PublicationError as exc:
            assert str(exc) == "synthetic_controller_outcome_unknown"
        else:
            raise AssertionError("fresh controller must not invent an outcome")
        result["fresh_controller_boundary"] = "outcome_unknown_no_retry"
    result["stage_lifetimes"] = store.stage_lifetimes
    result["handoffs"] = handoffs
    result["source_digest"] = store.staging_source
    result["production_or_performance_qualification"] = False
    return result


def recorded_case(name, root):
    failures = []
    try:
        small_staged_case(name, root)
    except BaseException as exc:
        failures.append(failure(exc, "case"))
    report = {
        "case": name,
        "state": "failed",
        "failures": failures,
        "final_durable_state": {"state": "unavailable"},
        "production_or_performance_qualification": False,
    }
    if len(RecordedStorage.instances) == 1:
        store = RecordedStorage.instances[0]
        failures.extend(store.receipt_failures)
        report.update(
            final_durable_state=store.final_durable_state,
            response=store.response,
            executor_errors=store.executor_errors,
            source_digest=store.staging_source,
            cleanup={
                "runtime_shutdown_completed": store.runtime_shutdown_completed,
                "executor_active": store.process is not None,
                "quarantined": store.quarantined,
                "stage_descriptor_present": store.stage_descriptor is not None,
                "stage_paths_remaining": len(list(store.root.glob("unpublished-*"))),
                "executor_lifetimes": len(store.lifetimes),
                "executor_exit_codes": [item["exit_code"] for item in store.lifetimes],
                "executors_reaped": bool(store.lifetimes)
                and all(item["reaped"] for item in store.lifetimes),
                "stage_lifetimes": len(store.stage_lifetimes),
                "stages_reaped_removed": bool(store.stage_lifetimes)
                and all(
                    item["reaped"] and item["stage_removed"]
                    for item in store.stage_lifetimes
                ),
            },
        )
        if (
            store.final_durable_state["state"] == "read"
            and store.runtime_shutdown_completed
            and not failures
        ):
            report["state"] = "passed"
    return report


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    name, root_arg = sys.argv[1:]
    root = Path(root_arg).resolve(strict=True)
    report = recorded_case(name, root)
    write_json(sys.stdout.buffer, report)
    raise SystemExit(0 if report["state"] == "passed" else 1)
