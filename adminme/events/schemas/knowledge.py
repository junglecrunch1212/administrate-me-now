"""Typed member-owned knowledge events admitted through Cat-D bridges."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


KnowledgeSourceKind = Literal[
    "apple_notes",
    "apple_voice_memos",
    "obsidian",
    "fixture",
]


class MemberKnowledgeNoteV1(BaseModel):
    model_config = {"extra": "forbid"}

    knowledge_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    title: str | None = Field(default=None, max_length=1000)
    body: str = Field(min_length=1)
    source_kind: KnowledgeSourceKind
    source_external_id: str = Field(min_length=1, max_length=1000)
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    captured_at: datetime


class MemberKnowledgeCorrectionV1(MemberKnowledgeNoteV1):
    supersedes_event_id: str = Field(min_length=1)


class MemberKnowledgeIndexSkippedV1(BaseModel):
    model_config = {"extra": "forbid"}

    knowledge_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1)
    reason: Literal["privileged_content"]


registry.register("member_knowledge.note_added", 1, MemberKnowledgeNoteV1)
registry.register("member_knowledge.note_updated", 1, MemberKnowledgeNoteV1)
registry.register("member_knowledge.note_corrected", 1, MemberKnowledgeCorrectionV1)
registry.register("member_knowledge.index_skipped", 1, MemberKnowledgeIndexSkippedV1)


__all__ = [
    "KnowledgeSourceKind",
    "MemberKnowledgeCorrectionV1",
    "MemberKnowledgeIndexSkippedV1",
    "MemberKnowledgeNoteV1",
]
