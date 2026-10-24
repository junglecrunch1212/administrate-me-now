"""Exact owner-approved file pins, separate from an uploaded Books package."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry
from adminme.events.schemas.finance_access import FinanceBookBinding

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ByteCount = Annotated[int, Field(gt=0, le=1024 * 1024)]


class ImportBlobPin(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}
    sha256: Digest
    bytes: int = Field(gt=0, le=8 * 1024 * 1024)


class FinanceImportManifest(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}
    contract: Literal["household-books.closed-import.v1"]
    binding: FinanceBookBinding
    source_snapshot_sha256: Digest
    publication: ImportBlobPin
    projection: ImportBlobPin | None = None
    index: ImportBlobPin
    blobs: dict[Digest, ByteCount] = Field(min_length=1, max_length=128)
    independently_reviewed_evidence: list[Digest] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_closed_set(self) -> "FinanceImportManifest":
        if self.publication.bytes > 4 * 1024 * 1024 or self.index.bytes > 262144:
            raise ValueError("finance import component limit")
        if sum(self.blobs.values()) + self.index.bytes > 32 * 1024 * 1024:
            raise ValueError("finance import provenance limit")
        evidence = set(self.independently_reviewed_evidence)
        if (
            len(evidence) != len(self.independently_reviewed_evidence)
            or not evidence <= self.blobs.keys()
        ):
            raise ValueError("finance import reviewed evidence invalid")
        return self

    def encoded_body_limit(self) -> int:
        sizes = [self.publication.bytes, self.index.bytes, *self.blobs.values()]
        if self.projection is not None:
            sizes.append(self.projection.bytes)
        return sum(4 * ((size + 2) // 3) for size in sizes) + 16384


class FinanceImportApprovedV1(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}
    owner_id: str = Field(min_length=1, max_length=160)
    changed_by_member_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    policy_event_id: str = Field(min_length=1, max_length=160)
    manifest: FinanceImportManifest
    approved_at: str
    expires_at: str
    provider_effects: Literal[False] = False

    @model_validator(mode="after")
    def exact_approval(self) -> "FinanceImportApprovedV1":
        start, end = (
            datetime.fromisoformat(self.approved_at),
            datetime.fromisoformat(self.expires_at),
        )
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or not 0 < (end - start).total_seconds() <= 1800
        ):
            raise ValueError("finance import approval expiry invalid")
        if self.owner_id != self.changed_by_member_id:
            raise ValueError("finance import approval is self-owned")
        return self


class FinanceImportResultV1(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}
    owner_id: str = Field(min_length=1, max_length=160)
    approval_event_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    outcome: Literal["accepted", "rejected", "incomplete"]
    reason: str = Field(min_length=1, max_length=100)
    publication_event_id: str | None = Field(default=None, max_length=160)
    projection_event_id: str | None = Field(default=None, max_length=160)
    provider_effects: Literal[False] = False


registry.register("settings.finance_import_approved", 1, FinanceImportApprovedV1)
registry.register("finance.import_result", 1, FinanceImportResultV1)
