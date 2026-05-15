"""Handler-direct unit tests for reminder_dispatch.

Constructor-injection-loader pattern per PM-27 (mirroring the
morning_digest shape). Tests cover the due-window filter, event-log
dedupe, per-member opt-out, defensive-default (skill failure), and
observation-mode suppression.

Tenant-identity firewall per [§12.4]: member ids and party ids in
fixtures are placeholders ("member-a", "party-1"); no real tenant
names.
"""

from __future__ import annotations

import importlib.util
import time
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
    / "reminder_dispatch"
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
DEFENSIVE_TONE_NOTE = _handler.DEFENSIVE_TONE_NOTE


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
            "reminder_dispatch must route outbound() through the "
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


# Fixed "now" so window math is deterministic.
_FAKE_NOW = 1_750_000_000.0


def _commitment_due_in(minutes: float, *, cid: str = "commit-1") -> dict[str, Any]:
    return {
        "id": cid,
        "owner_member_id": "member-a",
        "recipient_party_id": "party-1",
        "due_at": _FAKE_NOW + minutes * 60,
        "title": "send paperwork",
    }


def _build_pipeline(
    *,
    commitments: list[dict[str, Any]] | None = None,
    recent_dispatched: list[dict[str, Any]] | None = None,
    member_profiles: dict[str, dict[str, Any]] | None = None,
    parties: dict[str, dict[str, Any]] | None = None,
    per_member_overrides: dict[str, dict[str, Any]] | None = None,
) -> Any:
    profiles = member_profiles or {}
    party_map = parties or {}
    return ReminderDispatchPipeline(
        commitments_loader=lambda _w: list(commitments or []),
        recent_dispatched_loader=lambda _w: list(recent_dispatched or []),
        member_profile_loader=lambda mid: profiles.get(mid),
        party_loader=lambda pid: party_map.get(pid),
        per_member_overrides=per_member_overrides or {},
        now_fn=lambda: _FAKE_NOW,
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_commitment_emits_dispatched() -> None:
    """One open commitment due 30 min from now → one reminder.dispatched."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment_due_in(30)],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reminder: send paperwork by 5pm.",
            "suggested_channel": "imessage",
            "tone_note": "",
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx, triggering_event={"event_id": "tick-1"})
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert len(dispatched) == 1
    payload = dispatched[0].envelope.payload
    assert payload["commitment_id"] == "commit-1"
    assert payload["composed_text"].startswith("Reminder:")
    assert payload["delivered"] is True


@pytest.mark.asyncio
async def test_outside_lookahead_window_no_emit() -> None:
    """Commitment due 3 hours away (outside 60-min window) → no emit."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment_due_in(180)],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert dispatched == []


@pytest.mark.asyncio
async def test_dedupe_hit_no_emit() -> None:
    """Recent reminder.dispatched for the same commitment_id → no second emit."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment_due_in(30)],
        recent_dispatched=[{"commitment_id": "commit-1"}],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert dispatched == []


@pytest.mark.asyncio
async def test_skill_failure_emits_dispatched_with_empty_composed_text() -> None:
    """Skill raises → per-row defensive default: reminder.dispatched emits
    with composed_text='' and tone_note='skill_failure_defensive_default'.
    No outbound() side-effect (composed_text empty)."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment_due_in(30)],
    )
    ctx = _make_ctx(
        log,
        skill_raises=OpenClawTimeout,
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert len(dispatched) == 1
    payload = dispatched[0].envelope.payload
    assert payload["composed_text"] == ""
    assert payload["tone_note"] == DEFENSIVE_TONE_NOTE
    assert payload["delivered"] is False
    # No external.sent on the defensive-default path.
    assert not any(c.envelope.type == "external.sent" for c in log.calls)


@pytest.mark.asyncio
async def test_observation_mode_suppresses_delivery_but_event_landed() -> None:
    """Observation mode active → outbound() suppressed → reminder.dispatched
    still in log with delivered=False; observation.suppressed in log."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment_due_in(30)],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reminder text.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch(ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert len(dispatched) == 1
    assert dispatched[0].envelope.payload["delivered"] is False
    suppressed = [c for c in log.calls if c.envelope.type == "observation.suppressed"]
    assert len(suppressed) == 1


@pytest.mark.asyncio
async def test_per_member_reminders_disabled() -> None:
    """Per-member override sets reminders_enabled=false → no emit even
    when commitment is in window and not deduped."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment_due_in(30)],
        per_member_overrides={"member-a": {"reminders_enabled": False}},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    dispatched = [c for c in log.calls if c.envelope.type == "reminder.dispatched"]
    assert dispatched == []
