"""
Unit tests for adminme.lib.observation.

Covers SYSTEM_INVARIANTS.md §6.13-6.16 + CONSOLE_PATTERNS.md §11 +
DIAGRAMS.md §9:

- outbound() suppresses when observation is active; emits
  observation.suppressed with full would-have-sent payload
- outbound() fires action_fn when inactive; emits external.sent
- toggle persistence (round-trip through runtime.yaml)
- default-on at fresh-instance bootstrap (no runtime.yaml present)
- enable/disable emit observation.enabled / observation.disabled events
- prior_state correctness on the toggle events
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adminme.events.log import EventLog
from adminme.lib.observation import (
    ObservationManager,
    ObservationState,
    OutboundResult,
    outbound,
)
from adminme.lib.session import Session, build_internal_session

TEST_KEY = b"o" * 32


def _session(tenant_id: str = "tenant-a") -> Session:
    return build_internal_session(
        actor="member-principal", role="principal", tenant_id=tenant_id
    )


@pytest.fixture
async def log(tmp_path: Path):
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    # Test-only hint so the manager can construct toggle envelopes for a
    # specific tenant. Production code carries tenant via the session
    # passed to outbound(); manager toggle envelopes use this hint.
    log._tenant_id_hint = "tenant-a"  # type: ignore[attr-defined]
    try:
        yield log
    finally:
        await log.close()


@pytest.fixture
def runtime_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "runtime.yaml"


@pytest.fixture
def manager(log: EventLog, runtime_path: Path) -> ObservationManager:
    return ObservationManager(event_log=log, runtime_config_path=runtime_path)


async def _read_events_of_type(log: EventLog, type_: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for ev in log.read_since(types=[type_]):
        out.append(ev)
    return out


# ---------------------------------------------------------------------------
# Default-on for new instances [§6.16]
# ---------------------------------------------------------------------------


async def test_default_on_when_runtime_yaml_absent(
    manager: ObservationManager, runtime_path: Path
) -> None:
    assert not runtime_path.exists()
    state = await manager.get_state()
    assert state.active is True
    assert state.enabled_at is None
    assert state.enabled_by is None
    assert await manager.is_active() is True


async def test_default_on_when_runtime_yaml_corrupt(
    manager: ObservationManager, runtime_path: Path
) -> None:
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text("this is: not: yaml: { broken")
    # Should fail closed (default-on) rather than open.
    assert await manager.is_active() is True


# ---------------------------------------------------------------------------
# Toggle persistence
# ---------------------------------------------------------------------------


async def test_disable_persists_to_runtime_yaml(
    manager: ObservationManager, runtime_path: Path
) -> None:
    await manager.disable(actor="member-principal", reason="ready_for_live")
    assert runtime_path.exists()
    state = await manager.get_state()
    assert state.active is False


async def test_enable_persists_to_runtime_yaml(
    manager: ObservationManager,
) -> None:
    await manager.disable(actor="member-principal", reason="ready_for_live")
    assert await manager.is_active() is False
    await manager.enable(actor="member-principal", reason="re_observing")
    assert await manager.is_active() is True


async def test_toggle_round_trip_via_fresh_manager(
    log: EventLog, runtime_path: Path
) -> None:
    """A fresh manager pointing at the same runtime.yaml sees the persisted
    state — matters for console restart."""
    m1 = ObservationManager(event_log=log, runtime_config_path=runtime_path)
    await m1.disable(actor="member-principal", reason="ready_for_live")
    m2 = ObservationManager(event_log=log, runtime_config_path=runtime_path)
    assert await m2.is_active() is False


# ---------------------------------------------------------------------------
# Toggle audit events
# ---------------------------------------------------------------------------


async def test_enable_emits_observation_enabled_event(
    manager: ObservationManager, log: EventLog
) -> None:
    # First disable so we have a non-default prior state.
    await manager.disable(actor="member-principal", reason="ready")
    await manager.enable(actor="member-principal", reason="something_off")
    enabled = await _read_events_of_type(log, "observation.enabled")
    assert len(enabled) == 1
    p = enabled[0]["payload"]
    assert p["actor"] == "member-principal"
    assert p["reason"] == "something_off"
    assert p["prior_state"] is False
    assert "enabled_at" in p


async def test_disable_emits_observation_disabled_event(
    manager: ObservationManager, log: EventLog
) -> None:
    await manager.disable(actor="member-principal", reason="ready_for_live")
    disabled = await _read_events_of_type(log, "observation.disabled")
    assert len(disabled) == 1
    p = disabled[0]["payload"]
    assert p["actor"] == "member-principal"
    assert p["reason"] == "ready_for_live"
    # prior_state == True because the default was active.
    assert p["prior_state"] is True


# ---------------------------------------------------------------------------
# outbound() — suppress path
# ---------------------------------------------------------------------------


async def test_outbound_suppresses_when_active(
    manager: ObservationManager, log: EventLog
) -> None:
    s = _session()
    fired = []

    async def action_fn() -> str:
        fired.append("fired")
        return "external_id_123"

    payload = {"body": "hi", "channel_specific": {"k": "v"}}
    result = await outbound(
        s,
        action="message.send",
        payload=payload,
        action_fn=action_fn,
        manager=manager,
        event_log=log,
        target_channel="imessage",
        target_identifier="+15555550100",
    )
    assert result.suppressed is True
    assert result.result is None
    # action_fn must NOT have been called per CONSOLE_PATTERNS.md §11.
    assert fired == []
    suppressed_events = await _read_events_of_type(log, "observation.suppressed")
    assert len(suppressed_events) == 1
    p = suppressed_events[0]["payload"]
    assert p["attempted_action"] == "message.send"
    assert p["target_channel"] == "imessage"
    assert p["target_identifier"] == "+15555550100"
    # Full would-have-sent payload preserved for the review pane.
    assert p["would_have_sent_payload"] == payload
    assert p["reason"] == "observation_mode_active"
    assert p["observation_mode_active"] is True


async def test_outbound_does_not_emit_external_sent_when_active(
    manager: ObservationManager, log: EventLog
) -> None:
    s = _session()

    async def action_fn() -> str:
        return "shouldnt_fire"

    await outbound(
        s,
        action="message.send",
        payload={},
        action_fn=action_fn,
        manager=manager,
        event_log=log,
    )
    sent = await _read_events_of_type(log, "external.sent")
    assert sent == []


# ---------------------------------------------------------------------------
# outbound() — pass-through path (observation off)
# ---------------------------------------------------------------------------


async def test_outbound_calls_action_fn_when_inactive(
    manager: ObservationManager, log: EventLog
) -> None:
    s = _session()
    await manager.disable(actor="bootstrap_op", reason="ready_for_live")

    fired = []

    async def action_fn() -> str:
        fired.append("fired")
        return "external_id_xyz"

    payload = {"body": "live message"}
    result = await outbound(
        s,
        action="message.send",
        payload=payload,
        action_fn=action_fn,
        manager=manager,
        event_log=log,
        target_channel="imessage",
        target_identifier="+15555550100",
    )
    assert result.suppressed is False
    assert result.result == "external_id_xyz"
    assert fired == ["fired"]
    sent = await _read_events_of_type(log, "external.sent")
    assert len(sent) == 1
    p = sent[0]["payload"]
    assert p["action"] == "message.send"
    assert p["target_channel"] == "imessage"
    assert p["target_identifier"] == "+15555550100"
    assert p["payload"] == payload
    # No suppressed event on the live path.
    suppressed = await _read_events_of_type(log, "observation.suppressed")
    assert suppressed == []


async def test_outbound_propagates_action_fn_exceptions(
    manager: ObservationManager, log: EventLog
) -> None:
    s = _session()
    await manager.disable(actor="bootstrap_op", reason="ready_for_live")

    async def action_fn() -> Any:
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        await outbound(
            s,
            action="message.send",
            payload={},
            action_fn=action_fn,
            manager=manager,
            event_log=log,
        )
    # external.sent must NOT be emitted on the failure path.
    sent = await _read_events_of_type(log, "external.sent")
    assert sent == []


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------


def test_observation_state_is_frozen() -> None:
    s = ObservationState(active=True, enabled_at=None, enabled_by=None)
    with pytest.raises(Exception):
        s.active = False  # type: ignore[misc]


def test_outbound_result_is_frozen() -> None:
    r = OutboundResult(suppressed=True)
    with pytest.raises(Exception):
        r.suppressed = False  # type: ignore[misc]
