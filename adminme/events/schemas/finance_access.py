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


class FinanceAccessControlV2(FinanceAccessControlV1):
    """Owner-approved readers of one book; never a general private-data grant."""

    reader_member_ids: list[str] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def book_audience(self) -> "FinanceAccessControlV2":
        if (
            self.owner_id not in self.reader_member_ids
            or len(set(self.reader_member_ids)) != len(self.reader_member_ids)
            or any(not 1 <= len(member) <= 160 for member in self.reader_member_ids)
        ):
            raise ValueError(
                "finance audience requires its owner and unique principals"
            )
        return self


registry.register("settings.finance_access_changed", 2, FinanceAccessControlV2)


class FinanceAdvisorAccessedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=160)
    operation: str = Field(min_length=1, max_length=120)
    data_owner_id: str | None = Field(default=None, max_length=160)
    control_event_id: str | None = Field(default=None, max_length=160)
    control_revision: int = Field(ge=0)
    book_id: str | None = Field(default=None, max_length=96)
    authority_available: bool
    service_principal_id: str = Field(min_length=1, max_length=160)
    operator_principal_id: str = Field(min_length=1, max_length=160)
    channel_id: str = Field(min_length=1, max_length=120)
    provider_effect_count: Literal[0] = 0


registry.register("finance.advisor_accessed", 1, FinanceAdvisorAccessedV1)
