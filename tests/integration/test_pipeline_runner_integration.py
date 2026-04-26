"""Integration tests for the pipeline runner.

End-to-end against a real EventBus + EventLog + tmp InstanceConfig +
the on-disk echo_logger / echo_emitter fixture packs:

  1. End-to-end dispatch: 10 events -> counter == 10.
  2. Filter: messaging.sent events do not increment a messaging.received
     subscriber's counter.
  3. Restart survival: a second runner with the same checkpoint DB
     resumes from the saved checkpoint.
  4. Causation wiring: emitted derivative events carry
     causation_id == triggering event_id (carry-forward from 09a).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.runner import PipelineRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINES_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "pipelines"
ECHO_LOGGER_ROOT = PIPELINES_FIXTURE_ROOT / "echo_logger"
ECHO_EMITTER_ROOT = PIPELINES_FIXTURE_ROOT / "echo_emitter"
TEST_KEY = b"i" * 32


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(asyncio.get_event_loop().time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload=payload,
    )


def _messaging_received_payload(idx: int = 0) -> dict[str, Any]:
    return {
        "source_channel": "test",
        "from_identifier": f"alice{idx}@example.com",
        "to_identifier": "bob@example.com",
        "received_at": EventEnvelope.now_utc_iso(),
    }


def _messaging_sent_payload() -> dict[str, Any]:
    return {
        "source_channel": "test",
        "to_identifier": "alice@example.com",
        "sent_at": EventEnvelope.now_utc_iso(),
        "delivery_status": "sent",
    }


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


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_cache()
    # Reset class-level counters on the fixture packs so each test starts
    # from a known state (fixture packs are cached and shared).
    pack = load_pipeline_pack(ECHO_LOGGER_ROOT)
    pack.instance.__class__.reset()  # type: ignore[attr-defined]
    emitter_pack = load_pipeline_pack(ECHO_EMITTER_ROOT)
    emitter_pack.instance.__class__.reset()  # type: ignore[attr-defined]
    yield
    invalidate_cache()


async def test_end_to_end_dispatch(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)

    await runner.discover(
        builtin_root=PIPELINES_FIXTURE_ROOT / "echo_logger",
        installed_root=tmp_path / "no-installed-packs",
    )
    assert "pipeline:echo_logger" in runner.registered_pack_ids()

    await runner.start()
    try:
        last_eid = ""
        for i in range(10):
            last_eid = await log.append(
                _envelope(
                    "messaging.received",
                    _messaging_received_payload(i),
                    tenant_id=config.tenant_id,
                )
            )
            await bus.notify(last_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:echo_logger", last_eid
        )
        pack = load_pipeline_pack(ECHO_LOGGER_ROOT)
        assert pack.instance.__class__._count_for_test() == 10  # type: ignore[attr-defined]
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_filter_skips_non_matching_types(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    pack = load_pipeline_pack(ECHO_LOGGER_ROOT)
    runner.register(pack)

    await runner.start()
    try:
        # Interleave messaging.received with messaging.sent.
        rid1 = await log.append(
            _envelope(
                "messaging.received",
                _messaging_received_payload(1),
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(rid1)
        await log.append(
            _envelope(
                "messaging.sent",
                _messaging_sent_payload(),
                tenant_id=config.tenant_id,
            )
        )
        rid2 = await log.append(
            _envelope(
                "messaging.received",
                _messaging_received_payload(2),
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(rid2)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:echo_logger", rid2
        )

        assert pack.instance.__class__._count_for_test() == 2  # type: ignore[attr-defined]
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_restart_survival(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)

    bus_a = EventBus(log, config.bus_checkpoint_path)
    runner_a = PipelineRunner(bus_a, log, config)
    pack = load_pipeline_pack(ECHO_LOGGER_ROOT)
    runner_a.register(pack)
    await runner_a.start()
    last_eid = ""
    for i in range(5):
        last_eid = await log.append(
            _envelope(
                "messaging.received",
                _messaging_received_payload(i),
                tenant_id=config.tenant_id,
            )
        )
        await bus_a.notify(last_eid)
    await _wait_for_checkpoint(
        bus_a, "pipeline:pipeline:echo_logger", last_eid
    )
    assert pack.instance.__class__._count_for_test() == 5  # type: ignore[attr-defined]
    await runner_a.stop()
    await bus_a.stop()

    # New bus + runner against the same checkpoint DB. We do NOT reset
    # the class-level counter; we only assert the delta on subsequent
    # appends to confirm checkpoint resumption.
    pack.instance.__class__.reset()  # type: ignore[attr-defined]
    bus_b = EventBus(log, config.bus_checkpoint_path)
    runner_b = PipelineRunner(bus_b, log, config)
    runner_b.register(pack)
    await runner_b.start()
    try:
        last_eid_b = ""
        for i in range(5, 10):
            last_eid_b = await log.append(
                _envelope(
                    "messaging.received",
                    _messaging_received_payload(i),
                    tenant_id=config.tenant_id,
                )
            )
            await bus_b.notify(last_eid_b)
        await _wait_for_checkpoint(
            bus_b, "pipeline:pipeline:echo_logger", last_eid_b
        )
        # Only the 5 new events should be delivered to the post-restart
        # runner; the prior 5 are already past the checkpoint.
        assert pack.instance.__class__._count_for_test() == 5  # type: ignore[attr-defined]
    finally:
        await runner_b.stop()
        await bus_b.stop()
        await log.close()


async def test_causation_wiring(tmp_path: Path) -> None:
    """The echo_emitter pipeline emits messaging.sent with
    causation_id=triggering_event_id. Verifies the carry-forward from
    09a's build_log entry."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    pack = load_pipeline_pack(ECHO_EMITTER_ROOT)
    runner.register(pack)

    await runner.start()
    try:
        triggering_eid = await log.append(
            _envelope(
                "messaging.received",
                _messaging_received_payload(),
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:echo_emitter", triggering_eid
        )

        emitted_ids = pack.instance.__class__.emitted_event_ids  # type: ignore[attr-defined]
        assert len(emitted_ids) == 1
        emitted_id = emitted_ids[0]

        emitted = await log.get(emitted_id)
        assert emitted is not None
        assert emitted["type"] == "messaging.sent"
        assert emitted["causation_id"] == triggering_eid
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
