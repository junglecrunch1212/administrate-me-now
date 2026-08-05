"""Typed member-owned knowledge events admitted through Cat-D bridges."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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


class AppleNoteAttachmentV2(BaseModel):
    model_config = {"extra": "forbid"}
    attachment_id: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    export_ref: str | None = None


class MemberKnowledgeNoteV2(BaseModel):
    """Rich Apple Notes snapshot without claiming unsupported automation.

    The macOS Notes scripting bridge can reliably round-trip folders, title,
    and HTML body.  The remaining fields preserve provider observations and
    explicitly report capability gaps instead of silently discarding them.
    """

    model_config = {"extra": "forbid"}
    knowledge_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    title: str | None = Field(default=None, max_length=1000)
    body: str = Field(min_length=1)
    body_format: Literal["plain", "html", "markdown"] = "html"
    source_kind: Literal["apple_notes"]
    source_external_id: str = Field(min_length=1, max_length=1000)
    account_external_id: str | None = None
    account_name: str | None = None
    folder_external_id: str | None = None
    folder_path: str | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    created_at: datetime | None = None
    captured_at: datetime
    provider_modified_at: datetime | None = None
    provider_revision: str | None = None
    locked: bool = False
    shared: bool = False
    pinned: bool | None = None
    tags: list[str] | None = None
    attachments: list[AppleNoteAttachmentV2] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    capability_warnings: list[str] = Field(default_factory=list)


class MemberKnowledgeCorrectionV2(MemberKnowledgeNoteV2):
    supersedes_event_id: str = Field(min_length=1)


class MemberKnowledgeNoteDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    knowledge_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    source_kind: Literal["apple_notes"]
    source_external_id: str = Field(min_length=1, max_length=1000)
    deleted_at: datetime


class MemberKnowledgeFolderChangedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    source_kind: Literal["apple_notes"]
    account_external_id: str | None = None
    folder_external_id: str = Field(min_length=1, max_length=1000)
    folder_path: str = Field(min_length=1, max_length=2000)
    parent_folder_external_id: str | None = None
    changed_at: datetime


registry.register("member_knowledge.note_added", 1, MemberKnowledgeNoteV1)
registry.register("member_knowledge.note_updated", 1, MemberKnowledgeNoteV1)
registry.register("member_knowledge.note_corrected", 1, MemberKnowledgeCorrectionV1)
registry.register("member_knowledge.index_skipped", 1, MemberKnowledgeIndexSkippedV1)
registry.register("member_knowledge.note_added", 2, MemberKnowledgeNoteV2)
registry.register("member_knowledge.note_updated", 2, MemberKnowledgeNoteV2)
registry.register("member_knowledge.note_corrected", 2, MemberKnowledgeCorrectionV2)
registry.register("member_knowledge.note_deleted", 1, MemberKnowledgeNoteDeletedV1)
registry.register("member_knowledge.folder_created", 1, MemberKnowledgeFolderChangedV1)
registry.register("member_knowledge.folder_updated", 1, MemberKnowledgeFolderChangedV1)
registry.register("member_knowledge.folder_deleted", 1, MemberKnowledgeFolderChangedV1)


__all__ = [
    "KnowledgeSourceKind",
    "MemberKnowledgeCorrectionV1",
    "MemberKnowledgeIndexSkippedV1",
    "MemberKnowledgeNoteV1",
]
