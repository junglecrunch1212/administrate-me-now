"""Patch-compatible language execution through the encrypted composition root."""

from __future__ import annotations

import asyncio
import re
import threading
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
from adminme.events.writer import EventWriter
from adminme.lib.session import build_internal_session
from adminme.pipelines import load_pipeline_pack
from adminme.pipelines.base import PipelineContext
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


def _retained_prepublication_writer(monkeypatch, is_old, transform_assessment=None):
    """Reproduce only the old producer's append shape, through the real writer."""
    actual_batch = EventWriter.write_batch
    actual_skill = application.run_skill

    async def selected_skill(skill_id, inputs, context, **kwargs):
        # The retained classifier caller did not yet request clean-only results.
        if is_old():
            context = replace(context, require_clean_result=False)
        return await actual_skill(skill_id, inputs, context, **kwargs)

    monkeypatch.setattr(application, "run_skill", selected_skill)

    async def selected_batch(writer, envelopes, **kwargs):
        if not is_old():
            return await actual_batch(writer, envelopes, **kwargs)
        retained = []
        positions = []
        for envelope in envelopes:
            if envelope.type == "triage.source_publication.planned":
                # The former producer wrote the assessment alone, then its
                # proposals. Returning its ID at this unused plan position
                # leaves production publication of the same proposals intact.
                assert retained[-1].type == "triage.intent.features_extracted"
                positions.append(len(retained) - 1)
                continue
            if (
                transform_assessment
                and envelope.type == "triage.intent.features_extracted"
            ):
                envelope = transform_assessment(envelope)
            positions.append(len(retained))
            retained.append(envelope)
        ids = await actual_batch(writer, retained, **kwargs)
        return [ids[position] for position in positions]

    monkeypatch.setattr(EventWriter, "write_batch", selected_batch)


def _index(args):
    match = re.search(r"preparation:required:(\d+)", args["input"]["body_text"])
    assert match is not None
    return int(match.group(1)) - 1


