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
    "EmitResult",
    "EventLogBridgeSink",
    "IngestClientBridgeSink",
    "KnowledgeAdapter",
    "StateMirrorBridgeAdapter",
]
