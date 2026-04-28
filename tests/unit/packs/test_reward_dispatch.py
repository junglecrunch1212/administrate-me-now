"""Handler-direct unit tests for reward_dispatch.

Construct a fake PipelineContext with stub profile + persona loaders
and verify the reward.ready emit contract for both source events
(task.completed, commitment.completed) and the defensive-default
fallbacks per [§7.7]. Quality bar: tests/unit/test_pipeline_noise_filtering.py.

Tenant-identity firewall per [§12.4]: member ids in fixtures are
placeholders ("member-a", "member-b"); no real tenant names.
"""

from __future__ import annotations

import importlib.util
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext

PACK_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packs"
    / "pipelines"
    / "reward_dispatch"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_reward_dispatch_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
RewardDispatchPipeline = _handler.RewardDispatchPipeline
_seed_from_event_id = _handler._seed_from_event_id


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


@dataclass
class _RecordedAppend:
    envelope: EventEnvelope
    correlation_id: str | None
    causation_id: str | None


@dataclass
class _FakeEventLog:
    calls: list[_RecordedAppend] = field(default_factory=list)
    _next_id: int = 0

    async def append(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        self.calls.append(
            _RecordedAppend(
                envelope=envelope,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        )
        self._next_id += 1
        return f"fake-event-id-{self._next_id}"


def _make_ctx(
    event_log: Any,
    *,
    triggering_event_id: str = "trigger-eid-001",
    correlation_id: str | None = "corr-001",
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session(
        "pipeline_runner", "device", "tenant-test"
    )

    async def stub_run_skill(*a: Any, **kw: Any) -> Any:
        raise AssertionError("reward_dispatch must not call run_skill")

    return PipelineContext(
        session=session,
        event_log=event_log,
        run_skill_fn=stub_run_skill,
        outbound_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=None,
        triggering_event_id=triggering_event_id,
        correlation_id=correlation_id,
    )


def _task_completed_event(
    *,
    event_id: str = "trigger-eid-001",
    member_id: str = "member-a",
    task_id: str = "task-001",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "type": "task.completed",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "correlation_id": "corr-001",
        "payload": {
            "task_id": task_id,
            "completed_by_member_id": member_id,
            "completed_at": EventEnvelope.now_utc_iso(),
        },
    }


def _commitment_completed_event(
    *,
    event_id: str = "trigger-eid-002",
    member_id: str = "member-a",
    commitment_id: str = "commit-001",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "type": "commitment.completed",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "correlation_id": "corr-001",
        "payload": {
            "commitment_id": commitment_id,
            "completed_at": EventEnvelope.now_utc_iso(),
            "completed_by_party_id": member_id,
        },
    }


_EVENT_BASED_PROFILE = {
    "rewards_mode": "event_based",
    "reward_distribution": {"done": 1.0},
}

_VARIABLE_RATIO_60_25_10_5 = {
    "rewards_mode": "variable_ratio",
    "reward_distribution": {
        "done": 0.60,
        "warm": 0.25,
        "delight": 0.10,
        "jackpot": 0.05,
    },
}

_CHILD_WARMTH_PROFILE = {
    "rewards_mode": "child_warmth",
    "reward_distribution": {"warm": 1.0},
}

_PERSONA_PACK = {
    "reward_templates": {
        "done": [{"id": "done-corny-1", "text": "Crushed it."}],
        "warm": [{"id": "warm-corny-1", "text": "That's a win!"}],
        "delight": [{"id": "delight-corny-1", "text": "Glorious."}],
        "jackpot": [{"id": "jackpot-corny-1", "text": "🎰 JACKPOT 🎰"}],
    }
}


# ---------------------------------------------------------------------------
# task.completed paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_completed_event_based_profile_emits_done() -> None:
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _EVENT_BASED_PROFILE,
        persona_loader=lambda: _PERSONA_PACK,
    )
    ctx = _make_ctx(log)
    await pipeline.handle(_task_completed_event(), ctx)
    assert len(log.calls) == 1
    env = log.calls[0].envelope
    assert env.type == "reward.ready"
    assert env.payload["tier"] == "done"
    assert env.payload["template_id"] == "done-corny-1"
    assert env.payload["template_text"] == "Crushed it."
    assert env.payload["triggering_task_id"] == "task-001"
    assert env.payload["triggering_commitment_id"] is None
    assert env.payload["member_id"] == "member-a"


@pytest.mark.asyncio
async def test_commitment_completed_event_based_profile_emits_done() -> None:
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _EVENT_BASED_PROFILE,
        persona_loader=lambda: _PERSONA_PACK,
    )
    ctx = _make_ctx(log, triggering_event_id="trigger-eid-002")
    await pipeline.handle(_commitment_completed_event(), ctx)
    assert len(log.calls) == 1
    env = log.calls[0].envelope
    assert env.type == "reward.ready"
    assert env.payload["tier"] == "done"
    assert env.payload["triggering_task_id"] is None
    assert env.payload["triggering_commitment_id"] == "commit-001"


# ---------------------------------------------------------------------------
# tier sampling determinism
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variable_ratio_tier_is_deterministic_per_event_id() -> None:
    """Same event_id rolls the same tier on re-processing — required so
    subscriber rewind cannot double-toast a different reward."""
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _VARIABLE_RATIO_60_25_10_5,
        persona_loader=lambda: _PERSONA_PACK,
    )

    log_a = _FakeEventLog()
    await pipeline.handle(
        _task_completed_event(event_id="trigger-eid-deterministic-001"),
        _make_ctx(log_a),
    )

    log_b = _FakeEventLog()
    await pipeline.handle(
        _task_completed_event(event_id="trigger-eid-deterministic-001"),
        _make_ctx(log_b),
    )

    assert log_a.calls[0].envelope.payload["tier"] == (
        log_b.calls[0].envelope.payload["tier"]
    )