@pytest.mark.parametrize("retained_skill_version", ["8.0.0", "8.0.1"])
def test_patch_reuses_retained_successes_and_requires_explicit_failed_source_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retained_skill_version: str
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
                version=retained_skill_version,
                skill_frontmatter={
                    **pack.skill_frontmatter,
                    "version": retained_skill_version,
                    "timeout_seconds": 60 if retained_skill_version == "8.0.0" else 120,
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
    _retained_prepublication_writer(monkeypatch, lambda: old_release)
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
        grounded = [event for event in proposals if event["schema_version"] == 5]
        assert {event["payload"]["source_event_id"] for event in grounded} == {
            source_ids[index] for index in (0, 1, 2, 4)
        }
        fallback = [event for event in proposals if event["schema_version"] == 4]
        assert {event["payload"]["source_event_id"] for event in fallback} == {
            source_ids[3]
        }
        assert all(
            event["owner_scope"]
            == event["visibility_scope"]
            == f"private:{OTHER if event['payload']['source_event_id'] == source_ids[4] else MEMBER}"
            for event in proposals
        )
        assert {receipt["payload"]["skill_version"] for receipt in receipts} == {
            retained_skill_version
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
        assert failed["payload"]["assessment_outcome"] == "failed"
        assert failed["payload"]["extraction_state"] == "unavailable"
        assert failed["payload"]["candidates"]
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
            and event["payload"]["skill_version"] == "8.0.5"
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
        assessments = client.portal.call(
            _events, runtime, "triage.intent.features_extracted"
        )
        assert len(assessments) == 8
        assert all(
            event["payload"]["assessment_outcome"] == "failed"
            and event["payload"]["candidates"]
            for event in assessments
        )
        proposals = client.portal.call(_events, runtime, "triage.item.proposed")
        assert {event["payload"]["source_event_id"] for event in proposals} == set(
            source_ids
        )
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
    _retained_prepublication_writer(monkeypatch, lambda: old_release)
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
        assert _assurance(client, 1)["state"] == "degraded"

    old_release = False
    asyncio.run(_rewind_retained_pipeline(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert sorted(requests) == [0, 1]
        assert _assurance(client, 1)["state"] == "degraded"
        assert client.portal.call(_events, runtime, *types) == baseline
        status = client.portal.call(runtime.bus.subscriber_status, SUBSCRIBER)
        assert status["checkpoint_event_id"] >= max(source_ids)
        assert not status["degraded"] and status["poison_event_id"] is None
        reason = (
            "language_seam_SkillHandlerError"
            if failure_class == "handler_raised"
            else "language_seam_SkillOutputInvalid"
        )
        refused = client.post(
            "/api/system/source-assessment-recoveries",
            headers=AUTH,
            json={
                "request_id": "fixture-nonretryable-default-recovery",
                "targets": [
                    {
                        "source_event_id": source_ids[0],
                        "failed_assessment_event_id": assessment["event_id"],
                        "expected_failure_reason": reason,
                    }
                ],
            },
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"] == "failure_not_retryable"
        assert sorted(requests) == [0, 1]
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_requested", "task.created"
            )
            == []
        )


def test_historical_complete_fallback_with_failed_call_is_not_rewritten_on_patch_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    [source_id] = asyncio.run(_seed(settings, count=1))
    old_release = True
    requests = []
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

    def legacy_terminal(envelope):
        if old_release and envelope.type == "triage.intent.features_extracted":
            # Reproduce the former v23 precedence at the append boundary. All
            # source, failed skill receipts, fallback candidates and proposals
            # are produced by the real pipeline; only this historical terminal
            # outcome differs from the corrected producer.
            assert envelope.payload["source_event_id"] == source_id
            assert envelope.payload["candidates"]
            assert (
                envelope.payload["reason_codes"][0] == "language_seam_OpenClawTimeout"
            )
            envelope = envelope.model_copy(
                update={
                    "payload": {
                        **envelope.payload,
                        "assessment_outcome": "eligible_candidates",
                        "extraction_state": "complete",
                    }
                }
            )
        return envelope

    async def responder(request, args):
        requests.append(args["timeoutMs"])
        if old_release:
            raise httpx.ReadTimeout("fixture terminal timeout", request=request)
        return skill_fixture._response(request)

    monkeypatch.setattr(skill_wrapper, "load_pack", selected_pack)
    _retained_prepublication_writer(monkeypatch, lambda: old_release, legacy_terminal)
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
        assert requests == [60_000] * 3
        assert (
            len([event for event in baseline if event["type"] == "skill.call.failed"])
            == 3
        )
        assert not any(event["type"] == "skill.call.recorded" for event in baseline)
        [assessment] = [
            event
            for event in baseline
            if event["type"] == "triage.intent.features_extracted"
        ]
        assert assessment["payload"]["assessment_outcome"] == "eligible_candidates"
        assert any(event["type"] == "triage.item.proposed" for event in baseline)
        assert _assurance(client, 1)["state"] == "degraded"

    old_release = False
    asyncio.run(_rewind_retained_pipeline(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert requests == [60_000] * 3
        assert client.portal.call(_events, runtime, *types) == baseline
        status = client.portal.call(runtime.bus.subscriber_status, SUBSCRIBER)
        assert status["checkpoint_event_id"] >= source_id
        assert not status["degraded"] and status["poison_event_id"] is None
        assert _assurance(client, 1)["state"] == "degraded"
        recovery = client.post(
            "/api/system/source-assessment-recoveries",
            headers=AUTH,
            json={
                "request_id": "fixture-retained-failure-recovery",
                "targets": [
                    {
                        "source_event_id": source_id,
                        "failed_assessment_event_id": assessment["event_id"],
                        "expected_failure_reason": "language_seam_OpenClawTimeout",
                    }
                ],
            },
        )
        assert recovery.status_code == 202, recovery.text
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert requests == [60_000] * 3 + [120_000]
        assert _assurance(client, 0)["state"] == "healthy"
        assert (
            client.portal.call(runtime.event_log.get, assessment["event_id"])
            == assessment
        )
        assert all(
            client.portal.call(runtime.event_log.get, event["event_id"]) == event
            for event in baseline
        )
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_requested", "task.created"
            )
            == []
        )


async def _projected_triage_row(runtime, proposal_id):
    row = (
        runtime.projections.connection("triage")
        .execute(
            "SELECT state,last_event_id FROM triage_items WHERE tenant_id=? AND proposal_id=?",
            (runtime.settings.instance.tenant_id, proposal_id),
        )
        .fetchone()
    )
    assert row is not None
    return dict(row)


async def _simulate_predecessor_checkpoint_past_plan(settings, plan_id):
    """The retained v23 subscription ignores plans and advances on later sources."""
    log = EventLog(settings.instance, settings.encryption_key)
    bus = EventBus(log, settings.instance.bus_checkpoint_path)
    seen = []
    pack = load_pipeline_pack(REPO_ROOT / "packs" / "pipelines" / "noise_filtering")
    writer = EventWriter(log, bus)

    async def no_model_or_provider(*args, **kwargs):
        raise AssertionError("an empty fixture source needs no model or provider")

    async def predecessor_subscription(event):
        seen.append(event["event_id"])
        if event["event_id"] == later_id:
            # Exercise the unchanged deterministic empty-source path, so the
            # later source has an honest terminal assessment before advancing.
            await pack.instance.handle(
                event,
                PipelineContext(
                    session=build_internal_session(
                        "fixture-predecessor", "device", settings.instance.tenant_id
                    ),
                    event_log=log,
                    event_sink=writer,
                    run_skill_fn=no_model_or_provider,
                    outbound_fn=no_model_or_provider,
                    guarded_write=None,
                    observation_manager=None,
                    triggering_event_id=event["event_id"],
                    correlation_id=None,
                ),
            )

    try:
        bus.subscribe(
            SUBSCRIBER,
            [
                "messaging.received",
                "messaging.sent",
                "telephony.sms_received",
                "source.assessment.recovery_requested",
            ],
            predecessor_subscription,
            consumer_version="23.0.0",
            delivery_concurrency=8,
        )
        # A content-free fixture source is a real later delivery in the old
        # subscription. No old model implementation or provider is simulated.
        empty = _source(settings, 98)
        empty = empty.model_copy(update={"payload": {**empty.payload, "body_text": ""}})
        later_id = await log.append(empty)
        assert later_id > plan_id
        await bus.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = await bus.subscriber_status(SUBSCRIBER)
            if status["checkpoint_event_id"] == later_id:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("retained predecessor subscription did not advance")
        assert plan_id not in seen
        assert later_id in seen
        return later_id
    finally:
        await bus.stop()
        await log.close()


@pytest.mark.parametrize("phase", ["normal", "recovery"])
@pytest.mark.parametrize("published_before_crash", [0, 1])
def test_atomic_assessment_publication_resumes_after_crash_and_predecessor_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    published_before_crash: int,
) -> None:
    settings = _settings(tmp_path)
    source = _source(settings, 0)
    second_quote = "Please complete the required consent form for activity 1."
    source = source.model_copy(
        update={
            "payload": {
                **source.payload,
                "body_text": source.payload["body_text"] + "\n" + second_quote,
            }
        }
    )

    async def seed_source():
        log = EventLog(settings.instance, settings.encryption_key)
        try:
            return await log.append(source)
        finally:
            await log.close()

    source_id = asyncio.run(seed_source())
    recovery_started = False
    crash_enabled = phase == "normal"
    blocked = threading.Event()
    published = []
    requests = []
    actual_write = EventWriter.write
    desired_schema = 4 if phase == "normal" else 5

    async def responder(request, args):
        requests.append(args["timeoutMs"])
        if not recovery_started:
            raise httpx.ReadTimeout(
                "fixture required-language failure", request=request
            )
        response = skill_fixture._response(request).json()
        candidates = response["result"]["details"]["json"]["candidates"]
        assert len(candidates) == 1
        candidates.append(
            {
                **candidates[0],
                "title": second_quote,
                "evidence_quote": second_quote,
                "compact_summary": second_quote,
            }
        )
        return httpx.Response(200, json=response, request=request)

    async def crash_before_suffix(writer, envelope, **kwargs):
        selected = (
            crash_enabled
            and envelope.type == "triage.item.proposed"
            and envelope.schema_version == desired_schema
            and envelope.payload.get("source_event_id") == source_id
        )
        if selected and len(published) == published_before_crash:
            blocked.set()
            # Simulate process loss after the transaction and before a missing
            # proposal append. Runtime shutdown cancels this exact callback;
            # no classifier/proposal/assessment return value is fabricated.
            await asyncio.Future()
        event_id = await actual_write(writer, envelope, **kwargs)
        if selected:
            published.append(event_id)
        return event_id

    monkeypatch.setattr(EventWriter, "write", crash_before_suffix)
    _fixture_runtime(monkeypatch, responder)
    dismissed = None
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        if phase == "recovery":
            client.portal.call(_wait_for_composition_quiescence, runtime, 60)
            [failure] = client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            assert failure["payload"]["assessment_outcome"] == "failed"
            assert (
                len(client.portal.call(_events, runtime, "triage.item.proposed")) == 2
            )
            recovery_started = True
            crash_enabled = True
            response = client.post(
                "/api/system/source-assessment-recoveries",
                headers=AUTH,
                json={
                    "request_id": "fixture-publication-crash-recovery",
                    "targets": [
                        {
                            "source_event_id": source_id,
                            "failed_assessment_event_id": failure["event_id"],
                            "expected_failure_reason": "language_seam_OpenClawTimeout",
                        }
                    ],
                },
            )
            assert response.status_code == 202, response.text
        assert blocked.wait(30), "real proposal publisher did not reach crash boundary"
        plans = client.portal.call(
            _events, runtime, "triage.source_publication.planned"
        )
        [plan] = [
            event
            for event in plans
            if event["payload"]["assessment_schema_version"]
            == (3 if phase == "normal" else 4)
        ]
        assessment = client.portal.call(
            runtime.event_log.get_by_idempotency,
            settings.instance.tenant_id,
            plan["payload"]["assessment_idempotency_key"],
        )
        assert assessment is not None
        assert len(plan["payload"]["proposals"]) == 2
        assert assessment["event_id"] < plan["event_id"]
        assert len(published) == published_before_crash
        completions = client.portal.call(
            _events, runtime, "triage.source_publication.completed"
        )
        assert not any(
            event["causation_id"] == plan["event_id"] for event in completions
        )
        if published:
            first = client.portal.call(runtime.event_log.get, published[0])
            proposal_id = first["payload"]["proposal_id"]
            client.portal.call(runtime.wait_for_projection, "triage", published[0])
            for _ in range(5):
                row = client.portal.call(_projected_triage_row, runtime, proposal_id)
                response = client.post(
                    f"/api/member/v1/triage/{proposal_id}/dismiss",
                    headers=AUTH,
                    json={
                        "request_id": "fixture-publication-member-dismissal",
                        "expected_last_event_id": row["last_event_id"],
                        "reason": "Already handled",
                    },
                )
                if response.status_code != 409:
                    break
                time.sleep(0.05)
            assert response.status_code == 200, response.text
            dismissed = (proposal_id, response.json()["event_id"])
        before_crash = list(requests)
        assert before_crash == [120_000] * (3 if phase == "normal" else 4)

    # The same pipeline version can retain a predecessor's later checkpoint.
    # Startup must discover this unfinished private plan independently of it.
    predecessor_checkpoint = asyncio.run(
        _simulate_predecessor_checkpoint_past_plan(settings, plan["event_id"])
    )
    crash_enabled = False
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert requests == before_crash
        assert (
            client.portal.call(runtime.event_log.get, assessment["event_id"])
            == assessment
        )
        assert client.portal.call(runtime.event_log.get, plan["event_id"]) == plan
        proposals = client.portal.call(_events, runtime, "triage.item.proposed")
        for draft in plan["payload"]["proposals"]:
            [proposal] = [
                event
                for event in proposals
                if event["idempotency_key"] == draft["idempotency_key"]
            ]
            assert proposal["payload"] == {
                **draft["payload"],
                "extraction_event_id": assessment["event_id"],
            }
            assert proposal["causation_id"] == assessment["event_id"]
            if proposal["event_id"] not in published:
                assert proposal["event_id"] > predecessor_checkpoint
        [completion] = [
            event
            for event in client.portal.call(
                _events, runtime, "triage.source_publication.completed"
            )
            if event["causation_id"] == plan["event_id"]
        ]
        assert completion["payload"]["planned_count"] == 2
        assert completion["payload"]["already_present_count"] == published_before_crash
        assert completion["payload"]["published_count"] == 2 - published_before_crash
        if dismissed:
            assert (
                client.portal.call(_projected_triage_row, runtime, dismissed[0])[
                    "state"
                ]
                == "dismissed"
            )
            assert (
                client.portal.call(runtime.event_log.get, dismissed[1])["type"]
                == "triage.item.dismissed"
            )
        assert _assurance(client, 1 if phase == "normal" else 0)["state"] == (
            "degraded" if phase == "normal" else "healthy"
        )
        own = client.get("/api/core/v1/activity?limit=200", headers=AUTH)
        other = client.get("/api/core/v1/activity?limit=200", headers=OTHER_AUTH)
        assert own.status_code == other.status_code == 200
        assert source_id in own.text and source_id not in other.text
        assert second_quote not in other.text
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_requested", "task.created"
            )
            == []
        )
        terminal = completion
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert requests == before_crash
        assert (
            client.portal.call(runtime.event_log.get, terminal["event_id"]) == terminal
        )


