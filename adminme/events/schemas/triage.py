"""Human-gated proposal events for the canonical Triage surface.

Source observations remain facts.  These events represent derived candidates
that may become household truth or an external write only after a member has
reviewed (and, when necessary, enriched) them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


TriageTargetKind = Literal[
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "list",
    "list_item",
    "recurrence",
    "party_identifier",
    "party_candidate",
    "party_relationship",
    "relationship_fact",
]
TriageProvider = Literal[
    "google_calendar",
    "apple_calendar",
    "apple_reminders",
    "google_tasks",
]


class TriageRouteV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1, max_length=240)
    provider: TriageProvider | None = None
    provider_account_id: str | None = Field(default=None, min_length=1, max_length=500)
    container_external_id: str | None = Field(
        default=None, min_length=1, max_length=1000
    )


class TriageItemProposedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    target_kind: TriageTargetKind
    owner_member_id: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=1000)
    candidate: dict[str, Any] = Field(default_factory=dict)
    route: TriageRouteV1 | None = None
    source_event_id: str = Field(min_length=1, max_length=240)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list, max_length=20)


class TriageItemEnrichedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    enriched_at: str = Field(min_length=1)
    enriched_by_member_id: str = Field(min_length=1, max_length=240)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    target_kind: TriageTargetKind | None = None
    candidate_updates: dict[str, Any] = Field(default_factory=dict)
    route: TriageRouteV1 | None = None

    @model_validator(mode="after")
    def require_change(self) -> "TriageItemEnrichedV1":
        if not any(
            (
                self.title is not None,
                self.target_kind is not None,
                bool(self.candidate_updates),
                self.route is not None,
            )
        ):
            raise ValueError("an enrichment must change at least one field")
        return self


class TriageItemConfirmedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    confirmed_at: str = Field(min_length=1)
    confirmed_by_member_id: str = Field(min_length=1, max_length=240)
    materialized_event_ids: list[str] = Field(min_length=1, max_length=10)


class TriageItemDismissedV1(BaseModel):
    model_config = {"extra": "forbid"}

    proposal_id: str = Field(min_length=1, max_length=240)
    dismissed_at: str = Field(min_length=1)
    dismissed_by_member_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=500)


class TriageItemSupersededV1(BaseModel):
    """System invalidation when the exact source version has changed."""

    model_config = {"extra": "forbid"}
    proposal_id: str = Field(min_length=1, max_length=240)
    superseded_at: str = Field(min_length=1)
    replacement_source_event_id: str = Field(min_length=1, max_length=240)
    reason: Literal["source_changed", "source_deleted", "source_privileged"]


registry.register("triage.item.proposed", 1, TriageItemProposedV1)
registry.register("triage.item.enriched", 1, TriageItemEnrichedV1)
registry.register("triage.item.confirmed", 1, TriageItemConfirmedV1)
registry.register("triage.item.dismissed", 1, TriageItemDismissedV1)
registry.register("triage.item.superseded", 1, TriageItemSupersededV1)


__all__ = [
    "TriageItemConfirmedV1",
    "TriageItemDismissedV1",
    "TriageItemEnrichedV1",
    "TriageItemProposedV1",
    "TriageItemSupersededV1",
    "TriageRouteV1",
    "TriageTargetKind",
]
