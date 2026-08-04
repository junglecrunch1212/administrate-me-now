"""Unit tests for ``adminme_bridge.adapters.sinks``.

Pins the BridgeEventSink contract + the two impls so 11b-ii-α-2 (and
every future dual-deployment Cat-B adapter) can rely on the emit-pathway
abstraction without re-deriving the transport / typed-writer
unification (F-7).

[§6.19] + memo 04 §2.2: production bridge emit always goes through
``IngestClientBridgeSink`` → central :3330 ingest namespace so
``owner_scope`` is stamped from Tailscale identity. F-8 pins that
``EventLogBridgeSink`` is for tests + central-deployment-fallback ONLY."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme_bridge.adapters.sinks import (
    BridgeEventSink,
    BridgeTransportEventSink,
    EmitResult,
    EventLogBridgeSink,
    IngestClientBridgeSink,
)
from adminme_bridge.ingest_client import SubmitResult


def _envelope(**overrides: Any) -> EventEnvelope:
    """Build a minimal-valid EventEnvelope; overrides win field-by-field."""
    base: dict[str, Any] = {
        "event_at_ms": 1_700_000_000_000,
        "tenant_id": "t1",
        "type": "test.event",
        "schema_version": 1,
        "occurred_at": "2024-01-01T00:00:00Z",
        "source_adapter": "test-adapter",
        "source_account_id": "acct-1",
        "owner_scope": "private:m1",
        "visibility_scope": "private:m1",
        "correlation_id": "cor-1",
        "causation_id": "cau-1",
        "payload": {},
    }
    base.update(overrides)
    return EventEnvelope(**base)


@dataclass
class _RecordingIngestClient:
    """``IngestClient.submit`` stand-in scripted via constructor result."""

    result: SubmitResult
    calls: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    async def submit(self, envelope: dict[str, Any]) -> SubmitResult:
        assert self.calls is not None
        self.calls.append(envelope)
        return self.result


@dataclass
class _RecordingEventSink:
    """``EventLog.append`` stand-in — records kwargs and returns scripted id.

    If ``raise_on_append`` is set, raises it instead — used to pin the
    sink's exception → ``EmitResult(ok=False, ...)`` boundary."""

    event_id: str = "ev_recorded"
    raise_on_append: Exception | None = None
    calls: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    @property
    def tenant_id(self) -> str:
        return "t1"

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        assert self.calls is not None
        self.calls.append(
            {
                "envelope": envelope,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            }
        )
        if self.raise_on_append is not None:
            raise self.raise_on_append
        return self.event_id


def test_emit_result_is_frozen_dataclass() -> None:
    """F-7: ``EmitResult`` is frozen so sinks cannot mutate the result
    record once returned to the adapter — adapter-side handling sees a
    stable shape."""
    result = EmitResult(ok=True, event_id="ev_1", error=None)
    assert result.ok is True
    assert result.event_id == "ev_1"
    assert result.error is None
    try:
        result.ok = False  # type: ignore[misc]
    except FrozenInstanceError:
        return
    raise AssertionError("EmitResult must be a frozen dataclass per F-7")


async def test_ingest_client_bridge_sink_maps_submit_result_to_emit_result_ok_case() -> (
    None
):
    """Production path: ``IngestClientBridgeSink`` wraps ``IngestClient.submit``
    and drops ``status_code`` at the boundary per F-7. ``ok=True`` round-trips
    to ``EmitResult(ok=True, event_id=<id>, error=None)``."""
    client = _RecordingIngestClient(
        result=SubmitResult(ok=True, event_id="ev_abc", status_code=202, error=None)
    )
    sink = IngestClientBridgeSink(client)  # type: ignore[arg-type]
    env = _envelope()
    result = await sink.emit(env)
    assert isinstance(result, EmitResult)
    assert result.ok is True
    assert result.event_id == "ev_abc"
    assert result.error is None
    # The underlying client.submit got the model-dumped envelope, not the
    # Pydantic instance — IngestClient expects a dict per its signature.
    assert client.calls is not None and len(client.calls) == 1
    assert isinstance(client.calls[0], dict)
    assert client.calls[0]["tenant_id"] == "t1"


