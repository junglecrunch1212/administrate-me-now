"""Handler-direct unit tests for morning_digest.

Constructor-injection-loader pattern per PM-27 (mirroring 10c-i's
reward_dispatch shape). Tests cover defensive defaults, the
validation guard per [BUILD.md §1289] (≥3 fabrication shapes + ≥1
sentinel-emission test), happy path, and observation-mode suppression.

Tenant-identity firewall per [§12.4]: member ids in fixtures are
placeholders ("member-a"); no real tenant names.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import OpenClawTimeout
from adminme.pipelines.base import PipelineContext

PACK_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packs"
    / "pipelines"
    / "morning_digest"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_morning_digest_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
MorningDigestPipeline = _handler.MorningDigestPipeline
SENTINEL_BODY = _handler.SENTINEL_BODY


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


@dataclass
class _FakeSkillResult:
    output: dict[str, Any]


@dataclass
class _FakeObservationManager:
    active: bool = True

    async def is_active(self) -> bool:
        return self.active


def _make_ctx(
    event_log: Any,
    *,
    skill_outputs: dict[str, Any] | None = None,
    skill_raises: type[BaseException] | None = None,
    triggering_event_id: str = "trigger-eid-001",
    correlation_id: str | None = "corr-001",
    observation_manager: _FakeObservationManager | None = None,
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session(
        "pipeline_runner", "device", "tenant-test"
    )

    async def stub_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        if skill_raises is not None:
            raise skill_raises("simulated skill failure")
        return _FakeSkillResult(output=dict(skill_outputs or {}))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "morning_digest must route outbound() through the "
            "observation seam, not the ctx.outbound_fn callable"
        )

    return PipelineContext(
        session=session,
        event_log=event_log,
        run_skill_fn=stub_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=observation_manager,
        triggering_event_id=triggering_event_id,
        correlation_id=correlation_id,
    )


def _gather_state(
    *,
    tasks: list[dict[str, Any]] | None = None,
    commitments: list[dict[str, Any]] | None = None,
    calendars: list[dict[str, Any]] | None = None,
    recurrences: list[dict[str, Any]] | None = None,
    profile_format: str = "fog_aware",
) -> dict[str, Any]:
    profile = {"profile_format": profile_format}
    return {
        "profile": profile,
        "tasks": tasks or [],
        "commitments": commitments or [],
        "calendars": calendars or [],
        "recurrences": recurrences or [],
    }


def _build_pipeline_with_seeded_state(
    *, profile_format: str = "fog_aware"
) -> MorningDigestPipeline:
    return MorningDigestPipeline(
        profile_loader=lambda _mid: {"profile_format": profile_format},
        persona_loader=lambda: {"some": "persona"},
        tasks_loader=lambda _mid, _today: [
            {"id": "task-1", "title": "buy milk"},
            {"id": "task-2", "title": "pay bill"},
        ],
        commitments_loader=lambda _mid, _today: [
            {"id": "commit-1", "title": "call doc"},
        ],
        calendars_loader=lambda _mid, _today: [
            {"id": "cal-1", "title": "standup", "start_at": "09:00"},
        ],
        recurrences_loader=lambda _mid, _today: [],
    )


# ---------------------------------------------------------------------------
# defensive-default paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defensive_default_with_all_no_op_loaders_emits_sentinel() -> None:
    """No profile, no persona, empty loaders. compose() returns no
    body_text → validation guard rejects → sentinel emits."""
    log = _FakeEventLog()
    pipeline = MorningDigestPipeline()
    ctx = _make_ctx(log, skill_outputs={
        "body_text": "",
        "claimed_event_ids": [],
        "validation_failed": True,
        "reasons": ["skill_failure_defensive_default"],
    })
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    assert len(log.calls) == 1
    env = log.calls[0].envelope
    assert env.type == "digest.composed"
    assert env.payload["body_text"] == SENTINEL_BODY
    assert env.payload["validation_failed"] is True
    assert env.payload["delivered"] is False
    assert env.payload["profile_format"] == "none"


@pytest.mark.asyncio
async def test_skill_failure_emits_sentinel_no_outbound() -> None:
    """compose_morning_digest raises (timeout, etc.) → sentinel emits;
    outbound() is NOT called per [§7.7]."""
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_raises=OpenClawTimeout,
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest_events = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert len(digest_events) == 1
    assert digest_events[0].envelope.payload["validation_failed"] is True
    assert digest_events[0].envelope.payload["delivered"] is False
    assert digest_events[0].envelope.payload["body_text"] == SENTINEL_BODY
    # No observation events on the sentinel path.
    assert all(
        c.envelope.type == "digest.composed"
        for c in log.calls
    )


# ---------------------------------------------------------------------------
# validation guard per [BUILD.md §1289] (≥3 fabrication shapes + ≥1 sentinel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_guard_rejects_fabricated_calendar_event_id() -> None:
    """Skill claims a calendar event id that isn't in the gather payload
    → sentinel emits."""
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "Today: standup @ 09:00 (cal-FAKE).",
            "claimed_event_ids": ["cal-FAKE"],
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest_events = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert len(digest_events) == 1
    assert digest_events[0].envelope.payload["validation_failed"] is True
    assert digest_events[0].envelope.payload["body_text"] == SENTINEL_BODY


@pytest.mark.asyncio
async def test_validation_guard_rejects_fabricated_commitment_id() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "Owed: commit-FAKE.",
            "claimed_event_ids": ["commit-FAKE"],
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest_events = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert digest_events[0].envelope.payload["validation_failed"] is True


@pytest.mark.asyncio
async def test_validation_guard_rejects_fabricated_task_id() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "Tasks: task-99.",
            "claimed_event_ids": ["task-99"],
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest_events = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert digest_events[0].envelope.payload["validation_failed"] is True


@pytest.mark.asyncio
async def test_validation_guard_passes_with_only_real_ids() -> None:
    """All claimed ids exist in gather → validation passes → composed
    body_text emits, validation_failed=false."""
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "Today: standup (cal-1); buy milk (task-1).",
            "claimed_event_ids": ["cal-1", "task-1"],
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest_events = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert len(digest_events) == 1
    env = digest_events[0].envelope
    assert env.payload["validation_failed"] is False
    assert env.payload["body_text"].startswith("Today: standup")
    assert env.payload["delivered"] is True


# ---------------------------------------------------------------------------
# observation-mode suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observation_mode_active_suppresses_outbound_but_emits_digest() -> None:
    """Observation mode active → outbound() suppressed → digest.composed
    emits with delivered=false; observation.suppressed lands in the log."""
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "Today: 1 thing (task-1).",
            "claimed_event_ids": ["task-1"],
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest_events = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert len(digest_events) == 1
    assert digest_events[0].envelope.payload["delivered"] is False
    assert digest_events[0].envelope.payload["validation_failed"] is False
    suppressed_events = [
        c for c in log.calls if c.envelope.type == "observation.suppressed"
    ]
    assert len(suppressed_events) == 1


# ---------------------------------------------------------------------------
# happy path with seeded fakes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_emits_external_sent_and_digest_composed() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "Today: 1 task (task-1), 1 event (cal-1).",
            "claimed_event_ids": ["task-1", "cal-1"],
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    sent_events = [c for c in log.calls if c.envelope.type == "external.sent"]
    digest_events = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert len(sent_events) == 1
    assert len(digest_events) == 1
    assert digest_events[0].envelope.payload["delivered"] is True


# ---------------------------------------------------------------------------
# correlation / causation discipline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correlation_id_propagates_to_digest_composed() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "task-1",
            "claimed_event_ids": ["task-1"],
            "validation_failed": False,
            "reasons": [],
        },
        correlation_id="corr-XYZ",
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert digest[0].correlation_id == "corr-XYZ"


@pytest.mark.asyncio
async def test_causation_id_equals_triggering_event_id() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline_with_seeded_state()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "task-1",
            "claimed_event_ids": ["task-1"],
            "validation_failed": False,
            "reasons": [],
        },
        triggering_event_id="trigger-eid-cause",
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("member-a", "2026-05-05", ctx)
    digest = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert digest[0].causation_id == "trigger-eid-cause"


# ---------------------------------------------------------------------------
# handle() protocol entrypoint (proactive packs are not bus-driven, but the
# Pipeline protocol requires async handle())
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_without_member_id_skips_silently() -> None:
    log = _FakeEventLog()
    pipeline = MorningDigestPipeline()
    ctx = _make_ctx(log, skill_outputs={})
    bad_event = {"event_id": "x", "type": "digest.tick", "payload": {}}
    await pipeline.handle(bad_event, ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_handle_with_valid_payload_routes_through_dispatch() -> None:
    log = _FakeEventLog()
    pipeline = MorningDigestPipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "body_text": "",
            "claimed_event_ids": [],
            "validation_failed": True,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    triggering_event = {
        "event_id": "tick-1",
        "type": "digest.tick",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "correlation_id": None,
        "payload": {"member_id": "member-a", "today_iso": "2026-05-05"},
    }
    await pipeline.handle(triggering_event, ctx)
    digest = [c for c in log.calls if c.envelope.type == "digest.composed"]
    assert len(digest) == 1
    # Sentinel path because skill produced an empty/invalid output.
    assert digest[0].envelope.payload["validation_failed"] is True
