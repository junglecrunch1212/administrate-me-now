"""Authenticated Settings stays bounded while real delivery keeps advancing."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from adminme.simulations.source_pipeline_stress import (
    _wait_for_composition_quiescence,
)
from tests.acceptance.test_source_policy_v2 import (
    A,
    A_HEADERS,
    B,
    B_HEADERS,
    _settings,
)


PRIVATE_TEXT = "private-bounded-readiness-task-text"


def _task(runtime: Any, key: str) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=0,
        tenant_id=runtime.settings.instance.tenant_id,
        type="task.created",
        schema_version=1,
        occurred_at="2026-08-07T12:00:00Z",
        source_adapter="fixture-bounded-readiness",
        source_account_id="fixture-bounded-readiness-account",
        owner_scope=f"private:{A}",
        visibility_scope=f"private:{A}",
        idempotency_key=f"fixture-bounded-readiness:{key}",
        payload={
            "task_id": f"fixture-bounded-readiness:{key}",
            "title": PRIVATE_TEXT,
            "owner_member_id": A,
        },
    )


def test_settings_bounds_status_work_during_advancement_and_preserves_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "bounded-settings"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime

        async def exercise() -> None:
            assert runtime.events is not None
            assert runtime.event_log is not None
            assert runtime.bus is not None
            assert runtime.projections is not None
            assert runtime.pipelines is not None
            await runtime.events.write(_task(runtime, "initial"))
            await _wait_for_composition_quiescence(runtime, 30)

            request_name: ContextVar[str | None] = ContextVar(
                "bounded_settings_request", default=None
            )
            subscriber: ContextVar[tuple[str, ...]] = ContextVar(
                "bounded_settings_subscriber", default=()
            )
            original_count = runtime.event_log.count_since_many
            original_status = runtime.bus.subscriber_statuses
            original_refresh = runtime._refresh_capability_registry
            original_diagnostics = runtime.diagnostics
            original_row_counts = runtime.projections._collect_row_counts_for
            counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            refreshes: dict[str, int] = defaultdict(int)
            diagnostics_modes: dict[str, list[bool]] = defaultdict(list)
            pairs: dict[str, tuple[str, int, str]] = {}
            reported: dict[str, dict[str, Any]] = {}
            row_count_calls: list[str] = []

            async def observe_status(subscriber_ids, *, ignore_missing=False):
                token = subscriber.set(tuple(subscriber_ids))
                try:
                    result = await original_status(
                        subscriber_ids, ignore_missing=ignore_missing
                    )
                    name = request_name.get()
                    if name is not None and "projection:tasks" in result:
                        reported[name] = result["projection:tasks"]
                    return result
                finally:
                    subscriber.reset(token)

            async def advancing_count(queries):
                name = request_name.get()
                subscriber_ids = subscriber.get()
                if name is None or not subscriber_ids:
                    return await original_count(queries)
                task_count = None
                for index, (subscriber_id, (cursor, _types)) in enumerate(
                    zip(subscriber_ids, queries, strict=True)
                ):
                    counts[name][subscriber_id] += 1
                    assert counts[name][subscriber_id] == 1
                    if subscriber_id == "projection:tasks":
                        assert cursor is not None
                        token = request_name.set(None)
                        try:
                            event_id = await runtime.events.write(
                                _task(runtime, f"{name}:{counts[name][subscriber_id]}")
                            )
                            await runtime.wait_for_projection(
                                "tasks", event_id, timeout_s=30
                            )
                        finally:
                            request_name.reset(token)
                        advanced = runtime.bus._subscribers[subscriber_id].checkpoint
                        assert advanced is not None and advanced > cursor
                        task_count = (index, cursor, advanced)
                # Execute the real native batch after advancing a captured
                # checkpoint, rather than substituting independent counts.
                lags = await original_count(queries)
                if task_count is not None:
                    index, cursor, advanced = task_count
                    assert lags[index] > 0
                    pairs[name] = (cursor, lags[index], advanced)
                return lags

            async def observe_refresh() -> None:
                name = request_name.get()
                if name is not None:
                    refreshes[name] += 1
                await original_refresh()

            async def observe_diagnostics(
                *, refresh_capabilities: bool = True
            ) -> dict[str, Any]:
                name = request_name.get()
                if name is not None:
                    diagnostics_modes[name].append(refresh_capabilities)
                return await original_diagnostics(
                    refresh_capabilities=refresh_capabilities
                )

            def observe_row_counts(projection_name: str) -> dict[str, int]:
                row_count_calls.append(projection_name)
                return original_row_counts(projection_name)

            monkeypatch.setattr(runtime.event_log, "count_since_many", advancing_count)
            monkeypatch.setattr(runtime.bus, "subscriber_statuses", observe_status)
            monkeypatch.setattr(
                runtime, "_refresh_capability_registry", observe_refresh
            )
            monkeypatch.setattr(runtime, "diagnostics", observe_diagnostics)
            monkeypatch.setattr(
                runtime.projections, "_collect_row_counts_for", observe_row_counts
            )
            expected_consumers = {
                f"projection:{name}" for name in runtime.projections.projection_names()
            } | {f"pipeline:{name}" for name in runtime.pipelines.registered_pack_ids()}
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as http:

                async def request(
                    name: str, path: str, headers: dict[str, str]
                ) -> dict[str, Any]:
                    token = request_name.set(name)
                    try:
                        response = await asyncio.wait_for(
                            http.get(path, headers=headers), timeout=30
                        )
                        if path.endswith("/settings"):
                            assert response.status_code == 200, response.text
                        else:
                            assert response.status_code in {200, 503}, response.text
                        return response.json()
                    finally:
                        request_name.reset(token)

                for name, member, headers, forbidden in (
                    ("owner-a", A, A_HEADERS, "provider-account-b"),
                    ("owner-b", B, B_HEADERS, "provider-account-a"),
                ):
                    payload = await request(
                        name, "/api/member/v1/surfaces/settings", headers
                    )
                    control = payload["control"]
                    sources = control["source_policy_v2"]
                    assert sources["owner_visible"] is True
                    assert sources["owner_principal_id"] == member
                    assert all(
                        row["coordinate"] is None
                        or row["coordinate"]["owner_principal_id"] == member
                        for row in sources["sources"]
                    )
                    assert forbidden not in json.dumps(control)
                    assert PRIVATE_TEXT not in json.dumps(payload)
                    assert payload["observation"]["active"] is True
                    assert diagnostics_modes[name] == [False]
                    details = payload["system"]["runtime"]["capabilities"][
                        "projection:tasks"
                    ]["details"]
                    assert details["checkpoint_event_id"] == pairs[name][0]
                    assert details["lag_count"] == pairs[name][1]
                    assert details["poison_event_id"] is None
                    assert details["consecutive_failures"] == 0

                viewed = await request(
                    "view-as",
                    "/api/member/v1/surfaces/settings",
                    {**A_HEADERS, "X-AdminMe-View-Member": B},
                )
                assert viewed["control"]["source_policy_v2"]["owner_visible"] is False
                assert viewed["control"]["source_policy_v2"]["sources"] == []
                assert viewed["control"]["connections"] == []
                assert diagnostics_modes["view-as"] == [False]

                # General readiness keeps its fresh-read default. Its next
                # captured checkpoint advances beyond the prior observation.
                ready = await request("ready", "/api/system/ready", A_HEADERS)
                assert diagnostics_modes["ready"] == [True]
                assert pairs["ready"][0] >= pairs["view-as"][2]
                details = ready["capabilities"]["projection:tasks"]["details"]
                assert details["checkpoint_event_id"] == pairs["ready"][0]
                assert details["lag_count"] == pairs["ready"][1]

            assert set(counts) == {"owner-a", "owner-b", "view-as", "ready"}
            for name, per_consumer in counts.items():
                assert set(per_consumer) == expected_consumers
                assert set(per_consumer.values()) == {1}
                assert refreshes[name] == 1
                assert reported[name]["checkpoint_event_id"] == pairs[name][0]
                assert reported[name]["lag_count"] == pairs[name][1]
                assert pairs[name][0] < pairs[name][2]
            assert row_count_calls == []
            assert runtime.fixture_adapter.delivered == []

        client.portal.call(exercise)


def test_settings_reports_actual_callback_poison_during_its_status_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "failed-settings"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime

        async def exercise() -> None:
            assert runtime.events is not None
            assert runtime.event_log is not None
            assert runtime.bus is not None
            assert runtime.projections is not None
            await runtime.events.write(_task(runtime, "failure-initial"))
            await _wait_for_composition_quiescence(runtime, 30)
            state = runtime.bus._subscribers["projection:tasks"]
            assert state.degraded is False
            assert state.consecutive_failures == 0
            assert state.poison_event_id is None
            projection = runtime.projections._projections["tasks"]
            original_apply = projection.apply
            original_count = runtime.event_log.count_since_many
            original_status = runtime.bus.subscriber_statuses
            request_name: ContextVar[str | None] = ContextVar(
                "failed_settings_request", default=None
            )
            subscriber: ContextVar[tuple[str, ...]] = ContextVar(
                "failed_settings_subscriber", default=()
            )
            attempts: list[str] = []
            counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            pairs: dict[str, tuple[str, int]] = {}
            poison_ids: list[str] = []
            failure_detail = "private-bounded-readiness-callback-detail"
            failed_task_id = "fixture-bounded-readiness:failure-poison"

            def fail_selected_callback(envelope: dict[str, Any], conn: Any) -> None:
                original_apply(envelope, conn)
                if (envelope.get("payload") or {}).get("task_id") == failed_task_id:
                    # Fail inside the real projection transaction. Its runner
                    # must roll back; the durable bus owns retries and poison.
                    attempts.append(envelope["event_id"])
                    raise RuntimeError(failure_detail)

            async def observe_status(subscriber_ids, *, ignore_missing=False):
                token = subscriber.set(tuple(subscriber_ids))
                try:
                    return await original_status(
                        subscriber_ids, ignore_missing=ignore_missing
                    )
                finally:
                    subscriber.reset(token)

            async def count_after_real_failure(queries):
                name = request_name.get()
                subscriber_ids = subscriber.get()
                if name is None or not subscriber_ids:
                    return await original_count(queries)
                task_count = None
                for index, (subscriber_id, (cursor, _types)) in enumerate(
                    zip(subscriber_ids, queries, strict=True)
                ):
                    counts[name][subscriber_id] += 1
                    assert counts[name][subscriber_id] == 1
                    if subscriber_id != "projection:tasks":
                        continue
                    assert cursor is not None
                    if name == "settings":
                        token = request_name.set(None)
                        try:
                            advanced_id = await runtime.events.write(
                                _task(runtime, "failure-advance")
                            )
                            await runtime.wait_for_projection(
                                "tasks", advanced_id, timeout_s=30
                            )
                            assert (
                                state.checkpoint is not None
                                and state.checkpoint > cursor
                            )
                            poison_id = await runtime.events.write(
                                _task(runtime, "failure-poison")
                            )
                            poison_ids.append(poison_id)

                            async def wait_for_poison():
                                while not (
                                    state.degraded
                                    and state.poison_event_id == poison_id
                                ):
                                    await asyncio.sleep(0.01)

                            await asyncio.wait_for(wait_for_poison(), timeout=30)
                        finally:
                            request_name.reset(token)
                    task_count = (index, cursor)
                lags = await original_count(queries)
                if task_count is not None:
                    index, cursor = task_count
                    assert lags[index] > 0
                    pairs[name] = (cursor, lags[index])
                return lags

            monkeypatch.setattr(projection, "apply", fail_selected_callback)
            monkeypatch.setattr(runtime.bus, "subscriber_statuses", observe_status)
            monkeypatch.setattr(
                runtime.event_log, "count_since_many", count_after_real_failure
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as http:
                for name, path in (
                    ("settings", "/api/member/v1/surfaces/settings"),
                    ("ready", "/api/system/ready"),
                ):
                    token = request_name.set(name)
                    try:
                        response = await asyncio.wait_for(
                            http.get(path, headers=A_HEADERS), timeout=30
                        )
                    finally:
                        request_name.reset(token)
                    if name == "settings":
                        assert response.status_code == 200, response.text
                    else:
                        assert response.status_code in {200, 503}, response.text
                    payload = response.json()
                    diagnostic = (
                        payload["system"]["runtime"] if name == "settings" else payload
                    )
                    capability = diagnostic["capabilities"]["projection:tasks"]
                    details = capability["details"]
                    assert capability["state"] == "degraded"
                    assert details["checkpoint_event_id"] == pairs[name][0]
                    assert details["lag_count"] == pairs[name][1]
                    assert (
                        details["consecutive_failures"]
                        == runtime.bus._failure_threshold
                    )
                    assert details["poison_event_id"] == poison_ids[0]
                    assert details["last_failure_at"] is not None
                    assert diagnostic["status"] in {"degraded", "blocked"}
                    assert diagnostic["release_ready"] is False
                    assert diagnostic["activation_eligible"] is False
                    assert diagnostic["observation_active"] is True
                    assert PRIVATE_TEXT not in response.text
                    assert failure_detail not in response.text
                    if name == "settings":
                        assert "provider-account-b" not in json.dumps(
                            payload["control"]
                        )
                        sources = payload["control"]["source_policy_v2"]["sources"]
                        assert all(
                            row["coordinate"] is None
                            or row["coordinate"]["owner_principal_id"] == A
                            for row in sources
                        )

            assert pairs["settings"][0] < pairs["ready"][0] < poison_ids[0]
            assert attempts == [poison_ids[0]] * runtime.bus._failure_threshold
            assert set(counts) == {"settings", "ready"}
            assert all(
                set(per_consumer.values()) == {1} for per_consumer in counts.values()
            )
            assert (
                runtime.projections.connection("tasks")
                .execute("SELECT task_id FROM tasks WHERE task_id=?", (failed_task_id,))
                .fetchone()
                is None
            )
            assert runtime.fixture_adapter.delivered == []

        client.portal.call(exercise)
