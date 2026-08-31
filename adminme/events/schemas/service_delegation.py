"""Typed authority events for the household Chief-of-Staff service.

These events grant AdministrateMe context access only.  They never enroll a
provider, approve an OpenClaw tool, or authorize an external write.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


ServiceContextScope = Literal["member_private", "household_shared", "external_contact"]
DelegationPermission = Literal[
    "read_context", "create_internal_work", "manage_internal_work"
]
ApprovalQuorum = Literal["member_owner", "all_adult_principals"]


class ServiceDelegationRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}

    grant_id: str = Field(min_length=1)
    service_principal_id: str = Field(min_length=1)
    service_party_id: str = Field(min_length=1)
    context_scope: ServiceContextScope
    context_member_id: str | None = None
    resource_owner_principal_id: str | None = None
    owner_party_id: str | None = None
    requested_by_member_id: str = Field(min_length=1)
    required_approving_member_ids: list[str] = Field(min_length=1)
    approval_quorum: ApprovalQuorum
    permissions: list[DelegationPermission] = Field(min_length=1)
    audience: list[str] = Field(default_factory=list)
    visibility: list[str] = Field(default_factory=list)
    requested_at: str = Field(min_length=1)
    expires_at: str | None = None

    @model_validator(mode="after")
    def validate_authority_shape(self) -> ServiceDelegationRequestedV1:
        required = self.required_approving_member_ids
        if len(set(required)) != len(required):
            raise ValueError("delegation approval principals must be unique")
        if self.context_scope == "member_private":
            if not self.context_member_id:
                raise ValueError("member-private delegation requires a member")
            if self.resource_owner_principal_id != self.context_member_id:
                raise ValueError("member-private delegation requires exact ownership")
            if self.approval_quorum != "member_owner" or required != [
                self.context_member_id
            ]:
                raise ValueError("member-private delegation requires owner approval")
            if self.owner_party_id is not None:
                raise ValueError(
                    "member-private delegation cannot name an external Party"
                )
        elif self.context_scope == "household_shared":
            if any(
                (
                    self.context_member_id,
                    self.resource_owner_principal_id,
                    self.owner_party_id,
                )
            ):
                raise ValueError(
                    "household-shared delegation cannot impersonate an owner"
                )
            if self.approval_quorum != "all_adult_principals" or len(required) < 2:
                raise ValueError("household-shared delegation requires adult quorum")
        elif (
            not self.owner_party_id
            or not self.context_member_id
            or self.resource_owner_principal_id != self.context_member_id
            or self.approval_quorum != "member_owner"
            or required != [self.context_member_id]
        ):
            raise ValueError("external-contact delegation requires one member owner")
        return self


class ServiceDelegationApprovedV1(BaseModel):
    model_config = {"extra": "forbid"}

    grant_id: str = Field(min_length=1)
    approved_by_member_id: str = Field(min_length=1)
    approved_at: str = Field(min_length=1)


class ServiceDelegationRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}

    grant_id: str = Field(min_length=1)
    revoked_by_member_id: str = Field(min_length=1)
    revoked_at: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


registry.register("service.delegation.requested", 1, ServiceDelegationRequestedV1)
registry.register("service.delegation.approved", 1, ServiceDelegationApprovedV1)
registry.register("service.delegation.revoked", 1, ServiceDelegationRevokedV1)


__all__ = [
    "ApprovalQuorum",
    "DelegationPermission",
    "ServiceContextScope",
    "ServiceDelegationApprovedV1",
    "ServiceDelegationRequestedV1",
    "ServiceDelegationRevokedV1",
]
