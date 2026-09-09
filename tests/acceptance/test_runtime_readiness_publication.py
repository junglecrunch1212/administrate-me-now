"""Concurrent readiness requests cannot publish older consumer checkpoints."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.simulations.source_pipeline_stress import (
    _wait_for_composition_quiescence,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-readiness-owner"
PRIVATE_MARKER = "private-readiness-publication-fixture"


def _task(settings: RuntimeSettings, key: str) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=0,
        tenant_id=settings.instance.tenant_id,
        type="task.created",
        schema_version=1,
        occurred_at="2026-08-07T12:00:00Z",
        source_adapter="fixture-readiness",
        source_account_id="fixture-readiness-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity="normal",
        idempotency_key=f"fixture-readiness:{key}",
        payload={
            "task_id": f"fixture-readiness:{key}",
            "title": PRIVATE_MARKER,
            "owner_member_id": MEMBER,
        },
    )


@pytest.mark.parametrize("first_result", ["complete", "cancel", "failure"])
def test_ready_serializes_deferred_projection_snapshots_and_releases_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_result: str
) -> None:
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=b"r" * 32,
        repository_root=REPO_ROOT,
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        member_identities={"fixture-readiness.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
    )
    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime

        async def exercise() -> None:
            assert runtime.events is not None
            assert runtime.bus is not None
            assert runtime.projections is not None
            assert runtime.observation is not None
            first_id = await runtime.events.write(_task(settings, "before"))
            await _wait_for_composition_quiescence(runtime, 30)
            original_status = runtime.projections.status
            original_refresh = runtime._refresh_capability_registry
            observation_active = await runtime.observation.is_active()
            request_name: ContextVar[str | None] = ContextVar(
                "readiness_fixture_request", default=None
            )
            first_captured = asyncio.Event()
            release_first = asyncio.Event()
            second_entered_refresh = asyncio.Event()
            second_started_collection = asyncio.Event()
            collected: dict[str, dict[str, Any]] = {}
            completed: list[tuple[str, dict[str, Any]]] = []

            async def stable_observation() -> bool:
                # The real observation value is fixed for this read-only
                # scenario. An immediate return makes entry into collection
                # observable without sleeps or a thread-pool scheduling race.
                return observation_active

            async def observe_refresh() -> None:
                if request_name.get() == "second":
                    second_entered_refresh.set()
                await original_refresh()

            async def deferred_status(**kwargs: Any) -> dict[str, dict[str, Any]]:
                name = request_name.get()
                if name == "second":
                    second_started_collection.set()
                result = await original_status(**kwargs)
                if name is not None:
                    collected[name] = result["tasks"]
                if name == "first":
                    first_captured.set()
                    await release_first.wait()
                    if first_result == "failure":
                        raise RuntimeError("fixture projection status failure")
                return result

            monkeypatch.setattr(runtime.observation, "is_active", stable_observation)
            monkeypatch.setattr(
                runtime, "_refresh_capability_registry", observe_refresh
            )
            monkeypatch.setattr(runtime.projections, "status", deferred_status)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as http:

                async def request(name: str) -> dict[str, Any]:
                    token = request_name.set(name)
                    try:
                        response = await http.get("/api/system/ready")
                        # Absence of a live Gateway in this credential-free
                        # composition may block release readiness. Its actual
                        # consumer evidence must still remain coherent.
                        assert response.status_code in {200, 503}, response.text
                        payload = response.json()
                        completed.append((name, payload))
                        return payload
                    finally:
                        request_name.reset(token)

                first = asyncio.create_task(request("first"))
                second: asyncio.Task[dict[str, Any]] | None = None
                try:
                    await asyncio.wait_for(first_captured.wait(), timeout=30)
                    assert collected["first"]["checkpoint"] >= first_id
                    # Advance the real durable bus while the earlier complete
                    # projection snapshot is retained inside its request.
                    second_id = await runtime.events.write(_task(settings, "after"))
                    await _wait_for_composition_quiescence(runtime, 30)
                    current = await runtime.bus.subscriber_status("projection:tasks")
                    assert current["checkpoint_event_id"] >= second_id
                    assert collected["first"]["checkpoint"] < second_id

                    second = asyncio.create_task(request("second"))
                    await asyncio.wait_for(second_entered_refresh.wait(), timeout=30)
                    overtook = second_started_collection.is_set()
                    if overtook:
                        # The old implementation reaches here: finish its newer
                        # request first, then let the retained older snapshot
                        # overwrite it. This makes the regression deterministic.
                        await asyncio.wait_for(asyncio.shield(second), timeout=30)
                    if first_result == "cancel":
                        first.cancel()
                    release_first.set()
                    results = await asyncio.wait_for(
                        asyncio.gather(first, second, return_exceptions=True),
                        timeout=30,
                    )
                    assert not overtook, "later refresh overtook a retained snapshot"
                    if first_result == "complete":
                        assert all(isinstance(result, dict) for result in results)
                        assert [name for name, _payload in completed] == [
                            "first",
                            "second",
                        ]
                    elif first_result == "cancel":
                        assert isinstance(results[0], asyncio.CancelledError)
                    else:
                        assert isinstance(results[0], RuntimeError)
                        assert str(results[0]) == "fixture projection status failure"
                    assert isinstance(results[1], dict)
                    checkpoints = [
                        payload["capabilities"]["projection:tasks"]["details"][
                            "checkpoint_event_id"
                        ]
                        for _name, payload in completed
                    ]
                    assert checkpoints == sorted(checkpoints)
                    assert checkpoints[-1] >= second_id
                    assert collected["second"]["checkpoint"] >= second_id
                    for _name, payload in completed:
                        consumer = payload["capabilities"]["projection:tasks"][
                            "details"
                        ]
                        assert consumer["version"] == collected["second"]["version"]
                        assert consumer["consecutive_failures"] == 0
                        assert consumer["poison_event_id"] is None
                        assert PRIVATE_MARKER not in json.dumps(payload)
                    assert runtime.fixture_adapter.delivered == []
                finally:
                    release_first.set()
                    pending = [first] + ([second] if second is not None else [])
                    for task in pending:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

        client.portal.call(exercise)
