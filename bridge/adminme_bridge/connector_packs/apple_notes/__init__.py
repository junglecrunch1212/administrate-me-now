"""Apple Notes export source for the member bridge."""

from __future__ import annotations

from typing import Any, ClassVar

from adminme_bridge.knowledge_sources import LocalKnowledgeSourceAdapter
from adminme_bridge.connector_packs.apple_notes.provider import AppleNotesScriptingProvider


class AppleNotesAdapter(LocalKnowledgeSourceAdapter):
    name: ClassVar[str] = "knowledge-apple-notes"
    source_kind: ClassVar[str] = "apple_notes"
    suffixes: ClassVar[tuple[str, ...]] = (".md", ".txt", ".html")
    version: ClassVar[str] = "2.0.0"
    provider: ClassVar[str] = "apple_notes"
    write_capabilities: ClassVar[list[str]] = [
        "create_note", "update_note", "append_note", "move_note", "delete_note",
        "create_folder", "update_folder", "delete_folder",
    ]

    def __init__(self, *, config: dict[str, Any] | None = None, provider: Any = None, cursor_store: Any = None) -> None:
        resolved = dict(config or {})
        if provider is None and not resolved.get("root") and resolved.get("automation_mode", "notes_scripting") == "notes_scripting":
            provider = AppleNotesScriptingProvider()
        super().__init__(config=resolved, provider=provider, cursor_store=cursor_store)

    async def apply_write(
        self,
        operation: str,
        payload: dict[str, Any],
        target_external_id: str | None = None,
    ) -> dict[str, Any]:
        if operation not in self.write_capabilities:
            raise ValueError(f"unsupported Apple Notes operation: {operation}")
        provider = self._provider
        if provider is None or not hasattr(provider, "apply_write"):
            raise RuntimeError("Apple Notes scripting provider is not active")
        return await provider.apply_write(operation, payload, target_external_id)


Adapter = AppleNotesAdapter

__all__ = ["Adapter", "AppleNotesAdapter", "AppleNotesScriptingProvider"]
