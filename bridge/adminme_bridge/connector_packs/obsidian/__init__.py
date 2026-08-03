"""Optional Obsidian vault source for an adult member bridge."""

from __future__ import annotations

from typing import ClassVar

from adminme.events.envelope import Sensitivity
from adminme_bridge.knowledge_sources import LocalKnowledgeSourceAdapter


class ObsidianAdapter(LocalKnowledgeSourceAdapter):
    name: ClassVar[str] = "knowledge-obsidian"
    source_kind: ClassVar[str] = "obsidian"
    suffixes: ClassVar[tuple[str, ...]] = (".md",)
    sensitivity_default: ClassVar[Sensitivity] = "sensitive"


__all__ = ["ObsidianAdapter"]
