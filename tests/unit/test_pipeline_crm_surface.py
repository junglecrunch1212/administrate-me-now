"""Handler-direct unit tests for crm_surface.

Constructor-injection-loader pattern per PM-27. Tests cover opt-in
gating per [D22], the max_surfaced_per_week clamp, per-row defensive
defaults, and observation-mode suppression.

Tenant-identity firewall per [§12.4]: member/party ids in fixtures
are placeholders; no real tenant names.
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
    / "crm_surface"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_crm_surface_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
CrmSurfacePipeline = _handler.CrmSurfacePipeline


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
    skill_outputs_per_call: list[dict[str, Any]] | None = None,
    skill_outputs: dict[str, Any] | None = None,
    skill_raises_for_members: dict[str, type[BaseException]] | None = None,
    triggering_event_id: str = "trigger-eid-001",
    correlation_id: str | None = "corr-001",
    observation_manager: _FakeObservationManager | None = None,
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session(
        "pipeline_runner", "device", "tenant-test"
    )
    call_index = [0]
    queued = list(skill_outputs_per_call or [])
    raises_map = skill_raises_for_members or {}

    async def stub_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        member_id = (
            inputs.get("member_profile", {}).get("id")
            if isinstance(inputs, dict)
            else None
        )
        if member_id in raises_map:
            raise raises_map[member_id]("simulated skill failure")
        if queued:
            i = call_index[0]
            call_index[0] += 1
            return _FakeSkillResult(
                output=dict(queued[i % len(queued)])
            )
        return _FakeSkillResult(output=dict(skill_outputs or {}))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "crm_surface must route outbound() through the observation "
            "seam, not the ctx.outbound_fn callable"
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


def _party(pid: str) -> dict[str, Any]:
    return {"id": pid, "name": pid, "last_contact_at": "2025-01-01"}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_with_stale_parties_emits_composed() -> None:
    log = _FakeEventLog()
    pipeline = CrmSurfacePipeline(
        profile_loader=lambda mid: {"id": mid, "tone": "warm"},
        opted_in_members_loader=lambda: ["member-a"],
        stale_parties_loader=lambda _mid, _days: [
            _party("party-1"),
            _party("party-2"),
        ],
        config={
            "per_member_overrides": {
                "member-a": {"crm_proactive_enabled": True}
            }
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Stale contacts: party-1, party-2.",
            "suggested_channel": "imessage",
            "sort_order": ["party-2", "party-1"],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    composed = [
        c for c in log.calls if c.envelope.type == "crm_surface.composed"
    ]
    assert len(composed) == 1
    payload = composed[0].envelope.payload
    assert payload["member_id"] == "member-a"
    # sort_order influences the surfaced_party_ids order.
    assert payload["surfaced_party_ids"] == ["party-2", "party-1"]
    assert payload["channel"] == "imessage"


@pytest.mark.asyncio
async def test_member_without_opt_in_no_emit() -> None:
    """Default crm_proactive_enabled=false → no emit even with stale
    parties present (the loader would have to return false, but we
    test that opted_in_members_loader filtering AND the per-member
    config default both gate the emit)."""
    log = _FakeEventLog()
    pipeline = CrmSurfacePipeline(
        profile_loader=lambda mid: {"id": mid},
        # opted_in_members_loader returns a member that the
        # per_member_overrides config does NOT explicitly enable.
        opted_in_members_loader=lambda: ["member-a"],
        stale_parties_loader=lambda _mid, _days: [_party("party-1")],
        config={},  # no per-member opt-in
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "x",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_no_stale_parties_no_emit() -> None:
    log = _FakeEventLog()
    pipeline = CrmSurfacePipeline(
        profile_loader=lambda mid: {"id": mid},
        opted_in_members_loader=lambda: ["member-a"],
        stale_parties_loader=lambda _mid, _days: [],
        config={
            "per_member_overrides": {
                "member-a": {"crm_proactive_enabled": True}
            }
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
    await pipeline.dispatch("2026-05-10", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_max_surfaced_clamp() -> None:
    """12 stale parties + max_surfaced_per_week=5 → exactly 5 in
    surfaced_party_ids."""
    log = _FakeEventLog()
    parties_in = [_party(f"party-{i}") for i in range(12)]
    pipeline = CrmSurfacePipeline(
        profile_loader=lambda mid: {"id": mid},
        opted_in_members_loader=lambda: ["member-a"],
        stale_parties_loader=lambda _mid, _days: parties_in,
        config={
            "max_surfaced_per_week": 5,
            "per_member_overrides": {
                "member-a": {"crm_proactive_enabled": True}
            },
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Stale.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    composed = [
        c for c in log.calls if c.envelope.type == "crm_surface.composed"
    ]
    assert len(composed) == 1
    assert len(composed[0].envelope.payload["surfaced_party_ids"]) == 5


@pytest.mark.asyncio
async def test_observation_mode_suppresses_delivery_but_event_landed() -> None:
    log = _FakeEventLog()
    pipeline = CrmSurfacePipeline(
        profile_loader=lambda mid: {"id": mid},
        opted_in_members_loader=lambda: ["member-a"],
        stale_parties_loader=lambda _mid, _days: [_party("party-1")],
        config={
            "per_member_overrides": {
                "member-a": {"crm_proactive_enabled": True}
            }
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Stale.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    types = [c.envelope.type for c in log.calls]
    assert "crm_surface.composed" in types
    assert "observation.suppressed" in types
    assert "external.sent" not in types


@pytest.mark.asyncio
async def test_skill_failure_skips_row_not_tick() -> None:
    """One member's skill fails → no event for them; another member's
    surface still composes."""
    log = _FakeEventLog()

    def _profile(mid: str) -> dict[str, Any]:
        return {"id": mid}

    pipeline = CrmSurfacePipeline(
        profile_loader=_profile,
        opted_in_members_loader=lambda: ["member-a", "member-b"],
        stale_parties_loader=lambda _mid, _days: [_party("party-1")],
        config={
            "per_member_overrides": {
                "member-a": {"crm_proactive_enabled": True},
                "member-b": {"crm_proactive_enabled": True},
            }
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "OK.",
            "suggested_channel": "imessage",
        },
        skill_raises_for_members={"member-a": OpenClawTimeout},
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    composed = [
        c for c in log.calls if c.envelope.type == "crm_surface.composed"
    ]
    # Only member-b's event lands.
    assert len(composed) == 1
    assert composed[0].envelope.payload["member_id"] == "member-b"


@pytest.mark.asyncio
async def test_handle_without_week_of_iso_skips_silently() -> None:
    log = _FakeEventLog()
    pipeline = CrmSurfacePipeline()
    ctx = _make_ctx(log)
    bad_event = {"event_id": "x", "type": "crm.tick", "payload": {}}
    await pipeline.handle(bad_event, ctx)
    assert log.calls == []
