"""Encrypted native workstation requests and observations, not provider writes."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from adminme.events.registry import registry
from adminme.workstation_inventory import AccountIdentity


class WorkstationReadRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}
    operation: Literal["native_browser_read"]
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    worker_session_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction_source_event_id: str = Field(min_length=1, max_length=240)
    work_event_id: str = Field(min_length=1, max_length=240)
    requester_member_id: str = Field(min_length=1, max_length=240)
    resource_id: str = Field(min_length=1, max_length=300)
    inventory_binding_id: str = Field(min_length=1, max_length=300)
    inventory_binding_revision: int = Field(ge=1)
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audience_member_ids: list[str] = Field(min_length=1, max_length=20)
    requested_at: AwareDatetime
    operating_principal_id: Literal["service-principal:chief-of-staff"]
    resource_owner_principal_id: Literal["service-principal:chief-of-staff"]
    provider_effect_count: Literal[0] = 0


class WorkstationSourceObservedV1(BaseModel):
    model_config = {"extra": "forbid"}
    operation: Literal["native_browser_read"]
    request_event_id: str = Field(min_length=1, max_length=240)
    instruction_source_event_id: str = Field(min_length=1, max_length=240)
    worker_session_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_id: str = Field(min_length=1, max_length=300)
    inventory_binding_id: str = Field(min_length=1, max_length=300)
    inventory_binding_revision: int = Field(ge=1)
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_target_id: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    authorized_url_prefix: str = Field(min_length=1, max_length=2000)
    device: str = Field(min_length=1, max_length=300)
    os_user: str = Field(min_length=1, max_length=300)
    browser_profile: str = Field(min_length=1, max_length=300)
    actual_identity: AccountIdentity
    excerpt: str = Field(min_length=1, max_length=2000)
    truncated: bool
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    restrictions: list[str] = Field(default_factory=list, max_length=20)
    audience_member_ids: list[str] = Field(min_length=1, max_length=20)
    observed_at: AwareDatetime
    operating_principal_id: Literal["service-principal:chief-of-staff"]
    resource_owner_principal_id: Literal["service-principal:chief-of-staff"]
    provider_effect_count: Literal[0] = 0


registry.register("cos.workstation.read_requested", 1, WorkstationReadRequestedV1)
registry.register("cos.workstation.source_observed", 1, WorkstationSourceObservedV1)
