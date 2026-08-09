"""Process-level contract tests for the AdministrateMe composition root."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.session import Session
from adminme.lib.skill_runner import wrapper as skill_wrapper
from adminme.lib.skill_runner.wrapper import SkillContext
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
    "pipeline:deterministic_source_candidates",
    "pipeline:lifecycle_maintenance",
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


def test_source_assurance_refresh_never_blocks_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert runtime._source_assurance_cache is not None
        cached = dict(runtime._source_assurance_cache)
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_REFRESH_SECONDS - 1
        )
        refresh_calls = 0

        async def slow_refresh(*_args: object, **_kwargs: object) -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            await asyncio.sleep(0.5)
            return cached

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", slow_refresh
        )

        started = time.perf_counter()
        first = client.get("/api/system/ready")
        elapsed = time.perf_counter() - started
        second = client.get("/api/system/ready")

        assert elapsed < 0.2
        assert (
            first.json()["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["state"]
            == "fresh"
        )
        assert (
            first.json()["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["refresh_in_progress"]
            is True
        )
        assert second.status_code == first.status_code
        assert refresh_calls == 1

        client.portal.call(asyncio.sleep, 0.55)
        refreshed = client.get("/api/system/ready").json()
        assert (
            refreshed["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["state"]
            == "fresh"
        )
        assert (
            refreshed["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["refresh_in_progress"]
            is False
        )


def test_over_age_source_assurance_proof_degrades_during_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_MAX_STALE_SECONDS - 1
        )

        async def hung_refresh(*_args: object, **_kwargs: object) -> dict[str, object]:
            await asyncio.sleep(60)
            return {}

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", hung_refresh
        )

        response = client.get("/api/system/ready").json()
        capability = response["capabilities"]["policy:source-assurance"]
        assert capability["state"] == "degraded"
        assert capability["details"]["freshness"]["state"] == "stale"
        assert capability["details"]["freshness"]["refresh_in_progress"] is True


def test_failed_source_assurance_refresh_degrades_without_retry_storm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_REFRESH_SECONDS - 1
        )
        refresh_calls = 0

        async def failed_refresh(
            *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            raise RuntimeError("content-free fixture failure")

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", failed_refresh
        )

        client.get("/api/system/ready")
        client.portal.call(asyncio.sleep, 0.01)
        first_failed = client.get("/api/system/ready").json()
        second_failed = client.get("/api/system/ready").json()

        assert refresh_calls == 1
        for diagnostics in (first_failed, second_failed):
            capability = diagnostics["capabilities"]["policy:source-assurance"]
            assert capability["state"] == "degraded"
            assert capability["details"]["freshness"]["state"] == "failed"
            assert capability["details"]["freshness"]["refresh_error_type"] == (
                "RuntimeError"
            )


def test_production_composition_injects_skill_gateway_auth_without_ambient_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "composition-gateway-token-at-least-24-characters"
    (tmp_path / "instance").mkdir()
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        openclaw_enabled=True,
        openclaw_shared_secret=token,
        openclaw_actor_identities={"portal|central|member-a": "member-a"},
        openclaw_gateway_token=token,
        member_roles={"member-a": "principal"},
    )
    captured: dict[str, object] = {}

    async def invoke(**kwargs: object) -> skill_wrapper._OpenClawCallOutcome:
        captured.update(kwargs)
        return skill_wrapper._OpenClawCallOutcome(
            kind="ok",
            raw_result={"is_thing": True, "confidence": 0.9},
            invocation_id="composition-invocation",
            provider_model="fixture/composition",
        )

    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(skill_wrapper, "_post_one_provider", invoke)
    app = create_application(settings)
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        session = Session(
            tenant_id=runtime.settings.instance.tenant_id,
            auth_member_id="member-a",
            auth_role="principal",
            view_member_id="member-a",
            view_role="principal",
            dm_scope="per_channel_peer",
            source="product_api_internal",
        )
        result = client.portal.call(
            runtime.run_skill_fn,
            "skill:classify_test",
            {"text": "hi"},
            SkillContext(session=session),
        )

    assert result.output == {"is_thing": True, "confidence": 0.9}
    assert captured["token"] == token


def test_reviewed_household_enrollment_becomes_idempotent_people_truth(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    mapping = {
        "schema_version": 2,
        "household_label": "Fixture household",
        "members": [
            {"member_id": "member-a", "label": "Member A", "role": "principal"},
            {"member_id": "member-b", "label": "Member B", "role": "principal"},
        ],
        "parties": [
            {
                "party_id": "party-contact",
                "label": "Outside Contact",
                "kind": "person",
                "visibility_scope": "shared:household",
            }
        ],
        "memberships": [
            {
                "membership_id": "membership-a",
                "party_id": "member-a",
                "role": "principal",
            },
            {
                "membership_id": "membership-b",
                "party_id": "member-b",
                "role": "principal",
            },
        ],
        "relationships": [
            {
                "relationship_id": "relationship-partners",
                "from_party_id": "member-a",
                "to_party_id": "member-b",
                "kind": "spouse",
                "direction": "mutual",
            },
            {
                "relationship_id": "relationship-contact",
                "from_party_id": "member-b",
                "to_party_id": "party-contact",
                "kind": "coparent",
                "direction": "mutual",
            },
        ],
    }
    settings = RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        member_roles={"member-a": "principal", "member-b": "principal"},
        household_mapping=mapping,
    )

    enrollment_event_ids: list[str] = []
    for run in range(2):
        app = create_application(settings)
        with TestClient(app) as client:
            response = client.get(
                "/api/member/v1/surfaces/people",
                headers={"Tailscale-User-Login": "member-a.ts.net"},
            )
            assert response.status_code == 200, response.text
            cockpit = response.json()["cockpit"]
            assert cockpit["contract"] == "deterministic-people-cockpit-v1"
            assert {person["party_id"] for person in cockpit["directory"]} == {
                "member-a",
                "member-b",
                "party-contact",
            }
            assert cockpit["lens_counts"]["household"] == 3
            assert (
                next(
                    person
                    for person in cockpit["directory"]
                    if person["party_id"] == "member-a"
                )["relationship_label"]
                == "You"
            )
            assert (
                next(
                    person
                    for person in cockpit["directory"]
                    if person["party_id"] == "member-b"
                )["relationship_label"]
                == "Spouse"
            )
            assert cockpit["sources"] == []

            search = client.get(
                "/api/member/v1/people/search?q=Outside",
                headers={"Tailscale-User-Login": "member-a.ts.net"},
            )
            assert search.status_code == 200
            assert [item["party_id"] for item in search.json()["results"]] == [
                "party-contact"
            ]

            async def enrollment_ids() -> list[str]:
                assert app.state.runtime.event_log is not None
                return [
                    event["event_id"]
                    async for event in app.state.runtime.event_log.read_since()
                    if event["source_adapter"] == "bootstrap-household-enrollment"
                ]

            current_ids = client.portal.call(enrollment_ids)
            if run == 0:
                enrollment_event_ids = current_ids
                assert len(current_ids) == 8
            else:
                assert current_ids == enrollment_event_ids


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
