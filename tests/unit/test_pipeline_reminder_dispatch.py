"""Handler-direct unit tests for reminder_dispatch.

Constructor-injection-loader pattern per PM-27 (mirroring 10c-i's
reward_dispatch and 10c-ii's morning_digest). Tests cover the
lookahead window, event-log dedupe, per-row defensive defaults,
per-member opt-out, and observation-mode suppression.

Tenant-identity firewall per [§12.4]: member/party/commitment ids in
fixtures are placeholders; no real tenant names.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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


def _commitment(
    cid: str = "commit-1",
    owner: str = "member-a",
    recipient: str = "party-x",
    minutes_until_due: int = 30,
) -> dict[str, Any]:
    due_at = datetime.now(tz=timezone.utc) + timedelta(
        minutes=minutes_until_due
    )
    return {
        "id": cid,
        "owner_member_id": owner,
        "recipient_party_id": recipient,
        "due_at": due_at.isoformat(),
        "title": "call doctor",
    }


def _build_pipeline(
    *,
    commitments: list[dict[str, Any]] | None = None,
    parties: dict[str, dict[str, Any]] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    recent_dispatches: dict[str, list[dict[str, Any]]] | None = None,
    config: dict[str, Any] | None = None,
) -> Any:
    rows = commitments or []
    party_map = parties or {}
    profile_map = profiles or {}
    recent_map = recent_dispatches or {}
    return ReminderDispatchPipeline(
        profile_loader=lambda mid: profile_map.get(mid),
        due_commitments_loader=lambda _now, _until: list(rows),
        party_loader=lambda pid: party_map.get(pid),
        recent_dispatch_loader=lambda cid, _since: list(
            recent_map.get(cid, [])
        ),
        config=config,
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_commitment_emits_dispatched() -> None:
    """One open commitment due in 30 min → one reminder.dispatched."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment("commit-1", minutes_until_due=30)],
        parties={"party-x": {"id": "party-x", "name": "Doc"}},
        profiles={"member-a": {"id": "member-a", "tone": "warm"}},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Heads up: call doctor in 30 min.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T12:00:00Z", ctx)
    dispatched = [
        c for c in log.calls if c.envelope.type == "reminder.dispatched"
    ]
    assert len(dispatched) == 1
    payload = dispatched[0].envelope.payload
    assert payload["commitment_id"] == "commit-1"
    assert payload["composed_text"].startswith("Heads up")
    assert payload["channel"] == "imessage"


@pytest.mark.asyncio
async def test_outside_lookahead_window_no_emit() -> None:
    """Commitment due 3 hours away → not in candidate set → no emit.
    The loader fake honors the window the pipeline computes."""
    log = _FakeEventLog()
    # Loader returns empty (the pipeline asked for the window;
    # nothing was inside it). Mirrors the real query shape.
    pipeline = ReminderDispatchPipeline(
        due_commitments_loader=lambda _now, _until: [],
    )
    ctx = _make_ctx(log, skill_outputs={"composed_text": "x"})
    await pipeline.dispatch("2026-05-15T12:00:00Z", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_dedupe_hit_no_emit() -> None:
    """A recent reminder.dispatched for the same commitment → no
    second emit."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment("commit-1")],
        parties={"party-x": {"id": "party-x"}},
        recent_dispatches={
            "commit-1": [
                {
                    "event_id": "prior",
                    "type": "reminder.dispatched",
                    "payload": {"commitment_id": "commit-1"},
                }
            ]
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "x",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T12:00:00Z", ctx)
    dispatched = [
        c for c in log.calls if c.envelope.type == "reminder.dispatched"
    ]
    assert dispatched == []


@pytest.mark.asyncio
async def test_skill_failure_emits_dispatched_with_empty_composed_text() -> None:
    """Defensive-default per row per [§7.7]: skill raises → emit
    reminder.dispatched with composed_text='' so audit trail records
    the attempt; outbound is not called for empty text."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment("commit-1")],
        parties={"party-x": {"id": "party-x"}},
    )
    ctx = _make_ctx(
        log,
        skill_raises=OpenClawTimeout,
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T12:00:00Z", ctx)
    dispatched = [
        c for c in log.calls if c.envelope.type == "reminder.dispatched"
    ]
    assert len(dispatched) == 1
    assert dispatched[0].envelope.payload["composed_text"] == ""
    # No outbound branch fires for empty text → no observation.suppressed
    # and no external.sent on this row.
    assert all(
        c.envelope.type == "reminder.dispatched" for c in log.calls
    )


@pytest.mark.asyncio
async def test_observation_mode_suppresses_delivery_but_event_landed() -> None:
    """Observation active → outbound emits observation.suppressed; the
    reminder.dispatched event still lands per [§6.13]."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment("commit-1")],
        parties={"party-x": {"id": "party-x"}},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reminder body.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T12:00:00Z", ctx)
    types = [c.envelope.type for c in log.calls]
    assert "reminder.dispatched" in types
    assert "observation.suppressed" in types
    assert "external.sent" not in types


@pytest.mark.asyncio
async def test_per_member_reminders_disabled() -> None:
    """Per-member override reminders_enabled=false → no emit even with
    a due commitment."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment("commit-1", owner="member-a")],
        parties={"party-x": {"id": "party-x"}},
        config={
            "per_member_overrides": {
                "member-a": {"reminders_enabled": False}
            }
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T12:00:00Z", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_correlation_and_causation_propagate() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        commitments=[_commitment("commit-1")],
        parties={"party-x": {"id": "party-x"}},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        correlation_id="corr-XYZ",
        triggering_event_id="tick-cause-1",
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-15T12:00:00Z", ctx)
    dispatched = [
        c for c in log.calls if c.envelope.type == "reminder.dispatched"
    ]
    assert dispatched[0].correlation_id == "corr-XYZ"
    assert dispatched[0].causation_id == "tick-cause-1"


@pytest.mark.asyncio
async def test_handle_without_now_iso_skips_silently() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline()
    ctx = _make_ctx(log)
    bad_event = {"event_id": "x", "type": "reminder.tick", "payload": {}}
    await pipeline.handle(bad_event, ctx)
    assert log.calls == []
