"""
Observation mode — the final-outbound-filter wrapper.

Per ADMINISTRATEME_BUILD.md §OBSERVATION (lines 2147-2160),
SYSTEM_INVARIANTS.md §6.13-6.16, CONSOLE_PATTERNS.md §11 (canonical
algorithm), DIAGRAMS.md §9 (fire/suppress diagram), and DECISIONS.md §D5
(the xlsx forward projector exception).

Observation mode is enforced at the FINAL OUTBOUND FILTER — not at the
policy layer and not at the action-decision layer [§6.13]. All internal
logic (pipelines, skill calls, projection updates, console UI, reward
previews) runs normally; only the external side effect is suppressed.

Every outbound-capable action calls ``outbound(session, action, payload,
action_fn)``. Emitting ``external.sent`` anywhere else is a bug [§6.14].

Observation mode is **per-tenant** [§6.15] and **default-on for new
instances** [§6.16]; the bootstrap wizard ends with observation enabled
and the principal opts out explicitly only after reviewing the
suppressed-action log.

State is persisted to ``<config_dir>/runtime.yaml`` per ADMINISTRATEME_BUILD.md
line 2149. Reads of state are async-safe (the underlying file open + parse
is dispatched through ``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import yaml

from adminme.events.envelope import EventEnvelope

if TYPE_CHECKING:
    from adminme.events.log import EventLog
    from adminme.lib.session import Session

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State + persistence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationState:
    """Persisted observation toggle state. ``active=True`` means external
    side effects are suppressed [§6.16]."""

    active: bool
    enabled_at: str | None
    enabled_by: str | None


_DEFAULT_STATE = ObservationState(active=True, enabled_at=None, enabled_by=None)


def _read_state_sync(path: Path) -> ObservationState:
    """Sync helper for ``ObservationManager.is_active``. Default-on per
    [§6.16] — if the file does not exist, observation is ACTIVE."""
    if not path.exists():
        return _DEFAULT_STATE
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        # A corrupt runtime.yaml falls back to default-on; failing closed
        # would suppress every send and is the safer choice for a security
        # invariant.
        _log.warning("observation: runtime.yaml unreadable; defaulting to ON")
        return _DEFAULT_STATE
    obs = data.get("observation_mode_override")
    if obs is None:
        return _DEFAULT_STATE
    return ObservationState(
        active=bool(obs.get("active", True)),
        enabled_at=obs.get("enabled_at"),
        enabled_by=obs.get("enabled_by"),
    )


def _write_state_sync(path: Path, state: ObservationState) -> None:
    """Sync helper for ``enable``/``disable``. Atomic via tmp + replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "observation_mode_override": {
            "active": state.active,
            "enabled_at": state.enabled_at,
            "enabled_by": state.enabled_by,
        }
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w") as f:
        yaml.safe_dump(payload, f, sort_keys=True)
    tmp_path.replace(path)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ObservationManager:
    """Owns the observation toggle state. Persists to a runtime.yaml under
    the instance config directory; emits ``observation.enabled`` /
    ``observation.disabled`` on each toggle so the audit log records the
    actor and reason [§6.16, §13]."""

    def __init__(
        self,
        event_log: "EventLog",
        runtime_config_path: Path,
    ) -> None:
        self._log = event_log
        self._path = runtime_config_path
        # Lock guards the read-modify-write of toggle operations; reads do
        # not need the lock because file reads are atomic at the size we
        # write (one yaml block).
        self._toggle_lock = asyncio.Lock()

    async def get_state(self) -> ObservationState:
        return await asyncio.to_thread(_read_state_sync, self._path)

    async def is_active(self) -> bool:
        state = await self.get_state()
        return state.active

    async def enable(self, actor: str, reason: str) -> None:
        async with self._toggle_lock:
            prior = await self.get_state()
            now = EventEnvelope.now_utc_iso()
            new_state = ObservationState(
                active=True,
                enabled_at=now,
                enabled_by=actor,
            )
            await asyncio.to_thread(_write_state_sync, self._path, new_state)
        await self._emit_toggle(
            event_type="observation.enabled",
            actor=actor,
            reason=reason,
            prior_active=prior.active,
            timestamp_field="enabled_at",
            timestamp_value=now,
        )

    async def disable(self, actor: str, reason: str) -> None:
        async with self._toggle_lock:
            prior = await self.get_state()
            now = EventEnvelope.now_utc_iso()
            new_state = ObservationState(
                active=False,
                enabled_at=now,
                enabled_by=actor,
            )
            await asyncio.to_thread(_write_state_sync, self._path, new_state)
        await self._emit_toggle(
            event_type="observation.disabled",
            actor=actor,
            reason=reason,
            prior_active=prior.active,
            timestamp_field="disabled_at",
            timestamp_value=now,
        )

    async def _emit_toggle(
        self,
        *,
        event_type: str,
        actor: str,
        reason: str,
        prior_active: bool,
        timestamp_field: str,
        timestamp_value: str,
    ) -> None:
        # tenant_id is implicit on the event_log; we resolve it via the
        # log's bound config in production, but per the test rig the
        # caller passes the tenant_id at envelope time. ObservationManager
        # does not own a Session — the actor/reason argument carries
        # provenance per [§13].
        # Resolve tenant via the log's configured connection: we keep the
        # contract simple and require the toggle caller (CLI / wizard) to
        # provide a tenant-bound EventLog.
        tenant_id = getattr(self._log, "_tenant_id_hint", None) or "tenant-default"
        envelope = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=tenant_id,
            type=event_type,
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="observation",
            source_account_id="system",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            sensitivity="normal",
            actor_identity=actor,
            payload={
                "actor": actor,
                "reason": reason,
                "prior_state": prior_active,
                timestamp_field: timestamp_value,
            },
        )
        await self._log.append(envelope)


