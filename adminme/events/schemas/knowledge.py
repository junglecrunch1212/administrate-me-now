"""Typed member-owned knowledge events admitted through Cat-D bridges."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class MemberKnowledgeNoteV1(BaseModel):
    model_config = {"extra": "forbid"}

    knowledge_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=500)
    body: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    source_external_id: str = Field(min_length=1)
    content_sha256: str = Field(min_length=64, max_length=64)
    captured_at: datetime
    supersedes_event_id: str | None = None


for _event_type in (
    "member_knowledge.note_added",
    "member_knowledge.note_updated",
    "member_knowledge.note_corrected",
):
    registry.register(_event_type, 1, MemberKnowledgeNoteV1)


__all__ = ["MemberKnowledgeNoteV1"]
