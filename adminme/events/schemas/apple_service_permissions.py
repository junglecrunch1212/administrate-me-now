"""Explicit Apple write grants, separate from immutable read enrollment."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.apple_service_permissions import EVENT_TYPE, OPERATIONS, digest
from adminme.events.registry import registry


class AppleServicePermissionControlledV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: Literal["apple_calendar", "apple_reminders", "apple_notes"]
    request_id: str = Field(min_length=8, max_length=160)
    action: Literal["enable", "revoke"]
    enabled: bool
    revision: int = Field(ge=1)
    scope_ids: list[str] = Field(default_factory=list)
    coordinates: dict[str, Any]
    coordinates_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_by_member_id: str = Field(min_length=1, max_length=240)
    changed_at: str = Field(min_length=1)
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def exact_member_authority(self) -> AppleServicePermissionControlledV1:
        expected = {
            "member_id",
            "provider",
            "enrollment_id",
            "device_identity_sha256",
            "configuration_sha256",
            "provider_account_id",
            "account_external_id",
            "allowed_container_ids",
            "operations",
        }
        if (
            self.changed_by_member_id != self.member_id
            or self.enabled != (self.action == "enable")
            or self.scope_ids != sorted(set(self.scope_ids))
            or set(self.coordinates) != expected
            or self.coordinates.get("member_id") != self.member_id
            or self.coordinates.get("provider") != self.provider
            or self.coordinates.get("operations") != OPERATIONS[self.provider]
            or self.coordinates_sha256 != digest(self.coordinates)
        ):
            raise ValueError(
                "Apple service permission has contradictory ownership or binding"
            )
        containers = self.coordinates.get("allowed_container_ids")
        if (
            not isinstance(containers, list)
            or not containers
            or any(not isinstance(item, str) or not item for item in containers)
            or containers != sorted(set(containers))
        ):
            raise ValueError(
                "Apple service permission requires exact selected destinations"
            )
        if self.enabled and not self.scope_ids:
            raise ValueError(
                "Apple service permission requires a reviewed source selection"
            )
        return self


registry.register(EVENT_TYPE, 1, AppleServicePermissionControlledV1)
