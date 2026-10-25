"""Small API/lifecycle cases in a disposable process, never the large fixture."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import resource
import sys
import traceback

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adminme.finance.publication import PublicationError
from adminme.finance.worker_protocol import write_json
from tests.fixtures import finance_storage_cases as cases
from tests.fixtures.finance_staging import StagedStorage, fresh_controller_recovery


class RecordedStorage(StagedStorage):
    instances = []
    upload_fault = None
    expires = None

    def __init__(self, root, **kwargs):
        super().__init__(root, **kwargs)
        self.instances.append(self)

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


def cancelled_upload(root):
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
            approval = cases.approve(client, manifest)

            async def exercise():
                entered, release = asyncio.Event(), asyncio.Event()

                async def hook(stage):
                    if stage == "upload_pause":
                        entered.set()
                        await release.wait()

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
                    await asyncio.wait_for(entered.wait(), 15)
                    assert (
                        store.process is not None and store.stage_descriptor is not None
                    )
                    task.cancel()
                    release.set()
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

            result = client.portal.call(exercise)
        return {
            "case": "cancel_upload",
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
    actual_case = (
        "revoke_prepared"
        if name == "revoke_upload"
        else "role_before_commit"
        if name == "expiry_upload"
        else name
    )
    if name == "expiry_upload":
        from adminme.products.member import finance_import

        normal_approve = cases.approve
        clock = finance_import.datetime

        def short_approval(client, manifest):
            class ShortClock(datetime):
                @classmethod
                def now(cls, tz=None):
                    real = clock.now(tz)
                    RecordedStorage.expires = real + timedelta(seconds=15)
                    return RecordedStorage.expires - timedelta(minutes=30)

            finance_import.datetime = ShortClock
            try:
                return normal_approve(client, manifest)
            finally:
                finance_import.datetime = clock

        cases.approve = short_approval
    result = (
        cancelled_upload(root)
        if name == "cancel_upload"
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


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    name, root_arg = sys.argv[1:]
    root = Path(root_arg).resolve(strict=True)
    try:
        write_json(sys.stdout.buffer, small_staged_case(name, root))
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        raise
