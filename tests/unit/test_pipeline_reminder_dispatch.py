"""Handler-direct unit tests for reminder_dispatch.

Constructor-injection-loader pattern per PM-27 (mirroring 10c-i's
reward_dispatch shape and 10c-ii's morning_digest / paralysis_detection
shape). Tests cover the six per-prompt cases listed in §8 C3:

- emit on due commitment;
- no-emit outside window (loader returns empty);
- no-emit on dedupe-hit;
- sentinel on skill-failure-defensive-default;
- observation-on suppression keeps event but `delivered=False`;
- per-member `reminders_enabled: false` skip.

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
    Path(__file__).resolve().parents[2] / "packs" / "pipelines" / "reminder_dispatch"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_reminder_dispatch_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
ReminderDispatchPipeline = _handler.ReminderDispatchPipeline


@dataclass
class _RecordedAppend:
    envelope: EventEnvelope
    correlation_id: str | None
    causation_id: str | None


@dataclass
class _FakeEventLog:
    calls: list[_RecordedAppend] = field(default_factory=list)
    _next_id: int = 0

    async def write(
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

    session = build_internal_session("pipeline_runner", "device", "tenant-test")

    async def stub_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        if skill_raises is not None:
            raise skill_raises("simulated skill failure")
        return _FakeSkillResult(output=dict(skill_outputs or {}))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "reminder_dispatch must route outbound() through the "
            "observation seam, not the ctx.outbound_fn callable"
        )

    return PipelineContext(
        session=session,
        event_log=event_log,
        event_sink=event_log,
        run_skill_fn=stub_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=observation_manager,
        triggering_event_id=triggering_event_id,
        correlation_id=correlation_id,
    )


def _commitment(
    *,
    commitment_id: str = "commit-1",
    owner_member_id: str = "member-a",
    recipient_party_id: str = "party-c",
    title: str = "call doc",
    due_at: str = "2026-05-15T10:00:00+00:00",
) -> dict[str, Any]:
    return {
        "commitment_id": commitment_id,
        "owner_member_id": owner_member_id,
        "recipient_party_id": recipient_party_id,
        "title": title,
        "due_at": due_at,
    }


def _seeded_pipeline(
    *,
    commitments: list[dict[str, Any]] | None = None,
    recent_ids: set[str] | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    profile_format: str = "fog_aware",
) -> ReminderDispatchPipeline:
    commitments = commitments if commitments is not None else [_commitment()]
    recent_ids = recent_ids if recent_ids is not None else set()
    overrides = overrides or {}
    return ReminderDispatchPipeline(
        profile_loader=lambda _mid: {"profile_format": profile_format},
        commitments_due_loader=lambda _now, _look: commitments,
        recent_reminders_loader=lambda _now, _ded: recent_ids,
        member_override_loader=lambda mid: overrides.get(mid),
    )


@pytest.mark.asyncio
async def test_emit_on_due_commitment() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reminder: call doc at 10:00.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-15T09:30:00+00:00", ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert len(dispatched) == 1
    env = dispatched[0].envelope
    assert env.payload["commitment_id"] == "commit-1"
    assert env.payload["owner_member_id"] == "member-a"
    assert env.payload["recipient_party_id"] == "party-c"
    assert env.payload["composed_text"].startswith("Reminder")
    assert env.payload["validation_failed"] is False
    assert env.payload["delivered"] is False
    assert env.payload["channel"] == "imessage"
    chain = [
        call.envelope.type
        for call in log.calls
        if call.envelope.type.startswith("notification.")
    ]
    assert chain == ["notification.signal.raised", "notification.decision.made"]
    decision = next(
        call.envelope
        for call in log.calls
        if call.envelope.type == "notification.decision.made"
    )
    assert decision.payload["decision"] == "route"
    assert decision.payload["route"] == "openclaw"


@pytest.mark.asyncio
async def test_no_emit_outside_window() -> None:
    """When the commitments_due_loader returns [] (nothing in window),
    no reminder.dispatched event emits."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(commitments=[])
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "unreachable",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-15T09:30:00+00:00", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_no_emit_on_dedupe_hit() -> None:
    """When recent_reminders_loader contains the commitment_id, the
    commitment is skipped — no reminder.dispatched emits."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(recent_ids={"commit-1"})
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "unreachable",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-15T09:30:00+00:00", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_skill_failure_emits_sentinel_no_outbound() -> None:
    """compose_reminder raises (timeout) → sentinel emits with
    validation_failed=True, delivered=False; outbound() is NOT called
    per [§7.7]."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_raises=OpenClawTimeout,
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T09:30:00+00:00", ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert len(dispatched) == 1
    assert dispatched[0].envelope.payload["validation_failed"] is True
    assert dispatched[0].envelope.payload["delivered"] is False
    assert dispatched[0].envelope.payload["composed_text"] == ""
    # No observation events on the sentinel path.
    assert all(c.envelope.type == "reminder.dispatched" for c in log.calls)


