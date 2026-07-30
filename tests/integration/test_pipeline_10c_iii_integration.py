"""Round-trip integration tests for 10c-iii proactive pipelines.

Clones the harness shape from
``tests/integration/test_pipeline_10c_ii_integration.py`` per
[prompt §8 C4]. Three proactive pipelines (`reminder_dispatch`,
`crm_surface`, `custody_brief`) x two observation-mode paths
(observation-OFF → ``external.sent`` lands;
observation-ON → ``observation.suppressed`` lands, emitted event's
``delivered=False``) = 6 round-trip cases.

The in-process PipelineRunner SKIPS proactive packs (no
``triggers.events`` per `runner.py:131-138`); these tests load each
pack directly and call its ``handle()`` against a real EventBus +
EventLog + tmp InstanceConfig to confirm end-to-end emit + observation-
mode integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventSink, EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.lib.observation import ObservationManager
from adminme.lib.session import build_internal_session
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import PipelineContext

REPO_ROOT = Path(__file__).resolve().parents[2]
REMINDER_DISPATCH_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "reminder_dispatch"
CRM_SURFACE_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "crm_surface"
CUSTODY_BRIEF_PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "custody_brief"
TEST_KEY = b"i" * 32


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


def _seed_reminder_dispatch(pack: Any) -> None:
    pack.instance._profile_loader = lambda _mid: {
        "profile_format": "fog_aware",
        "delivery_channel": "imessage",
    }
    pack.instance._commitments_due_loader = lambda _now, _look: [_commitment()]
    pack.instance._recent_reminders_loader = lambda _now, _ded: set()
    pack.instance._member_override_loader = lambda _mid: None


def _seed_crm_surface(pack: Any) -> None:
    pack.instance._profile_loader = lambda _mid: {
        "profile_format": "compressed",
        "delivery_channel": "imessage",
    }
    pack.instance._crm_targets_loader = lambda _week: [
        {
            "member_id": "member-a",
            "surfaced_parties": [
                {"party_id": "party-1", "gap_kind": "stale_contact"},
                {"party_id": "party-2", "gap_kind": "stale_contact"},
            ],
        }
    ]
    pack.instance._member_override_loader = lambda mid: (
        {"crm_proactive_enabled": True} if mid == "member-a" else None
    )


def _seed_custody_brief(pack: Any) -> None:
    pack.instance._profile_loader = lambda _mid: {
        "profile_format": "fog_aware",
        "delivery_channel": "imessage",
    }
    pack.instance._coparent_targets_loader = lambda _brief_date: [
        {
            "member_id": "member-a",
            "coparent_member_id": "member-b",
            "child_member_ids": ["child-1"],
            "next_day_events": [
                {"id": "cal-1", "title": "soccer", "child_member_id": "child-1"}
            ],
            "handoff_timing": {"morning": "08:00 school"},
            "allergen_notes": [],
            "medication_notes": [],
        }
    ]
    pack.instance._member_override_loader = lambda _mid: None


def _trigger_event(eid: str) -> dict[str, Any]:
    return {
        "event_id": eid,
        "event_at_ms": 1_715_000_000_000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "type": "standing_order.tick",
        "payload": {},
    }


async def _build_runtime(
    tmp_path: Path, *, observation_active: bool
) -> tuple[Any, EventLog, EventBus, ObservationManager, PipelineContext, Any]:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    events = EventWriter(log, bus)
    runtime_path = tmp_path / "config" / "runtime.yaml"
    obs_mgr = ObservationManager(event_sink=events, runtime_config_path=runtime_path)
    if not observation_active:
        await obs_mgr.disable(actor="test", reason="test_setup_off")

    session = build_internal_session("pipeline_runner", "device", config.tenant_id)
    return config, log, bus, obs_mgr, session, events


def _make_ctx(
    log: EventLog,
    session: Any,
    obs_mgr: ObservationManager,
    events: EventSink,
    *,
    skill_outputs: dict[str, Any],
    expected_skill_id: str,
    triggering_event_id: str,
    correlation_id: str,
) -> PipelineContext:
    async def fake_run_skill(skill_id: str, inputs: Any, ctx: Any) -> Any:
        assert skill_id == expected_skill_id
        return _FakeSkillResult(output=dict(skill_outputs))

    async def stub_outbound_fn(*a: Any, **kw: Any) -> Any:
        raise AssertionError(
            "10c-iii pipelines route outbound() through the observation seam"
        )

    return PipelineContext(
        session=session,
        event_log=log,
        event_sink=events,
        run_skill_fn=fake_run_skill,
        outbound_fn=stub_outbound_fn,
        guarded_write=None,
        observation_manager=obs_mgr,
        triggering_event_id=triggering_event_id,
        correlation_id=correlation_id,
    )


# --- reminder_dispatch ---


async def test_reminder_dispatch_observation_off_round_trip(
    tmp_path: Path,
) -> None:
    """Obs OFF → outbound() lands external.sent; reminder.dispatched
    payload carries delivered=true, validation_failed=false."""
    _config, log, bus, obs_mgr, session, events = await _build_runtime(
        tmp_path, observation_active=False
    )
    pack = load_pipeline_pack(REMINDER_DISPATCH_PACK_ROOT)
    _seed_reminder_dispatch(pack)
    ctx = _make_ctx(
        log,
        session,
        obs_mgr,
        events,
        skill_outputs={
            "composed_text": "Reminder: call doc at 10:00.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        expected_skill_id="compose_reminder",
        triggering_event_id="trigger-eid-rem-off",
        correlation_id="corr-rem-off",
    )
    triggering = _trigger_event("trigger-eid-rem-off")
    triggering["payload"] = {"now_iso": "2026-05-15T09:30:00+00:00"}

    try:
        await pack.instance.handle(triggering, ctx)

        all_events = await _read_all(log)
        dispatched = [e for e in all_events if e["type"] == "reminder.dispatched"]
        assert len(dispatched) == 1
        payload = dispatched[0]["payload"]
        assert payload["commitment_id"] == "commit-1"
        assert payload["owner_member_id"] == "member-a"
        assert payload["recipient_party_id"] == "party-c"
        assert payload["validation_failed"] is False
        assert payload["delivered"] is True
        assert payload["channel"] == "imessage"

        sent = [e for e in all_events if e["type"] == "external.sent"]
        suppressed = [e for e in all_events if e["type"] == "observation.suppressed"]
        assert len(sent) == 1
        assert suppressed == []
    finally:
        await bus.stop()
        await log.close()


async def test_reminder_dispatch_observation_on_suppresses(
    tmp_path: Path,
) -> None:
    """Obs ON → outbound() suppresses; reminder.dispatched still emits
    with delivered=false; observation.suppressed lands."""
    _config, log, bus, obs_mgr, session, events = await _build_runtime(
        tmp_path, observation_active=True
    )
    pack = load_pipeline_pack(REMINDER_DISPATCH_PACK_ROOT)
    _seed_reminder_dispatch(pack)
    ctx = _make_ctx(
        log,
        session,
        obs_mgr,
        events,
        skill_outputs={
            "composed_text": "Reminder: call doc at 10:00.",
            "suggested_channel": "imessage",
            "validation_failed": False,
            "reasons": [],
        },
        expected_skill_id="compose_reminder",
        triggering_event_id="trigger-eid-rem-on",
        correlation_id="corr-rem-on",
    )
    triggering = _trigger_event("trigger-eid-rem-on")
    triggering["payload"] = {"now_iso": "2026-05-15T09:30:00+00:00"}

    try:
        await pack.instance.handle(triggering, ctx)

        all_events = await _read_all(log)
        dispatched = [e for e in all_events if e["type"] == "reminder.dispatched"]
        assert len(dispatched) == 1
        payload = dispatched[0]["payload"]
        assert payload["validation_failed"] is False
        assert payload["delivered"] is False

        sent = [e for e in all_events if e["type"] == "external.sent"]
        suppressed = [e for e in all_events if e["type"] == "observation.suppressed"]
        assert sent == []
        assert len(suppressed) == 1
    finally:
        await bus.stop()
        await log.close()


# --- crm_surface ---


async def test_crm_surface_observation_off_round_trip(
    tmp_path: Path,
) -> None:
    """Obs OFF → external.sent lands; crm.surfaced payload carries
    delivered=true, gap_kind discriminator and the loader-anchored
    surfaced_party_ids."""
    _config, log, bus, obs_mgr, session, events = await _build_runtime(
        tmp_path, observation_active=False
    )
    pack = load_pipeline_pack(CRM_SURFACE_PACK_ROOT)
    _seed_crm_surface(pack)
    ctx = _make_ctx(
        log,
        session,
        obs_mgr,
        events,
        skill_outputs={
            "composed_text": "Catch up with party-1, party-2.",
            "suggested_channel": "imessage",
            "validation_failed": False,
        },
        expected_skill_id="compose_crm_surface",
        triggering_event_id="trigger-eid-crm-off",
        correlation_id="corr-crm-off",
    )
    triggering = _trigger_event("trigger-eid-crm-off")
    triggering["payload"] = {"week_of_date": "2026-05-10"}

    try:
        await pack.instance.handle(triggering, ctx)

        all_events = await _read_all(log)
        surfaced = [e for e in all_events if e["type"] == "crm.surfaced"]
        assert len(surfaced) == 1
        payload = surfaced[0]["payload"]
        assert payload["member_id"] == "member-a"
        assert payload["surfaced_party_ids"] == ["party-1", "party-2"]
        assert payload["gap_kind"] == "stale_contact"
        assert payload["week_of_date"] == "2026-05-10"
        assert payload["validation_failed"] is False
        assert payload["delivered"] is True

        sent = [e for e in all_events if e["type"] == "external.sent"]
        suppressed = [e for e in all_events if e["type"] == "observation.suppressed"]
        assert len(sent) == 1
        assert suppressed == []
    finally:
        await bus.stop()
        await log.close()


async def test_crm_surface_observation_on_suppresses(
    tmp_path: Path,
) -> None:
    """Obs ON → crm.surfaced emits with delivered=false;
    observation.suppressed lands."""
    _config, log, bus, obs_mgr, session, events = await _build_runtime(
        tmp_path, observation_active=True
    )
    pack = load_pipeline_pack(CRM_SURFACE_PACK_ROOT)
    _seed_crm_surface(pack)
    ctx = _make_ctx(
        log,
        session,
        obs_mgr,
        events,
        skill_outputs={
            "composed_text": "Catch up with party-1, party-2.",
            "suggested_channel": "imessage",
            "validation_failed": False,
        },
        expected_skill_id="compose_crm_surface",
        triggering_event_id="trigger-eid-crm-on",
        correlation_id="corr-crm-on",
    )
    triggering = _trigger_event("trigger-eid-crm-on")
    triggering["payload"] = {"week_of_date": "2026-05-10"}

    try:
        await pack.instance.handle(triggering, ctx)

        all_events = await _read_all(log)
        surfaced = [e for e in all_events if e["type"] == "crm.surfaced"]
        assert len(surfaced) == 1
        payload = surfaced[0]["payload"]
        assert payload["validation_failed"] is False
        assert payload["delivered"] is False

        sent = [e for e in all_events if e["type"] == "external.sent"]
        suppressed = [e for e in all_events if e["type"] == "observation.suppressed"]
        assert sent == []
        assert len(suppressed) == 1
    finally:
        await bus.stop()
        await log.close()


# --- custody_brief ---


async def test_custody_brief_observation_off_round_trip(
    tmp_path: Path,
) -> None:
    """Obs OFF → external.sent lands; custody_brief.composed payload
    carries delivered=true and the loader-anchored coparent + child
    ids."""
    _config, log, bus, obs_mgr, session, events = await _build_runtime(
        tmp_path, observation_active=False
    )
    pack = load_pipeline_pack(CUSTODY_BRIEF_PACK_ROOT)
    _seed_custody_brief(pack)
    ctx = _make_ctx(
        log,
        session,
        obs_mgr,
        events,
        skill_outputs={
            "composed_text": "Tomorrow: soccer 16:00. Handoff 08:00 school.",
            "suggested_channel": "imessage",
            "validation_failed": False,
        },
        expected_skill_id="compose_custody_brief",
        triggering_event_id="trigger-eid-cust-off",
        correlation_id="corr-cust-off",
    )
    triggering = _trigger_event("trigger-eid-cust-off")
    triggering["payload"] = {"brief_date": "2026-05-16"}

    try:
        await pack.instance.handle(triggering, ctx)

        all_events = await _read_all(log)
        composed = [e for e in all_events if e["type"] == "custody_brief.composed"]
        assert len(composed) == 1
        payload = composed[0]["payload"]
        assert payload["member_id"] == "member-a"
        assert payload["coparent_member_id"] == "member-b"
        assert payload["child_member_ids"] == ["child-1"]
        assert payload["brief_date"] == "2026-05-16"
        assert payload["validation_failed"] is False
        assert payload["delivered"] is True

        sent = [e for e in all_events if e["type"] == "external.sent"]
        suppressed = [e for e in all_events if e["type"] == "observation.suppressed"]
        assert len(sent) == 1
        assert suppressed == []
    finally:
        await bus.stop()
        await log.close()


async def test_custody_brief_observation_on_suppresses(
    tmp_path: Path,
) -> None:
    """Obs ON → custody_brief.composed emits with delivered=false;
    observation.suppressed lands."""
    _config, log, bus, obs_mgr, session, events = await _build_runtime(
        tmp_path, observation_active=True
    )
    pack = load_pipeline_pack(CUSTODY_BRIEF_PACK_ROOT)
    _seed_custody_brief(pack)
    ctx = _make_ctx(
        log,
        session,
        obs_mgr,
        events,
        skill_outputs={
            "composed_text": "Tomorrow: soccer 16:00. Handoff 08:00 school.",
            "suggested_channel": "imessage",
            "validation_failed": False,
        },
        expected_skill_id="compose_custody_brief",
        triggering_event_id="trigger-eid-cust-on",
        correlation_id="corr-cust-on",
    )
    triggering = _trigger_event("trigger-eid-cust-on")
    triggering["payload"] = {"brief_date": "2026-05-16"}

    try:
        await pack.instance.handle(triggering, ctx)

        all_events = await _read_all(log)
        composed = [e for e in all_events if e["type"] == "custody_brief.composed"]
        assert len(composed) == 1
        payload = composed[0]["payload"]
        assert payload["validation_failed"] is False
        assert payload["delivered"] is False

        sent = [e for e in all_events if e["type"] == "external.sent"]
        suppressed = [e for e in all_events if e["type"] == "observation.suppressed"]
        assert sent == []
        assert len(suppressed) == 1
    finally:
        await bus.stop()
        await log.close()
