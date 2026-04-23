"""
Artifacts projection — documents, images, structured records.

Per ADMINISTRATEME_BUILD.md §3.3 and SYSTEM_INVARIANTS.md §3 invariant 6.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.artifacts import handlers
from adminme.projections.base import Projection


class ArtifactsProjection(Projection):
    name = "artifacts"
    version = 1
    subscribes_to = ["artifact.received"]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["ArtifactsProjection"]
