"""Triage projection — human adjudication state for derived candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.triage import handlers


class TriageProjection(Projection):
    name = "triage"
    version = 1
    subscribes_to = [
        "triage.item.proposed",
        "triage.item.enriched",
        "triage.item.confirmed",
        "triage.item.dismissed",
        "household.action.proposed",
        "identity.merge_suggested",
        "commitment.proposed",
        "commitment.edited",
        "commitment.confirmed",
        "commitment.dismissed",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["TriageProjection"]
