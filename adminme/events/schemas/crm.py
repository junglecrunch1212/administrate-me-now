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


class PartyMergedV1(BaseModel):
    """Party identity merge — emitted by the ``identity_resolution`` pipeline
    (prompt 10b) after a human-approved merge suggestion. Registered here
    so the ``parties`` projection has a stable shape to react to when
    prompt 10b wires the subscription.

    Prompt 05 does NOT subscribe to this type — merge handling lands in
    prompt 10b.
    """

    model_config = {"extra": "forbid"}
    surviving_party_id: str = Field(min_length=1)
    merged_party_id: str = Field(min_length=1)
    merged_at: str = Field(min_length=1)
    merged_by_member_id: str | None = None
    rationale: str | None = None


class IdentityMergeSuggestedV1(BaseModel):
    """Heuristic merge suggestion from ``identity_resolution`` (prompt 10b-i)
    when an unresolved identifier scores above the merge threshold against
    an existing party. NEVER auto-applied — the suggestion lands in the
    inbox for human approval per [BUILD.md §1130]."""

    model_config = {"extra": "forbid"}
    surviving_party_id: str = Field(min_length=1)
    candidate_value: str = Field(min_length=1)
    candidate_kind: Literal["email", "phone", "imessage_handle"]
    candidate_value_normalized: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    heuristic: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)


registry.register("party.created", 1, PartyCreatedV1)
registry.register("identifier.added", 1, IdentifierAddedV1)
registry.register("membership.added", 1, MembershipAddedV1)
registry.register("relationship.added", 1, RelationshipAddedV1)
registry.register("party.merged", 1, PartyMergedV1)
registry.register("identity.merge_suggested", 1, IdentityMergeSuggestedV1)