@pytest.mark.parametrize("failure_class", ["handler_raised", "output_invalid"])
def test_required_classifier_never_counts_a_fresh_defensive_default_as_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_class: str,
) -> None:
    settings = _settings(tmp_path)
    source_ids = asyncio.run(_seed(settings, count=2))
    actual_load = skill_wrapper.load_pack
    calls = []

    def selected_pack(path):
        pack = actual_load(path)
        if pack.pack_id != "skill:classify_message_nature":
            return pack

        def invalid_result(raw, inputs, context):
            assert context.require_clean_result
            if _index({"input": inputs}) == 0:
                if failure_class == "handler_raised":
                    raise ValueError("fixture invalid required-language postprocessor")
                return {"fixture_invalid": True}
            return pack.handler_post_process(raw, inputs, context)

        return replace(pack, handler_post_process=invalid_result)

    async def responder(request, args):
        calls.append(_index(args))
        return skill_fixture._response(request)

    monkeypatch.setattr(skill_wrapper, "load_pack", selected_pack)
    _fixture_runtime(monkeypatch, responder)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert sorted(calls) == [0, 1]
        assert _assurance(client, 1)["state"] == "degraded"
        baseline = client.portal.call(
            _events,
            runtime,
            "skill.call.recorded",
            "skill.call.failed",
            "triage.intent.features_extracted",
            "triage.item.proposed",
        )
        [failure] = [
            event for event in baseline if event["type"] == "skill.call.failed"
        ]
        assert failure["payload"]["failure_class"] == failure_class
        assert failure["payload"]["skill_version"] == "8.0.5"
        assert not any(
            event["type"] == "skill.call.recorded"
            and event["causation_id"] == source_ids[0]
            for event in baseline
        )
        [assessment] = [
            event
            for event in baseline
            if event["type"] == "triage.intent.features_extracted"
            and event["causation_id"] == source_ids[0]
        ]
        assert assessment["payload"]["assessment_outcome"] == "failed"
        assert assessment["payload"]["candidates"]
        assert any(
            event["type"] == "triage.item.proposed"
            and event["payload"]["source_event_id"] == source_ids[0]
            for event in baseline
        )
    asyncio.run(_rewind_retained_pipeline(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert sorted(calls) == [0, 1]
        assert _assurance(client, 1)["state"] == "degraded"
        for event in baseline:
            assert client.portal.call(runtime.event_log.get, event["event_id"]) == event
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_requested", "task.created"
            )
            == []
        )