@pytest.mark.asyncio
async def test_warm_only_distribution_always_rolls_warm() -> None:
    profile = {
        "rewards_mode": "variable_ratio",
        "reward_distribution": {
            "done": 0.0,
            "warm": 1.0,
            "delight": 0.0,
            "jackpot": 0.0,
        },
    }
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: profile,
        persona_loader=lambda: _PERSONA_PACK,
    )
    for i in range(20):
        log = _FakeEventLog()
        await pipeline.handle(
            _task_completed_event(event_id=f"trigger-{i}"),
            _make_ctx(log),
        )
        assert log.calls[0].envelope.payload["tier"] == "warm"


@pytest.mark.asyncio
async def test_50_50_split_distribution_matches_reference() -> None:
    """Across many event_ids, the empirical distribution from
    _seed_from_event_id+random.Random(seed) matches what the reference
    Python random with the same seed produces."""
    profile = {
        "rewards_mode": "variable_ratio",
        "reward_distribution": {"done": 0.5, "warm": 0.5},
    }
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: profile,
        persona_loader=lambda: _PERSONA_PACK,
    )

    actual: Counter[str] = Counter()
    expected: Counter[str] = Counter()
    for i in range(100):
        eid = f"trigger-eid-mix-{i:04d}"
        log = _FakeEventLog()
        await pipeline.handle(
            _task_completed_event(event_id=eid),
            _make_ctx(log),
        )
        actual[log.calls[0].envelope.payload["tier"]] += 1
        seed = _seed_from_event_id(eid)
        ref = random.Random(seed).random()
        expected["done" if ref <= 0.5 else "warm"] += 1
    assert actual == expected


@pytest.mark.asyncio
async def test_child_warmth_profile_emits_warm() -> None:
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _CHILD_WARMTH_PROFILE,
        persona_loader=lambda: _PERSONA_PACK,
    )
    await pipeline.handle(_task_completed_event(), _make_ctx(log))
    env = log.calls[0].envelope
    assert env.payload["tier"] == "warm"
    assert env.payload["template_id"] == "warm-corny-1"