# ---------------------------------------------------------------------------
# outbound() — the single enforcement point per [§6.14]
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutboundResult:
    """Result of an outbound() call. ``suppressed=True`` means observation
    mode short-circuited the action; ``result`` carries whatever
    ``action_fn`` returned (None when suppressed)."""

    suppressed: bool
    result: Any = None


async def outbound(
    session: "Session",
    action: str,
    payload: dict[str, Any],
    action_fn: Callable[[], Awaitable[Any]],
    *,
    manager: ObservationManager,
    event_log: "EventLog",
    target_channel: str = "unknown",
    target_identifier: str = "unknown",
) -> OutboundResult:
    """Wrap an outbound-capable action. The single enforcement point per
    [§6.14] — every L1/L4/L5 path that fires a side effect routes through
    here.

    If observation mode is active:
      - emits ``observation.suppressed`` with the full would-have-sent
        payload (target_channel + target_identifier carried separately
        per the schema)
      - does NOT call ``action_fn``
      - returns ``OutboundResult(suppressed=True)``

    If observation mode is inactive:
      - calls ``action_fn`` (awaited)
      - emits ``external.sent`` with the same payload shape on success
      - returns ``OutboundResult(suppressed=False, result=action_fn_return)``

    Failure-path behavior: if ``action_fn`` raises, the exception
    propagates and NO ``external.sent`` event is emitted (the action did
    not, in fact, send). The caller is responsible for emitting a domain
    failure event of its own choosing if needed.
    """
    is_active = await manager.is_active()
    now = EventEnvelope.now_utc_iso()
    if is_active:
        suppressed = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="observation.suppressed",
            schema_version=1,
            occurred_at=now,
            source_adapter="observation",
            source_account_id="system",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            sensitivity="normal",
            actor_identity=session.auth_member_id,
            payload={
                "attempted_action": action,
                "attempted_at": now,
                "target_channel": target_channel,
                "target_identifier": target_identifier,
                "would_have_sent_payload": payload,
                "reason": "observation_mode_active",
                "session_correlation_id": session.correlation_id,
                "observation_mode_active": True,
            },
        )
        await event_log.append(suppressed, correlation_id=session.correlation_id)
        return OutboundResult(suppressed=True)

    result = await action_fn()
    sent = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type="external.sent",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="observation",
        source_account_id="system",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        actor_identity=session.auth_member_id,
        payload={
            "action": action,
            "sent_at": EventEnvelope.now_utc_iso(),
            "target_channel": target_channel,
            "target_identifier": target_identifier,
            "payload": payload,
            "session_correlation_id": session.correlation_id,
        },
    )
    await event_log.append(sent, correlation_id=session.correlation_id)
    return OutboundResult(suppressed=False, result=result)


__all__ = [
    "ObservationManager",
    "ObservationState",
    "OutboundResult",
    "outbound",
]
