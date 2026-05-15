"""Handler-direct unit tests for crm_surface.

Constructor-injection-loader pattern per PM-27 (mirroring the
morning_digest shape). Tests cover the opt-in gate (default-false
crm_proactive_enabled), the staleness scan, the max_surfaced clamp,
defensive-default (skill failure on one member does not abort the
tick), and observation-mode suppression.

Tenant-identity firewall per [§12.4]: member ids and party ids in
fixtures are placeholders.
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
    skill_outputs: dict[str, Any] | None = None,
    skill_raises: type[BaseException] | None = None,
    skill_per_member: dict[str, Any] | None = None,
    skill_raises_for_member: str | None = None,
    triggering_event_id: str = "trigger-eid-001",
    observation_manager: _FakeObservationManager | None = None,
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session(
        "pipeline_runner", "device", "tenant-test"
    )

    async def stub_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        member = (inputs or {}).get("member_profile", {}).get("id")
        if skill_raises_for_member is not None and member == skill_raises_for_member:
            raise OpenClawTimeout("simulated failure for one member")
        if skill_raises is not None:
            raise skill_raises("simulated skill failure")
        if skill_per_member and member in skill_per_member:
            return _FakeSkillResult(output=dict(skill_per_member[member]))
        return _FakeSkillResult(output=dict(skill_outputs or {}))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError("crm_surface must use observation seam")

    return PipelineContext(
        session=session,
        event_log=event_log,
        run_skill_fn=stub_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=observation_manager,
        triggering_event_id=triggering_event_id,
        correlation_id="corr-001",
    )


def _build_pipeline(
    *,
    members: list[dict[str, Any]] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    stale_by_member: dict[str, list[dict[str, Any]]] | None = None,
    per_member_overrides: dict[str, dict[str, Any]] | None = None,
    max_surfaced_per_week: int = 5,
) -> Any:
    prof = profiles or {}
    stale = stale_by_member or {}
    return CrmSurfacePipeline(
        members_loader=lambda: list(members or []),
        member_profile_loader=lambda mid: prof.get(mid),
        stale_parties_loader=lambda mid, _d: list(stale.get(mid, [])),
        max_surfaced_per_week=max_surfaced_per_week,
        per_member_overrides=per_member_overrides or {},
    )


def _party(pid: str) -> dict[str, Any]:
    return {"id": pid, "display_name": f"Party {pid}"}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_with_stale_parties_emits_composed() -> None:
    """Opted-in member with 3 stale parties → crm_surface.composed emits
    with the surfaced party ids."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        members=[{"id": "member-a"}],
        profiles={
            "member-a": {
                "id": "member-a",
                "crm_proactive_enabled": True,
                "tone": "direct",
            },
        },
        stale_by_member={
            "member-a": [_party("party-1"), _party("party-2"), _party("party-3")],
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Stale: party-1, party-2, party-3.",
            "suggested_channel": "imessage",
            "sort_order": ["party-1", "party-2", "party-3"],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx, triggering_event={"event_id": "tick-1"})
    composed = [c for c in log.calls if c.envelope.type == "crm_surface.composed"]
    assert len(composed) == 1
    payload = composed[0].envelope.payload
    assert payload["member_id"] == "member-a"
    assert payload["surfaced_party_ids"] == ["party-1", "party-2", "party-3"]
    assert payload["delivered"] is True


@pytest.mark.asyncio
async def test_member_without_opt_in_no_emit() -> None:
    """Default-false crm_proactive_enabled → no emit even with stale parties."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        members=[{"id": "member-a"}],
        profiles={"member-a": {"id": "member-a"}},  # no opt-in
        stale_by_member={"member-a": [_party("party-1")]},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "crm_surface.composed"]
    assert composed == []


@pytest.mark.asyncio
async def test_no_stale_parties_no_emit() -> None:
    """Opt-in true but no stale parties → no emit."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        members=[{"id": "member-a"}],
        profiles={
            "member-a": {"id": "member-a", "crm_proactive_enabled": True},
        },
        stale_by_member={"member-a": []},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "crm_surface.composed"]
    assert composed == []


@pytest.mark.asyncio
async def test_max_surfaced_clamp() -> None:
    """12 stale parties + max_surfaced_per_week=5 → surfaced_party_ids has 5."""
    log = _FakeEventLog()
    parties = [_party(f"party-{i}") for i in range(12)]
    pipeline = _build_pipeline(
        members=[{"id": "member-a"}],
        profiles={
            "member-a": {"id": "member-a", "crm_proactive_enabled": True},
        },
        stale_by_member={"member-a": parties},
        max_surfaced_per_week=5,
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Five stale contacts.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "crm_surface.composed"]
    assert len(composed) == 1
    assert len(composed[0].envelope.payload["surfaced_party_ids"]) == 5


@pytest.mark.asyncio
async def test_observation_mode_suppresses_delivery_but_event_landed() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        members=[{"id": "member-a"}],
        profiles={
            "member-a": {"id": "member-a", "crm_proactive_enabled": True},
        },
        stale_by_member={"member-a": [_party("party-1")]},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reach out to party-1.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "crm_surface.composed"]
    assert len(composed) == 1
    assert composed[0].envelope.payload["delivered"] is False
    suppressed = [c for c in log.calls if c.envelope.type == "observation.suppressed"]
    assert len(suppressed) == 1


@pytest.mark.asyncio
async def test_skill_failure_skips_row_not_tick() -> None:
    """Two members, one's compose fails — the other's brief still emits."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        members=[{"id": "member-a"}, {"id": "member-b"}],
        profiles={
            "member-a": {"id": "member-a", "crm_proactive_enabled": True},
            "member-b": {"id": "member-b", "crm_proactive_enabled": True},
        },
        stale_by_member={
            "member-a": [_party("party-1")],
            "member-b": [_party("party-2")],
        },
    )
    ctx = _make_ctx(
        log,
        skill_per_member={
            "member-b": {
                "composed_text": "Stale: party-2.",
                "suggested_channel": "imessage",
            },
        },
        skill_raises_for_member="member-a",
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "crm_surface.composed"]
    member_ids = [c.envelope.payload["member_id"] for c in composed]
    assert "member-b" in member_ids
    assert "member-a" not in member_ids
