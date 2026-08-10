"""Round-trip integration tests for 10b-i pipelines.

Same harness pattern as ``tests/integration/test_pipeline_runner_integration.py``:
construct in-memory EventBus + EventLog + tmp InstanceConfig, instantiate
the runner, register pipeline packs via ``register()`` directly (bypass
``discover()`` to avoid filesystem-walk overhead — discover() is exercised
by 10a's integration suite already).

Tests:
1. identity_resolution emits endpoint.observed for an unknown sender without
   claiming that an inbox address is a Person.
2. identity_resolution also retains an outbound recipient endpoint.
3. noise_filtering emits messaging.classified for an inbound message,
   with the stubbed skill's classification + confidence.
4. noise_filtering emits the defensive default ("personal" / 0.0) when
   the skill raises OpenClawTimeout — does NOT propagate per [§7.7].
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.lib.skill_runner import OpenClawTimeout, SkillResult
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.runner import PipelineRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "identity_resolution"
NOISE_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "noise_filtering"
TEST_KEY = b"i" * 32


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str,
    sensitivity: str = "normal",
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(asyncio.get_event_loop().time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope="private:member-test",
        visibility_scope="private:member-test",
        sensitivity=sensitivity,  # type: ignore[arg-type]
        payload=payload,
    )


async def _wait_for_checkpoint(
    bus: EventBus, subscriber_id: str, target: str, timeout_iters: int = 200
) -> None:
    for _ in range(timeout_iters):
        status = await bus.subscriber_status(subscriber_id)
        if status["checkpoint_event_id"] == target and status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"subscriber {subscriber_id} did not reach {target}: "
        f"{await bus.subscriber_status(subscriber_id)}"
    )


async def _read_all(log: EventLog) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for row in log.read_since():
        out.append(row)
    return out


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    invalidate_cache()
    yield
    invalidate_cache()


async def test_identity_resolution_retains_endpoint_for_unknown_sender(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    pack = load_pipeline_pack(IDENTITY_PACK_ROOT)
    runner.register(pack)
    await runner.start()

    try:
        triggering_eid = await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "unknown@example.com",
                    "to_identifier": "me@example.com",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:identity_resolution", triggering_eid
        )

        all_events = await _read_all(log)
        derived = [e for e in all_events if e.get("causation_id") == triggering_eid]
        types = sorted(e["type"] for e in derived)
        assert types == ["endpoint.observed"]
        for e in derived:
            assert e["correlation_id"] is None or isinstance(e["correlation_id"], str)
        assert derived[0]["payload"]["classification"] == "unresolved"
        assert derived[0]["payload"]["value_normalized"] == "unknown@example.com"
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_identity_resolution_retains_messaging_sent_endpoint(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    pack = load_pipeline_pack(IDENTITY_PACK_ROOT)
    runner.register(pack)
    await runner.start()

    try:
        sent_eid = await log.append(
            _envelope(
                "messaging.sent",
                {
                    "source_channel": "gmail",
                    "to_identifier": "ada@example.com",
                    "sent_at": EventEnvelope.now_utc_iso(),
                    "delivery_status": "sent",
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(sent_eid)
        # Follow-up innocuous event of a type the pipeline ALSO subscribes
        # to (telephony.sms_received), so we know the subscriber processed
        # past sent_eid by the time the follow-up advances the checkpoint.
        followup_eid = await log.append(
            _envelope(
                "telephony.sms_received",
                {
                    "from_number": "+15551112222",
                    "to_number": "+15553334444",
                    "body": "hello",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(followup_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:identity_resolution", followup_eid
        )

        all_events = await _read_all(log)
        derived_from_sent = [e for e in all_events if e.get("causation_id") == sent_eid]
        assert [event["type"] for event in derived_from_sent] == ["endpoint.observed"]
        assert derived_from_sent[0]["payload"]["value_normalized"] == "ada@example.com"
        # The follow-up SMS also remains an unconfirmed endpoint.
        derived_from_sms = [
            e for e in all_events if e.get("causation_id") == followup_eid
        ]
        assert any(e["type"] == "endpoint.observed" for e in derived_from_sms)
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_identity_resolution_accepts_repeated_messages_from_same_endpoint(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    runner.register(load_pipeline_pack(IDENTITY_PACK_ROOT))
    await runner.start()

    try:
        source_ids = []
        for index in range(2):
            source_id = await log.append(
                _envelope(
                    "messaging.received",
                    {
                        "source_channel": "gmail",
                        "from_identifier": "repeat@example.com",
                        "to_identifier": "me@example.com",
                        "subject": f"Message {index}",
                        "received_at": EventEnvelope.now_utc_iso(),
                    },
                    tenant_id=config.tenant_id,
                )
            )
            source_ids.append(source_id)
            await bus.notify(source_id)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:identity_resolution", source_ids[-1]
        )

        status = await bus.subscriber_status("pipeline:pipeline:identity_resolution")
        assert status["degraded"] is False
        all_events = await _read_all(log)
        observations = [
            event
            for event in all_events
            if event["type"] == "endpoint.observed"
            and event["payload"]["value_normalized"] == "repeat@example.com"
        ]
        assert len(observations) == 2
        assert {event["causation_id"] for event in observations} == set(source_ids)
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_noise_filtering_emits_classified_with_stubbed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stub_run_skill(skill_id: str, inputs: dict, ctx: Any) -> SkillResult:
        return SkillResult(
            output={
                "classification": "transactional",
                "confidence": 0.93,
                "reasons": ["stub: receipt-shaped"],
            },
            openclaw_invocation_id="stub",
            provider="stub",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.0001,
            duration_ms=42,
        )

    monkeypatch.setattr("adminme.pipelines.runner.run_skill", stub_run_skill)

    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    pack = load_pipeline_pack(NOISE_PACK_ROOT)
    runner.register(pack)
    await runner.start()

    try:
        triggering_eid = await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "store@example.com",
                    "to_identifier": "me@example.com",
                    "subject": "Your receipt",
                    "body_text": "Thanks for your order #12345.",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:noise_filtering", triggering_eid
        )

        all_events = await _read_all(log)
        classified = [e for e in all_events if e["type"] == "messaging.classified"]
        assert len(classified) == 1
        e = classified[0]
        assert e["causation_id"] == triggering_eid
        assert e["payload"]["classification"] == "transactional"
        assert e["payload"]["confidence"] == 0.93
        assert e["payload"]["source_event_id"] == triggering_eid
        assert e["payload"]["skill_name"] == "classify_message_nature"
        assert e["payload"]["skill_version"] == "6.0.1"
        semantic = [
            event
            for event in all_events
            if event["type"] == "messaging.semantic_features_extracted"
        ]
        assert len(semantic) == 1
        assert semantic[0]["payload"]["source_event_id"] == triggering_eid
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_noise_filtering_skill_failure_lands_defensive_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def stub_run_skill(skill_id: str, inputs: dict, ctx: Any) -> SkillResult:
        nonlocal calls
        calls += 1
        raise OpenClawTimeout("openclaw stub timeout")

    monkeypatch.setattr("adminme.pipelines.runner.run_skill", stub_run_skill)

    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    pack = load_pipeline_pack(NOISE_PACK_ROOT)
    runner.register(pack)
    await runner.start()

    try:
        triggering_eid = await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "ada@example.com",
                    "to_identifier": "me@example.com",
                    "body_text": "see you tomorrow!",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus,
            "pipeline:pipeline:noise_filtering",
            triggering_eid,
            timeout_iters=600,
        )

        all_events = await _read_all(log)
        assert calls == 3
        classified = [e for e in all_events if e["type"] == "messaging.classified"]
        assert len(classified) == 1
        e = classified[0]
        assert e["causation_id"] == triggering_eid
        assert e["payload"]["classification"] == "personal"
        assert e["payload"]["confidence"] == 0.0
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
