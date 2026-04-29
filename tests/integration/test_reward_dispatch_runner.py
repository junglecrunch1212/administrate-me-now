"""Round-trip integration tests for reward_dispatch.

Same harness pattern as
``tests/integration/test_pipeline_10b_i_integration.py``: in-memory
EventBus + EventLog + tmp InstanceConfig; load the pack via
``load_pipeline_pack`` and register with the runner. The handler's
private profile/persona loaders are replaced on the loaded instance
so the test fixtures supply canned reward distributions and template
pools rather than reaching for nonexistent on-disk loader modules
(carry-forward — the real loaders ship in a later prompt).

Tests:
1. task.completed round-trip → reward.ready lands with expected
   tier + template + triggering ids; correlation + causation wired.
2. commitment.completed round-trip → reward.ready lands.
3. Failure-mode handler-direct discipline: a malformed source event
   (missing member id) does NOT route through the bus per the
   universal-preamble carry-forward; the handler is called directly
   and asserted to drop silently. A follow-up healthy event then
   round-trips through the runner to confirm the subscriber survived.
4. Observation mode is N/A for reward_dispatch (no outbound() call) —
   one assertion that toggling observation does not affect emit.
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
from adminme.lib.observation import ObservationManager
from adminme.lib.session import build_internal_session
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import PipelineContext
from adminme.pipelines.runner import PipelineRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
REWARD_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "reward_dispatch"
TEST_KEY = b"r" * 32


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


_EVENT_BASED_PROFILE = {
    "rewards_mode": "event_based",
    "reward_distribution": {"done": 1.0},
}
_PERSONA_PACK = {
    "reward_templates": {
        "done": [{"id": "done-corny-1", "text": "Crushed it."}],
        "warm": [{"id": "warm-corny-1", "text": "That's a win!"}],
    }
}


def _stub_loaders(pack: Any) -> None:
    """Replace the pack instance's private loaders with test fakes."""
    pack.instance._profile_loader = lambda _mid: _EVENT_BASED_PROFILE
    pack.instance._persona_loader = lambda: _PERSONA_PACK


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    invalidate_cache()
    yield
    invalidate_cache()


