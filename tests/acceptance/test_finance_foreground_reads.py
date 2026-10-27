"""Ordinary household reads remain responsive beside projection work."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

import pytest
from fastapi.testclient import TestClient

from adminme.products.core import main as core
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import enabled_settings
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, PRINCIPAL


@pytest.mark.parametrize(
    "path,query_name",
    [
        ("/api/core/v1/what-now", "_what_now"),
        ("/api/core/v1/commitments/active", "active_for_member"),
    ],
)
def test_slow_household_read_does_not_block_http_liveness(
    tmp_path, monkeypatch, path, query_name
):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        entered = threading.Event()
        release = threading.Event()
        original = getattr(core, query_name)

        def blocked_query(*args, **kwargs):
            entered.set()
            assert release.wait(10), "synthetic read was not released"
            return original(*args, **kwargs)

        monkeypatch.setattr(core, query_name, blocked_query)
        with ThreadPoolExecutor(max_workers=2) as callers:
            read = callers.submit(client.get, path, headers=DEMO_HEADERS)
            try:
                assert entered.wait(5), "household read did not start"
                health = callers.submit(client.get, "/api/core/status")
                assert health.result(timeout=2).status_code == 200
                assert not read.done()
            finally:
                release.set()
            response = read.result(timeout=5)
        assert response.status_code == 200, response.text
        assert ("ranked" if query_name == "_what_now" else "items") in response.json()


@pytest.mark.parametrize(
    "path,read_name",
    [
        ("/api/core/v1/what-now", "read_many"),
        ("/api/core/v1/commitments/active", "read"),
    ],
)
@pytest.mark.parametrize("change", ["role", "login_binding"])
def test_household_read_rechecks_identity_after_worker_yield(
    tmp_path, monkeypatch, path, read_name, change
):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        runtime = client.app.state.runtime
        original = getattr(runtime.projections, read_name)
        changed = False

        async def read_then_change(*args, **kwargs):
            nonlocal changed
            result = await original(*args, **kwargs)
            if not changed:
                changed = True
                if change == "role":
                    runtime.settings = replace(
                        runtime.settings,
                        member_roles={
                            **runtime.settings.member_roles,
                            PRINCIPAL: "child",
                        },
                    )
                else:
                    runtime.settings = replace(runtime.settings, member_identities={})
            return result

        monkeypatch.setattr(runtime.projections, read_name, read_then_change)
        response = client.get(path, headers=DEMO_HEADERS)
        assert changed and response.status_code in {401, 403}, response.text
        assert "ranked" not in response.text and "items" not in response.text
