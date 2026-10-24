"""One retrospective ownership receipt; no acquisition or sending authority."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from adminme.adapters.cos_apple_reminders import CoSRemindersSelection
from adminme.events.registry import registry

EVENT_TYPE = "adapter.source_ownership_reconciled"


class ServiceSourceOwnershipReconciledV1(BaseModel):
    """Bind one retained first-poll list fact to reviewed original evidence."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    source_event_id: str = Field(min_length=1, max_length=240)
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    authentication_event_id: str = Field(min_length=1, max_length=240)
    authentication_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection: CoSRemindersSelection
    activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    activation_journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    activation_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_adapter_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_member_id: str = Field(min_length=1, max_length=240)
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    recovery_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reconciled_at: str = Field(min_length=1, max_length=80)
    authority: Literal["historical_attribution_only"]

    @field_validator(
        "source_event_id",
        "authentication_event_id",
        "owner_member_id",
        "operator_principal_id",
    )
    @classmethod
    def exact_identifier(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("historical ownership identifier is invalid")
        return value

    @field_validator("reconciled_at")
    @classmethod
    def aware_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("historical ownership timestamp is invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("historical ownership timestamp is invalid")
        return value


registry.register(EVENT_TYPE, 1, ServiceSourceOwnershipReconciledV1)
