"""Round-trip integration tests for 10c-iii proactive pipelines.

Same harness pattern as ``tests/integration/test_pipeline_10c_ii_integration.py``:
the in-process PipelineRunner SKIPS proactive packs (no ``triggers.events``
per ``runner.py:131-138``), so these tests load each pack directly and call
its ``dispatch()`` against a real ``EventBus`` + ``EventLog`` + tmp
``InstanceConfig`` + ``ObservationManager`` to confirm end-to-end emit +
observation-mode integration.

Six tests — one observation-on case and one observation-off case per
pipeline:

1. ``reminder_dispatch`` observation-on: ``outbound()`` is suppressed,
   ``observation.suppressed`` lands, ``reminder.dispatched`` still emits
   with ``delivered=False``.
2. ``crm_surface`` observation-on: same shape; ``crm_surface.composed``
   still emits with ``delivered=False``.
3. ``custody_brief`` observation-on: same shape;
   ``custody_brief.composed`` still emits with ``delivered=False``.
4. ``reminder_dispatch`` observation-off: ``outbound()`` fires,
   ``external.sent`` lands, ``reminder.dispatched`` emits with
   ``delivered=True``, no ``observation.suppressed``.
5. ``crm_surface`` observation-off: analogous.
6. ``custody_brief`` observation-off: analogous.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.lib.observation import ObservationManager
from adminme.lib.session import build_internal_session
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import PipelineContext

REPO_ROOT = Path(__file__).resolve().parents[2]
REMINDER_DISPATCH_PACK_ROOT = (
    REPO_ROOT / "packs" / "pipelines" / "reminder_dispatch"
)
CRM_SURFACE_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "crm_surface"
CUSTODY_BRIEF_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "custody_brief"
TEST_KEY = b"i" * 32

# Fixed "now" so reminder_dispatch's lookahead-window math is deterministic.
_FAKE_NOW = 1_750_000_000.0


@dataclass
class _FakeSkillResult:
    output: dict[str, Any]


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    invalidate_cache()
    yield
    invalidate_cache()


async def _read_all(log: EventLog) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for row in log.read_since():
        out.append(row)
    return out


def _commitment_due_in(minutes: float) -> dict[str, Any]:
    return {
        "id": "commit-1",
        "owner_member_id": "member-a",
        "recipient_party_id": "party-1",
        "due_at": _FAKE_NOW + minutes * 60,
        "title": "send paperwork",
    }


def _seed_reminder_pack(pack: Any) -> None:
    pack.instance._commitments_loader = lambda _w: [_commitment_due_in(30)]
    pack.instance._recent_dispatched_loader = lambda _w: []
    pack.instance._member_profile_loader = lambda _mid: {"tone": "warm"}
    pack.instance._party_loader = lambda _pid: {"id": "party-1"}
    pack.instance._now_fn = lambda: _FAKE_NOW


def _seed_crm_pack(pack: Any) -> None:
    pack.instance._members_loader = lambda: [{"id": "member-a"}]
    pack.instance._member_profile_loader = lambda _mid: {
        "tone": "warm",
        "crm_proactive_enabled": True,
    }
    pack.instance._stale_parties_loader = lambda _mid, _d: [
        {"id": "party-1"},
        {"id": "party-2"},
        {"id": "party-3"},
    ]


def _seed_custody_pack(pack: Any) -> None:
    pack.instance._coparent_members_loader = lambda: [{"id": "member-a"}]

    def _profile(mid: str) -> dict[str, Any]:
        if mid == "member-a":
            return {
                "tone": "warm",
                "coparent_relationship_member_id": "member-b",
                "coparent_handoff_pattern": {"hand_off_time": "18:00"},
                "child_health_flags": ["peanut allergy"],
            }
        return {"id": mid, "tone": "neutral"}

    pack.instance._member_profile_loader = _profile
    pack.instance._child_profiles_loader = lambda _mid: [
        {"id": "child-1", "name": "kid-1"},
        {"id": "child-2", "name": "kid-2"},
    ]
    pack.instance._calendar_events_loader = lambda _child, _date: [
        {"id": "cal-1", "title": "soccer practice"},
    ]


async def _build_rig(
    tmp_path: Path, *, observation_off: bool
) -> tuple[EventBus, EventLog, ObservationManager, Any]:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runtime_path = tmp_path / "config" / "runtime.yaml"
    obs_mgr = ObservationManager(
        event_log=log, runtime_config_path=runtime_path
    )
    if observation_off:
        await obs_mgr.disable(actor="test", reason="test_setup_off")
    session = build_internal_session(
        "pipeline_runner", "device", config.tenant_id
    )
    return bus, log, obs_mgr, session


def _make_ctx(
    log: EventLog,
    obs_mgr: ObservationManager,
    session: Any,
    *,
    skill_output: dict[str, Any],
    triggering_event_id: str,
    correlation_id: str,
) -> PipelineContext:
    async def fake_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        return _FakeSkillResult(output=dict(skill_output))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "10c-iii pipelines must route outbound() through the "
            "observation seam, not ctx.outbound_fn"
        )

    return PipelineContext(
        session=session,
        event_log=log,
        run_skill_fn=fake_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=obs_mgr,
        triggering_event_id=triggering_event_id,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# observation-ON path (default per [D22]) — outbound suppressed, event lands
# ---------------------------------------------------------------------------


async def test_reminder_dispatch_round_trip_observation_on(
    tmp_path: Path,
) -> None:
    bus, log, obs_mgr, session = await _build_rig(
        tmp_path, observation_off=False
    )
    pack = load_pipeline_pack(REMINDER_DISPATCH_PACK_ROOT)
    _seed_reminder_pack(pack)
    ctx = _make_ctx(
        log,
        obs_mgr,
        session,
        skill_output={
            "composed_text": "Reminder: send paperwork by 5pm.",
            "suggested_channel": "imessage",
            "tone_note": "",
        },
        triggering_event_id="tick-eid-rem-on",
        correlation_id="corr-rem-on",
    )
    try:
        await pack.instance.dispatch(
            ctx,
            triggering_event={
                "event_id": "tick-eid-rem-on",
                "tenant_id": session.tenant_id,
            },
        )
        events = await _read_all(log)
        dispatched = [e for e in events if e["type"] == "reminder.dispatched"]
        assert len(dispatched) == 1
        assert dispatched[0]["payload"]["delivered"] is False
        suppressed = [
            e for e in events if e["type"] == "observation.suppressed"
        ]
        assert len(suppressed) == 1
        assert not any(e["type"] == "external.sent" for e in events)
    finally:
        await bus.stop()
        await log.close()


async def test_crm_surface_round_trip_observation_on(tmp_path: Path) -> None:
    bus, log, obs_mgr, session = await _build_rig(
        tmp_path, observation_off=False
    )
    pack = load_pipeline_pack(CRM_SURFACE_PACK_ROOT)
    _seed_crm_pack(pack)
    ctx = _make_ctx(
        log,
        obs_mgr,
        session,
        skill_output={
            "composed_text": "You haven't talked to Alex, Sam, Pat in a while.",
            "suggested_channel": "imessage",
            "sort_order": ["party-1", "party-2", "party-3"],
        },
        triggering_event_id="tick-eid-crm-on",
        correlation_id="corr-crm-on",
    )
    try:
        await pack.instance.dispatch(
            ctx,
            triggering_event={
                "event_id": "tick-eid-crm-on",
                "tenant_id": session.tenant_id,
            },
        )
        events = await _read_all(log)
        composed = [e for e in events if e["type"] == "crm_surface.composed"]
        assert len(composed) == 1
        assert composed[0]["payload"]["delivered"] is False
        assert composed[0]["payload"]["surfaced_party_ids"] == [
            "party-1",
            "party-2",
            "party-3",
        ]
        suppressed = [
            e for e in events if e["type"] == "observation.suppressed"
        ]
        assert len(suppressed) == 1
        assert not any(e["type"] == "external.sent" for e in events)
    finally:
        await bus.stop()
        await log.close()


async def test_custody_brief_round_trip_observation_on(
    tmp_path: Path,
) -> None:
    bus, log, obs_mgr, session = await _build_rig(
        tmp_path, observation_off=False
    )
    pack = load_pipeline_pack(CUSTODY_BRIEF_PACK_ROOT)
    _seed_custody_pack(pack)
    ctx = _make_ctx(
        log,
        obs_mgr,
        session,
        skill_output={
            "composed_text": (
                "Tomorrow with the kids: soccer practice; "
                "handoff at 18:00; watch peanut allergy."
            ),
            "suggested_channel": "imessage",
        },
        triggering_event_id="tick-eid-cb-on",
        correlation_id="corr-cb-on",
    )
    try:
        await pack.instance.dispatch(
            ctx,
            triggering_event={
                "event_id": "tick-eid-cb-on",
                "tenant_id": session.tenant_id,
            },
        )
        events = await _read_all(log)
        composed = [
            e for e in events if e["type"] == "custody_brief.composed"
        ]
        assert len(composed) == 1
        assert composed[0]["payload"]["delivered"] is False
        assert composed[0]["payload"]["child_member_ids"] == [
            "child-1",
            "child-2",
        ]
        assert composed[0]["payload"]["coparent_member_id"] == "member-b"
        suppressed = [
            e for e in events if e["type"] == "observation.suppressed"
        ]
        assert len(suppressed) == 1
        assert not any(e["type"] == "external.sent" for e in events)
    finally:
        await bus.stop()
        await log.close()


# ---------------------------------------------------------------------------
# observation-OFF path — outbound fires, external.sent lands
# ---------------------------------------------------------------------------


async def test_reminder_dispatch_round_trip_observation_off_delivers(
    tmp_path: Path,
) -> None:
    bus, log, obs_mgr, session = await _build_rig(
        tmp_path, observation_off=True
    )
    pack = load_pipeline_pack(REMINDER_DISPATCH_PACK_ROOT)
    _seed_reminder_pack(pack)
    ctx = _make_ctx(
        log,
        obs_mgr,
        session,
        skill_output={
            "composed_text": "Reminder: send paperwork by 5pm.",
            "suggested_channel": "imessage",
            "tone_note": "",
        },
        triggering_event_id="tick-eid-rem-off",
        correlation_id="corr-rem-off",
    )
    try:
        await pack.instance.dispatch(
            ctx,
            triggering_event={
                "event_id": "tick-eid-rem-off",
                "tenant_id": session.tenant_id,
            },
        )
        events = await _read_all(log)
        dispatched = [
            e for e in events if e["type"] == "reminder.dispatched"
        ]
        assert len(dispatched) == 1
        assert dispatched[0]["payload"]["delivered"] is True
        sent = [e for e in events if e["type"] == "external.sent"]
        assert len(sent) == 1
        assert not any(
            e["type"] == "observation.suppressed"
            and e["payload"].get("action") == "reminder_send"
            for e in events
        )
    finally:
        await bus.stop()
        await log.close()


async def test_crm_surface_round_trip_observation_off_delivers(
    tmp_path: Path,
) -> None:
    bus, log, obs_mgr, session = await _build_rig(
        tmp_path, observation_off=True
    )
    pack = load_pipeline_pack(CRM_SURFACE_PACK_ROOT)
    _seed_crm_pack(pack)
    ctx = _make_ctx(
        log,
        obs_mgr,
        session,
        skill_output={
            "composed_text": "You haven't talked to Alex, Sam, Pat in a while.",
            "suggested_channel": "imessage",
            "sort_order": ["party-1", "party-2", "party-3"],
        },
        triggering_event_id="tick-eid-crm-off",
        correlation_id="corr-crm-off",
    )
    try:
        await pack.instance.dispatch(
            ctx,
            triggering_event={
                "event_id": "tick-eid-crm-off",
                "tenant_id": session.tenant_id,
            },
        )
        events = await _read_all(log)
        composed = [
            e for e in events if e["type"] == "crm_surface.composed"
        ]
        assert len(composed) == 1
        assert composed[0]["payload"]["delivered"] is True
        sent = [e for e in events if e["type"] == "external.sent"]
        assert len(sent) == 1
        assert not any(
            e["type"] == "observation.suppressed"
            and e["payload"].get("action") == "crm_surface_send"
            for e in events
        )
    finally:
        await bus.stop()
        await log.close()


async def test_custody_brief_round_trip_observation_off_delivers(
    tmp_path: Path,
) -> None:
    bus, log, obs_mgr, session = await _build_rig(
        tmp_path, observation_off=True
    )
    pack = load_pipeline_pack(CUSTODY_BRIEF_PACK_ROOT)
    _seed_custody_pack(pack)
    ctx = _make_ctx(
        log,
        obs_mgr,
        session,
        skill_output={
            "composed_text": (
                "Tomorrow with the kids: soccer practice; "
                "handoff at 18:00; watch peanut allergy."
            ),
            "suggested_channel": "imessage",
        },
        triggering_event_id="tick-eid-cb-off",
        correlation_id="corr-cb-off",
    )
    try:
        await pack.instance.dispatch(
            ctx,
            triggering_event={
                "event_id": "tick-eid-cb-off",
                "tenant_id": session.tenant_id,
            },
        )
        events = await _read_all(log)
        composed = [
            e for e in events if e["type"] == "custody_brief.composed"
        ]
        assert len(composed) == 1
        assert composed[0]["payload"]["delivered"] is True
        assert composed[0]["payload"]["child_member_ids"] == [
            "child-1",
            "child-2",
        ]
        sent = [e for e in events if e["type"] == "external.sent"]
        assert len(sent) == 1
        assert not any(
            e["type"] == "observation.suppressed"
            and e["payload"].get("action") == "custody_brief_send"
            for e in events
        )
    finally:
        await bus.stop()
        await log.close()
