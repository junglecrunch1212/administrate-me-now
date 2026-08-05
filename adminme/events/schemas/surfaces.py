"""Typed lifecycle evidence for disposable, data-only household views."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class TemporarySurfaceTransitionV1(BaseModel):
    model_config = {"extra": "forbid"}
    stable_id: str = Field(min_length=1, max_length=80)
    owner_member_id: str = Field(min_length=1)
    actor_member_id: str = Field(min_length=1)
    transition: Literal["created", "activated", "promoted", "expired", "archived"]
    audience_member_ids: list[str]
    specification_version: int = Field(ge=1)
    occurred_at: str = Field(min_length=1)


for _transition in ("created", "activated", "promoted", "expired", "archived"):
    registry.register(
        f"temporary_surface.{_transition}", 1, TemporarySurfaceTransitionV1
    )


__all__ = ["TemporarySurfaceTransitionV1"]