async def test_task_completed_round_trip_emits_reward_ready(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    pack = load_pipeline_pack(REWARD_PACK_ROOT)
    _stub_loaders(pack)
    runner.register(pack)
    await runner.start()

    try:
        triggering_eid = await log.append(
            _envelope(
                "task.completed",
                {
                    "task_id": "task-001",
                    "completed_by_member_id": "member-a",
                    "completed_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:reward_dispatch", triggering_eid
        )

        all_events = await _read_all(log)
        rewards = [e for e in all_events if e["type"] == "reward.ready"]
        assert len(rewards) == 1
        e = rewards[0]
        assert e["causation_id"] == triggering_eid
        assert e["payload"]["member_id"] == "member-a"
        assert e["payload"]["tier"] == "done"
        assert e["payload"]["template_id"] == "done-corny-1"
        assert e["payload"]["template_text"] == "Crushed it."
        assert e["payload"]["triggering_task_id"] == "task-001"
        assert e["payload"]["triggering_commitment_id"] is None
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_commitment_completed_round_trip_emits_reward_ready(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    pack = load_pipeline_pack(REWARD_PACK_ROOT)
    _stub_loaders(pack)
    runner.register(pack)
    await runner.start()

    try:
        triggering_eid = await log.append(
            _envelope(
                "commitment.completed",
                {
                    "commitment_id": "commit-001",
                    "completed_at": EventEnvelope.now_utc_iso(),
                    "completed_by_party_id": "member-a",
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:reward_dispatch", triggering_eid
        )

        all_events = await _read_all(log)
        rewards = [e for e in all_events if e["type"] == "reward.ready"]
        assert len(rewards) == 1
        e = rewards[0]
        assert e["causation_id"] == triggering_eid
        assert e["payload"]["triggering_task_id"] is None
        assert e["payload"]["triggering_commitment_id"] == "commit-001"
        assert e["payload"]["tier"] == "done"
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_malformed_source_event_does_not_emit_reward(
    tmp_path: Path,
) -> None:
    """Failure-mode handler-direct discipline per the universal
    preamble carry-forward: route the malformed event THROUGH the
    handler directly (not through the bus) to avoid putting the
    subscriber in a degraded state. Then run a healthy event through
    the runner end-to-end to confirm the subscriber survives.
    """
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    pack = load_pipeline_pack(REWARD_PACK_ROOT)
    _stub_loaders(pack)
    runner.register(pack)
    await runner.start()

    try:
        # Handler-direct invocation with a malformed event (missing
        # completed_by_member_id). Per [§7.7] this drops silently.
        bad_event = {
            "event_id": "bad-eid-001",
            "type": "task.completed",
            "event_at_ms": 1700000000000,
            "tenant_id": config.tenant_id,
            "owner_scope": "shared:household",
            "visibility_scope": "shared:household",
            "correlation_id": None,
            "payload": {
                # No completed_by_member_id; intentionally malformed.
                "task_id": "task-malformed",
                "completed_at": EventEnvelope.now_utc_iso(),
            },
        }
        session = build_internal_session(
            "pipeline_runner", "device", config.tenant_id
        )

        async def _no_skill(*a: Any, **kw: Any) -> Any:
            raise AssertionError("reward_dispatch must not call run_skill")

        ctx = PipelineContext(
            session=session,
            event_log=log,
            run_skill_fn=_no_skill,
            outbound_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
            guarded_write=None,
            observation_manager=None,
            triggering_event_id="bad-eid-001",
            correlation_id=None,
        )
        await pack.instance.handle(bad_event, ctx)

        # Now run a healthy event through the runner to confirm the
        # subscriber is alive.
        good_eid = await log.append(
            _envelope(
                "task.completed",
                {
                    "task_id": "task-good",
                    "completed_by_member_id": "member-a",
                    "completed_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(good_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:reward_dispatch", good_eid
        )

        all_events = await _read_all(log)
        rewards = [e for e in all_events if e["type"] == "reward.ready"]
        # Exactly one reward — for the healthy event.
        assert len(rewards) == 1
        assert rewards[0]["causation_id"] == good_eid
        # And no event has the malformed event's id as causation.
        from_bad = [
            e for e in all_events if e.get("causation_id") == "bad-eid-001"
        ]
        assert from_bad == []
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_observation_mode_does_not_affect_reward_emit(
    tmp_path: Path,
) -> None:
    """reward_dispatch does not call outbound(); observation mode is
    therefore N/A. Toggling observation on must not change the emit
    behavior — reward.ready still lands."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runtime_path = tmp_path / "config" / "runtime.yaml"
    obs_mgr = ObservationManager(
        event_log=log, runtime_config_path=runtime_path
    )
    runner = PipelineRunner(
        bus, log, config, observation_manager=obs_mgr
    )
    pack = load_pipeline_pack(REWARD_PACK_ROOT)
    _stub_loaders(pack)
    runner.register(pack)
    await runner.start()

    try:
        # observation defaults ON for a fresh instance per [§6.16]
        assert await obs_mgr.is_active() is True

        triggering_eid = await log.append(
            _envelope(
                "task.completed",
                {
                    "task_id": "task-obs",
                    "completed_by_member_id": "member-a",
                    "completed_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:reward_dispatch", triggering_eid
        )

        all_events = await _read_all(log)
        rewards = [e for e in all_events if e["type"] == "reward.ready"]
        assert len(rewards) == 1
        # No observation.suppressed events were emitted by this pipeline
        # (which would indicate it called outbound() — it must not).
        suppressed_from_reward = [
            e
            for e in all_events
            if e["type"] == "observation.suppressed"
            and e.get("causation_id") == triggering_eid
        ]
        assert suppressed_from_reward == []
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
