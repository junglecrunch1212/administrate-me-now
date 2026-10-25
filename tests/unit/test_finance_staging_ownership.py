"""Synthetic ASGI ownership and unfinished-upload cleanup; no provider connections."""

import asyncio
from types import SimpleNamespace

import pytest

from adminme.finance.import_work import await_settled
from tests.fixtures import finance_staging as staging


@pytest.mark.parametrize("cancel", [False, True])
def test_owner_remains_in_domain_task_until_audit_and_operation_settle(cancel):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        operation = asyncio.Lock()
        observations = []

        async def close():
            assert not operation.locked() and storage.imports == 0
            assert observations[-1] == "domain_settled"
            observations.append("closed")

        storage = SimpleNamespace(imports=0, close=close)

        async def audit():
            await asyncio.sleep(0)
            assert operation.locked() and storage.imports == 1
            observations.append("audit_settled")

        async def domain(*_args):
            assert asyncio.current_task() is request
            async with operation:
                assert storage.imports == 1
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    observations.append("cancel_at_upload")
                    await await_settled(audit())
                    raise
                finally:
                    observations.append("domain_settled")

        wrapper = staging.StagedImportOwnership(domain, storage)
        request = asyncio.create_task(
            wrapper(
                {"type": "http", "path": "/api/member/v1/finance/imports/test"},
                None,
                None,
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            release.set()
            await request
        assert observations == (
            ["cancel_at_upload", "audit_settled"] if cancel else []
        ) + ["domain_settled", "closed"]
        assert storage.imports == 0 and not operation.locked()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "scope", [{"type": "lifespan"}, {"type": "http", "path": "/api/member/v1/status"}]
)
def test_other_scopes_pass_without_storage_ownership(scope):
    async def exercise():
        async def close():
            raise AssertionError("unrelated request closed storage")

        storage = SimpleNamespace(imports=0, close=close)

        async def domain(actual, receive, send):
            assert actual is scope and storage.imports == 0
            return "passed"

        assert (
            await staging.StagedImportOwnership(domain, storage)(scope, None, None)
            == "passed"
        )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "prepared,uncertain", [(False, False), (True, False), (False, True)]
)
def test_upload_abort_confirms_reap_before_stage_cleanup(
    monkeypatch, tmp_path, prepared, uncertain
):
    store = staging.StagedStorage.__new__(staging.StagedStorage)
    store.__dict__.update(
        root=tmp_path,
        process=SimpleNamespace(pid=123),
        stage_descriptor={"operation": "test-operation"},
        preparation_complete=prepared,
        quarantined=False,
        stage_lifetimes=[],
    )
    calls = []

    def reap(self, graceful=True):
        calls.append(("reap", graceful))
        if uncertain:
            self.quarantined = True
            raise OSError("test wait uncertainty")
        self.process = None

    def cleanup(root, operation):
        assert store.process is None and not store.quarantined
        assert root == tmp_path and operation == "test-operation"
        calls.append(("cleanup", True))

    monkeypatch.setattr(staging.SyntheticStorage, "_reap", reap)
    monkeypatch.setattr(staging, "cleanup", cleanup)
    if uncertain:
        with pytest.raises(OSError):
            store._reap()
        assert calls == [("reap", False)]
        assert store.stage_descriptor is not None and store.process is not None
        assert not store.stage_lifetimes and store.quarantined
    else:
        store._reap()
        assert calls == [("reap", prepared), ("cleanup", True)]
        assert store.stage_descriptor is None and store.process is None
        assert store.stage_lifetimes == [
            {
                "operation": "test-operation",
                "pid": 123,
                "reaped": True,
                "stage_removed": True,
            }
        ]
