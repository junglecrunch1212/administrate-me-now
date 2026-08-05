"""BridgeEventSink Protocol + impls — emit-pathway abstraction for L1-bridge adapters.

Per [§6.19] (bridge sovereignty) + memo 04 §2.2 (Tailscale-bound bridge → central
emit identity): production bridge adapters always emit through ``IngestClient``,
which forwards envelopes over HTTPS to the central :3330 ingest namespace. The router
stamps ``owner_scope`` from Tailscale identity. Bridges never write directly to a
local event log on the bridge host.

This module introduces ``BridgeEventSink`` so adapters depend on the emit
*contract* rather than ``IngestClient`` concretely. Two impls land here:

- ``IngestClientBridgeSink`` — wraps ``IngestClient.submit``. Production path.
- ``EventLogBridgeSink`` — wraps the central ``EventWriter``. Tests
  + central-deployment-fallback only (F-8). NOT a substitute for the production
  HTTP path on real bridge hosts; per [§6.19] those always traverse the central
  ingest router so ``owner_scope`` is stamped from Tailscale identity.

F-7: ``EmitResult`` unifies ``SubmitResult`` (HTTP path: ``ok``, ``event_id``,
``status_code``, ``error``) and ``EventWriter.write`` (returns ``event_id``; raises
on failure) into a single shape. HTTP ``status_code`` is dropped at this boundary
as an implementation-internal concern of ``IngestClientBridgeSink``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from adminme.events.envelope import EventEnvelope

from adminme_bridge.ingest_client import IngestClient

if TYPE_CHECKING:
    from adminme.events.writer import EventSink


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


class BridgeTransportEventSink:
    """Adapt bridge transport to the central typed ``EventSink`` contract.

    A dual-deployment adapter can therefore receive a complete, non-null
    ``AdapterContext`` even though its durable commit occurs in the central
    runtime after HTTPS ingestion rather than on the member bridge.
    """

    def __init__(self, sink: BridgeEventSink, *, tenant_id: str) -> None:
        self._sink = sink
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        del correlation_id, causation_id
        result = await self._sink.emit(envelope)
        if not result.ok or result.event_id is None:
            raise RuntimeError(result.error or "bridge ingest returned no event id")
        return result.event_id

    async def write_batch(
        self,
        envelopes: list[EventEnvelope],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[str]:
        return [
            await self.write(
                envelope,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            for envelope in envelopes
        ]


class IngestClientBridgeSink:
    """Production bridge emit path — HTTPS to central :3330 ingest namespace.

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
    to ``EventWriter.write`` as explicit kwargs (never dict-squatted).
    """

    def __init__(self, event_sink: EventSink) -> None:
        self._event_sink = event_sink

    async def emit(self, envelope: EventEnvelope) -> EmitResult:
        try:
            event_id = await self._event_sink.write(
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
    "BridgeTransportEventSink",
    "EmitResult",
    "EventLogBridgeSink",
    "IngestClientBridgeSink",
]
