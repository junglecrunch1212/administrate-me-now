"""Apple Notes export source for the member bridge."""

from __future__ import annotations

from typing import ClassVar

from adminme_bridge.knowledge_sources import LocalKnowledgeSourceAdapter


class AppleNotesAdapter(LocalKnowledgeSourceAdapter):
    name: ClassVar[str] = "knowledge-apple-notes"
    source_kind: ClassVar[str] = "apple_notes"
    suffixes: ClassVar[tuple[str, ...]] = (".md", ".txt", ".html")


__all__ = ["AppleNotesAdapter"]
