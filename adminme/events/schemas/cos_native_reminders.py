"""Exact native source at the durable boundary before a CoS reminder save."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class CoSReminderWriteAttemptedV2(BaseModel):
    model_config = {"extra": "forbid"}

    command_id: str = Field(min_length=1, max_length=240)
    command_event_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: Literal["apple_reminders"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    operation: Literal["create_reminder"]
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    source_id: str = Field(min_length=1, max_length=1000)
    list_id: str = Field(min_length=1, max_length=1000)
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_owner: Literal["service-principal:chief-of-staff"]
    operating_principal: Literal["service-principal:chief-of-staff"]
    attempted_at: datetime
    automatic_retry: Literal[False] = False


registry.register("external_sync.write_attempted", 2, CoSReminderWriteAttemptedV2)
