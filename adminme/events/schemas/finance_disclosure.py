"""Private disclosure provenance; no evidence values or bearer session keys."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from adminme.events.registry import registry


class FinanceDisclosureV1(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["session", "run"]
    member_id: str = Field(min_length=1, max_length=240)
    session_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    slot: int | None = Field(default=None, ge=1, le=6)
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    book_id: str | None = Field(default=None, max_length=240)
    publication_id: str | None = Field(default=None, max_length=240)
    calculator_id: str | None = Field(default=None, max_length=100)
    model_version: str | None = Field(default=None, max_length=100)
    issued_at: AwareDatetime
    expires_at: AwareDatetime | None = None


registry.register("finance.disclosure_recorded", 1, FinanceDisclosureV1)
