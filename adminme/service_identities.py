"""Non-human service principals and provider-channel routing contracts.

A Chief-of-Staff persona, its provider account, an endpoint, a household
Party, and an OpenClaw agent/session are intentionally different coordinates.
This module represents only the first two and an explicit routing table. It
does not create an account, enroll a channel, infer a member from message
content, or grant access to member-owned provider data.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
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


DEFAULT_SERVICE_IDENTITY_CONFIG: dict[str, Any] = {
    "principals": [
        {
            "service_principal_id": "service-principal:chief-of-staff",
            "kind": "chief_of_staff",
            "party_id": "service-party:chief-of-staff",
        }
    ],
    "channels": [
        {
            "channel_id": "channel:chief-of-staff:apple-imessage",
            "display_name": "CoS Apple service identity",
            "provider": "apple_imessage",
            "service_principal_id": "service-principal:chief-of-staff",
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
            )
        )

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
                "content_exposed": False,
            }
            for channel in channels
        ],
        "content_exposed": False,
    }


__all__ = [
    "CHANNEL_STATES",
    "DEFAULT_SERVICE_IDENTITY_CONFIG",
    "ChannelRoute",
    "ServiceChannelAccount",
    "ServicePrincipal",
    "public_service_identity_inventory",
    "resolve_service_channel_route",
    "validate_service_identity_config",
]
