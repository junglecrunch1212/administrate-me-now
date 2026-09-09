"""Patch-compatible language execution through the encrypted composition root."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import adminme.lib.skill_runner.fixture as skill_fixture
import adminme.lib.skill_runner.wrapper as skill_wrapper
import adminme.runtime.application as application
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.simulations.source_pipeline_stress import _wait_for_composition_quiescence


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-execution-owner"  # fixture:tenant_data:ok
OTHER = "fixture-execution-other"  # fixture:tenant_data:ok
AUTH = {"Tailscale-User-Login": "execution-owner.ts.net"}  # fixture:tenant_data:ok
OTHER_AUTH = {
    "Tailscale-User-Login": "execution-other.ts.net"
}  # fixture:tenant_data:ok
SUBSCRIBER = "pipeline:pipeline:noise_filtering"


def _settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=b"b" * 32,
        repository_root=REPO_ROOT,
        member_identities={
            AUTH["Tailscale-User-Login"]: MEMBER,
            OTHER_AUTH["Tailscale-User-Login"]: OTHER,
        },
        member_roles={MEMBER: "principal", OTHER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )


def _source(
    settings: RuntimeSettings, index: int, *, owner: str = MEMBER
) -> EventEnvelope:
    occurred = "2034-02-01T10:00:00Z"
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=settings.instance.tenant_id,
        type="messaging.received",
        schema_version=3,
        occurred_at=occurred,
        source_adapter="fixture-bounded-execution",
        source_account_id=f"fixture-execution-account:{owner}",
        owner_scope=f"private:{owner}",
        visibility_scope=f"private:{owner}",
        sensitivity="normal",
        actor_identity=owner,
        idempotency_key=f"fixture-bounded-execution:{index}",
        payload={
            "member_id": owner,
            "provider": "fixture",
            "provider_account_id": f"fixture-execution-account:{owner}",
            "workspace": "personal",
            "source_channel": "sms",
            "native_message_id": f"fixture-execution-message:{index}",
            "thread_id": f"fixture-execution-thread:{index}",
            "from_identifier": "fixture-counterparty@example.invalid",
            "to_identifier": f"{owner}@example.invalid",
            "subject": f"Fixture execution source {index}",
            "body_text": (
                f"[fixture:attention-controls:preparation:required:{index + 1}]\n"
                f"Please complete the required preparation form for activity {index + 1}."
            ),
            "received_at": occurred,
            "attachments": [],
        },
    )


async def _seed(settings: RuntimeSettings, *, count: int, other_last: bool = False):
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        return await log.append_batch(
            [
                _source(
                    settings,
                    index,
                    owner=OTHER if other_last and index == count - 1 else MEMBER,
                )
                for index in range(count)
            ]
        )
    finally:
        await log.close()


async def _events(runtime, *types):
    return [event async for event in runtime.event_log.read_since(types=list(types))]


async def _rewind_retained_pipeline(settings: RuntimeSettings) -> None:
    log = EventLog(settings.instance, settings.encryption_key)
    bus = EventBus(log, settings.instance.bus_checkpoint_path)

    async def unused_callback(_event):
        raise AssertionError("the fixture checkpoint editor never starts delivery")

    try:
        bus.subscribe(
            SUBSCRIBER,
            ["messaging.received"],
            unused_callback,
            consumer_version="23.0.0",
            delivery_concurrency=8,
        )
        await bus.set_checkpoint(SUBSCRIBER, None)
    finally:
        await bus.stop()
        await log.close()


def _assurance(client: TestClient, failed: int):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get("/api/system/ready")
        capability = response.json()["capabilities"]["policy:source-assurance"]
        details = capability["details"]
        counts = details["counts"]
        if (
            counts["failed"] == failed
            and counts["pending"] == counts["missing"] == 0
            and details["freshness"]["revision_current"]
        ):
            assert details["provider_effects"]["zero_effect_state"] == "healthy"
            return capability
        time.sleep(0.05)
    raise AssertionError(f"source assurance did not settle: {capability}")


def _fixture_runtime(monkeypatch, responder):
    def build(*, event_sink, event_log, raw_events_dir):
        class Client:
            async def post(self, url, *, json, headers):
                request = httpx.Request("POST", url, json=json, headers=headers)
                if str(json["args"]["schema"].get("title", "")).startswith(
                    "classify_message_nature"
                ):
                    return await responder(request, json["args"])
                return skill_fixture._response(request)

        return skill_wrapper._Runtime(
            event_sink=event_sink,
            event_log=event_log,
            raw_events_dir=raw_events_dir,
            httpx_client_factory=Client,
            gateway_url="http://fixture.invalid/tools/invoke",
            gateway_token="fixture-token",
        )

    monkeypatch.setattr(application, "build_fixture_runtime", build)


def _index(args):
    match = re.search(r"preparation:required:(\d+)", args["input"]["body_text"])
    assert match is not None
    return int(match.group(1)) - 1


def test_patch_reuses_retained_successes_and_requires_explicit_failed_source_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    source_ids = asyncio.run(_seed(settings, count=5, other_last=True))
    old_release = True
    requests: list[tuple[int, int]] = []
    attempts: dict[int, int] = {}
    actual_load = skill_wrapper.load_pack

    def selected_pack(path):
        pack = actual_load(path)
        if old_release and pack.pack_id == "skill:classify_message_nature":
            return replace(
                pack,
                version="8.0.0",
                skill_frontmatter={
                    **pack.skill_frontmatter,
                    "version": "8.0.0",
                    "timeout_seconds": 60,
                    "compatible_success_versions": [],
                },
            )
        return pack

    async def responder(request, args):
        index = _index(args)
        requests.append((index, args["timeoutMs"]))
        attempts[index] = attempts.get(index, 0) + 1
        if old_release and (index == 3 or attempts[index] <= index < 3):
            raise httpx.ReadTimeout("fixture terminal stream timeout", request=request)
        return skill_fixture._response(request)

    monkeypatch.setattr(skill_wrapper, "load_pack", selected_pack)
    _fixture_runtime(monkeypatch, responder)
    retained_types = (
        "skill.call.recorded",
        "skill.call.failed",
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "triage.intent.features_extracted",
        "triage.item.proposed",
    )
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert _assurance(client, 1)["state"] == "degraded"
        baseline = client.portal.call(_events, runtime, *retained_types)
        receipts = [
            event
            for event in baseline
            if event["type"] == "skill.call.recorded"
            and event["payload"]["skill_name"] == "skill:classify_message_nature"
        ]
        assert len(receipts) == 4
        proposals = [
            event for event in baseline if event["type"] == "triage.item.proposed"
        ]
        assert {event["payload"]["source_event_id"] for event in proposals} == {
            source_ids[index] for index in (0, 1, 2, 4)
        }
        assert all(event["schema_version"] == 5 for event in proposals)
        assert all(
            event["owner_scope"]
            == event["visibility_scope"]
            == f"private:{OTHER if event['payload']['source_event_id'] == source_ids[4] else MEMBER}"
            for event in proposals
        )
        assert {receipt["payload"]["skill_version"] for receipt in receipts} == {
            "8.0.0"
        }
        assert any(":recovery-1:" in receipt["idempotency_key"] for receipt in receipts)
        assert any(":recovery-2:" in receipt["idempotency_key"] for receipt in receipts)
        assert attempts == {0: 1, 1: 2, 2: 3, 3: 3, 4: 1}
        failed = next(
            event
            for event in baseline
            if event["type"] == "triage.intent.features_extracted"
            and event["payload"].get("source_event_id") == source_ids[3]
        )
        # Simulate an interrupted retained replay using the real durable bus
        # checkpoint seam; no version change or source rewrite requests replay.

    old_release = False
    asyncio.run(_rewind_retained_pipeline(settings))
    before_replay = list(requests)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert requests == before_replay
        assert client.portal.call(_events, runtime, *retained_types) == baseline
        assert _assurance(client, 1)["state"] == "degraded"
        own = client.get("/api/core/v1/activity?limit=200", headers=AUTH)
        other = client.get("/api/core/v1/activity?limit=200", headers=OTHER_AUTH)
        assert own.status_code == other.status_code == 200
        assert source_ids[0] in own.text
        assert source_ids[4] in other.text
        assert source_ids[4] not in own.text
        assert source_ids[0] not in other.text
        fresh_id = client.portal.call(runtime.events.write, _source(settings, 5))
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert requests[-1] == (5, 120_000)
        recovery = client.post(
            "/api/system/source-assessment-recoveries",
            headers=AUTH,
            json={
                "request_id": "fixture-bounded-execution-recovery",
                "targets": [
                    {
                        "source_event_id": source_ids[3],
                        "failed_assessment_event_id": failed["event_id"],
                        "expected_failure_reason": "language_seam_OpenClawTimeout",
                    }
                ],
            },
        )
        assert recovery.status_code == 202, recovery.text
        assert recovery.json()["external_write_requests"] == 0
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert _assurance(client, 0)["state"] == "healthy"
        assert requests[-1] == (3, 120_000)
        new_receipts = client.portal.call(_events, runtime, "skill.call.recorded")
        assert any(
            event["causation_id"] == fresh_id
            and event["payload"]["skill_version"] == "8.0.1"
            for event in new_receipts
        )
        for event in baseline:
            assert client.portal.call(runtime.event_log.get, event["event_id"]) == event
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_requested", "task.created"
            )
            == []
        )
        stable_requests = list(requests)
    with TestClient(create_application(settings)) as client:
        client.portal.call(
            _wait_for_composition_quiescence, client.app.state.runtime, 60
        )
        assert requests == stable_requests
        assert _assurance(client, 0)["state"] == "healthy"


def test_width_eight_timeouts_preserve_bounded_attempts_checkpoint_and_failure_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    source_ids = asyncio.run(_seed(settings, count=8))
    barriers: list[asyncio.Barrier] = []
    attempts: dict[int, int] = {}
    arguments = []
    active = 0
    peak = 0

    async def responder(request, args):
        nonlocal active, peak
        if not barriers:
            barriers.extend(asyncio.Barrier(8) for _ in range(3))
        index = _index(args)
        attempts[index] = attempts.get(index, 0) + 1
        arguments.append(args["timeoutMs"])
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.wait_for(barriers[attempts[index] - 1].wait(), timeout=10)
            raise httpx.ReadTimeout("fixture bounded stream timeout", request=request)
        finally:
            active -= 1

    _fixture_runtime(monkeypatch, responder)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert attempts == dict.fromkeys(range(8), 3)
        assert peak == 8
        assert arguments == [120_000] * 24
        status = client.portal.call(runtime.bus.subscriber_status, SUBSCRIBER)
        assert status["checkpoint_event_id"] >= max(source_ids)
        assert status["lag_count"] == 0 and not status["degraded"]
        failures = client.portal.call(_events, runtime, "skill.call.failed")
        assert len(failures) == 24
        assert {event["payload"]["failure_class"] for event in failures} == {
            "openclaw_timeout"
        }
        health = _assurance(client, 8)
        assert health["state"] == "degraded"
        assert health["details"]["counts"]["source_facts"] == 8
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_requested", "task.created"
            )
            == []
        )
    with TestClient(create_application(settings)) as client:
        client.portal.call(
            _wait_for_composition_quiescence, client.app.state.runtime, 60
        )
        assert attempts == dict.fromkeys(range(8), 3)
        assert _assurance(client, 8)["state"] == "degraded"


@pytest.mark.parametrize("failure_class", ["handler_raised", "output_invalid"])
def test_refused_old_default_keeps_completed_assessment_and_proposals_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_class: str
) -> None:
    settings = _settings(tmp_path)
    source_ids = asyncio.run(_seed(settings, count=2))
    old_release = True
    requests = []
    actual_load = skill_wrapper.load_pack

    def selected_pack(path):
        pack = actual_load(path)
        if not old_release or pack.pack_id != "skill:classify_message_nature":
            return pack

        def failing_postprocessor(raw, inputs, context):
            if _index({"input": inputs}) == 0:
                if failure_class == "handler_raised":
                    raise ValueError("fixture postprocessor failure")
                return {"fixture_invalid_output": True}
            assert pack.handler_post_process is not None
            return pack.handler_post_process(raw, inputs, context)

        return replace(
            pack,
            version="8.0.0",
            handler_post_process=failing_postprocessor,
            skill_frontmatter={
                **pack.skill_frontmatter,
                "version": "8.0.0",
                "timeout_seconds": 60,
                "compatible_success_versions": [],
            },
        )

    async def responder(request, args):
        requests.append(_index(args))
        return skill_fixture._response(request)

    monkeypatch.setattr(skill_wrapper, "load_pack", selected_pack)
    _fixture_runtime(monkeypatch, responder)
    types = (
        "skill.call.recorded",
        "skill.call.failed",
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "triage.intent.features_extracted",
        "triage.item.proposed",
        "triage.item.superseded",
    )
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        baseline = client.portal.call(_events, runtime, *types)
        [failed] = [event for event in baseline if event["type"] == "skill.call.failed"]
        assert failed["payload"]["failure_class"] == failure_class
        assert failed["causation_id"] == source_ids[0]
        [default] = [
            event
            for event in baseline
            if event["type"] == "skill.call.recorded"
            and event["causation_id"] == source_ids[0]
        ]
        assert default["payload"]["outputs"]["confidence"] == 0.0
        [assessment] = [
            event
            for event in baseline
            if event["type"] == "triage.intent.features_extracted"
            and event["payload"]["source_event_id"] == source_ids[0]
        ]
        assert assessment["payload"]["extraction_state"] == "complete"
        assert assessment["payload"]["assessment_outcome"] != "failed"
        assert any(
            event["type"] == "triage.item.proposed"
            and event["payload"]["source_event_id"] == source_ids[1]
            for event in baseline
        )
        assert sorted(requests) == [0, 1]

    old_release = False
    asyncio.run(_rewind_retained_pipeline(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert sorted(requests) == [0, 1]
        assert client.portal.call(_events, runtime, *types) == baseline
        status = client.portal.call(runtime.bus.subscriber_status, SUBSCRIBER)
        assert status["checkpoint_event_id"] >= max(source_ids)
        assert not status["degraded"] and status["poison_event_id"] is None
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_requested", "task.created"
            )
            == []
        )
