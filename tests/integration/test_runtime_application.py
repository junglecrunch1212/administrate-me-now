"""Process-level contract tests for the AdministrateMe composition root."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.events.envelope import EventEnvelope
from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.session import Session
from adminme.lib.skill_runner import wrapper as skill_wrapper
from adminme.lib.skill_runner.wrapper import SkillContext
from adminme.runtime.application import (
    AdministrateMeRuntime,
    RuntimeSettings,
    create_application,
)
from adminme.source_assessment_recovery import latest_source_assessment
from bootstrap.contracts import PROJECTION_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"r" * 32

PROJECTIONS = set(PROJECTION_NAMES)

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


class _CalendarListRequest:
    def __init__(self, service: "_CalendarProtocolService") -> None:
        self._service = service

    def execute(self) -> dict[str, Any]:
        if self._service.pages:
            value = self._service.pages.pop(0)
            if isinstance(value, BaseException):
                raise value
            return dict(value)
        return {"items": []}


class _CalendarEventsResource:
    def __init__(self, service: "_CalendarProtocolService") -> None:
        self._service = service

    def list(self, **kwargs: Any) -> _CalendarListRequest:
        self._service.list_calls.append(dict(kwargs))
        return _CalendarListRequest(self._service)


class _CalendarProtocolService:
    def __init__(self, pages: list[dict[str, Any] | BaseException]) -> None:
        self.pages = list(pages)
        self.list_calls: list[dict[str, Any]] = []

    def events(self) -> _CalendarEventsResource:
        return _CalendarEventsResource(self)


def _calendar_runtime_settings(tmp_path: Path) -> RuntimeSettings:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        member_roles={"member-a": "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id="member-a",
        openclaw_enabled=True,
        openclaw_shared_secret="calendar-fixture-secret-at-least-24-characters",
        openclaw_model_configured=True,
        member_chat_enabled_members={"member-a"},
        deterministic_member_chat_fixture=True,
        adapter_config={
            "adapter_supervisor_port": 0,
            "adapters": [
                {
                    "id": "calendar-runtime-fixture",
                    "kind": "calendaring-google-calendar",
                    "config": {
                        "oauth_credentials_ref": "keychain://fixture/calendar",
                        "calendar_id": "primary",
                        "member_id": "member-a",
                        "provider_account_id": "calendar-account-a",
                        "calendar_name": "Personal calendar",
                        "poll_interval_s": 60,
                    },
                }
            ],
        },
    )


def _install_calendar_protocol(
    monkeypatch: pytest.MonkeyPatch,
    service: _CalendarProtocolService,
    *,
    max_poll_cycles: int | None = None,
) -> None:
    from adminme.adapters.loader import load_builtin_adapters as real_loader

    def fixture_loader(config: dict[str, Any], *, repository_root: Path) -> list[Any]:
        loaded = real_loader(config, repository_root=repository_root)
        for adapter in loaded:
            if adapter.name != "calendaring-google-calendar":
                continue
            adapter._credentials_resolver = lambda _ref: {}
            adapter._service_factory = lambda *_args: service
            adapter._poll_interval_s = 0
            adapter._max_poll_cycles = max_poll_cycles
        return loaded

    monkeypatch.setattr(
        "adminme.runtime.application.load_builtin_adapters", fixture_loader
    )


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


def test_calendar_real_protocol_recovery_reaches_runtime_and_member_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _CalendarProtocolService(
        [
            ConnectionResetError("credential-free transient"),
            {
                "items": [
                    {
                        "id": "runtime-event",
                        "etag": "runtime-revision-1",
                        "summary": "Credential-free calendar event",
                        "start": {"dateTime": "2026-05-28T15:00:00Z"},
                        "end": {"dateTime": "2026-05-28T16:00:00Z"},
                        "updated": "2026-05-28T14:00:00Z",
                    },
                    {
                        "id": "runtime-deletion",
                        "etag": "runtime-revision-2",
                        "status": "cancelled",
                        "updated": "2026-05-28T14:01:00Z",
                    },
                ],
                "nextSyncToken": "runtime-sync-token",
            },
        ]
    )
    _install_calendar_protocol(monkeypatch, service)
    app = create_application(_calendar_runtime_settings(tmp_path))
    with TestClient(app) as client:
        deadline = time.monotonic() + 2
        diagnostics: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = client.get("/api/system/ready")
            diagnostics = response.json()
            capability = diagnostics["capabilities"][
                "adapter:calendaring-google-calendar"
            ]
            if response.status_code == 200 and capability["state"] in {
                "healthy",
                "observation_only",
            }:
                break
            time.sleep(0.01)
        else:
            raise AssertionError(diagnostics)

        runtime: AdministrateMeRuntime = app.state.runtime
        durable = runtime.adapter_cursor_store.get("calendar-runtime-fixture")
        assert durable.provider_watermark is not None
        assert durable.circuit_state == "closed"
        assert durable.error_count == 0
        assert diagnostics["ready"] is True
        assert diagnostics["configured_adapter_degradations"] == []
        assert diagnostics["observation_active"] is True

        settings = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert settings.status_code == 200, settings.text
        [connection] = settings.json()["control"]["connections"]
        assert connection["id"] == "calendar-runtime-fixture"
        assert connection["runtime"] == "running"
        assert connection["setup"] == "connected"
        assert connection["safety"]["observation_only"] is True
        rendered = settings.text
        assert "Credential-free calendar event" not in rendered
        assert "runtime-sync-token" not in rendered
        assert "keychain://" not in rendered

        async def calendar_facts() -> list[str]:
            assert runtime.event_log is not None
            return [
                event["type"]
                async for event in runtime.event_log.read_since(
                    types=["calendar.event_added", "calendar.event_deleted"]
                )
            ]

        facts = client.portal.call(calendar_facts)
        assert facts.count("calendar.event_added") == 1
        assert facts.count("calendar.event_deleted") == 1


def test_calendar_malformed_protocol_fails_readiness_and_member_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _CalendarProtocolService(
        [
            {
                "items": [
                    {
                        "summary": "No provider id",
                        "start": {"dateTime": "2026-05-28T15:00:00Z"},
                        "end": {"dateTime": "2026-05-28T16:00:00Z"},
                    }
                ],
                "nextSyncToken": "must-not-advance",
            }
        ]
    )
    _install_calendar_protocol(monkeypatch, service, max_poll_cycles=1)
    app = create_application(_calendar_runtime_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/system/ready")
        assert response.status_code == 503
        diagnostics = response.json()
        assert diagnostics["ready"] is False
        assert diagnostics["configured_adapter_degradations"] == [
            "adapter:calendaring-google-calendar"
        ]
        capability = diagnostics["capabilities"]["adapter:calendaring-google-calendar"]
        assert capability["state"] == "degraded"

        runtime: AdministrateMeRuntime = app.state.runtime
        durable = runtime.adapter_cursor_store.get("calendar-runtime-fixture")
        assert durable.provider_watermark is None
        assert durable.error_count == 1

        settings = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert settings.status_code == 200
        [connection] = settings.json()["control"]["connections"]
        assert connection["runtime"] != "running"
        assert connection["setup"] != "connected"


def test_calendar_restart_replays_same_provider_page_without_duplicate_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = {
        "id": "restart-event",
        "etag": "restart-revision-1",
        "summary": "Restart replay fixture",
        "start": {"dateTime": "2026-05-28T15:00:00Z"},
        "end": {"dateTime": "2026-05-28T16:00:00Z"},
        "updated": "2026-05-28T14:00:00Z",
    }
    settings = _calendar_runtime_settings(tmp_path)

    def fact_count(client: TestClient, runtime: AdministrateMeRuntime) -> int:
        async def count() -> int:
            assert runtime.event_log is not None
            return len(
                [
                    row
                    async for row in runtime.event_log.read_since(
                        types=["calendar.event_added"]
                    )
                ]
            )

        return client.portal.call(count)

    first_service = _CalendarProtocolService(
        [{"items": [event], "nextSyncToken": "restart-token-a"}]
    )
    _install_calendar_protocol(monkeypatch, first_service)
    first_app = create_application(settings)
    with TestClient(first_app) as client:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if client.get("/api/system/ready").status_code == 200:
                break
            time.sleep(0.01)
        assert fact_count(client, first_app.state.runtime) == 1

    second_service = _CalendarProtocolService(
        [{"items": [event], "nextSyncToken": "restart-token-b"}]
    )
    _install_calendar_protocol(monkeypatch, second_service)
    second_app = create_application(settings)
    with TestClient(second_app) as client:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if client.get("/api/system/ready").status_code == 200:
                break
            time.sleep(0.01)
        assert second_service.list_calls[0]["syncToken"] == "restart-token-a"
        assert fact_count(client, second_app.state.runtime) == 1


def test_source_assurance_refresh_never_blocks_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert runtime._source_assurance_cache is not None
        cached = dict(runtime._source_assurance_cache)
        runtime._source_assurance_revision = ("ev_outdated", ())
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
        assert (
            first.json()["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["revision_current"]
            is False
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
        assert (
            refreshed["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["revision_current"]
            is True
        )


def test_settled_current_source_assurance_proof_does_not_rescan_on_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        for _ in range(100):
            freshness = client.get("/api/system/ready").json()["capabilities"][
                "policy:source-assurance"
            ]["details"]["freshness"]
            if (
                freshness["revision_current"] is True
                and freshness["refresh_in_progress"] is False
            ):
                break
            client.portal.call(asyncio.sleep, 0.01)
        assert freshness["revision_current"] is True
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_MAX_STALE_SECONDS - 1
        )
        refresh_calls = 0

        async def unexpected_refresh(
            *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            return {}

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", unexpected_refresh
        )

        details = client.get("/api/system/ready").json()["capabilities"][
            "policy:source-assurance"
        ]["details"]
        assert refresh_calls == 0
        assert details["freshness"]["state"] == "fresh"
        assert details["freshness"]["settled_revision"] is True
        assert details["freshness"]["refresh_in_progress"] is False


def test_pending_source_assurance_proof_rechecks_on_grace_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        for _ in range(100):
            freshness = client.get("/api/system/ready").json()["capabilities"][
                "policy:source-assurance"
            ]["details"]["freshness"]
            if (
                freshness["revision_current"] is True
                and freshness["refresh_in_progress"] is False
            ):
                break
            client.portal.call(asyncio.sleep, 0.01)
        assert freshness["revision_current"] is True
        assert runtime._source_assurance_cache is not None
        cached = dict(runtime._source_assurance_cache)
        counts = dict(cached["counts"])
        counts["pending"] = 1
        cached["counts"] = counts
        runtime._source_assurance_cache = cached
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

        details = client.get("/api/system/ready").json()["capabilities"][
            "policy:source-assurance"
        ]["details"]
        assert refresh_calls == 1
        assert details["freshness"]["pending_recheck_required"] is True
        assert details["freshness"]["refresh_in_progress"] is True
        assert details["freshness"]["settled_revision"] is False


def test_over_age_source_assurance_proof_degrades_during_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        runtime._source_assurance_revision = ("ev_outdated", ())
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
        runtime._source_assurance_revision = ("ev_outdated", ())
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


def test_runtime_recovery_endpoint_is_scoped_append_only_and_human_gated(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    settings = RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "member-a.ts.net": "member-a",
            "child.ts.net": "member-child",
        },
        member_roles={"member-a": "principal", "member-child": "child"},
        deterministic_openclaw_fixture=True,
        fixture_member_id="member-a",
    )

    async def preload() -> tuple[str, str]:
        log = EventLog(settings.instance, TEST_KEY)
        try:
            source_id = await log.append(
                EventEnvelope(
                    event_at_ms=1_786_410_000_000,
                    tenant_id=settings.instance.tenant_id,
                    type="messaging.received",
                    schema_version=3,
                    occurred_at="2026-08-11T15:00:00Z",
                    source_adapter="gmail",
                    source_account_id="fixture-account",
                    owner_scope="private:member-a",
                    visibility_scope="private:member-a",
                    sensitivity="normal",
                    idempotency_key="runtime-recovery-source",
                    payload={
                        "provider": "gmail",
                        "provider_account_id": "fixture-account",
                        "member_id": "member-a",
                        "source_channel": "gmail",
                        "native_message_id": "runtime-recovery-source",
                        "thread_id": "runtime-recovery-thread",
                        "from_identifier": "sender@example.invalid",
                        "to_identifier": "member@example.invalid",
                        "workspace": "personal",
                        "subject": "Fixture recovery",
                        "body_text": "A private fixture update without an action.",
                        "received_at": "2026-08-11T15:00:00Z",
                        "provider_labels": ["INBOX"],
                        "attachments": [],
                    },
                )
            )
            failed_id = await log.append(
                EventEnvelope(
                    event_at_ms=1_786_410_000_001,
                    tenant_id=settings.instance.tenant_id,
                    type="triage.intent.features_extracted",
                    schema_version=3,
                    occurred_at="2026-08-11T15:00:01Z",
                    source_adapter="deterministic_candidate_eligibility",
                    source_account_id="pipeline",
                    owner_scope="private:member-a",
                    visibility_scope="private:member-a",
                    sensitivity="normal",
                    idempotency_key=f"triage:message-features:v19:{source_id}",
                    payload={
                        "intent_id": f"source:{source_id}",
                        "source_event_id": source_id,
                        "skill_call_event_id": None,
                        "extractor_id": "fixture-source-assessor",
                        "language": None,
                        "candidates": [],
                        "extraction_state": "unavailable",
                        "extracted_at": "2026-08-11T15:00:01Z",
                        "assessment_outcome": "failed",
                        "reason_codes": [
                            "language_seam_OpenClawUnreachable",
                            "no_eligible_candidate",
                        ],
                    },
                ),
                causation_id=source_id,
            )
            bus = EventBus(log, settings.instance.bus_checkpoint_path)

            async def noop(_event: dict[str, object]) -> None:
                return None

            bus.subscribe(
                "pipeline:pipeline:noise_filtering",
                ["messaging.received", "messaging.sent", "telephony.sms_received"],
                noop,
                consumer_version="19.0.0",
            )
            await bus.set_checkpoint("pipeline:pipeline:noise_filtering", failed_id)
            await bus.stop()
            return source_id, failed_id
        finally:
            await log.close()

    source_id, failed_id = asyncio.run(preload())
    app = create_application(settings)
    with TestClient(app) as client:
        body = {
            "request_id": "runtime-recovery-request-1",
            "targets": [
                {
                    "source_event_id": source_id,
                    "failed_assessment_event_id": failed_id,
                    "expected_failure_reason": ("language_seam_OpenClawUnreachable"),
                }
            ],
        }
        child = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={"Tailscale-User-Login": "child.ts.net"},
        )
        assert child.status_code == 403
        cross_scope = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={
                "Tailscale-User-Login": "member-a.ts.net",
                "X-AdminMe-View-Member": "member-child",
            },
        )
        assert cross_scope.status_code == 409
        assert cross_scope.json()["detail"] == "request_scope_mismatch"

        accepted = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert accepted.status_code == 202, accepted.text
        receipt = accepted.json()
        assert receipt["content_exposed"] is False
        assert receipt["canonical_objects_created"] == 0
        assert receipt["external_write_requests"] == 0
        assert "private fixture update" not in accepted.text.lower()

        runtime: AdministrateMeRuntime = app.state.runtime
        deadline = time.monotonic() + 5
        latest = None
        while time.monotonic() < deadline:
            latest = client.portal.call(
                latest_source_assessment, runtime.event_log, source_id
            )
            if latest and latest["schema_version"] == 4:
                break
            time.sleep(0.02)
        assert latest is not None
        assert latest["schema_version"] == 4
        assert latest["payload"]["assessment_outcome"] == "no_candidate"
        assert latest["payload"]["supersedes_assessment_event_id"] == failed_id

        duplicate = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["request_event_ids"] == receipt["request_event_ids"]

        all_events = client.portal.call(_event_types, runtime.event_log)
        assert not {
            "triage.item.confirmed",
            "triage.item.materialized",
            "task.created",
            "commitment.created",
            "external_sync.write_requested",
            "external_sync.write_completed",
        }.intersection(all_events)


async def _event_types(log: EventLog) -> list[str]:
    return [str(event["type"]) async for event in log.read_since()]


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
