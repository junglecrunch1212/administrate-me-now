"""Explicit write component selection; the existing read source stays unchanged.

This configuration is an authority ceiling, not approval of an individual write.
Each command still requires current independent domain and OpenClaw decisions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.adapters.cos_apple_reminders import (
    CoSRemindersSelection,
    NativeHelperBinding,
    policy_from_config as read_policy_from_config,
    selected_entries,
)
from adminme.adapters.cos_reminders_write import selected_source_sha256

CONFIG_KEY = "cos_apple_reminders_writes"
RULE_ID = "current-human-request.cos-reminder-create.v1"


class CoSRemindersWritePolicy(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract: Literal["cos-apple-reminders-writes-v1"] = "cos-apple-reminders-writes-v1"
    enabled: bool = False
    native_helper: NativeHelperBinding
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def policy_from_config(value: dict[str, Any]) -> CoSRemindersWritePolicy | None:
    if CONFIG_KEY not in value:
        return None
    policy = CoSRemindersWritePolicy.model_validate(value[CONFIG_KEY])
    read_policy = read_policy_from_config(value)
    entries = selected_entries(value)
    selection = read_policy.selection if read_policy else None
    if (
        selection is None
        or selection.adapter_id not in entries
        or selected_source_sha256(selection) != policy.selection_sha256
        or policy.native_helper.app_path == selection.native_helper.app_path
    ):
        raise ValueError(
            "CoS reminder writes require the unchanged selected read source"
        )
    return policy


def write_selection(value: dict[str, Any]) -> CoSRemindersSelection:
    """Return the exact source with its separate reviewed write component."""
    policy = policy_from_config(value)
    read_policy = read_policy_from_config(value)
    selection = read_policy.selection if read_policy else None
    if policy is None or not policy.enabled or selection is None:
        raise PermissionError("CoS-owned reminder writes are not enabled")
    entry = selected_entries(value)[selection.adapter_id]
    if entry.get("enabled") is not True:
        raise PermissionError("The selected CoS reminder source is paused")
    return selection.model_copy(update={"native_helper": policy.native_helper})


def validate_authorized_change(
    previous: dict[str, Any],
    requested: dict[str, Any],
    *,
    owner_authorization_sha256: str,
) -> None:
    old = policy_from_config(previous)
    new = policy_from_config(requested)
    if new is not None and old != new:
        if new.owner_authorization_sha256 != owner_authorization_sha256:
            raise ValueError(
                "CoS reminder write selection requires exact owner authority"
            )
        if read_policy_from_config(previous) != read_policy_from_config(
            requested
        ) or selected_entries(previous) != selected_entries(requested):
            raise ValueError("Write activation must preserve the working read source")
