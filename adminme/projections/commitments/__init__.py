"""
Commitments projection — the obligation tracker.

Per ADMINISTRATEME_BUILD.md §3.4 and SYSTEM_INVARIANTS.md §4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.commitments import handlers


class CommitmentsProjection(Projection):
    name = "commitments"
    version = 1
    subscribes_to = [
        "commitment.proposed",
        "commitment.confirmed",
        "commitment.completed",
        "commitment.dismissed",
        "commitment.edited",
        "commitment.snoozed",
        "commitment.cancelled",
        "commitment.delegated",
        "commitment.expired",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["CommitmentsProjection"]
