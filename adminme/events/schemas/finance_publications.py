"""Private immutable Books publication events (no ingress enabled by default)."""

from typing import Any
from pydantic import BaseModel, field_validator
from adminme.events.registry import registry
from adminme.finance.publication import validate_publication


class AcceptedPublicationV1(BaseModel):
    model_config = {"extra": "forbid"}
    publication: dict[str, Any]

    @field_validator("publication")
    @classmethod
    def valid_publication(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_publication(value)


registry.register("finance.publication_accepted", 1, AcceptedPublicationV1)
