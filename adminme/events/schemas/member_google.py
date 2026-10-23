"""Private, exact Google account permission authorization and result receipts."""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from adminme.events.registry import registry
from adminme.member_google import ServiceSelection, EVENT, COMPLETE_EVENT


class GoogleConnectionReceipt(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    member_id: str = Field(min_length=1, max_length=240)
    changed_by_member_id: str = Field(min_length=1, max_length=240)
    account_id: str = Field(pattern=r"^google-[0-9a-f]{24}$")
    request_id: str = Field(min_length=36, max_length=36)
    services: ServiceSelection
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_effects: Literal[False] = False
    automated_candidate_extraction: Literal[False] = False
    sharing_changed: Literal[False] = False

    @model_validator(mode="after")
    def owner(self) -> GoogleConnectionReceipt:
        if self.member_id != self.changed_by_member_id:
            raise ValueError("Google connection permission belongs to its owner")
        return self


registry.register(EVENT, 1, GoogleConnectionReceipt)
registry.register(COMPLETE_EVENT, 1, GoogleConnectionReceipt)