# ---------------------------------------------------------------------------
# defensive-default paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_profile_emits_default_done_via_default_template() -> None:
    """Loader returns None for unknown member_id. Per [§7.7] the
    pipeline emits reward.ready with tier=done and the default-done
    sentinel template; does NOT raise."""
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: None,
        persona_loader=lambda: None,
    )
    await pipeline.handle(_task_completed_event(), _make_ctx(log))
    env = log.calls[0].envelope
    assert env.payload["tier"] == "done"
    assert env.payload["template_id"] == "default-done"
    assert env.payload["template_text"] == "✓"


@pytest.mark.asyncio
async def test_persona_pack_has_no_reward_templates_falls_back_to_default() -> None:
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _EVENT_BASED_PROFILE,
        persona_loader=lambda: {},
    )
    await pipeline.handle(_task_completed_event(), _make_ctx(log))
    env = log.calls[0].envelope
    assert env.type == "reward.ready"
    assert env.payload["tier"] == "done"
    assert env.payload["template_id"] == "default-done"


@pytest.mark.asyncio
async def test_persona_pack_missing_rolled_tier_falls_back_to_done() -> None:
    """Persona has reward_templates but no entry for the rolled tier
    (jackpot here). Falls back to done-tier template."""
    persona_only_done = {
        "reward_templates": {
            "done": [{"id": "done-only", "text": "Nice."}],
        }
    }
    profile = {
        "rewards_mode": "variable_ratio",
        "reward_distribution": {"jackpot": 1.0},
    }
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: profile,
        persona_loader=lambda: persona_only_done,
    )
    await pipeline.handle(_task_completed_event(), _make_ctx(log))
    env = log.calls[0].envelope
    assert env.payload["tier"] == "jackpot"
    assert env.payload["template_id"] == "done-only"
    assert env.payload["template_text"] == "Nice."


@pytest.mark.asyncio
async def test_source_event_missing_member_id_skips_silently() -> None:
    """A malformed source event (no completed_by_member_id /
    completed_by_party_id) is absorbed silently per [§7.7]; the bus
    checkpoint advances normally."""
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _EVENT_BASED_PROFILE,
        persona_loader=lambda: _PERSONA_PACK,
    )
    bad_event = _task_completed_event()
    bad_event["payload"].pop("completed_by_member_id")
    await pipeline.handle(bad_event, _make_ctx(log))
    assert log.calls == []


# ---------------------------------------------------------------------------
# correlation / causation discipline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correlation_id_propagates() -> None:
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _EVENT_BASED_PROFILE,
        persona_loader=lambda: _PERSONA_PACK,
    )
    ctx = _make_ctx(log, correlation_id="corr-XYZ")
    await pipeline.handle(_task_completed_event(), ctx)
    assert log.calls[0].correlation_id == "corr-XYZ"


@pytest.mark.asyncio
async def test_causation_id_equals_triggering_event_id() -> None:
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _EVENT_BASED_PROFILE,
        persona_loader=lambda: _PERSONA_PACK,
    )
    ctx = _make_ctx(log, triggering_event_id="trigger-eid-cause")
    await pipeline.handle(
        _task_completed_event(event_id="trigger-eid-cause"), ctx
    )
    assert log.calls[0].causation_id == "trigger-eid-cause"


@pytest.mark.asyncio
async def test_unrelated_event_type_is_ignored() -> None:
    """Defense in depth — runner only subscribes the configured types,
    but a stray dispatch for messaging.received is a no-op."""
    log = _FakeEventLog()
    pipeline = RewardDispatchPipeline(
        profile_loader=lambda _mid: _EVENT_BASED_PROFILE,
        persona_loader=lambda: _PERSONA_PACK,
    )
    other_event = _task_completed_event()
    other_event["type"] = "messaging.received"
    await pipeline.handle(other_event, _make_ctx(log))
    assert log.calls == []
