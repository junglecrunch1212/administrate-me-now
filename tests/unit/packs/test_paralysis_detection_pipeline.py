"""Handler-direct unit tests for paralysis_detection.

Constructor-injection-loader pattern per PM-27 (mirroring 10c-i's
reward_dispatch shape). Tests cover pre-condition skip paths,
deterministic template selection per [BUILD.md §1297-1302] +
operating rule 20 ([BUILD.md §124]), and defensive skips on missing
profile / persona.

Tenant-identity firewall per [§12.4]: member ids in fixtures are
placeholders ("member-a", "member-b"); no real tenant names.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext

PACK_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packs"
    / "pipelines"
    / "paralysis_detection"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_paralysis_detection_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
ParalysisDetectionPipeline = _handler.ParalysisDetectionPipeline


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


def _make_ctx(
    event_log: Any,
    *,
    triggering_event_id: str = "trigger-eid-001",
    correlation_id: str | None = "corr-001",
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session(
        "pipeline_runner", "device", "tenant-test"
    )

    async def stub_run_skill(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "paralysis_detection must not call run_skill — "
            "deterministic per [BUILD.md §1297-1302] + operating rule 20"
        )

    return PipelineContext(
        session=session,
        event_log=event_log,
        run_skill_fn=stub_run_skill,
        outbound_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=None,
        triggering_event_id=triggering_event_id,
        correlation_id=correlation_id,
    )


_PROFILE_DEFAULT = {
    "fog_window": ["15:00", "17:00"],
}

_ENERGY_LOW = {"level": "low", "as_of_iso": "2026-05-05T15:00:00Z"}
_ENERGY_MEDIUM = {"level": "medium", "as_of_iso": "2026-05-05T15:00:00Z"}

_PERSONA_PACK = {
    "paralysis_templates": [
        {"id": "paralysis-template-1", "text": "One small thing: water glass."},
        {"id": "paralysis-template-2", "text": "Pick your shoes up first."},
        {"id": "paralysis-template-3", "text": "30-second timer; go."},
    ]
}


# ---------------------------------------------------------------------------
# pre-condition skip paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_completion_skips_no_emit() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [
            {"id": "task-1", "completed_at": "2026-05-05T14:30:00Z"}
        ],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log))
    assert log.calls == []


@pytest.mark.asyncio
async def test_energy_not_low_skips_no_emit() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_MEDIUM,
    )
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log))
    assert log.calls == []


@pytest.mark.asyncio
async def test_outside_fog_window_skips_no_emit() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    # Now is 09:00 — well outside 15:00–17:00.
    await pipeline.dispatch("member-a", "2026-05-05T09:00:00Z", _make_ctx(log))
    assert log.calls == []


@pytest.mark.asyncio
async def test_profile_none_skips_no_emit() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: None,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log))
    assert log.calls == []


# ---------------------------------------------------------------------------
# happy path emit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_preconditions_met_emits_paralysis_triggered() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log))
    assert len(log.calls) == 1
    env = log.calls[0].envelope
    assert env.type == "paralysis.triggered"
    assert env.payload["member_id"] == "member-a"
    assert env.payload["template_id"].startswith("paralysis-template-")
    assert env.payload["template_text"]
    assert env.payload["triggered_at"] == "2026-05-05T15:00:00Z"


# ---------------------------------------------------------------------------
# deterministic template selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_member_same_day_picks_same_template_across_calls() -> None:
    """Round-robin seeded by (member_id, today_iso): re-invocation on
    the same day for the same member must pick the same template
    deterministically per [BUILD.md §1297-1302]."""
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    log_a = _FakeEventLog()
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log_a))
    log_b = _FakeEventLog()
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log_b))
    assert log_a.calls[0].envelope.payload["template_id"] == (
        log_b.calls[0].envelope.payload["template_id"]
    )


@pytest.mark.asyncio
async def test_different_members_can_get_different_templates() -> None:
    """Across two members on the same day, the round-robin can yield
    different templates. We assert at least one of the two pairings
    yields a different template — exact pairing is implementation
    detail, but the seed depends on (member_id, today_iso) and the
    pool has 3 templates so the SHA-1 distribution is unlikely to
    collide every time."""
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    seen: set[str] = set()
    for member_id in ["member-a", "member-b", "member-c", "member-d"]:
        log = _FakeEventLog()
        await pipeline.dispatch(member_id, "2026-05-05T15:00:00Z", _make_ctx(log))
        seen.add(log.calls[0].envelope.payload["template_id"])
    # Across 4 members, at least 2 distinct templates ought to appear.
    assert len(seen) >= 2


# ---------------------------------------------------------------------------
# defensive paths on missing persona
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persona_none_skips_no_emit() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: None,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log))
    assert log.calls == []


@pytest.mark.asyncio
async def test_persona_with_empty_paralysis_templates_skips_no_emit() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: {"paralysis_templates": []},
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", _make_ctx(log))
    assert log.calls == []


# ---------------------------------------------------------------------------
# correlation / causation discipline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correlation_id_propagates_to_paralysis_triggered() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    ctx = _make_ctx(log, correlation_id="corr-XYZ")
    await pipeline.dispatch("member-a", "2026-05-05T15:00:00Z", ctx)
    assert log.calls[0].correlation_id == "corr-XYZ"


@pytest.mark.asyncio
async def test_handle_with_valid_payload_routes_through_dispatch() -> None:
    log = _FakeEventLog()
    pipeline = ParalysisDetectionPipeline(
        profile_loader=lambda _mid: _PROFILE_DEFAULT,
        persona_loader=lambda: _PERSONA_PACK,
        tasks_loader=lambda _mid, _now: [],
        commitments_loader=lambda _mid, _now: [],
        energy_loader=lambda _mid: _ENERGY_LOW,
    )
    triggering_event = {
        "event_id": "tick-1",
        "type": "paralysis.tick",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "correlation_id": None,
        "payload": {"member_id": "member-a", "now_iso": "2026-05-05T15:00:00Z"},
    }
    await pipeline.handle(triggering_event, _make_ctx(log))
    assert len(log.calls) == 1
    assert log.calls[0].envelope.type == "paralysis.triggered"
