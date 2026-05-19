"""Bridge adapter contracts (11c-i ships Protocol + dataclasses only)."""

from __future__ import annotations

from adminme_bridge.adapters.base import (
    AdapterState,
    AdapterStatus,
    BridgeContext,
    KnowledgeAdapter,
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
    "BridgeContext",
    "BridgeEventSink",
    "EmitResult",
    "EventLogBridgeSink",
    "IngestClientBridgeSink",
    "KnowledgeAdapter",
]
