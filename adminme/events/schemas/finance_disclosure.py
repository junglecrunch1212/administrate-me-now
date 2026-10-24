"""Encrypted private disclosure results; never source documents or bearer keys."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.registry import registry


class FinanceDisclosureV1(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["session", "run"]
    member_id: str = Field(min_length=1, max_length=240)
    session_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    book_binding_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    slot: int | None = Field(default=None, ge=1, le=6)
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_json: str | None = Field(default=None, max_length=2000)
    book_id: str | None = Field(default=None, max_length=240)
    publication_id: str | None = Field(default=None, max_length=240)
    head_publication_id: str | None = Field(default=None, max_length=240)
    calculator_id: str | None = Field(default=None, max_length=100)
    model_version: str | None = Field(default=None, max_length=100)
    issued_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def bound_receipt(self) -> "FinanceDisclosureV1":
        run_fields = (
            self.run_sha256,
            self.slot,
            self.result_sha256,
            self.result_json,
            self.expires_at,
        )
        if self.kind == "run":
            if any(value is None for value in run_fields):
                raise ValueError(
                    "finance run receipt requires exact identity and expiry"
                )
            if self.expires_at is None or self.expires_at <= self.issued_at:
                raise ValueError("finance run receipt must expire after issue")
        elif any(value is not None for value in run_fields):
            raise ValueError("session marker cannot carry run evidence")
        publication = (self.book_id, self.publication_id, self.head_publication_id)
        if any(publication) and not all(publication):
            raise ValueError("finance publication receipt requires all coordinates")
        if bool(self.calculator_id) != bool(self.model_version):
            raise ValueError("finance calculator receipt requires a version")
        return self


registry.register("finance.disclosure_recorded", 1, FinanceDisclosureV1)
