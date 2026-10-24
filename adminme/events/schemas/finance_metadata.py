"""Owner-controlled metadata access and truthful direct-member read receipts."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


class FinanceMetadataControlV1(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}
    owner_id: str = Field(min_length=1, max_length=160)
    changed_by_member_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    prior_event_id: str | None = Field(default=None, max_length=160)
    owner_read_enabled: bool
    cos_read_enabled: bool
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def self_owned(self) -> "FinanceMetadataControlV1":
        if self.owner_id != self.changed_by_member_id:
            raise ValueError("metadata control is self-owned")
        if (self.revision == 1) != (self.prior_event_id is None):
            raise ValueError("metadata control predecessor required")
        return self


class FinanceMetadataInspectedV1(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}
    owner_id: str = Field(min_length=1, max_length=160)
    access_path: Literal["authenticated_member"]
    policy_revision: int = Field(ge=0)
    state: str = Field(min_length=1, max_length=80)
    complete: bool
    through_event_id: str | None = Field(default=None, max_length=160)
    history_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider_effects: Literal[False] = False


registry.register("settings.finance_metadata_changed", 1, FinanceMetadataControlV1)
registry.register("finance.metadata_inspected", 1, FinanceMetadataInspectedV1)
