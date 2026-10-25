"""Verified producer projection; never accounting acceptance or action authority."""

from typing import Any
from pydantic import BaseModel, field_validator
from adminme.events.registry import registry
from adminme.finance.dated_projection import validate_projection


class DatedProjectionV1(BaseModel):
    model_config = {"extra": "forbid"}
    projection: dict[str, Any]

    @field_validator("projection")
    @classmethod
    def valid_projection(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_projection(value)


registry.register("finance.projection_verified", 1, DatedProjectionV1)
