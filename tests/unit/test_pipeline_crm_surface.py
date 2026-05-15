"""Handler-direct unit tests for crm_surface.

Constructor-injection-loader pattern per PM-27. Tests cover the six
per-prompt cases listed in §8 C3:

- emit on opt-in member with stale parties;
- no-emit without opt-in;
- no-emit with no stale parties;
- clamp at `max_surfaced_per_week`;
- obs-on suppression;
- per-row skill failure does not abort tick.

crm_surface intentionally has NO `validate()` method per [§7]
("absence of `validate()` for `crm_surface` — `gap_kind` is the
load-bearing payload field, not an id list"). Tests assert this
absence: AttributeError on hasattr check.

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
            "crm_surface must route outbound() through the "
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
    parties: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "member_id": member_id,
        "surfaced_parties": parties
        if parties is not None
        else [
            {"party_id": "party-1", "gap_kind": "stale_contact"},
            {"party_id": "party-2", "gap_kind": "stale_contact"},
        ],
    }


def _seeded_pipeline(
    *,
    targets: list[dict[str, Any]] | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    profile_format: str = "fog_aware",
) -> CrmSurfacePipeline:
    targets = targets if targets is not None else [_target()]
    overrides = (
        overrides
        if overrides is not None
        else {"member-a": {"crm_proactive_enabled": True}}
    )
    return CrmSurfacePipeline(
        profile_loader=lambda _mid: {"profile_format": profile_format},
        crm_targets_loader=lambda _week: targets,
        member_override_loader=lambda mid: overrides.get(mid),
    )


def test_crm_surface_has_no_validate_method() -> None:
    """Per [§7]: crm_surface has NO validate() method — the morning-
    digest claimed_event_ids guard does not apply. Assert the absence."""
    pipeline = CrmSurfacePipeline()
    assert not hasattr(pipeline, "validate")


@pytest.mark.asyncio
async def test_emit_on_opt_in_member_with_stale_parties() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Catch up with party-1, party-2.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    surfaced = [c for c in log.calls if c.envelope.type == "crm.surfaced"]
    assert len(surfaced) == 1
    env = surfaced[0].envelope
    assert env.payload["member_id"] == "member-a"
    assert env.payload["surfaced_party_ids"] == ["party-1", "party-2"]
    assert env.payload["gap_kind"] == "stale_contact"
    assert env.payload["validation_failed"] is False
    assert env.payload["delivered"] is True


@pytest.mark.asyncio
async def test_no_emit_without_opt_in() -> None:
    """Default is opt-in=false; member without crm_proactive_enabled=true
    in override → skipped entirely (no skill call, no emit)."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(overrides={})
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "unreachable"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_no_emit_with_no_stale_parties() -> None:
    """Opt-in member but loader returns empty surfaced_parties →
    no emit (the load-bearing payload is empty)."""
    log = _FakeEventLog()
    pipeline = _seeded_pipeline(
        targets=[_target(parties=[])],
    )
    ctx = _make_ctx(
        log,
        skill_outputs={"composed_text": "unreachable"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_clamp_at_max_surfaced_per_week() -> None:
    """Loader returns 8 parties; max_surfaced_per_week=3 clamps to 3."""
    log = _FakeEventLog()
    many = [
        {"party_id": f"party-{i}", "gap_kind": "stale_contact"}
        for i in range(8)
    ]
    pipeline = _seeded_pipeline(targets=[_target(parties=many)])
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Three to check on this week.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-10", ctx, max_surfaced=3)
    surfaced = [c for c in log.calls if c.envelope.type == "crm.surfaced"]
    assert len(surfaced) == 1
    assert surfaced[0].envelope.payload["surfaced_party_ids"] == [
        "party-0",
        "party-1",
        "party-2",
    ]


@pytest.mark.asyncio
async def test_observation_mode_active_suppresses_outbound() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Check on party-1.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    surfaced = [c for c in log.calls if c.envelope.type == "crm.surfaced"]
    assert len(surfaced) == 1
    assert surfaced[0].envelope.payload["delivered"] is False
    suppressed = [
        c for c in log.calls if c.envelope.type == "observation.suppressed"
    ]
    assert len(suppressed) == 1


@pytest.mark.asyncio
async def test_per_row_skill_failure_does_not_abort_tick() -> None:
    """Member-a's skill call raises; member-b's succeeds. The first
    emits a sentinel and the second emits normally — the tick
    completes."""
    log = _FakeEventLog()
    overrides = {
        "member-a": {"crm_proactive_enabled": True},
        "member-b": {"crm_proactive_enabled": True},
    }
    pipeline = _seeded_pipeline(
        targets=[
            _target(member_id="member-a"),
            _target(member_id="member-b"),
        ],
        overrides=overrides,
    )
    ctx = _make_ctx(
        log,
        skill_outputs={
            "composed_text": "Reach out to party-1.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        skill_raises_for={"member-a"},
        observation_manager=_FakeObservationManager(active=False),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    surfaced = [c for c in log.calls if c.envelope.type == "crm.surfaced"]
    assert len(surfaced) == 2
    by_member = {c.envelope.payload["member_id"]: c for c in surfaced}
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
            "composed_text": "Check in.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        triggering_event_id="tick-trigger-eid",
        correlation_id="corr-XYZ",
        observation_manager=_FakeObservationManager(active=True),
    )
    await pipeline.dispatch("2026-05-10", ctx)
    surfaced = [c for c in log.calls if c.envelope.type == "crm.surfaced"]
    assert surfaced[0].correlation_id == "corr-XYZ"
    assert surfaced[0].causation_id == "tick-trigger-eid"


@pytest.mark.asyncio
async def test_handle_without_week_of_date_skips_silently() -> None:
    log = _FakeEventLog()
    pipeline = _seeded_pipeline()
    ctx = _make_ctx(log, skill_outputs={})
    bad_event = {"event_id": "x", "type": "crm.tick", "payload": {}}
    await pipeline.handle(bad_event, ctx)
    assert log.calls == []
