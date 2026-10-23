"""Native hook capability derived only from the journaled standing grants."""

from typing import Any


def configure_reply_hook(plugin: dict[str, Any], bindings: list[str]) -> None:
    """Keep other hook policy intact and revoke access with the last grant."""
    plugin.setdefault("hooks", {})["allowConversationAccess"] = bool(bindings)


def require_reply_hook(plugin: dict[str, Any], bindings: list[str]) -> None:
    """A configured grant is unusable when the pinned host blocks agent_end."""
    if bindings and plugin.get("hooks", {}).get("allowConversationAccess") is not True:
        raise ValueError("CoS standing reply completion hook is not enabled")
