"""Handler-direct unit tests for custody_brief.

Constructor-injection-loader pattern per PM-27. Tests cover the six
per-prompt cases listed in §8 C3:

- emit on member with coparent relationship;
- no-emit without coparent;
- per-member opt-out via `custody_brief_enabled: false`;
- emit with empty next-day calendar still composes (handoff +
  allergen notes carry the brief);
- obs-on suppression;
- per-row skill failure does not abort tick.

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
    Path(__file__).resolve().parents[2]
    / "packs"
    / "pipelines"
    / "custody_brief"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_custody_brief_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
CustodyBriefPipeline = _handler.CustodyBriefPipeline


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
    skill_raises_for: set[str] | None = None,
    triggering_event_id: str = "trigger-eid-001",
    correlation_id: str | None = "corr-001",
    observation_manager: _FakeObservationManager | None = None,
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session(
        "pipeline_runner", "device", "tenant-test"
    )
    raises_for = skill_raises_for or set()

    async def stub_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        if skill_raises is not None:
            raise skill_raises("simulated skill failure")
        member_id = inputs.get("member_id") if isinstance(inputs, dict) else None
        if member_id in raises_for:
            raise OpenClawTimeout("simulated per-row skill failure")
        return _FakeSkillResult(output=dict(skill_outputs or {}))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "custody_brief must route outbound() through the "
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


def _target(
    *,
    member_id: str = "member-a",
    coparent_member_id: str = "member-b",
    child_member_ids: list[str] | None = None,
    next_day_events: list[dict[str, Any]] | None = None,
    handoff_timing: dict[str, Any] | None = None,
    allergen_notes: list[dict[str, Any]] | None = None,
    medication_notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "member_id": member_id,
        "coparent_member_id": coparent_member_id,
        "child_member_ids": child_member_ids
        if child_member_ids is not None
        else ["child-1"],
        "next_day_events": next_day_events
        if next_day_events is not None
        else [
            {"id": "cal-1", "title": "soccer practice", "child_member_id": "child-1"}
        ],
        "handoff_timing": handoff_timing
        if handoff_timing is not None
        else {"pickup_at": "08:00", "dropoff_at": "18:00", "location": "school"},
        "allergen_notes": allergen_notes or [],
        "medication_notes": medication_notes or [],
    }


def _seeded_pipeline(
    *,
    targets: list[dict[str, Any]] | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    profile_format: str = "fog_aware",
) -> CustodyBriefPipeline:
    targets = targets if targets is not None else [_target()]
    overrides = overrides or {}
    return CustodyBriefPipeline(
        profile_loader=lambda _mid: {"profile_format": profile_format},
        coparent_targets_loader=lambda _date: targets,
        member_override_loader=lambda mid: overrides.get(mid),
    )


@pytest.mark.asyncio
async def test_emit_on_member_with_coparent_relationship() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Tomorrow: soccer 16:00. Handoff at 08:00 school.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    briefs = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert len(briefs) == 1
    env = briefs[0].envelope
    assert env.payload["member_id"] == "member-a"
    assert env.payload["coparent_member_id"] == "member-b"
    assert env.payload["child_member_ids"] == ["child-1"]
    assert env.payload["brief_date"] == "2026-05-16"
    assert env.payload["validation_failed"] is False
    assert env.payload["delivered"] is True


@pytest.mark.asyncio
async def test_no_emit_without_coparent() -> None:
    """Target with empty / missing coparent_member_id → no emit
    (defensive skip; brief is coparent-anchored)."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(
        targets=[_target(coparent_member_id="")],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "unreachable"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_per_member_opt_out_skips() -> None:
    """Per-member override `custody_brief_enabled: False` → skipped
    (no skill call, no emit)."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(
        overrides={"member-a": {"custody_brief_enabled": False}}
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "unreachable"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_emit_with_empty_calendar_still_composes() -> None:
    """No next-day events but handoff + allergen notes → still
    composes (handoff is load-bearing). Per [§8] C3 unit case."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(
        targets=[
            _target(
                next_day_events=[],
                allergen_notes=[
                    {"child_member_id": "child-1", "note": "peanut allergy"}
                ],
            )
        ]
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Tomorrow: handoff 08:00. Peanut allergy reminder.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    briefs = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert len(briefs) == 1
    env = briefs[0].envelope
    assert env.payload["validation_failed"] is False
    assert "Peanut" in env.payload["composed_text"]


@pytest.mark.asyncio
async def test_observation_mode_active_suppresses_outbound() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Brief body.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    briefs = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert len(briefs) == 1
    assert briefs[0].envelope.payload["delivered"] is False
    suppressed = [
        c for c in log.calls if c.envelope.type == "observation.suppressed"
    ]
    assert len(suppressed) == 1


@pytest.mark.asyncio
async def test_per_row_skill_failure_does_not_abort_tick() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(
        targets=[
            _target(member_id="member-a"),
            _target(member_id="member-b", coparent_member_id="member-a"),
        ]
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Tomorrow: handoff 08:00.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        skill_raises_for={"member-a"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    briefs = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert len(briefs) == 2
    by_member = {c.envelope.payload["member_id"]: c for c in briefs}
    assert by_member["member-a"].envelope.payload["validation_failed"] is True
    assert by_member["member-a"].envelope.payload["delivered"] is False
    assert by_member["member-b"].envelope.payload["validation_failed"] is False
    assert by_member["member-b"].envelope.payload["delivered"] is True


@pytest.mark.asyncio
async def test_causation_and_correlation_propagate() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Brief.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        triggering_event_id="tick-trigger-eid",
        correlation_id="corr-XYZ",
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    briefs = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert briefs[0].correlation_id == "corr-XYZ"
    assert briefs[0].causation_id == "tick-trigger-eid"


@pytest.mark.asyncio
async def test_handle_without_brief_date_skips_silently() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(log, skill_outputs={})
    bad_event = {"event_id": "x", "type": "custody.tick", "payload": {}}
    await pipeline.handle(bad_event, ctx)
    assert log.calls == []
