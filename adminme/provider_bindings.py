"""Fail-closed provider-account ownership and routing coordinates.

Provider credentials prove that a provider accepted a token.  They do not prove
which household member owns the account, which workspace the facts belong to,
or which reviewed permission set was intended.  This module validates those
independent coordinates before an enabled adapter can be constructed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable


WORKSPACES = frozenset({"personal", "work", "service"})
PROVENANCE_KINDS = frozenset(
    {"oauth-subject", "service-account-delegation", "member-bridge-enrollment"}
)
ACCOUNT_KEYS = (
    "provider_account_id",
    "user_email",
    "subject_email",
    "source_account_id",
)


@dataclass(frozen=True)
class ProviderBinding:
    adapter_id: str
    kind: str
    provider_family: str
    owner_kind: str
    owner_id: str
    member_id: str | None
    service_principal_id: str | None
    provider_account_id: str
    workspace: str
    permission_scope: tuple[str, ...]
    binding_provenance: str

    def public(self) -> dict[str, Any]:
        """Return non-identifying binding metadata only.

        Provider coordinates are low-entropy identifiers and must not be
        published as unsalted hashes. Exact coordinates remain available only
        at the protected configuration source used for owner review.
        """

        return {
            "kind": self.kind,
            "owner_kind": self.owner_kind,
            "workspace": self.workspace,
            "permission_count": len(self.permission_scope),
            "binding_provenance": self.binding_provenance,
            "content_exposed": False,
        }


def _provider_family(kind: str) -> str:
    normalized = kind.casefold()
    if "gmail" in normalized or "google" in normalized:
        return "google"
    if "microsoft" in normalized or "outlook" in normalized:
        return "microsoft"
    if "apple" in normalized:
        return "apple"
    return normalized.split("-", 1)[0] or "unknown"


def _account_id(config: dict[str, Any]) -> str | None:
    authoritative = config.get("provider_account_id")
    if isinstance(authoritative, str) and authoritative.strip():
        return authoritative.strip()
    values = [
        value.strip()
        for key in ACCOUNT_KEYS
        if key != "provider_account_id"
        if isinstance((value := config.get(key)), str) and value.strip()
    ]
    if not values:
        return None
    normalized = {value.casefold() for value in values}
    if len(normalized) != 1:
        raise ValueError("provider account coordinates disagree")
    return values[0]


def _known_member_ids(
    member_roles: dict[str, str], household_mapping: dict[str, Any]
) -> set[str]:
    known = set(member_roles)
    members = household_mapping.get("members", []) if household_mapping else []
    if isinstance(members, list):
        known.update(
            str(member["member_id"])
            for member in members
            if isinstance(member, dict)
            and isinstance(member.get("member_id"), str)
            and member["member_id"]
        )
    return known


def validate_provider_bindings(
    config: dict[str, Any],
    *,
    member_roles: dict[str, str],
    household_mapping: dict[str, Any],
    service_principal_ids: set[str] | None = None,
) -> tuple[ProviderBinding, ...]:
    """Validate every enabled provider row as one explicit owned binding.

    Disabled rows may remain incomplete so optional integrations can be kept
    truthfully off.  Enabled fixture adapters without provider credentials are
    excluded; every enabled real provider row fails closed on missing or
    contradictory coordinates.
    """

    adapters = config.get("adapters", [])
    if not isinstance(adapters, list):
        raise ValueError("adapter_config.adapters must be a list")
    known_members = _known_member_ids(member_roles, household_mapping)
    known_service_principals = set(service_principal_ids or ())
    bindings: list[ProviderBinding] = []
    for index, entry in enumerate(adapters):
        if not isinstance(entry, dict):
            raise ValueError(f"adapter entry {index} must be a mapping")
        if entry.get("enabled") is False:
            continue
        kind = str(entry.get("kind") or "").removeprefix("adapter:")
        adapter_config = entry.get("config") or {}
        if not isinstance(adapter_config, dict):
            raise ValueError(f"adapter entry {index} config must be a mapping")
        has_provider_credential = any(
            isinstance(value, str) and value.startswith(("op://", "keychain://"))
            for key, value in adapter_config.items()
            if key.endswith("_ref") or key.endswith("_reference")
        )
        account_id = _account_id(adapter_config)
        if not has_provider_credential and account_id is None:
            continue
        member_id = adapter_config.get("member_id")
        service_principal_id = adapter_config.get("service_principal_id")
        workspace = adapter_config.get("workspace")
        permission_scope = adapter_config.get("permission_scope")
        provenance = adapter_config.get("binding_provenance")
        member_owner = isinstance(member_id, str) and bool(member_id.strip())
        service_owner = isinstance(service_principal_id, str) and bool(
            service_principal_id.strip()
        )
        if member_owner == service_owner:
            raise ValueError(
                f"enabled provider adapter {index} requires exactly one owner principal"
            )
        if member_owner and member_id not in known_members:
            raise ValueError(
                f"enabled provider adapter {index} names an unknown member"
            )
        if service_owner and service_principal_id not in known_service_principals:
            raise ValueError(
                f"enabled provider adapter {index} names an unknown service principal"
            )
        if account_id is None:
            raise ValueError(
                f"enabled provider adapter {index} requires provider_account_id"
            )
        if workspace not in WORKSPACES:
            raise ValueError(
                f"enabled provider adapter {index} requires an explicit workspace"
            )
        if (
            not isinstance(permission_scope, list)
            or not permission_scope
            or any(
                not isinstance(item, str) or not item.strip()
                for item in permission_scope
            )
            or len({item.strip() for item in permission_scope}) != len(permission_scope)
        ):
            raise ValueError(
                f"enabled provider adapter {index} requires a unique permission_scope"
            )
        if provenance not in PROVENANCE_KINDS:
            raise ValueError(
                f"enabled provider adapter {index} requires reviewed binding_provenance"
            )
        if adapter_config.get("ownership_attested") is not True:
            raise ValueError(
                f"enabled provider adapter {index} requires ownership_attested=true"
            )
        bindings.append(
            ProviderBinding(
                adapter_id=str(entry.get("id") or f"adapter-{index}"),
                kind=kind,
                provider_family=_provider_family(kind),
                owner_kind="member" if member_owner else "service_principal",
                owner_id=str(member_id if member_owner else service_principal_id),
                member_id=str(member_id) if member_owner else None,
                service_principal_id=(
                    str(service_principal_id) if service_owner else None
                ),
                provider_account_id=account_id,
                workspace=str(workspace),
                permission_scope=tuple(
                    sorted(item.strip() for item in permission_scope)
                ),
                binding_provenance=str(provenance),
            )
        )

    exact_seen: set[tuple[str, str, str]] = set()
    ownership: dict[tuple[str, str], tuple[str, str]] = {}
    for binding in bindings:
        exact = (
            binding.kind,
            binding.provider_account_id.casefold(),
            binding.workspace,
        )
        if exact in exact_seen:
            raise ValueError("duplicate enabled provider binding coordinate")
        exact_seen.add(exact)
        account = (binding.provider_family, binding.provider_account_id.casefold())
        coordinate = (binding.owner_id, binding.workspace)
        prior = ownership.setdefault(account, coordinate)
        if prior != coordinate:
            raise ValueError(
                "provider account has contradictory owner or workspace bindings"
            )
    return tuple(bindings)


def public_binding_inventory(bindings: Iterable[ProviderBinding]) -> dict[str, Any]:
    rows = [binding.public() for binding in bindings]
    kind_counts = Counter(str(row["kind"]) for row in rows)
    owner_kind_counts = Counter(str(row["owner_kind"]) for row in rows)
    workspace_counts = Counter(str(row["workspace"]) for row in rows)
    return {
        "contract": "provider-binding-readiness-v1",
        "state": "healthy",
        "binding_count": len(rows),
        "kind_counts": dict(sorted(kind_counts.items())),
        "owner_kind_counts": dict(sorted(owner_kind_counts.items())),
        "workspace_counts": dict(sorted(workspace_counts.items())),
        "content_exposed": False,
    }


__all__ = [
    "ACCOUNT_KEYS",
    "PROVENANCE_KINDS",
    "ProviderBinding",
    "WORKSPACES",
    "public_binding_inventory",
    "validate_provider_bindings",
]
