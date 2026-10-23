"""Reviewed device/source ownership for one CoS Reminders acquisition."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

CONFIG_KEY = "cos_apple_reminders"
ADAPTER_KIND = "reminders-cos-apple-eventkit"
READ_SCOPE = "eventkit.reminders.read"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def local_device_identity() -> tuple[str, int]:
    """Bind the hardware and Unix user without persisting their raw coordinates."""
    if sys.platform != "darwin":
        raise RuntimeError("CoS Reminders requires its reviewed macOS work computer")
    result = subprocess.run(
        ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice", "-a"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    records = plistlib.loads(result.stdout)
    hardware = (
        records[0].get("IOPlatformUUID")
        if isinstance(records, list) and len(records) == 1
        else None
    )
    if not isinstance(hardware, str) or not hardware:
        raise RuntimeError("CoS Reminders device identity is unavailable")
    uid = os.getuid()
    return digest(["cos-eventkit-device-v1", hardware, uid]), uid


class CoSRemindersLookup(BaseModel):
    """Setup-only metadata request; a match is not source ownership authority."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    list_name: str = Field(min_length=1, max_length=1000)
    source_kind: Literal["local", "icloud"]
    source_id: str | None = Field(default=None, min_length=1, max_length=1000)


class NativeHelperBinding(BaseModel):
    """An immutable, separately installed permission-bearing native app."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}
    app_path: str = Field(min_length=1, max_length=4096)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_identifier: Literal["com.administrateme.cos-reminders-helper"]

    @field_validator("app_path")
    @classmethod
    def exact_app_path(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute() or str(path) != value or path.suffix != ".app":
            raise ValueError("native helper requires an exact absolute app path")
        return value


class CoSRemindersSelection(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    native_helper: NativeHelperBinding
    adapter_id: str = Field(min_length=1, max_length=240)
    source_id: str = Field(min_length=1, max_length=1000)
    list_id: str = Field(min_length=1, max_length=1000)
    source_kind: Literal["local", "icloud"]
    device_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    os_uid: int = Field(ge=0)
    source_ownership_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("adapter_id", "source_id", "list_id")
    @classmethod
    def exact_identifier(cls, value: str) -> str:
        if (
            value != value.strip()
            or value.casefold() in {"default", "primary", "@default", "*"}
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("CoS Reminders requires exact native identifiers")
        return value

    @property
    def provider_account_id(self) -> str:
        return "provider-account:" + digest(
            [self.device_identity_sha256, self.os_uid, self.source_kind, self.source_id]
        )

    def adapter_config(self) -> dict[str, Any]:
        return {
            "selection": self.model_dump(mode="json"),
            "provider_account_id": self.provider_account_id,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "workspace": "service",
            "binding_provenance": "service-device-authorization",
            "ownership_attested": True,
            "permission_scope": [READ_SCOPE],
            "read_only": True,
        }


class CoSRemindersPolicy(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract: Literal["cos-apple-reminders-v1"] = "cos-apple-reminders-v1"
    selection: CoSRemindersSelection | None = None


def policy_from_config(value: dict[str, Any]) -> CoSRemindersPolicy | None:
    if CONFIG_KEY not in value:
        return None
    if "cos_channel_acquisition" not in value:
        raise ValueError("CoS Reminders requires the CoS acquisition policy")
    return CoSRemindersPolicy.model_validate(value[CONFIG_KEY])


def selected_entries(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policy = policy_from_config(value)
    if policy is None or policy.selection is None:
        return {}
    selection = policy.selection
    rows = [
        row
        for row in value.get("adapters", [])
        if isinstance(row, dict) and row.get("id") == selection.adapter_id
    ]
    if (
        len(rows) != 1
        or rows[0].get("kind") != ADAPTER_KIND
        or rows[0].get("config") != selection.adapter_config()
    ):
        raise ValueError("CoS Reminders source binding mismatch")
    return {selection.adapter_id: rows[0]}


def validate_adapter_config(config: dict[str, Any]) -> CoSRemindersSelection:
    selection = CoSRemindersSelection.model_validate(config.get("selection"))
    if config != selection.adapter_config():
        raise ValueError(
            "CoS Reminders requires exactly its reviewed read-only service binding"
        )
    return selection


def verify_device(selection: CoSRemindersSelection, identity: tuple[str, int]) -> None:
    if identity != (selection.device_identity_sha256, selection.os_uid):
        raise PermissionError("CoS Reminders work computer or OS account differs")


def validate_authorized_change(
    previous: dict[str, Any],
    requested: dict[str, Any],
    *,
    owner_authorization_sha256: str,
) -> None:
    old = policy_from_config(previous)
    new = policy_from_config(requested)
    before = selected_entries(previous)
    after = selected_entries(requested)
    if (
        new
        and new.selection
        and (old != new or before != after)
        and new.selection.owner_authorization_sha256 != owner_authorization_sha256
    ):
        raise ValueError(
            "CoS Reminders change requires this owner's exact authorization"
        )