async def test_ingest_client_bridge_sink_maps_submit_result_to_emit_result_error_case() -> (
    None
):
    """4xx / 5xx / transport failure: ``SubmitResult(ok=False, event_id=None,
    error=...)`` round-trips to ``EmitResult(ok=False, event_id=None,
    error=...)``. The status_code is dropped per F-7."""
    client = _RecordingIngestClient(
        result=SubmitResult(
            ok=False,
            event_id=None,
            status_code=503,
            error="server_error: upstream unavailable",
        )
    )
    sink = IngestClientBridgeSink(client)  # type: ignore[arg-type]
    result = await sink.emit(_envelope())
    assert result.ok is False
    assert result.event_id is None
    assert result.error is not None
    assert "server_error" in result.error


async def test_bridge_event_writer_adapts_transport_to_typed_event_sink() -> None:
    client = _RecordingIngestClient(
        result=SubmitResult(ok=True, event_id="ev_remote", status_code=202, error=None)
    )
    writer = BridgeTransportEventSink(IngestClientBridgeSink(client), tenant_id="t1")

    assert writer.tenant_id == "t1"
    assert await writer.write(_envelope()) == "ev_remote"


async def test_bridge_event_writer_fails_when_transport_does_not_commit() -> None:
    client = _RecordingIngestClient(
        result=SubmitResult(ok=False, event_id=None, status_code=503, error="offline")
    )
    writer = BridgeTransportEventSink(IngestClientBridgeSink(client), tenant_id="t1")

    with pytest.raises(RuntimeError, match="offline"):
        await writer.write(_envelope())


async def test_event_log_bridge_sink_passes_correlation_and_causation_kwargs_per_d8() -> (
    None
):
    """[D8] addition 2: correlation_id / causation_id are passed as explicit
    kwargs (never dict-squatted into the envelope). ``EventLogBridgeSink``
    forwards both from ``envelope.correlation_id`` / ``.causation_id``."""
    event_sink = _RecordingEventSink(event_id="ev_log_42")
    sink = EventLogBridgeSink(event_sink)  # type: ignore[arg-type]
    env = _envelope(correlation_id="cor-xyz", causation_id="cau-pqr")
    result = await sink.emit(env)
    assert result.ok is True
    assert result.event_id == "ev_log_42"
    assert result.error is None
    assert event_sink.calls is not None and len(event_sink.calls) == 1
    call = event_sink.calls[0]
    assert call["envelope"] is env  # EventLog gets the Pydantic instance directly
    assert call["correlation_id"] == "cor-xyz"
    assert call["causation_id"] == "cau-pqr"


async def test_event_log_bridge_sink_surfaces_exception_as_emit_result_error() -> None:
    """F-7 boundary: ``EventSink.write`` raises on failure; ``EventLogBridgeSink``
    catches and unifies the failure into ``EmitResult(ok=False, ...)`` so the
    adapter sees the same shape for both impls."""
    event_sink = _RecordingEventSink(raise_on_append=RuntimeError("db locked"))
    sink = EventLogBridgeSink(event_sink)  # type: ignore[arg-type]
    result = await sink.emit(_envelope())
    assert result.ok is False
    assert result.event_id is None
    assert result.error is not None
    assert "RuntimeError" in result.error
    assert "db locked" in result.error


def test_bridge_event_sink_protocol_runtime_check_both_impls() -> None:
    """``@runtime_checkable`` on ``BridgeEventSink`` means both impls pass
    ``isinstance`` — 11b-ii-α-2 relies on this to type-narrow when the
    sink is injected via ``BridgeContext.sink``."""

    class _NotASink:
        # missing `emit`
        async def submit(self, envelope: dict) -> None: ...

    ingest_sink = IngestClientBridgeSink(
        _RecordingIngestClient(
            result=SubmitResult(ok=True, event_id="x", status_code=202)
        )  # type: ignore[arg-type]
    )
    log_sink = EventLogBridgeSink(_RecordingEventSink())  # type: ignore[arg-type]
    assert isinstance(ingest_sink, BridgeEventSink)
    assert isinstance(log_sink, BridgeEventSink)
    assert not isinstance(_NotASink(), BridgeEventSink)