@pytest.mark.asyncio
async def test_observation_mode_active_suppresses_notification() -> None:
    """Observation mode persists a suppress decision and never delivers."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reminder: call doc at 10:00.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T09:30:00+00:00", ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert len(dispatched) == 1
    assert dispatched[0].envelope.payload["delivered"] is False
    assert dispatched[0].envelope.payload["validation_failed"] is False
    decision = next(
        call.envelope
        for call in log.calls
        if call.envelope.type == "notification.decision.made"
    )
    assert decision.payload["decision"] == "suppress"
    assert decision.payload["reasons"] == ["observation_mode_active"]


@pytest.mark.asyncio
async def test_per_member_reminders_disabled_skips() -> None:
    """Per-member override `reminders_enabled: False` → commitment is
    skipped (no reminder.dispatched, no skill call)."""
    log = _FakeEventLog()
    skill_calls: list[Any] = []

    pipeline = ReminderDispatchPipeline(
        profile_loader=lambda _mid: {"profile_format": "fog_aware"},
        commitments_due_loader=lambda _now, _look: [_commitment()],
        recent_reminders_loader=lambda _now, _ded: set(),
        member_override_loader=lambda _mid: {"reminders_enabled": False},
    )

    async def stub_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        skill_calls.append((skill_id, inputs))
        raise AssertionError("opt-out path must NOT call the skill")

    from adminme.lib.session import build_internal_session

    session = build_internal_session("pipeline_runner", "device", "tenant-test")

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError("opt-out path must NOT call outbound")

    ctx = PipelineContext(
        session=session,
        event_log=log,
        event_sink=log,
        run_skill_fn=stub_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=_FakeObservationManager(active=False),
        triggering_event_id="tick-eid",
        correlation_id="corr",
    )
    await pipeline.dispatch("2026-05-15T09:30:00+00:00", ctx)
    assert log.calls == []
    assert skill_calls == []


@pytest.mark.asyncio
async def test_causation_and_correlation_propagate() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reminder.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        triggering_event_id="tick-trigger-eid",
        correlation_id="corr-XYZ",
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T09:30:00+00:00", ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert dispatched[0].correlation_id == "corr-XYZ"
    assert dispatched[0].causation_id == "tick-trigger-eid"


@pytest.mark.asyncio
async def test_handle_without_now_iso_skips_silently() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(log, skill_outputs={})
    bad_event = {"event_id": "x", "type": "reminder.tick", "payload": {}}
    await pipeline.handle(bad_event, ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_handle_routes_through_dispatch() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reminder.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    triggering_event = {
        "event_id": "tick-1",
        "type": "reminder.tick",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "correlation_id": None,
        "payload": {"now_iso": "2026-05-15T09:30:00+00:00"},
    }
    await pipeline.handle(triggering_event, ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert len(dispatched) == 1
