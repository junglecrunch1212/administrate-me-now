"""
CRM spine schemas — parties, identifiers, memberships, relationships.

Per SYSTEM_INVARIANTS.md §3 and ADMINISTRATEME_BUILD.md §3.1. The CRM is a
shared L3 projection concern (DECISIONS.md §D4); these events feed the
`parties` projection (prompt 05).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class PartyCreatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    party_id: str = Field(min_length=1)
    kind: Literal["person", "organization", "household"]
    display_name: str = Field(min_length=1)
    sort_name: str = Field(min_length=1)
    nickname: str | None = None
    pronouns: str | None = None
    notes: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class IdentifierAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    identifier_id: str = Field(min_length=1)
    party_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    value: str = Field(min_length=1)
    value_normalized: str = Field(min_length=1)
    verified: bool
    primary_for_kind: bool


class MembershipAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    membership_id: str = Field(min_length=1)
    party_id: str = Field(min_length=1)
    parent_party_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    started_at: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class RelationshipAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    relationship_id: str = Field(min_length=1)
    party_a: str = Field(min_length=1)
    party_b: str = Field(min_length=1)
    label: str = Field(min_length=1)
    direction: Literal["a_to_b", "b_to_a", "mutual"]
    since: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


registry.register("party.created", 1, PartyCreatedV1)
registry.register("identifier.added", 1, IdentifierAddedV1)
registry.register("membership.added", 1, MembershipAddedV1)
registry.register("relationship.added", 1, RelationshipAddedV1)
