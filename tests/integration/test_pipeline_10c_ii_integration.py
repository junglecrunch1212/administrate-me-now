"""Round-trip integration tests for 10c-ii proactive pipelines.

Same harness pattern as ``tests/integration/test_reward_dispatch_runner.py``
but adapted for proactive packs: the in-process PipelineRunner SKIPS
proactive packs (no `triggers.events` per `runner.py:131-138`), so
these tests load the pack directly and call its ``dispatch()`` /
``handle()`` against a real EventBus + EventLog + tmp InstanceConfig
to confirm end-to-end emit + observation-mode integration. The
runner is constructed only to assert the skip behavior on
``discover()``.

Tests:
1. morning_digest end-to-end with seeded loaders + monkeypatched
   ``run_skill`` returning a clean ``compose_morning_digest`` payload
   (validation passes, ``outbound()`` called, ``digest.composed``
   emitted with ``delivered=true``, ``external.sent`` lands).
2. morning_digest with monkeypatched ``run_skill`` returning a
   fabrication (validation fails, sentinel path, no outbound,
   ``digest.composed`` emitted with ``validation_failed=true`` and
   ``delivered=false``).
3. paralysis_detection end-to-end with seeded persona, fakes for
   tasks/commitments/energy, deterministic template emission.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.lib.observation import ObservationManager
from adminme.lib.session import build_internal_session
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import PipelineContext

REPO_ROOT = Path(__file__).resolve().parents[2]
MORNING_DIGEST_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "morning_digest"
PARALYSIS_DETECTION_PACK_ROOT = (
    REPO_ROOT / "packs" / "pipelines" / "paralysis_detection"
)
TEST_KEY = b"i" * 32


@dataclass
class _FakeSkillResult:
    output: dict[str, Any]


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    invalidate_cache()
    yield
    invalidate_cache()


async def _read_all(log: EventLog) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for row in log.read_since():
        out.append(row)
    return out


def _seed_morning_digest_pack(
    pack: Any,
    *,
    profile_format: str = "fog_aware",
) -> None:
    pack.instance._profile_loader = lambda _mid: {
        "profile_format": profile_format,
        "delivery_channel": "imessage",
    }
    pack.instance._persona_loader = lambda: {"persona": "x"}
    pack.instance._tasks_loader = lambda _mid, _today: [
        {"id": "task-1", "title": "buy milk"},
    ]
    pack.instance._commitments_loader = lambda _mid, _today: [
        {"id": "commit-1", "title": "call doc"},
    ]
    pack.instance._calendars_loader = lambda _mid, _today: [
        {"id": "cal-1", "title": "standup"},
    ]
    pack.instance._recurrences_loader = lambda _mid, _today: []


async def test_morning_digest_happy_path_round_trip(tmp_path: Path) -> None:
    """Validation passes → outbound() called → digest.composed emitted
    with delivered=true; external.sent lands."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runtime_path = tmp_path / "config" / "runtime.yaml"
    obs_mgr = ObservationManager(
        event_log=log, runtime_config_path=runtime_path
    )
    # Force observation OFF so external.sent lands.
    await obs_mgr.disable(actor="test", reason="test_setup_off")

    pack = load_pipeline_pack(MORNING_DIGEST_PACK_ROOT)
    _seed_morning_digest_pack(pack)

    session = build_internal_session(
        "pipeline_runner", "device", config.tenant_id
    )

    async def fake_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        assert skill_id == "compose_morning_digest"
        return _FakeSkillResult(
            output={
                "body_text": "Today: 1 standup (cal-1); buy milk (task-1).",
                "claimed_event_ids": ["cal-1", "task-1"],
                "validation_failed": False,
                "reasons": [],
            }
        )

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError("morning_digest routes outbound through observation")

    ctx = PipelineContext(
        session=session,
        event_log=log,
        run_skill_fn=fake_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=obs_mgr,
        triggering_event_id="trigger-eid-int-1",
        correlation_id="corr-int-1",
    )

    try:
        await pack.instance.dispatch("member-a", "2026-05-05", ctx)

        all_events = await _read_all(log)
        digest_events = [e for e in all_events if e["type"] == "digest.composed"]
        assert len(digest_events) == 1
        e = digest_events[0]
        assert e["payload"]["validation_failed"] is False
        assert e["payload"]["delivered"] is True
        assert e["payload"]["body_text"].startswith("Today:")
        # external.sent lands because observation is off and outbound() ran.
        sent_events = [e for e in all_events if e["type"] == "external.sent"]
        assert len(sent_events) == 1
    finally:
        await bus.stop()
        await log.close()


