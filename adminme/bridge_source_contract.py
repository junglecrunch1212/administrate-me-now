"""Pure content-free coordinate contract for scoped member source admission."""

from __future__ import annotations

from typing import Any


def validate_member_source_coordinates(pack: str, config: dict[str, Any]) -> None:
    """The scoped receipt retains coordinates only, never arbitrary pack data."""
    if pack == "knowledge-apple-notes":
        keys = {"member_id", "automation_mode", "emit_schema_version"}
        if (
            config.get("automation_mode") != "notes_scripting"
            or config.get("emit_schema_version") != 2
        ):
            raise ValueError("member_bridge_notes_configuration_invalid")
    elif pack in {
        "calendaring-apple-calendar-bridge",
        "reminders-apple-eventkit-bridge",
    }:
        keys = {
            "member_id",
            "provider_account_id",
            "deployment_target",
            "workspace",
            "permission_scope",
            "binding_provenance",
            "ownership_attested",
            "allowed_container_ids",
            "shared_container_ids",
            "calendar_identifier"
            if pack == "calendaring-apple-calendar-bridge"
            else "target_list_identifier",
        }
    else:
        raise ValueError("member_bridge_source_not_selected")
    if set(config) != keys or config.get("shared_container_ids", []) != []:
        raise ValueError("member_bridge_content_free_private_coordinates_required")
