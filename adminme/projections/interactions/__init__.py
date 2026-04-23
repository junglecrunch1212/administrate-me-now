"""
Interactions projection — deduplicated touchpoints.

Per ADMINISTRATEME_BUILD.md §3.2 and SYSTEM_INVARIANTS.md §3 invariant 5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.interactions import handlers


class InteractionsProjection(Projection):
    name = "interactions"
    version = 1
    subscribes_to = [
        "messaging.received",
        "messaging.sent",
        "telephony.sms_received",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["InteractionsProjection"]
