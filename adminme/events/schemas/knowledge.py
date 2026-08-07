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
    "markdown",
    "evernote",
    "otter",
    "fathom",
    "zoom",
    "notion",
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


class KnowledgeAttachmentV3(BaseModel):
    """Provider-neutral attachment observation.

    Large content remains in the encrypted Artifact store.  A note event only
    carries stable identifiers, hashes, metadata, and an optional artifact
    relationship; it never turns an attachment into untyped note text.
    """

    model_config = {"extra": "forbid"}
    attachment_id: str = Field(min_length=1, max_length=1000)
    filename: str | None = Field(default=None, max_length=1000)
    mime_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    artifact_id: str | None = Field(default=None, max_length=240)
    source_url: str | None = Field(default=None, max_length=4000)


class MemberKnowledgeNoteV3(BaseModel):
    """Provider-neutral, owner-scoped note snapshot for the Notes surface."""

    model_config = {"extra": "forbid"}
    knowledge_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    title: str | None = Field(default=None, max_length=1000)
    body: str = Field(min_length=1)
    body_format: Literal["plain", "html", "markdown", "transcript"] = "plain"
    source_kind: KnowledgeSourceKind
    source_type: Literal["note", "meeting_note", "provider_summary"] = "note"
    source_external_id: str = Field(min_length=1, max_length=1000)
    source_url: str | None = Field(default=None, max_length=4000)
    account_external_id: str | None = Field(default=None, max_length=1000)
    account_name: str | None = Field(default=None, max_length=1000)
    folder_external_id: str | None = Field(default=None, max_length=1000)
    folder_path: str | None = Field(default=None, max_length=2000)
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    created_at: datetime | None = None
    captured_at: datetime
    provider_modified_at: datetime | None = None
    provider_revision: str | None = Field(default=None, max_length=1000)
    locked: bool = False
    shared: bool = False
    pinned: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=200)
    attachments: list[KnowledgeAttachmentV3] = Field(default_factory=list)
    interaction_id: str | None = Field(default=None, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)
    capability_warnings: list[str] = Field(default_factory=list, max_length=100)


class MemberKnowledgeCorrectionV3(MemberKnowledgeNoteV3):
    supersedes_event_id: str = Field(min_length=1)


class MemberKnowledgeNoteDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    knowledge_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    source_kind: KnowledgeSourceKind
    source_external_id: str = Field(min_length=1, max_length=1000)
    deleted_at: datetime


class MemberKnowledgeFolderChangedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    source_kind: KnowledgeSourceKind
    account_external_id: str | None = None
    folder_external_id: str = Field(min_length=1, max_length=1000)
    folder_path: str = Field(min_length=1, max_length=2000)
    parent_folder_external_id: str | None = None
    changed_at: datetime


KnowledgeGrantPermission = Literal["read", "search"]


class MemberKnowledgeAccessGrantedV1(BaseModel):
    """An explicit owner decision; provider collaboration is evidence only."""

    model_config = {"extra": "forbid"}
    grant_id: str = Field(min_length=1, max_length=240)
    knowledge_id: str = Field(min_length=1, max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    grantee_member_id: str = Field(min_length=1, max_length=240)
    permissions: list[KnowledgeGrantPermission] = Field(min_length=1, max_length=3)
    granted_at: datetime
    expires_at: datetime | None = None


class MemberKnowledgeAccessRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}
    grant_id: str = Field(min_length=1, max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    revoked_by_member_id: str = Field(min_length=1, max_length=240)
    revoked_at: datetime


registry.register("member_knowledge.note_added", 1, MemberKnowledgeNoteV1)
registry.register("member_knowledge.note_updated", 1, MemberKnowledgeNoteV1)
registry.register("member_knowledge.note_corrected", 1, MemberKnowledgeCorrectionV1)
registry.register("member_knowledge.index_skipped", 1, MemberKnowledgeIndexSkippedV1)
registry.register("member_knowledge.note_added", 2, MemberKnowledgeNoteV2)
registry.register("member_knowledge.note_updated", 2, MemberKnowledgeNoteV2)
registry.register("member_knowledge.note_corrected", 2, MemberKnowledgeCorrectionV2)
registry.register("member_knowledge.note_added", 3, MemberKnowledgeNoteV3)
registry.register("member_knowledge.note_updated", 3, MemberKnowledgeNoteV3)
registry.register("member_knowledge.note_corrected", 3, MemberKnowledgeCorrectionV3)
registry.register("member_knowledge.note_deleted", 1, MemberKnowledgeNoteDeletedV1)
registry.register("member_knowledge.folder_created", 1, MemberKnowledgeFolderChangedV1)
registry.register("member_knowledge.folder_updated", 1, MemberKnowledgeFolderChangedV1)
registry.register("member_knowledge.folder_deleted", 1, MemberKnowledgeFolderChangedV1)
registry.register("member_knowledge.access_granted", 1, MemberKnowledgeAccessGrantedV1)
registry.register("member_knowledge.access_revoked", 1, MemberKnowledgeAccessRevokedV1)


__all__ = [
    "KnowledgeGrantPermission",
    "KnowledgeSourceKind",
    "MemberKnowledgeAccessGrantedV1",
    "MemberKnowledgeAccessRevokedV1",
    "MemberKnowledgeCorrectionV1",
    "MemberKnowledgeCorrectionV2",
    "MemberKnowledgeCorrectionV3",
    "MemberKnowledgeFolderChangedV1",
    "MemberKnowledgeIndexSkippedV1",
    "MemberKnowledgeNoteV1",
    "MemberKnowledgeNoteV2",
    "MemberKnowledgeNoteV3",
    "MemberKnowledgeNoteDeletedV1",
]