async def test_morning_digest_fabrication_emits_sentinel(tmp_path: Path) -> None:
    """Skill claims a fabricated id → validation guard rejects →
    sentinel path; no outbound, digest.composed emits with
    validation_failed=true, delivered=false."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runtime_path = tmp_path / "config" / "runtime.yaml"
    obs_mgr = ObservationManager(
        event_log=log, runtime_config_path=runtime_path
    )
    await obs_mgr.disable(actor="test", reason="test_setup_off")

    pack = load_pipeline_pack(MORNING_DIGEST_PACK_ROOT)
    _seed_morning_digest_pack(pack)

    session = build_internal_session(
        "pipeline_runner", "device", config.tenant_id
    )

    async def fake_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        return _FakeSkillResult(
            output={
                "body_text": "Today: ghost event (cal-FAKE).",
                "claimed_event_ids": ["cal-FAKE"],
                "validation_failed": False,
                "reasons": [],
            }
        )

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError("morning_digest routes outbound through observation")

    ctx = PipelineContext(
        session=session,
        event_log=log,
        run_skill_fn=fake_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=obs_mgr,
        triggering_event_id="trigger-eid-int-2",
        correlation_id="corr-int-2",
    )

    try:
        await pack.instance.dispatch("member-a", "2026-05-05", ctx)

        all_events = await _read_all(log)
        digest_events = [e for e in all_events if e["type"] == "digest.composed"]
        assert len(digest_events) == 1
        e = digest_events[0]
        assert e["payload"]["validation_failed"] is True
        assert e["payload"]["delivered"] is False
        assert e["payload"]["body_text"].startswith("No morning brief")
        # No external.sent or observation.suppressed for the digest path —
        # outbound() was never called on the sentinel path.
        sent_events = [e for e in all_events if e["type"] == "external.sent"]
        assert sent_events == []
        suppressed_events = [
            e for e in all_events if e["type"] == "observation.suppressed"
        ]
        assert suppressed_events == []
    finally:
        await bus.stop()
        await log.close()


async def test_paralysis_detection_round_trip(tmp_path: Path) -> None:
    """Pre-conditions met → deterministic template selected →
    paralysis.triggered emits with correct payload shape."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)

    pack = load_pipeline_pack(PARALYSIS_DETECTION_PACK_ROOT)
    pack.instance._profile_loader = lambda _mid: {
        "fog_window": ["15:00", "17:00"],
    }
    pack.instance._persona_loader = lambda: {
        "paralysis_templates": [
            {"id": "pal-1", "text": "One small thing: water."},
            {"id": "pal-2", "text": "Pick your shoes up first."},
        ]
    }
    pack.instance._tasks_loader = lambda _mid, _now: []
    pack.instance._commitments_loader = lambda _mid, _now: []
    pack.instance._energy_loader = lambda _mid: {
        "level": "low",
        "as_of_iso": "2026-05-05T15:00:00Z",
    }

    session = build_internal_session(
        "pipeline_runner", "device", config.tenant_id
    )

    async def stub_run_skill(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "paralysis_detection must not call run_skill — "
            "deterministic per [BUILD.md §1297-1302]"
        )

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "paralysis_detection v1 must not call outbound() — "
            "[BUILD.md §1302] optional-outbound is deferred"
        )

    ctx = PipelineContext(
        session=session,
        event_log=log,
        run_skill_fn=stub_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=None,
        triggering_event_id="trigger-eid-int-3",
        correlation_id="corr-int-3",
    )

    try:
        await pack.instance.dispatch(
            "member-a", "2026-05-05T15:00:00Z", ctx
        )

        all_events = await _read_all(log)
        triggered = [
            e for e in all_events if e["type"] == "paralysis.triggered"
        ]
        assert len(triggered) == 1
        e = triggered[0]
        assert e["payload"]["member_id"] == "member-a"
        assert e["payload"]["template_id"] in {"pal-1", "pal-2"}
        assert e["payload"]["template_text"]
        assert e["payload"]["triggered_at"] == "2026-05-05T15:00:00Z"
        # No outbound activity on this pipeline.
        sent_events = [e for e in all_events if e["type"] == "external.sent"]
        suppressed_events = [
            e for e in all_events if e["type"] == "observation.suppressed"
        ]
        assert sent_events == []
        assert suppressed_events == []
    finally:
        await bus.stop()
        await log.close()
