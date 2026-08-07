"""People v2 source, lifecycle, and relationship-memory events.

Provider contact records and observed endpoints are facts.  Parties and
member-specific relationship context remain distinct, event-sourced domain
objects.  Nothing in this module grants portal membership or authorizes an
external write.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


ContactProvider = Literal[
    "apple_contacts",
    "google_people",
    "microsoft_graph",
    "carddav",
    "fixture",
]
EndpointKind = Literal[
    "email",
    "phone",
    "imessage_handle",
    "whatsapp_peer",
    "directory_id",
    "url",
    "other",
]
EndpointClassification = Literal[
    "unresolved",
    "person",
    "organization",
    "service",
    "automated",
]


class ContactIdentifierV1(BaseModel):
    model_config = {"extra": "forbid"}

    kind: EndpointKind
    value: str = Field(min_length=1, max_length=2000)
    value_normalized: str = Field(min_length=1, max_length=2000)
    label: str | None = Field(default=None, max_length=240)
    primary: bool = False
    verified: bool = False


class ContactRecordObservedV1(BaseModel):
    """Loss-aware snapshot of one curated provider contact record."""

    model_config = {"extra": "forbid"}

    contact_record_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ContactProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    container_external_id: str = Field(min_length=1, max_length=1000)
    external_id: str = Field(min_length=1, max_length=1000)
    provider_revision: str | None = Field(default=None, max_length=2000)
    kind: Literal["person", "organization", "household"] = "person"
    display_name: str = Field(min_length=1, max_length=1000)
    sort_name: str | None = Field(default=None, max_length=1000)
    given_name: str | None = Field(default=None, max_length=500)
    family_name: str | None = Field(default=None, max_length=500)
    nickname: str | None = Field(default=None, max_length=500)
    organization_name: str | None = Field(default=None, max_length=1000)
    job_title: str | None = Field(default=None, max_length=1000)
    identifiers: list[ContactIdentifierV1] = Field(default_factory=list, max_length=200)
    fields: dict[str, Any] = Field(default_factory=dict)
    group_external_ids: list[str] = Field(default_factory=list, max_length=200)
    source_url: str | None = Field(default=None, max_length=4000)
    observed_at: str = Field(min_length=1)
    capability_warnings: list[str] = Field(default_factory=list, max_length=50)


class ContactRecordDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}

    contact_record_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ContactProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    container_external_id: str = Field(min_length=1, max_length=1000)
    external_id: str = Field(min_length=1, max_length=1000)
    provider_revision: str | None = Field(default=None, max_length=2000)
    deleted_at: str = Field(min_length=1)


class ContactGroupObservedV1(BaseModel):
    model_config = {"extra": "forbid"}

    group_record_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ContactProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    container_external_id: str = Field(min_length=1, max_length=1000)
    external_id: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=1000)
    provider_revision: str | None = Field(default=None, max_length=2000)
    observed_at: str = Field(min_length=1)


class EndpointObservedV1(BaseModel):
    """One addressable endpoint observed without asserting a Party."""

    model_config = {"extra": "forbid"}

    endpoint_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    kind: EndpointKind
    value: str = Field(min_length=1, max_length=2000)
    value_normalized: str = Field(min_length=1, max_length=2000)
    classification: EndpointClassification = "unresolved"
    provider: str | None = Field(default=None, max_length=240)
    provider_account_id: str | None = Field(default=None, max_length=500)
    source_interaction_id: str | None = Field(default=None, max_length=240)
    observed_at: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list, max_length=20)


class PartyUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}

    party_id: str = Field(min_length=1, max_length=240)
    updated_at: str = Field(min_length=1)
    field_updates: dict[str, Any] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def allow_safe_fields(self) -> "PartyUpdatedV1":
        allowed = {
            "display_name",
            "sort_name",
            "nickname",
            "pronouns",
            "notes",
            "kind",
        }
        unknown = set(self.field_updates) - allowed
        if unknown:
            raise ValueError(f"unsupported party update fields: {sorted(unknown)}")
        return self


class PartyQualificationSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    party_id: str = Field(min_length=1, max_length=240)
    qualification: Literal["qualified", "unconfirmed_endpoint", "archived"]
    reason: str = Field(min_length=1, max_length=500)
    changed_at: str = Field(min_length=1)
    changed_by_member_id: str | None = Field(default=None, max_length=240)


class PartyArchivedV1(BaseModel):
    model_config = {"extra": "forbid"}

    party_id: str = Field(min_length=1, max_length=240)
    archived_at: str = Field(min_length=1)
    archived_by_member_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=500)


class PartyRestoredV1(BaseModel):
    model_config = {"extra": "forbid"}

    party_id: str = Field(min_length=1, max_length=240)
    restored_at: str = Field(min_length=1)
    restored_by_member_id: str = Field(min_length=1, max_length=240)


class PartySplitV1(BaseModel):
    model_config = {"extra": "forbid"}

    merged_party_id: str = Field(min_length=1, max_length=240)
    restored_party_id: str = Field(min_length=1, max_length=240)
    split_at: str = Field(min_length=1)
    split_by_member_id: str = Field(min_length=1, max_length=240)
    rationale: str | None = Field(default=None, max_length=1000)
    identifier_ids: list[str] = Field(default_factory=list, max_length=500)
    contact_record_ids: list[str] = Field(default_factory=list, max_length=500)
    relationship_fact_ids: list[str] = Field(default_factory=list, max_length=500)
    profile_ids: list[str] = Field(default_factory=list, max_length=100)
    relationship_party_a_ids: list[str] = Field(default_factory=list, max_length=500)
    relationship_party_b_ids: list[str] = Field(default_factory=list, max_length=500)


class IdentifierRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}

    identifier_id: str = Field(min_length=1, max_length=240)
    revoked_at: str = Field(min_length=1)
    revoked_by_member_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=500)


class RelationshipUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}

    relationship_id: str = Field(min_length=1, max_length=240)
    updated_at: str = Field(min_length=1)
    field_updates: dict[str, Any] = Field(min_length=1, max_length=20)


class RelationshipEndedV1(BaseModel):
    model_config = {"extra": "forbid"}

    relationship_id: str = Field(min_length=1, max_length=240)
    ended_at: str = Field(min_length=1)
    ended_by_member_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=500)


class MemberRelationshipProfileSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    profile_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    party_id: str = Field(min_length=1, max_length=240)
    circle: str | None = Field(default=None, max_length=240)
    desired_cadence_days: int | None = Field(default=None, ge=1, le=3650)
    cues_enabled: bool = False
    favorite: bool = False
    suppressed: bool = False
    suppression_reason: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=50)
    changed_at: str = Field(min_length=1)


RelationshipFactKind = Literal[
    "birthday",
    "important_date",
    "employer",
    "role",
    "preference",
    "address",
    "context",
]


class RelationshipFactConfirmedV1(BaseModel):
    model_config = {"extra": "forbid"}

    fact_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    party_id: str = Field(min_length=1, max_length=240)
    kind: RelationshipFactKind
    label: str | None = Field(default=None, max_length=240)
    value: str = Field(min_length=1, max_length=4000)
    effective_date: str | None = Field(default=None, max_length=40)
    source_event_id: str = Field(min_length=1, max_length=240)
    evidence_quote: str | None = Field(default=None, max_length=2000)
    confirmed_at: str = Field(min_length=1)
    confirmed_by_member_id: str = Field(min_length=1, max_length=240)


class RelationshipFactRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}

    fact_id: str = Field(min_length=1, max_length=240)
    revoked_at: str = Field(min_length=1)
    revoked_by_member_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=500)


class PartyAccessGrantedV1(BaseModel):
    model_config = {"extra": "forbid"}

    grant_id: str = Field(min_length=1, max_length=240)
    party_id: str = Field(min_length=1, max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    grantee_member_id: str = Field(min_length=1, max_length=240)
    field_groups: list[Literal["identifiers", "facts"]] = Field(
        min_length=1, max_length=2
    )
    granted_at: str = Field(min_length=1)
    expires_at: str | None = None


class PartyAccessRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}

    grant_id: str = Field(min_length=1, max_length=240)
    revoked_at: str = Field(min_length=1)
    revoked_by_member_id: str = Field(min_length=1, max_length=240)


registry.register("contact.record.observed", 1, ContactRecordObservedV1)
registry.register("contact.record.deleted", 1, ContactRecordDeletedV1)
registry.register("contact.group.observed", 1, ContactGroupObservedV1)
registry.register("endpoint.observed", 1, EndpointObservedV1)
registry.register("party.updated", 1, PartyUpdatedV1)
registry.register("party.qualification_set", 1, PartyQualificationSetV1)
registry.register("party.archived", 1, PartyArchivedV1)
registry.register("party.restored", 1, PartyRestoredV1)
registry.register("party.split", 1, PartySplitV1)
registry.register("identifier.revoked", 1, IdentifierRevokedV1)
registry.register("relationship.updated", 1, RelationshipUpdatedV1)
registry.register("relationship.ended", 1, RelationshipEndedV1)
registry.register("member_relationship.profile_set", 1, MemberRelationshipProfileSetV1)
registry.register("relationship_fact.confirmed", 1, RelationshipFactConfirmedV1)
registry.register("relationship_fact.revoked", 1, RelationshipFactRevokedV1)
registry.register("party.access_granted", 1, PartyAccessGrantedV1)
registry.register("party.access_revoked", 1, PartyAccessRevokedV1)


__all__ = [
    "ContactRecordObservedV1",
    "EndpointObservedV1",
    "MemberRelationshipProfileSetV1",
    "PartyAccessGrantedV1",
    "RelationshipFactConfirmedV1",
]
