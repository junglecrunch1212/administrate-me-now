"""Context Fabric projection registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.context_fabric import handlers


class ContextFabricProjection(Projection):
    name = "context_fabric"
    version = 4
    subscribes_to = "*"
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["ContextFabricProjection"]
