"""Non-human service principals and provider-channel routing contracts.

A Chief-of-Staff persona, its provider account, an endpoint, a household
Party, and an OpenClaw agent/session are intentionally different coordinates.
This module represents only the first two and an explicit routing table. It
does not create an account, enroll a channel, infer a member from message
content, or grant access to member-owned provider data.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any, Literal


ChannelState = Literal[
    "planned",
    "account_not_created",
    "account_created",
    "channel_not_enrolled",
    "observation",
    "healthy",
    "degraded",
    "disabled",
    "revoked",
]
RouteScope = Literal[
    "member_private", "household_shared", "external_contact", "unresolved"
]
ServiceContextScope = Literal[
    "service_private",
    "member_private",
    "household_shared",
    "external_contact",
    "unresolved",
]
DelegationPermission = Literal[
    "read_context",
    "create_internal_work",
    "manage_internal_work",
    "manage_member_obligations",
    "coordinate_household_work",
    "prepare_external_actions",
    "execute_fixture_actions",
    "execute_google_tasks_create_trial",
]
ApprovalQuorum = Literal["service_self", "member_owner", "all_adult_principals"]

COS_SERVICE_PRINCIPAL_ID = "service-principal:chief-of-staff"
COS_PARTY_ID = "service-party:chief-of-staff"
COS_AUTOMATION_OPERATOR_ID = "operator:openclaw:automation"
COS_DETERMINISTIC_OPERATOR_ID = "operator:administrateme:deterministic-automation"

CHANNEL_STATES = frozenset(
    {
        "planned",
        "account_not_created",
        "account_created",
        "channel_not_enrolled",
        "observation",
        "healthy",
        "degraded",
        "disabled",
        "revoked",
    }
)
ROUTE_SCOPES = frozenset(
    {"member_private", "household_shared", "external_contact", "unresolved"}
)
_PSEUDONYMOUS_REFERENCE = re.compile(
    r"^(?:cos-apple-account|cos-channel-account|provider-account):[0-9a-f]{32,64}$"
)
_ROUTE_REFERENCE = re.compile(r"^route:[0-9a-f]{32,64}$")


@dataclass(frozen=True)
class ServicePrincipal:
    service_principal_id: str
    kind: str
    party_id: str
    display_name: str
    private_scope: str


@dataclass(frozen=True)
class ServiceRequestContext:
    """Typed, immutable attribution for one authenticated service request.

    ``context_member_id`` identifies whose private context is active while
    ``actor_principal_id`` remains the non-human CoS.  The operator is the
    authenticated automation or individual human controlling that service;
    neither coordinate may impersonate the context member.
    """

    tenant_id: str
    actor_principal_id: str
    operator_principal_id: str
    represented_service_principal_id: str
    context_scope: ServiceContextScope
    context_member_id: str | None = None
    on_behalf_of_member_id: str | None = None
    authority_grant_id: str | None = None
    resource_owner_principal_id: str | None = None
    owner_party_id: str | None = None
    responsible_party_id: str | None = None
    assignee_party_id: str | None = None
    beneficiary_party_id: str | None = None
    provider_account_owner_principal_id: str | None = None
    audience: tuple[str, ...] = ()
    visibility: tuple[str, ...] = ()
    approving_member_ids: tuple[str, ...] = ()
    approval_quorum: ApprovalQuorum = "service_self"
    permissions: frozenset[DelegationPermission] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        required = {
            "tenant_id": self.tenant_id,
            "actor_principal_id": self.actor_principal_id,
            "operator_principal_id": self.operator_principal_id,
            "represented_service_principal_id": self.represented_service_principal_id,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"service request context is missing {missing[0]}")
        if self.actor_principal_id != self.represented_service_principal_id:
            raise ValueError("CoS actor and represented service principal disagree")
        if self.operator_principal_id == self.actor_principal_id:
            raise ValueError("operator identity must remain distinct from the CoS")
        if (
            self.context_member_id
            and self.operator_principal_id == self.context_member_id
        ):
            raise ValueError("operator must not impersonate the active member")
        if self.context_scope == "member_private":
            if not self.context_member_id or not self.on_behalf_of_member_id:
                raise ValueError("member-private service context requires one member")
            if self.context_member_id != self.on_behalf_of_member_id:
                raise ValueError(
                    "member-private context and on-behalf-of member disagree"
                )
            if not self.authority_grant_id:
                raise ValueError(
                    "member-private service context requires an exact grant"
                )
            if self.resource_owner_principal_id != self.context_member_id:
                raise ValueError(
                    "member-private resource owner must be the active member"
                )
            if self.approval_quorum != "member_owner":
                raise ValueError(
                    "member-private service context requires owner approval"
                )
            if self.approving_member_ids != (self.context_member_id,):
                raise ValueError(
                    "member-private service context requires exact owner approval"
                )
        elif self.context_scope == "household_shared":
            if (
                self.context_member_id is not None
                or self.on_behalf_of_member_id is not None
            ):
                raise ValueError("household-shared context cannot impersonate a member")
            if not self.authority_grant_id:
                raise ValueError(
                    "household-shared service context requires an exact grant"
                )
            if self.approval_quorum != "all_adult_principals":
                raise ValueError("household-shared context requires the adult quorum")
            if len(self.approving_member_ids) < 2:
                raise ValueError(
                    "household-shared context requires multiple adult approvals"
                )
        elif self.context_scope == "external_contact":
            if not self.authority_grant_id or not self.owner_party_id:
                raise ValueError("external-contact context requires a grant and Party")
            if self.approval_quorum != "member_owner":
                raise ValueError("external-contact context requires owner approval")
            if (
                not self.context_member_id
                or self.on_behalf_of_member_id != self.context_member_id
                or self.resource_owner_principal_id != self.context_member_id
                or self.approving_member_ids != (self.context_member_id,)
            ):
                raise ValueError(
                    "external-contact context requires its exact member owner"
                )
        elif self.context_scope == "service_private":
            if any(
                (
                    self.context_member_id,
                    self.on_behalf_of_member_id,
                    self.authority_grant_id,
                )
            ):
                raise ValueError(
                    "service-private context cannot carry member authority"
                )
            if self.approval_quorum != "service_self":
                raise ValueError("service-private context uses service-self authority")
        elif self.context_scope == "unresolved":
            if any(
                (
                    self.context_member_id,
                    self.on_behalf_of_member_id,
                    self.authority_grant_id,
                    self.resource_owner_principal_id,
                    self.owner_party_id,
                )
            ):
                raise ValueError("unresolved context cannot retain resolved authority")

    @property
    def allowed_scopes(self) -> frozenset[str]:
        service_scope = service_private_scope(self.actor_principal_id)
        if self.context_scope == "member_private":
            assert self.context_member_id is not None
            scopes = {service_scope}
            if "read_context" in self.permissions:
                scopes.add(f"private:{self.context_member_id}")
            return frozenset(scopes)
        if self.context_scope == "household_shared":
            scopes = {service_scope}
            if "read_context" in self.permissions:
                scopes.add("shared:household")
            return frozenset(scopes)
        if self.context_scope == "external_contact":
            assert self.owner_party_id is not None
            scopes = {service_scope}
            if "read_context" in self.permissions:
                scopes.add(f"external-contact:{self.owner_party_id}")
            return frozenset(scopes)
        return frozenset({service_scope})

    def audit_payload(self) -> dict[str, Any]:
        """Return every applicable attribution axis without overloading one."""

        return {
            "actor_principal_id": self.actor_principal_id,
            "operator_principal_id": self.operator_principal_id,
            "represented_service_principal_id": self.represented_service_principal_id,
            "context_scope": self.context_scope,
            "context_member_id": self.context_member_id,
            "on_behalf_of_member_id": self.on_behalf_of_member_id,
            "authority_grant_id": self.authority_grant_id,
            "resource_owner_principal_id": self.resource_owner_principal_id,
            "owner_party_id": self.owner_party_id,
            "responsible_party_id": self.responsible_party_id,
            "assignee_party_id": self.assignee_party_id,
            "beneficiary_party_id": self.beneficiary_party_id,
            "provider_account_owner_principal_id": (
                self.provider_account_owner_principal_id
            ),
            "audience": list(self.audience),
            "visibility": list(self.visibility),
            "approving_member_ids": list(self.approving_member_ids),
            "approval_quorum": self.approval_quorum,
        }


@dataclass(frozen=True)
class ChannelRoute:
    route_key: str
    scope: RouteScope
    target_id: str | None


@dataclass(frozen=True)
class ServiceChannelAccount:
    channel_id: str
    display_name: str
    provider: str
    service_principal_id: str
    state: ChannelState
    provider_account_reference: str | None
    endpoint_reference: str | None
    enrolled: bool
    observation_active: bool
    sending_enabled: bool
    required_for_activation: bool
    routes: tuple[ChannelRoute, ...]
    intake_mode: Literal["conversation", "member_submission"] = "conversation"


def cos_message_channel_for_account(
    channels: tuple[ServiceChannelAccount, ...],
    *,
    transport_channel_id: str,
    account_id: str,
) -> ServiceChannelAccount | None:
    """Resolve one enrolled CoS endpoint from exact OpenClaw coordinates.

    Provider aliases normalize only the transport kind. Account IDs are opaque
    case-sensitive coordinates and their fingerprint is never normalized.
    """

    if transport_channel_id not in {"imessage", "bluebubbles"} or not account_id:
        return None
    reference = "provider-account:" + hashlib.sha256(account_id.encode()).hexdigest()
    matches = [
        channel
        for channel in channels
        if channel.provider in {"apple_imessage", "imessage"}
        and channel.service_principal_id == COS_SERVICE_PRINCIPAL_ID
        and channel.provider_account_reference == reference
        and channel.enrolled
        and channel.observation_active
        and channel.state in {"observation", "healthy", "degraded"}
    ]
    return matches[0] if len(matches) == 1 else None


def service_private_scope(service_principal_id: str) -> str:
    if not isinstance(service_principal_id, str) or not service_principal_id.strip():
        raise ValueError("service_principal_id is required")
    return f"service-private:{service_principal_id.strip()}"


DEFAULT_SERVICE_IDENTITY_CONFIG: dict[str, Any] = {
    "principals": [
        {
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "kind": "chief_of_staff",
            "party_id": COS_PARTY_ID,
            "display_name": "Household Chief of Staff",
            "private_scope": service_private_scope(COS_SERVICE_PRINCIPAL_ID),
        }
    ],
    "channels": [
        {
            "channel_id": "channel:chief-of-staff:apple-imessage",
            "display_name": "CoS Apple service identity",
            "provider": "apple_imessage",
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "state": "planned",
            "provider_account_reference": None,
            "endpoint_reference": None,
            "enrolled": False,
            "observation_active": True,
            "sending_enabled": False,
            "required_for_activation": False,
            "routes": [],
        }
    ],
}


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def validate_service_identity_config(
    config: dict[str, Any] | None,
    *,
    member_ids: set[str],
    household_party_ids: set[str],
) -> tuple[tuple[ServicePrincipal, ...], tuple[ServiceChannelAccount, ...]]:
    """Validate exact non-human identities without interpreting content."""

    value = DEFAULT_SERVICE_IDENTITY_CONFIG if not config else config
    raw_principals = value.get("principals")
    raw_channels = value.get("channels")
    if not isinstance(raw_principals, list) or not isinstance(raw_channels, list):
        raise ValueError("service identity principals and channels must be lists")

    principals: list[ServicePrincipal] = []
    principal_ids: set[str] = set()
    party_ids: set[str] = set()
    for index, row in enumerate(raw_principals):
        if not isinstance(row, dict):
            raise ValueError(f"service principal {index} must be an object")
        principal_id = _required_text(
            row.get("service_principal_id"), "service_principal_id"
        )
        party_id = _required_text(row.get("party_id"), "service principal party_id")
        if principal_id in member_ids or party_id in member_ids:
            raise ValueError("service principal must remain distinct from every member")
        if party_id in household_party_ids:
            raise ValueError("service principal Party must not reuse an existing Party")
        if principal_id in principal_ids or party_id in party_ids:
            raise ValueError("service principal coordinates must be unique")
        principal_ids.add(principal_id)
        party_ids.add(party_id)
        principals.append(
            ServicePrincipal(
                service_principal_id=principal_id,
                kind=_required_text(row.get("kind"), "service principal kind"),
                party_id=party_id,
                display_name=_required_text(
                    row.get("display_name")
                    or str(row.get("kind") or "service").replace("_", " ").title(),
                    "service principal display_name",
                ),
                private_scope=_required_text(
                    row.get("private_scope") or service_private_scope(principal_id),
                    "service principal private_scope",
                ),
            )
        )
        if principals[-1].private_scope != service_private_scope(principal_id):
            raise ValueError("service principal private scope is not exact")

    channels: list[ServiceChannelAccount] = []
    channel_ids: set[str] = set()
    for index, row in enumerate(raw_channels):
        if not isinstance(row, dict):
            raise ValueError(f"service channel {index} must be an object")
        channel_id = _required_text(row.get("channel_id"), "channel_id")
        if channel_id in channel_ids:
            raise ValueError("service channel ids must be unique")
        channel_ids.add(channel_id)
        principal_id = _required_text(
            row.get("service_principal_id"), "channel service_principal_id"
        )
        if principal_id not in principal_ids:
            raise ValueError("service channel names an unknown service principal")
        state = row.get("state")
        if state not in CHANNEL_STATES:
            raise ValueError("service channel state is invalid")
        account_ref = row.get("provider_account_reference")
        endpoint_ref = row.get("endpoint_reference")
        if account_ref is not None:
            account_ref = _required_text(account_ref, "provider_account_reference")
            if _PSEUDONYMOUS_REFERENCE.fullmatch(account_ref) is None:
                raise ValueError(
                    "service channel account reference must contain a stable hash"
                )
        if endpoint_ref is not None:
            endpoint_ref = _required_text(endpoint_ref, "endpoint_reference")
            if (
                not endpoint_ref.startswith("cos-channel-account:")
                or _PSEUDONYMOUS_REFERENCE.fullmatch(endpoint_ref) is None
            ):
                raise ValueError(
                    "service channel endpoint reference must contain a stable hash"
                )
        enrolled = row.get("enrolled") is True
        observation_active = row.get("observation_active") is True
        sending_enabled = row.get("sending_enabled") is True
        if state in {"planned", "account_not_created"} and any(
            (account_ref, endpoint_ref, enrolled, sending_enabled)
        ):
            raise ValueError(
                "an uncreated service channel cannot claim account evidence"
            )
        if state in {"account_created", "channel_not_enrolled"} and (
            account_ref is None or enrolled or sending_enabled
        ):
            raise ValueError("an unenrolled service channel has contradictory evidence")
        if state in {"observation", "healthy", "degraded"} and (
            account_ref is None or not enrolled
        ):
            raise ValueError(
                "an active service channel requires account and enrollment evidence"
            )
        if state == "observation" and (not observation_active or sending_enabled):
            raise ValueError("an observation service channel cannot enable sending")
        if sending_enabled and state != "healthy":
            raise ValueError("service-channel sending requires healthy state")
        if sending_enabled:
            raise ValueError(
                "service-principal send command contract is not implemented"
            )

        routes: list[ChannelRoute] = []
        intake_mode = row.get("intake_mode", "conversation")
        if intake_mode not in {"conversation", "member_submission"}:
            raise ValueError("service channel intake_mode is invalid")
        route_keys: set[str] = set()
        raw_routes = row.get("routes") or []
        if not isinstance(raw_routes, list):
            raise ValueError("service channel routes must be a list")
        for route_index, route in enumerate(raw_routes):
            if not isinstance(route, dict):
                raise ValueError(
                    f"service channel route {route_index} must be an object"
                )
            route_key = _required_text(route.get("route_key"), "route_key")
            if _ROUTE_REFERENCE.fullmatch(route_key) is None:
                raise ValueError("service channel route_key must contain a stable hash")
            if route_key in route_keys:
                raise ValueError("service channel route keys must be unique")
            route_keys.add(route_key)
            scope = route.get("scope")
            target_id = route.get("target_id")
            if scope not in ROUTE_SCOPES:
                raise ValueError("service channel route scope is invalid")
            if target_id is not None:
                target_id = _required_text(target_id, "route target_id")
            if scope == "member_private" and target_id not in member_ids:
                raise ValueError("member-private channel route requires a known member")
            if scope == "external_contact" and (
                target_id is None
                or target_id in member_ids
                or target_id in principal_ids
                or target_id not in household_party_ids
            ):
                raise ValueError(
                    "external-contact route requires a reviewed non-member Party"
                )
            if scope in {"household_shared", "unresolved"} and target_id is not None:
                raise ValueError(
                    "shared or unresolved channel routes cannot name a target"
                )
            routes.append(
                ChannelRoute(
                    route_key=route_key,
                    scope=scope,
                    target_id=target_id,
                )
            )
        channels.append(
            ServiceChannelAccount(
                channel_id=channel_id,
                display_name=_required_text(row.get("display_name"), "display_name"),
                provider=_required_text(row.get("provider"), "channel provider"),
                service_principal_id=principal_id,
                state=state,
                provider_account_reference=account_ref,
                endpoint_reference=endpoint_ref,
                enrolled=enrolled,
                observation_active=observation_active,
                sending_enabled=sending_enabled,
                required_for_activation=row.get("required_for_activation") is True,
                routes=tuple(routes),
                intake_mode=intake_mode,
            )
        )
    return tuple(principals), tuple(channels)


def resolve_service_channel_route(
    channel: ServiceChannelAccount, route_key: str
) -> dict[str, Any]:
    """Resolve only an exact reviewed route; unknown or ambiguous fails closed."""

    matches = [route for route in channel.routes if route.route_key == route_key]
    if len(matches) != 1 or matches[0].scope == "unresolved":
        return {
            "state": "unresolved",
            "owner_scope": "unresolved:service-channel",
            "visibility_scope": "unresolved:service-channel",
            "content_exposed": False,
        }
    route = matches[0]
    if route.scope == "member_private":
        scope = f"private:{route.target_id}"
    elif route.scope == "household_shared":
        scope = "shared:household"
    else:
        scope = f"external-contact:{route.target_id}"
    return {
        "state": "resolved",
        "route_scope": route.scope,
        "routing_target_id": route.target_id,
        "owner_scope": scope,
        "visibility_scope": scope,
        "content_exposed": False,
    }


def public_service_identity_inventory(
    principals: tuple[ServicePrincipal, ...],
    channels: tuple[ServiceChannelAccount, ...],
) -> dict[str, Any]:
    states = Counter(channel.state for channel in channels)
    return {
        "contract": "service-channel-identity-v1",
        "principal_count": len(principals),
        "channel_count": len(channels),
        "state_counts": dict(sorted(states.items())),
        "channels": [
            {
                "display_name": channel.display_name,
                "provider": channel.provider,
                "state": channel.state,
                "account_reference_present": channel.provider_account_reference
                is not None,
                "endpoint_reference_present": channel.endpoint_reference is not None,
                "enrolled": channel.enrolled,
                "observation_active": channel.observation_active,
                "sending_enabled": channel.sending_enabled,
                "required_for_activation": channel.required_for_activation,
                "route_count": len(channel.routes),
                "intake_mode": channel.intake_mode,
                "content_exposed": False,
            }
            for channel in channels
        ],
        "content_exposed": False,
    }


__all__ = [
    "ApprovalQuorum",
    "CHANNEL_STATES",
    "COS_AUTOMATION_OPERATOR_ID",
    "COS_DETERMINISTIC_OPERATOR_ID",
    "COS_PARTY_ID",
    "COS_SERVICE_PRINCIPAL_ID",
    "DEFAULT_SERVICE_IDENTITY_CONFIG",
    "ChannelRoute",
    "DelegationPermission",
    "ServiceChannelAccount",
    "ServiceContextScope",
    "ServicePrincipal",
    "ServiceRequestContext",
    "cos_message_channel_for_account",
    "public_service_identity_inventory",
    "resolve_service_channel_route",
    "service_private_scope",
    "validate_service_identity_config",
]
