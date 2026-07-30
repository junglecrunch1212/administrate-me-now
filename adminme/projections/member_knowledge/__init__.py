"""Owner-scoped member knowledge projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.member_knowledge.handlers import EVENT_TYPES, apply_event


class MemberKnowledgeProjection(Projection):
    name = "member_knowledge"
    version = 1
    subscribes_to = sorted(EVENT_TYPES)
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        apply_event(envelope, conn)


__all__ = ["MemberKnowledgeProjection"]
