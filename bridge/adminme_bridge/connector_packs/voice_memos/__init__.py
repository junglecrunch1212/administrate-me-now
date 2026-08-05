"""Apple Voice Memos transcript source for the member bridge."""

from __future__ import annotations

from typing import ClassVar

from adminme.events.envelope import Sensitivity
from adminme_bridge.knowledge_sources import LocalKnowledgeSourceAdapter


class VoiceMemosAdapter(LocalKnowledgeSourceAdapter):
    name: ClassVar[str] = "knowledge-apple-voice-memos"
    source_kind: ClassVar[str] = "apple_voice_memos"
    suffixes: ClassVar[tuple[str, ...]] = (".txt", ".md")
    sensitivity_default: ClassVar[Sensitivity] = "sensitive"


__all__ = ["VoiceMemosAdapter"]
