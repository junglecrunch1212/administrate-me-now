"""Handler-direct unit tests for custody_brief.

Constructor-injection-loader pattern per PM-27 (mirroring the
morning_digest shape). Tests cover the coparent-relationship gate,
per-member opt-out, empty-calendar still-emits, defensive-default
(skill failure on one parent does not abort the tick), and
observation-mode suppression.

Tenant-identity firewall per [§12.4]: member ids in fixtures are
placeholders.
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
    skill_raises_for_member: str | None = None,
    skill_per_member: dict[str, Any] | None = None,
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
        if skill_per_member and member in skill_per_member:
            return _FakeSkillResult(output=dict(skill_per_member[member]))
        return _FakeSkillResult(output=dict(skill_outputs or {}))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError("custody_brief must use observation seam")

    return PipelineContext(
        session=session,
        event_log=event_log,
        run_skill_fn=stub_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=observation_manager,
        triggering_event_id="trigger-eid-001",
        correlation_id="corr-001",
    )


def _parent_profile(
    member_id: str,
    coparent_id: str | None,
    *,
    handoff: dict[str, Any] | None = None,
    flags: list[Any] | None = None,
) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "id": member_id,
        "tone": "direct",
    }
    if coparent_id is not None:
        profile["coparent_relationship_member_id"] = coparent_id
    if handoff is not None:
        profile["coparent_handoff_pattern"] = handoff
    if flags is not None:
        profile["child_health_flags"] = flags
    return profile


def _build_pipeline(
    *,
    coparent_members: list[dict[str, Any]] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    children_by_parent: dict[str, list[dict[str, Any]]] | None = None,
    calendar_by_child: dict[str, list[dict[str, Any]]] | None = None,
    per_member_overrides: dict[str, dict[str, Any]] | None = None,
) -> Any:
    prof = profiles or {}
    kids = children_by_parent or {}
    cal = calendar_by_child or {}
    return CustodyBriefPipeline(
        coparent_members_loader=lambda: list(coparent_members or []),
        member_profile_loader=lambda mid: prof.get(mid),
        child_profiles_loader=lambda mid: list(kids.get(mid, [])),
        calendar_events_loader=lambda cid, _date: list(cal.get(cid, [])),
        per_member_overrides=per_member_overrides or {},
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_with_coparent_relationship_emits_composed() -> None:
    """Parent + coparent + 2 children → custody_brief.composed with both kids."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        coparent_members=[{"id": "parent-a"}],
        profiles={
            "parent-a": _parent_profile(
                "parent-a",
                "parent-b",
                handoff={"day_of_week": "fri", "time": "17:00"},
                flags=["peanut-allergy"],
            ),
            "parent-b": _parent_profile("parent-b", None),
        },
        children_by_parent={
            "parent-a": [{"id": "child-1"}, {"id": "child-2"}],
        },
        calendar_by_child={
            "child-1": [{"id": "ev-1", "title": "soccer"}],
            "child-2": [{"id": "ev-2", "title": "piano"}],
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Tomorrow: soccer + piano; handoff Fri 5pm.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx, triggering_event={"event_id": "tick-1"})
    composed = [c for c in log.calls if c.envelope.type == "custody_brief.composed"]
    assert len(composed) == 1
    payload = composed[0].envelope.payload
    assert payload["member_id"] == "parent-a"
    assert payload["coparent_member_id"] == "parent-b"
    assert set(payload["child_member_ids"]) == {"child-1", "child-2"}
    assert payload["delivered"] is True


@pytest.mark.asyncio
async def test_member_without_coparent_relationship_no_emit() -> None:
    """No coparent set on profile → no emit."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        coparent_members=[{"id": "parent-a"}],
        profiles={"parent-a": _parent_profile("parent-a", None)},
        children_by_parent={"parent-a": [{"id": "child-1"}]},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "custody_brief.composed"]
    assert composed == []


@pytest.mark.asyncio
async def test_opt_out_via_per_member_override() -> None:
    """custody_brief_enabled=false override → no emit even with coparent set."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        coparent_members=[{"id": "parent-a"}],
        profiles={
            "parent-a": _parent_profile("parent-a", "parent-b"),
            "parent-b": _parent_profile("parent-b", None),
        },
        children_by_parent={"parent-a": [{"id": "child-1"}]},
        per_member_overrides={"parent-a": {"custody_brief_enabled": False}},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "custody_brief.composed"]
    assert composed == []


@pytest.mark.asyncio
async def test_empty_calendar_still_emits() -> None:
    """No events next day → brief still composes (handoff + allergens only)."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        coparent_members=[{"id": "parent-a"}],
        profiles={
            "parent-a": _parent_profile(
                "parent-a",
                "parent-b",
                handoff={"day_of_week": "sun", "time": "18:00"},
                flags=["epipen"],
            ),
            "parent-b": _parent_profile("parent-b", None),
        },
        children_by_parent={"parent-a": [{"id": "child-1"}]},
        calendar_by_child={"child-1": []},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Tomorrow: quiet day. Handoff Sun 6pm. Epipen.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "custody_brief.composed"]
    assert len(composed) == 1
    assert composed[0].envelope.payload["child_member_ids"] == ["child-1"]


@pytest.mark.asyncio
async def test_observation_mode_suppresses_delivery_but_event_landed() -> None:
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        coparent_members=[{"id": "parent-a"}],
        profiles={
            "parent-a": _parent_profile("parent-a", "parent-b"),
            "parent-b": _parent_profile("parent-b", None),
        },
        children_by_parent={"parent-a": [{"id": "child-1"}]},
        calendar_by_child={"child-1": [{"id": "ev-1"}]},
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Nightly brief.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "custody_brief.composed"]
    assert len(composed) == 1
    assert composed[0].envelope.payload["delivered"] is False
    suppressed = [c for c in log.calls if c.envelope.type == "observation.suppressed"]
    assert len(suppressed) == 1


@pytest.mark.asyncio
async def test_skill_failure_skips_row_not_tick() -> None:
    """Two parents, one's compose fails — the other's brief still emits."""
    log = _FakeEventLog()
    pipeline = _build_pipeline(
        coparent_members=[{"id": "parent-a"}, {"id": "parent-c"}],
        profiles={
            "parent-a": _parent_profile("parent-a", "parent-b"),
            "parent-b": _parent_profile("parent-b", None),
            "parent-c": _parent_profile("parent-c", "parent-d"),
            "parent-d": _parent_profile("parent-d", None),
        },
        children_by_parent={
            "parent-a": [{"id": "child-1"}],
            "parent-c": [{"id": "child-2"}],
        },
        calendar_by_child={"child-1": [], "child-2": []},
    )
    ctx = _make_ctx(
        log,
        skill_per_member={
            "parent-c": {
                "composed_text": "C's brief.",
                "suggested_channel": "imessage",
            },
        },
        skill_raises_for_member="parent-a",
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch(ctx)
    composed = [c for c in log.calls if c.envelope.type == "custody_brief.composed"]
    member_ids = [c.envelope.payload["member_id"] for c in composed]
    assert "parent-c" in member_ids
    assert "parent-a" not in member_ids
