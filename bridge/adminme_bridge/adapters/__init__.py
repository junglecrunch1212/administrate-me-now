"""Bridge adapter contracts (11c-i ships Protocol + dataclasses only)."""

from __future__ import annotations

from adminme_bridge.adapters.base import (
    AdapterState,
    AdapterStatus,
    BridgeAdapter,
    BridgeContext,
    KnowledgeAdapter,
    StateMirrorBridgeAdapter,
)
from adminme_bridge.adapters.sinks import (
    BridgeEventSink,
    BridgeTransportEventSink,
    EmitResult,
    EventLogBridgeSink,
    IngestClientBridgeSink,
)

__all__ = [
    "AdapterState",
    "AdapterStatus",
    "BridgeAdapter",
    "BridgeContext",
    "BridgeEventSink",
    "BridgeTransportEventSink",
    "EmitResult",
    "EventLogBridgeSink",
    "IngestClientBridgeSink",
    "KnowledgeAdapter",
    "StateMirrorBridgeAdapter",
]
