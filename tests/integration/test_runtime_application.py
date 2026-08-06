"""Process-level contract tests for the AdministrateMe composition root."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import (
    AdministrateMeRuntime,
    RuntimeSettings,
    create_application,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"r" * 32

PROJECTIONS = {
    "parties",
    "interactions",
    "artifacts",
    "commitments",
    "tasks",
    "recurrences",
    "calendars",
    "places_assets_accounts",
    "money",
    "vector_search",
    "xlsx_workbooks",
    "member_knowledge",
    "lists",
    "triage",
}

REACTIVE_PIPELINES = {
    "pipeline:commitment_extraction",
    "pipeline:identity_resolution",
    "pipeline:member_knowledge_indexing",
    "pipeline:noise_filtering",
    "pipeline:reward_dispatch",
    "pipeline:thank_you_detection",
}


def _settings(tmp_path: Path) -> RuntimeSettings:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
    )


def _bridge_envelope() -> dict[str, object]:
    return EventEnvelope(
        event_at_ms=1_780_000_000_000,
        tenant_id="tenant-instance",
        type="bridge.enrolled",
        schema_version=1,
        occurred_at="2026-05-28T12:00:00Z",
        source_adapter="bridge-test",
        source_account_id="bridge-node-a",
        owner_scope="private:member-a",
        visibility_scope="private:member-a",
        sensitivity="normal",
        payload={
            "member_id": "member-a",
            "bridge_node_id": "bridge-node-a",
            "tailscale_identity": "member-a.ts.net",
            "adapters_active": ["apple-notes"],
            "enrolled_at": "2026-05-28T12:00:00Z",
        },
    ).model_dump(mode="json")


def test_application_boots_with_complete_truthful_inventory_and_routes(
    tmp_path: Path,
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert set(runtime.projections.projection_names()) == PROJECTIONS
        assert set(runtime.pipelines.registered_pack_ids()) == REACTIVE_PIPELINES

        diagnostics = client.get("/api/system/ready")
        assert diagnostics.status_code == 503
        body = diagnostics.json()
        assert body["status"] == "blocked"
        assert body["ready"] is False
        assert body["observation_active"] is True
        assert {
            key.removeprefix("projection:")
            for key in body["capabilities"]
            if key.startswith("projection:")
        } == PROJECTIONS

        assert client.get("/api/system/live").json() == {
            "status": "alive",
            "runtime_started": True,
        }
        for product in ("core", "comms", "capture", "automation"):
            response = client.get(f"/api/{product}/status")
            assert response.status_code == 200
            assert response.json()["name"] == product
        assert client.get("/api/bridge/health").status_code == 200


def test_optional_unconfigured_chat_does_not_block_runtime_readiness(
    tmp_path: Path,
) -> None:
    (tmp_path / "instance").mkdir()
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        member_chat_enabled_members={"member-a"},
    )
    settings = replace(
        settings,
        openclaw_enabled=True,
        openclaw_shared_secret="fixture-secret-at-least-24-characters",
        member_roles={"member-a": "principal"},
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        runtime.openclaw_state = "unavailable"
        response = client.get("/api/system/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["status"] == "degraded"
        assert body["capabilities"]["openclaw:member-chat"]["state"] == "blocked"

        ingest = client.post(
            "/api/bridge/ingest",
            json=_bridge_envelope(),
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert ingest.status_code == 202, ingest.text
        assert ingest.json()["owner_scope"] == "private:member-a"


async def test_repeated_shutdown_is_safe_and_log_reopens(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runtime = AdministrateMeRuntime(settings)
    await runtime.start()
    await runtime.stop()
    await runtime.stop()
    assert runtime.started is False
    assert runtime.events is not None and runtime.events.accepting is False

    reopened = EventLog(settings.instance, TEST_KEY)
    try:
        events = [event async for event in reopened.read_since()]
        assert any(event["type"] == "runtime.started" for event in events)
    finally:
        await reopened.close()
        await reopened.close()


async def test_startup_failure_unwinds_all_open_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_start(_self: AdapterSupervisor) -> None:
        raise RuntimeError("simulated adapter supervisor startup failure")

    monkeypatch.setattr(AdapterSupervisor, "start", fail_start)
    settings = _settings(tmp_path)
    runtime = AdministrateMeRuntime(settings)
    with pytest.raises(RuntimeError, match="simulated adapter supervisor"):
        await runtime.start()

    assert runtime.started is False
    assert runtime.startup_error is not None
    assert runtime.events is not None and runtime.events.accepting is False
    await runtime.stop()

    reopened = EventLog(settings.instance, TEST_KEY)
    await reopened.close()
