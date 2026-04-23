"""
Parties projection — CRM spine.

Per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.parties import handlers


class PartiesProjection(Projection):
    name = "parties"
    version = 1
    subscribes_to = [
        "party.created",
        "identifier.added",
        "membership.added",
        "relationship.added",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["PartiesProjection"]
