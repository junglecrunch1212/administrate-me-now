"""Unit tests for adminme.pipelines.runner.PipelineRunner.

Five tests:
  1. register() after start() raises RuntimeError.
  2. register() with duplicate pack_id raises ValueError.
  3. happy-path dispatch: counter increments once for one event.
  4. handler raises -> bus checkpoint not advanced past the bad event.
  5. status() returns one entry per registered pack with non-None
     subscriber_id.

Per the universal preamble's failure-mode-handler-direct discipline,
test 4 uses a synthetic pack constructed in-process whose handle() is
intentionally raising; we route through the bus only to inspect the
bus checkpoint behavior, not to assert about anything else.
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
from adminme.pipelines import (
    LoadedPipelinePack,
    PipelineContext,
    invalidate_cache,
    load_pipeline_pack,
)
from adminme.pipelines.runner import PipelineRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
ECHO_LOGGER_ROOT = REPO_ROOT / "tests" / "fixtures" / "pipelines" / "echo_logger"
TEST_KEY = b"p" * 32


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


def _messaging_received_payload() -> dict[str, Any]:
    return {
        "source_channel": "test",
        "from_identifier": "alice@example.com",
        "to_identifier": "bob@example.com",
        "received_at": EventEnvelope.now_utc_iso(),
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


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    invalidate_cache()
    # Reset class-level counter on the fixture pack between tests.
    pack = load_pipeline_pack(ECHO_LOGGER_ROOT)
    pack.instance.__class__.reset()  # type: ignore[attr-defined]
    try:
        yield {
            "config": config,
            "log": log,
            "bus": bus,
            "runner": runner,
            "pack": pack,
        }
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
        invalidate_cache()


async def test_register_after_start_raises(rig: dict[str, Any]) -> None:
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]
    runner.register(pack)
    await runner.start()
    with pytest.raises(RuntimeError, match="must be called before start"):
        runner.register(pack)


async def test_register_duplicate_pack_id_raises(rig: dict[str, Any]) -> None:
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]
    runner.register(pack)
    with pytest.raises(ValueError, match="already registered"):
        runner.register(pack)


async def test_dispatch_happy_path(rig: dict[str, Any]) -> None:
    log: EventLog = rig["log"]
    bus: EventBus = rig["bus"]
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]

    runner.register(pack)
    await runner.start()

    eid = await log.append(
        _envelope(
            "messaging.received",
            _messaging_received_payload(),
            tenant_id=rig["config"].tenant_id,
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "pipeline:pipeline:echo_logger", eid)

    counter = pack.instance.__class__._count_for_test()  # type: ignore[attr-defined]
    assert counter == 1
    assert pack.instance.__class__.last_event_id == eid  # type: ignore[attr-defined]


async def test_handler_raise_does_not_advance_checkpoint(
    tmp_path: Path,
) -> None:
    """When the pack's handle() raises, the bus must NOT advance the
    checkpoint past the failing event ([§7.7])."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)

    class _RaisingPipeline:
        pack_id = "pipeline:raiser"
        version = "1.0.0"
        triggers = {"events": ["messaging.received"]}

        async def handle(
            self, event: dict[str, Any], ctx: PipelineContext
        ) -> None:
            raise RuntimeError("boom")

    bad_pack = LoadedPipelinePack(
        pack_id="pipeline:raiser",
        version="1.0.0",
        manifest={},
        triggers={"events": ["messaging.received"]},
        events_emitted=[],
        instance=_RaisingPipeline(),
        pack_root=tmp_path / "raiser",
    )
    runner.register(bad_pack)
    await runner.start()

    try:
        eid = await log.append(
            _envelope(
                "messaging.received",
                _messaging_received_payload(),
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(eid)
        # Give the bus a moment to attempt + fail + retry-pending; the
        # checkpoint should remain pre-event (None or whatever came
        # before).
        for _ in range(30):
            status = await bus.subscriber_status("pipeline:pipeline:raiser")
            if status["consecutive_failures"] >= 1:
                break
            await asyncio.sleep(0.01)
        status = await bus.subscriber_status("pipeline:pipeline:raiser")
        assert status["checkpoint_event_id"] != eid
        assert status["consecutive_failures"] >= 1
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_status_returns_per_pack_entry(rig: dict[str, Any]) -> None:
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]
    runner.register(pack)
    await runner.start()
    snap = await runner.status()
    assert pack.pack_id in snap
    entry = snap[pack.pack_id]
    assert entry["version"] == pack.version
    assert entry["subscriber_id"] == f"pipeline:{pack.pack_id}"
    assert entry["subscriber_status"] is not None
