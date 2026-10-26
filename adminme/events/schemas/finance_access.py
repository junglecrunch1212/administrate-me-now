"""Self-owned, book-specific Finance access; never a generic CoS delegation."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry

BookIdentifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}$")]


class FinanceBookBinding(BaseModel):
    model_config = {
        "extra": "forbid",
        "strict": True,
        "frozen": True,
        "hide_input_in_errors": True,
    }
    book_id: BookIdentifier
    entity_id: BookIdentifier
    audience_policy_id: BookIdentifier
    audience_policy_version: BookIdentifier


class FinanceAccessControlV1(BaseModel):
    model_config = {
        "extra": "forbid",
        "strict": True,
        "frozen": True,
        "hide_input_in_errors": True,
    }
    owner_id: str = Field(min_length=1, max_length=160)
    changed_by_member_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    prior_event_id: str | None = Field(default=None, max_length=160)
    binding: FinanceBookBinding
    books_enabled: bool
    cos_analysis_enabled: bool
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def self_owned(self) -> "FinanceAccessControlV1":
        if self.owner_id != self.changed_by_member_id:
            raise ValueError("finance control is self-owned")
        if (self.revision == 1) != (self.prior_event_id is None):
            raise ValueError("finance control predecessor required")
        if self.cos_analysis_enabled and not self.books_enabled:
            raise ValueError("finance analysis requires Books access")
        return self


registry.register("settings.finance_access_changed", 1, FinanceAccessControlV1)
