"""Handler-direct unit tests for custody_brief.

Constructor-injection-loader pattern per PM-27. Tests cover the
coparent-relationship gate, per-member opt-out, empty-calendar still
composes, per-row defensive defaults, and observation-mode suppression.

Tenant-identity firewall per [§12.4]: member ids in fixtures are
placeholders; no real tenant names.
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
    skill_raises_for_members: dict[str, type[BaseException]] | None = None,
    triggering_event_id: str = "trigger-eid-001",
    correlation_id: str | None = "corr-001",
    observation_manager: _FakeObservationManager | None = None,
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session(
        "pipeline_runner", "device", "tenant-test"
    )
    raises_map = skill_raises_for_members or {}

    async def stub_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        parent_id = (
            inputs.get("member_profile", {}).get("id")
            if isinstance(inputs, dict)
            else None
        )
        if parent_id in raises_map:
            raise raises_map[parent_id]("simulated skill failure")
        return _FakeSkillResult(output=dict(skill_outputs or {}))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "custody_brief must route outbound() through the observation "
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


def _parent(
    mid: str = "member-a",
    coparent: str = "member-cp",
    **kwargs: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": mid,
        "coparent_relationship_member_id": coparent,
        "coparent_handoff_pattern": {"weekday_pickup": "17:30"},
        "child_health_flags": [
            {"child_id": "child-1", "allergens": ["peanut"]}
        ],
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_with_coparent_relationship_emits_composed() -> None:
    log = _FakeEventLog()
    pipeline = CustodyBriefPipeline(
        profile_loader=lambda mid: {"id": mid},
        coparent_members_loader=lambda: [_parent("member-a")],
        child_profiles_loader=lambda _mid: [
            {"id": "child-1", "name": "Alex"},
            {"id": "child-2", "name": "Bea"},
        ],
        calendar_loader=lambda _cid, _anchor: [
            {"id": "cal-1", "title": "soccer", "start_at": "16:00"}
        ],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Tomorrow: soccer at 16:00. Handoff 17:30.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    composed = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert len(composed) == 1
    payload = composed[0].envelope.payload
    assert payload["member_id"] == "member-a"
    assert payload["coparent_member_id"] == "member-cp"
    assert payload["child_member_ids"] == ["child-1", "child-2"]
    assert payload["channel"] == "imessage"


@pytest.mark.asyncio
async def test_member_without_coparent_relationship_no_emit() -> None:
    """coparent_members_loader returns empty → no emit."""
    log = _FakeEventLog()
    pipeline = CustodyBriefPipeline(
        coparent_members_loader=lambda: [],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_opt_out_via_per_member_override() -> None:
    log = _FakeEventLog()
    pipeline = CustodyBriefPipeline(
        coparent_members_loader=lambda: [_parent("member-a")],
        child_profiles_loader=lambda _mid: [{"id": "child-1"}],
        config={
            "per_member_overrides": {
                "member-a": {"custody_brief_enabled": False}
            }
        },
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "x", "suggested_channel": "imessage"},
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_empty_calendar_still_emits() -> None:
    """No calendar events tomorrow → still compose (handoff timing +
    allergen reminders carry the brief)."""
    log = _FakeEventLog()
    pipeline = CustodyBriefPipeline(
        profile_loader=lambda mid: {"id": mid},
        coparent_members_loader=lambda: [_parent("member-a")],
        child_profiles_loader=lambda _mid: [{"id": "child-1"}],
        calendar_loader=lambda _cid, _anchor: [],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Quiet day tomorrow. Handoff 17:30. Peanut allergy reminder.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    composed = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert len(composed) == 1
    assert composed[0].envelope.payload["child_member_ids"] == ["child-1"]


@pytest.mark.asyncio
async def test_observation_mode_suppresses_delivery_but_event_landed() -> None:
    log = _FakeEventLog()
    pipeline = CustodyBriefPipeline(
        profile_loader=lambda mid: {"id": mid},
        coparent_members_loader=lambda: [_parent("member-a")],
        child_profiles_loader=lambda _mid: [{"id": "child-1"}],
        calendar_loader=lambda _cid, _anchor: [],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Brief body.",
            "suggested_channel": "imessage",
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-16", ctx)
    types = [c.envelope.type for c in log.calls]
    assert "custody_brief.composed" in types
    assert "observation.suppressed" in types
    assert "external.sent" not in types


@pytest.mark.asyncio
async def test_skill_failure_skips_row_not_tick() -> None:
    log = _FakeEventLog()
    pipeline = CustodyBriefPipeline(
        profile_loader=lambda mid: {"id": mid},
        coparent_members_loader=lambda: [
            _parent("member-a"),
            _parent("member-b", coparent="member-cp-2"),
        ],
        child_profiles_loader=lambda _mid: [{"id": "child-1"}],
        calendar_loader=lambda _cid, _anchor: [],
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
    await pipeline.dispatch("2026-05-16", ctx)
    composed = [
        c for c in log.calls if c.envelope.type == "custody_brief.composed"
    ]
    assert len(composed) == 1
    assert composed[0].envelope.payload["member_id"] == "member-b"


@pytest.mark.asyncio
async def test_handle_without_brief_date_iso_skips_silently() -> None:
    log = _FakeEventLog()
    pipeline = CustodyBriefPipeline()
    ctx = _make_ctx(log)
    bad_event = {"event_id": "x", "type": "custody.tick", "payload": {}}
    await pipeline.handle(bad_event, ctx)
    assert log.calls == []
