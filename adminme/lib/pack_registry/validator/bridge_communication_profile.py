"""Exact manifest predicates for member-private Messages bridge profiles."""

from __future__ import annotations

from pathlib import Path

import yaml

from adminme.lib.pack_registry.models import AdapterManifest


_MESSAGES_SOURCE_EVENTS = frozenset(
    {
        "messaging.received",
        "messaging.sent",
        "messaging.source_observed",
        "adapter.record.quarantined",
    }
)


def bridge_communication_profile(manifest: AdapterManifest) -> str | None:
    """Return the G10/G11 manifest *shape*, or ``None`` for all others."""

    if len(manifest.capabilities) != 1:
        return None
    capability = manifest.capabilities[0]
    if (
        capability.kind != "Cat-A"
        or capability.runtime != "bridge"
        or not manifest.observation_mode_required
        or manifest.owner_scope_overridability_at_install
        or not {"messaging.received", "messaging.sent"}.issubset(
            capability.event_families
        )
        or not set(capability.event_families) <= _MESSAGES_SOURCE_EVENTS
    ):
        return None
    if not manifest.write_capabilities:
        return "g10-read-only"
    if manifest.write_capabilities == ["reply"]:
        return "g11-governed-reply"
    return None


def sealed_bridge_communication_profile(
    pack_dir: Path, manifest: AdapterManifest
) -> str | None:
    """Recognize only the built-in BlueBubbles pack with the exact shape."""

    try:
        raw = yaml.safe_load((Path(pack_dir) / "pack.yaml").read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    pack = raw.get("pack") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or raw.get("kind") != "adapter"
        or raw.get("subkind") != "communication-source"
        or raw.get("family") != "messaging"
        or raw.get("entrypoint")
        != (
            "adminme_bridge.connector_packs.messaging_bluebubbles:"
            "BlueBubblesMessagesAdapter"
        )
        or not isinstance(pack, dict)
        or pack.get("name") != "messaging-bluebubbles"
    ):
        return None
    return bridge_communication_profile(manifest)


__all__ = ["bridge_communication_profile", "sealed_bridge_communication_profile"]
