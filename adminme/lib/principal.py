"""Canonical actor identities without converting services into human members."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from adminme.service_identities import ServiceRequestContext, service_private_scope


@dataclass(frozen=True)
class PrincipalIdentity:
    kind: Literal["human_member", "service_principal", "system_device"]
    principal_id: str
    party_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"human_member", "service_principal", "system_device"}:
            raise ValueError("unknown principal kind")
        if not self.principal_id or self.principal_id != self.principal_id.strip():
            raise ValueError("principal identity must be nonempty and exact")
        if self.party_id is not None and (
            not self.party_id or self.party_id != self.party_id.strip()
        ):
            raise ValueError("Party identity must be nonempty and exact")
        if self.kind == "service_principal":
            if (
                not self.principal_id.startswith("service-principal:")
                or not self.party_id
            ):
                raise ValueError(
                    "service identity requires a service principal and Party"
                )
        elif self.principal_id.startswith("service-principal:"):
            raise ValueError(
                "a service principal cannot impersonate another actor kind"
            )


@dataclass(frozen=True)
class ServiceSession:
    """One authenticated service-self request with no member identity aliases.

    Transport authentication belongs to the caller. Construction bounds the
    resulting authority to this service's private internal work; it cannot
    turn a configured service route into a member or provider grant.
    """

    tenant_id: str
    principal: PrincipalIdentity
    service_context: ServiceRequestContext
    source: str = "openclaw_plugin"
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        context = self.service_context
        if not self.tenant_id or context.tenant_id != self.tenant_id:
            raise ValueError("service session requires its exact tenant")
        if self.principal.kind != "service_principal":
            raise ValueError("service session requires a non-human service principal")
        if context.actor_principal_id != self.principal.principal_id:
            raise ValueError("service session actor and principal disagree")
        if context.context_scope != "service_private":
            raise ValueError("service session requires service-private authority")
        if context.permissions - {
            "read_context",
            "create_internal_work",
            "manage_internal_work",
        }:
            raise ValueError("service-self authority cannot include delegated effects")
        if context.resource_owner_principal_id not in {
            None,
            self.principal.principal_id,
        }:
            raise ValueError("service session cannot carry another resource owner")
        if context.owner_party_id not in {None, self.principal.party_id}:
            raise ValueError("service session cannot carry another Party owner")
        if any(
            value not in {None, self.principal.party_id}
            for value in (
                context.responsible_party_id,
                context.assignee_party_id,
                context.beneficiary_party_id,
            )
        ):
            raise ValueError("service session cannot carry another Party context")
        if context.provider_account_owner_principal_id is not None:
            raise ValueError("internal service work carries no provider authority")
        if (
            context.approving_member_ids
            or set(context.audience) - {self.principal.principal_id}
            or set(context.visibility) - {self.private_scope}
        ):
            raise ValueError("service session cannot expand its audience or authority")
        if not self.source.strip():
            raise ValueError("service session source is required")

    @property
    def private_scope(self) -> str:
        return service_private_scope(self.principal.principal_id)

    @property
    def allowed_scopes(self) -> frozenset[str]:
        return self.service_context.allowed_scopes

    @property
    def event_actor_identity(self) -> str:
        return self.principal.principal_id

    @property
    def operator_principal_id(self) -> str:
        return self.service_context.operator_principal_id
