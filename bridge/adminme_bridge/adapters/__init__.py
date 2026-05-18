"""Bridge adapter contracts (11c-i ships Protocol + dataclasses only)."""

from __future__ import annotations

from adminme_bridge.adapters.base import (
    AdapterState,
    AdapterStatus,
    BridgeContext,
    KnowledgeAdapter,
)

__all__ = ["AdapterState", "AdapterStatus", "BridgeContext", "KnowledgeAdapter"]
