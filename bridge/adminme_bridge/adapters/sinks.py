"""BridgeEventSink Protocol + impls — emit-pathway abstraction for L1-bridge adapters.

Per [§6.19] (bridge sovereignty) + memo 04 §2.2 (Tailscale-bound bridge → central
emit identity): production bridge adapters always emit through ``IngestClient``,
which forwards envelopes over HTTPS to the central :3337 ingest router. The router
stamps ``owner_scope`` from Tailscale identity. Bridges never write directly to a
local event log on the bridge host.

This module introduces ``BridgeEventSink`` so adapters depend on the emit
*contract* rather than ``IngestClient`` concretely. Two impls land here:

- ``IngestClientBridgeSink`` — wraps ``IngestClient.submit``. Production path.
- ``EventLogBridgeSink`` — wraps the central ``EventLog.append`` directly. Tests
  + central-deployment-fallback only (F-8). NOT a substitute for the production
  HTTP path on real bridge hosts; per [§6.19] those always traverse the central
  ingest router so ``owner_scope`` is stamped from Tailscale identity.

F-7: ``EmitResult`` unifies ``SubmitResult`` (HTTP path: ``ok``, ``event_id``,
``status_code``, ``error``) and ``EventLog.append`` (returns ``event_id``; raises
on failure) into a single shape. HTTP ``status_code`` is dropped at this boundary
as an implementation-internal concern of ``IngestClientBridgeSink``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from adminme.events.envelope import EventEnvelope

from adminme_bridge.ingest_client import IngestClient

if TYPE_CHECKING:
    from adminme.events.log import EventLog


@dataclass(frozen=True)
class EmitResult:
    """Unified result of a ``BridgeEventSink.emit`` call (F-7)."""

    ok: bool
    event_id: str | None
    error: str | None


@runtime_checkable
class BridgeEventSink(Protocol):
    """Structural contract for the bridge's event-emit pathway.

    Adapters depend on this Protocol; concrete sink choice (``IngestClientBridgeSink``
    in production, ``EventLogBridgeSink`` in tests / central-deployment fallback)
    is injected via ``BridgeContext.sink``.
    """

    async def emit(self, envelope: EventEnvelope) -> EmitResult: ...


class IngestClientBridgeSink:
    """Production bridge emit path — HTTPS to central :3337 ingest router.

    Per [§6.19] / memo 04 §2.2: bridge adapters always emit through this sink in
    production deployments. The underlying ``IngestClient.submit`` retries 5xx with
    exponential backoff and respects 429 ``Retry-After``; see ``ingest_client.py``.
    """

    def __init__(self, client: IngestClient) -> None:
        self._client = client

    async def emit(self, envelope: EventEnvelope) -> EmitResult:
        result = await self._client.submit(envelope.model_dump())
        event_id = result.event_id if isinstance(result.event_id, str) else None
        return EmitResult(ok=result.ok, event_id=event_id, error=result.error)


class EventLogBridgeSink:
    """Local-EventLog emit path — tests + central-deployment-fallback ONLY.

    F-8: NOT a substitute for the production HTTP path on real bridge hosts. Per
    [§6.19] / memo 04 §2.2, production bridges always traverse the central ingest
    router so ``owner_scope`` is stamped from Tailscale identity.

    Per [D8] addition 2: passes ``envelope.correlation_id`` / ``envelope.causation_id``
    to ``EventLog.append`` as explicit kwargs (never dict-squatted).
    """

    def __init__(self, event_log: EventLog) -> None:
        self._event_log = event_log

    async def emit(self, envelope: EventEnvelope) -> EmitResult:
        try:
            event_id = await self._event_log.append(
                envelope,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
            )
        except Exception as exc:  # noqa: BLE001 — sink boundary unifies error reporting
            return EmitResult(
                ok=False, event_id=None, error=f"{type(exc).__name__}: {exc}"
            )
        return EmitResult(ok=True, event_id=event_id, error=None)


__all__ = [
    "BridgeEventSink",
    "EmitResult",
    "EventLogBridgeSink",
    "IngestClientBridgeSink",
]
