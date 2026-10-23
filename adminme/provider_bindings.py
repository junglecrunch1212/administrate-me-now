"""Fail-closed provider-account ownership and routing coordinates.

Provider credentials prove that a provider accepted a token.  They do not prove
which household member owns the account, which workspace the facts belong to,
or which reviewed permission set was intended.  This module validates those
independent coordinates before an enabled adapter can be constructed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from collections.abc import Mapping, Set
from typing import Any, Iterable

from adminme.adapters.cos_channel_acquisition import (
    policy_from_config as cos_policy_from_config,
    restrict_cos_adapter_config,
)
from adminme.service_identities import service_private_scope
from adminme.adapters.cos_google_workspace import PURPOSES, validate_oauth_adapter
from adminme.google_tasks_execution import (
    ACTION_CLASS as B03_ACTION_CLASS,
    ADAPTER_IDENTITY as B03_ADAPTER_IDENTITY,
    GOOGLE_TASKS_WRITE_SCOPE,
    OPERATION as B03_OPERATION,
)


WORKSPACES = frozenset({"personal", "work", "service"})
PROVENANCE_KINDS = frozenset(
    {
        "oauth-subject",
        "service-account-delegation",
        "member-bridge-enrollment",
        "service-device-authorization",
    }
)
ACCOUNT_KEYS = (
    "provider_account_id",
    "user_email",
    "subject_email",
    "source_account_id",
)
MEMBER_BRIDGE_ONLY_ADAPTER_KINDS = frozenset(
    {
        "calendaring-apple-calendar",
        "contacts-apple-contacts",
        "messaging-bluebubbles",
        "reminders-apple-eventkit",
    }
)
SERVICE_OWNER_ADAPTER_KINDS = frozenset(
    {
        "messaging-gmail-api",
        "calendaring-google-calendar",
        "tasks-google-tasks",
        "reminders-cos-apple-eventkit",
    }
)
SERVICE_OWNER_READONLY_SCOPES = {
    "messaging-gmail-api": frozenset(
        {"https://www.googleapis.com/auth/gmail.readonly"}
    ),
    "calendaring-google-calendar": frozenset(
        {"https://www.googleapis.com/auth/calendar.readonly"}
    ),
    "tasks-google-tasks": frozenset({"https://www.googleapis.com/auth/tasks.readonly"}),
}
COS_GMAIL_OAUTH_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    }
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
    represented_service_principal_id: str | None = None
    context_scope: str | None = None
    on_behalf_of: str | None = None
    resource_owner_principal_id: str | None = None
    destination_id: str | None = None
    permitted_operations: tuple[str, ...] = ()
    write_binding_state: str = "read_only"
    binding_revision: int = 1
    review_provenance: str | None = None

    @property
    def coordinate(self) -> tuple[str, str, str, str, str]:
        """The isolation coordinate for cursor, lease, dedupe, and health state."""

        return (
            self.owner_kind,
            self.owner_id,
            self.provider_account_id.casefold(),
            self.workspace,
            self.kind,
        )

    @property
    def owner_scope(self) -> str:
        if self.owner_kind == "service_principal":
            return service_private_scope(self.owner_id)
        return f"private:{self.owner_id}"

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
            "write_binding_state": self.write_binding_state,
            "permitted_operations": list(self.permitted_operations),
            "destination_class": (
                "pre-existing dedicated task list" if self.destination_id else None
            ),
            "revision": self.binding_revision,
        }

    @property
    def b03_dispatch_coordinate(self) -> tuple[str, ...] | None:
        if self.write_binding_state != "trial":
            return None
        return (
            str(self.resource_owner_principal_id),
            self.owner_id,
            str(self.represented_service_principal_id),
            str(self.context_scope),
            str(self.on_behalf_of),
            "google_tasks",
            self.provider_account_id.casefold(),
            self.kind,
            str(self.destination_id),
            B03_OPERATION,
        )


@dataclass(frozen=True)
class HistoricalProviderBinding:
    """Ownership evidence for an audit, never an operational provider binding.

    Permission scopes are validated at the configuration boundary but are not
    carried into this view. It deliberately has no dispatch, write, capability,
    credential, or active-readiness interface.
    """

    adapter_id: str
    kind: str
    provider_family: str
    owner_kind: str
    owner_id: str
    member_id: str | None
    service_principal_id: str | None
    provider_account_id: str
    workspace: str
    binding_provenance: str

    @property
    def owner_scope(self) -> str:
        if self.owner_kind == "service_principal":
            return service_private_scope(self.owner_id)
        return f"private:{self.owner_id}"


def _provider_family(kind: str) -> str:
    normalized = kind.casefold()
    if "bluebubbles" in normalized:
        return "apple_messages"
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


def adapter_owner_coordinate_id(adapter_name: str, config: Mapping[str, Any]) -> str:
    """Return the exact durable namespace for one configured producer."""

    member_id = config.get("member_id")
    service_id = config.get("service_principal_id")
    member_owner = isinstance(member_id, str) and bool(member_id.strip())
    service_owner = isinstance(service_id, str) and bool(service_id.strip())
    if member_owner == service_owner:
        raise ValueError("adapter coordinate requires exactly one owner principal")
    account = _account_id(dict(config))
    workspace = config.get("workspace")
    if member_owner and not workspace:
        # Legacy fixture/direct-construction paths predate the production
        # binding validator. Production loader rows always carry workspace.
        workspace = "personal"
    if account is None or not isinstance(workspace, str) or not workspace:
        raise ValueError("adapter coordinate requires account and workspace")
    owner_kind = "member" if member_owner else "service_principal"
    owner_id = str(member_id if member_owner else service_id)
    return "|".join((adapter_name, owner_kind, owner_id, account, workspace))


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


def _validate_provider_binding_inventory(
    config: dict[str, Any],
    *,
    member_roles: dict[str, str],
    household_mapping: dict[str, Any],
    service_principal_ids: set[str] | None = None,
    service_principal_provider_authorizations: Mapping[str, Set[str]] | None = None,
    include_retired_member_bindings: bool = False,
) -> tuple[ProviderBinding, ...]:
    """Share exact ownership checks without exposing a public enablement switch."""

    adapters = config.get("adapters", [])
    if not isinstance(adapters, list):
        raise ValueError("adapter_config.adapters must be a list")
    known_members = _known_member_ids(member_roles, household_mapping)
    known_service_principals = set(service_principal_ids or ())
    cos_policy = cos_policy_from_config(config)
    if cos_policy is not None:
        # Validate the exact service account/channel and retained acquisition
        # ceiling without changing the caller's inventory or enabling a row.
        restrict_cos_adapter_config(config)
    # The current service-identity catalog contains planned channel identities,
    # not provider-account authority. Keep the parameter for source-compatible
    # callers, but no caller may turn a planned principal into an enabled
    # provider binding by supplying an in-memory kind allowlist.
    del service_principal_provider_authorizations
    bindings: list[ProviderBinding] = []
    for index, entry in enumerate(adapters):
        if not isinstance(entry, dict):
            raise ValueError(f"adapter entry {index} must be a mapping")
        if entry.get("enabled") is False:
            if not include_retired_member_bindings:
                continue
            retired_config = entry.get("config") or {}
            if not isinstance(retired_config, dict):
                raise ValueError(f"adapter entry {index} config must be a mapping")
            if retired_config.get("ownership_attested") is not True:
                # Optional unenrolled rows are not ownership evidence. A fact
                # relying on one remains unattributed in the historical audit.
                continue
            if (
                retired_config.get("member_id") is None
                and retired_config.get("service_principal_id") is not None
            ):
                # Retired service authority is outside this member-history
                # contract. Enabled services still pass all ordinary checks.
                continue
            if (
                any(
                    not isinstance(entry.get(key), str) or not entry[key].strip()
                    for key in ("id", "kind")
                )
                or not str(entry["kind"]).removeprefix("adapter:").strip()
            ):
                raise ValueError(
                    f"retired provider adapter {index} requires an exact id and kind"
                )
        kind = str(entry.get("kind") or "").removeprefix("adapter:")
        adapter_config = entry.get("config") or {}
        if not isinstance(adapter_config, dict):
            raise ValueError(f"adapter entry {index} config must be a mapping")
        if kind in MEMBER_BRIDGE_ONLY_ADAPTER_KINDS:
            raise ValueError(
                f"enabled member Apple adapter {index} is bridge-only and cannot "
                "be configured in the central adapter inventory"
            )
        has_provider_credential = any(
            isinstance(value, str) and value.startswith(("op://", "keychain://"))
            for key, value in adapter_config.items()
            if key.endswith("_ref") or key.endswith("_reference")
        )
        account_id = _account_id(adapter_config)
        if not has_provider_credential and account_id is None:
            raise ValueError(
                f"enabled provider adapter {index} requires binding evidence"
            )
        member_id = adapter_config.get("member_id")
        service_principal_id = adapter_config.get("service_principal_id")
        workspace = adapter_config.get("workspace")
        permission_scope = adapter_config.get("permission_scope")
        provenance = adapter_config.get("binding_provenance")
        cos_oauth = (
            cos_policy is not None
            and entry.get("id") == cos_policy.gmail_adapter_id
            and kind == "messaging-gmail-api"
            and provenance == "oauth-subject"
        )
        workspace_oauth = (
            service_principal_id is not None
            and kind in {row[0] for row in PURPOSES.values()}
            and provenance == "oauth-subject"
        )
        cos_reminders = kind == "reminders-cos-apple-eventkit"
        if cos_reminders:
            from adminme.adapters.cos_apple_reminders import validate_adapter_config

            validate_adapter_config(adapter_config)
        elif provenance == "service-device-authorization":
            raise ValueError(
                "device authorization is limited to reviewed CoS Reminders"
            )
        cos_writes = False
        b03 = adapter_config.get("b03_google_tasks_create")
        if b03 is not None and not isinstance(b03, dict):
            raise ValueError(
                f"enabled provider adapter {index} has invalid B03 binding"
            )
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
        owner_coordinate = str(member_id if member_owner else service_principal_id)
        if account_id.casefold() == owner_coordinate.casefold():
            raise ValueError(
                f"enabled provider adapter {index} collapses owner and provider account"
            )
        if service_owner and kind not in SERVICE_OWNER_ADAPTER_KINDS:
            raise ValueError(
                f"enabled provider adapter {index} has no reviewed service-owned "
                "provider authority and does not support service ownership"
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
        if member_owner and workspace == "service":
            raise ValueError(
                f"enabled provider adapter {index} cannot fake member ownership "
                "for a service workspace"
            )
        if service_owner:
            if workspace != "service":
                raise ValueError(
                    f"enabled provider adapter {index} requires service workspace"
                )
            if cos_reminders:
                pass  # Exact OS/source authority was validated before construction.
            elif cos_oauth:
                if (
                    b03 is not None
                    or frozenset(permission_scope) != COS_GMAIL_OAUTH_SCOPES
                ):
                    raise ValueError(
                        "CoS Gmail OAuth requires only the exact read and send grants"
                    )
                reference = adapter_config.get("oauth_credentials_ref")
                if (
                    not isinstance(reference, str)
                    or not reference.startswith(("op://", "keychain://"))
                    or adapter_config.get("service_account_key_ref") is not None
                ):
                    raise ValueError(
                        "CoS Gmail OAuth requires one explicit OAuth credential reference"
                    )
                read_only = adapter_config.get("read_only")
                enable_writes = adapter_config.get("enable_writes")
                if not (
                    (read_only is True and enable_writes is False)
                    or (read_only is False and enable_writes is True)
                ):
                    raise ValueError(
                        "CoS Gmail OAuth requires an explicit consistent write mode"
                    )
                # This records configuration readiness only. Recipient policy,
                # domain and OpenClaw approvals, observation and the final
                # privacy guard remain mandatory at the response transport.
                cos_writes = enable_writes is True
            elif workspace_oauth:
                purpose = next(key for key, row in PURPOSES.items() if row[0] == kind)
                validate_oauth_adapter(adapter_config, purpose=purpose)
            elif b03 is None:
                reviewed_scopes = SERVICE_OWNER_READONLY_SCOPES[kind]
                if frozenset(permission_scope) != reviewed_scopes:
                    raise ValueError(
                        f"enabled provider adapter {index} exceeds the reviewed "
                        "service-owner read scope"
                    )
                if adapter_config.get("read_only") is not True:
                    raise ValueError(
                        f"enabled provider adapter {index} requires read_only=true "
                        "for service ownership"
                    )
                if adapter_config.get("enable_writes") is True:
                    raise ValueError(
                        f"enabled provider adapter {index} cannot enable service writes"
                    )
            else:
                required = {
                    "action_class": B03_ACTION_CLASS,
                    "represented_service_principal_id": service_principal_id,
                    "permitted_operation": B03_OPERATION,
                    "required_permission_scope": GOOGLE_TASKS_WRITE_SCOPE,
                    "state": "trial",
                    "destination_preexisting": True,
                }
                if kind != B03_ADAPTER_IDENTITY or any(
                    b03.get(key) != value for key, value in required.items()
                ):
                    raise ValueError(
                        f"enabled provider adapter {index} has an unreviewed B03 coordinate"
                    )
                exact_text = (
                    "provider_account_id",
                    "destination_task_list_id",
                    "context_scope",
                    "on_behalf_of",
                    "resource_owner_principal_id",
                    "review_provenance",
                )
                if any(
                    not isinstance(b03.get(key), str) or not b03[key]
                    for key in exact_text
                ):
                    raise ValueError(
                        f"enabled provider adapter {index} has an incomplete B03 coordinate"
                    )
                if b03["provider_account_id"].casefold() != account_id.casefold():
                    raise ValueError(
                        "B03 provider account coordinate disagrees with binding"
                    )
                if b03["context_scope"] not in {"member_private", "household_shared"}:
                    raise ValueError(
                        "B03 binding requires an exact member or household scope"
                    )
                if b03["context_scope"] == "member_private" and (
                    b03["resource_owner_principal_id"] != b03["on_behalf_of"]
                ):
                    raise ValueError("B03 member-private resource owner is misaligned")
                if b03["context_scope"] == "household_shared" and (
                    b03["resource_owner_principal_id"] != "shared:household"
                ):
                    raise ValueError("B03 household resource owner is misaligned")
                if frozenset(permission_scope) != {GOOGLE_TASKS_WRITE_SCOPE}:
                    raise ValueError(
                        "B03 binding requires only the reviewed Google Tasks scope"
                    )
                if (
                    adapter_config.get("read_only") is not False
                    or adapter_config.get("enable_writes") is not True
                ):
                    raise ValueError(
                        "B03 trial binding must explicitly enable only its governed write"
                    )
                if not isinstance(b03.get("revision"), int) or b03["revision"] < 1:
                    raise ValueError(
                        "B03 binding requires a positive reviewed revision"
                    )
            if not (cos_oauth or workspace_oauth or cos_reminders) and (
                provenance != "service-account-delegation"
                or not isinstance(adapter_config.get("service_account_key_ref"), str)
            ):
                raise ValueError(
                    f"enabled provider adapter {index} requires reviewed "
                    "service-account delegation for service ownership"
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
                represented_service_principal_id=(
                    str(b03["represented_service_principal_id"]) if b03 else None
                ),
                context_scope=str(b03["context_scope"]) if b03 else None,
                on_behalf_of=str(b03["on_behalf_of"]) if b03 else None,
                resource_owner_principal_id=(
                    str(b03["resource_owner_principal_id"]) if b03 else None
                ),
                destination_id=str(b03["destination_task_list_id"]) if b03 else None,
                permitted_operations=(
                    (B03_OPERATION,) if b03 else ("send",) if cos_writes else ()
                ),
                write_binding_state=(
                    "trial"
                    if b03
                    else "governed_cos_response"
                    if cos_writes
                    else "read_only"
                ),
                binding_revision=int(b03["revision"]) if b03 else 1,
                review_provenance=str(b03["review_provenance"]) if b03 else None,
            )
        )

    adapter_ids: set[str] = set()
    exact_seen: set[tuple[str, str, str, str, str]] = set()
    ownership: dict[tuple[str, str], tuple[str, str]] = {}
    for binding in bindings:
        if binding.adapter_id in adapter_ids:
            raise ValueError("duplicate enabled adapter id")
        adapter_ids.add(binding.adapter_id)
        exact = binding.coordinate
        if exact in exact_seen:
            raise ValueError("duplicate full owner/account/adapter coordinate")
        exact_seen.add(exact)
        account = (binding.provider_family, binding.provider_account_id.casefold())
        coordinate = (binding.owner_id, binding.workspace)
        prior = ownership.setdefault(account, coordinate)
        if prior != coordinate:
            raise ValueError(
                "provider account has contradictory owner or workspace bindings"
            )
    return tuple(bindings)


def validate_provider_bindings(
    config: dict[str, Any],
    *,
    member_roles: dict[str, str],
    household_mapping: dict[str, Any],
    service_principal_ids: set[str] | None = None,
    service_principal_provider_authorizations: Mapping[str, Set[str]] | None = None,
) -> tuple[ProviderBinding, ...]:
    """Validate every enabled provider row as one explicit owned binding.

    Disabled rows may remain incomplete so optional integrations can be kept
    truthfully off. Every enabled row must carry complete binding evidence;
    fixture-only adapters bypass this production validator rather than looking
    indistinguishable from an unbound OS-permission provider.
    """
    return _validate_provider_binding_inventory(
        config,
        member_roles=member_roles,
        household_mapping=household_mapping,
        service_principal_ids=service_principal_ids,
        service_principal_provider_authorizations=(
            service_principal_provider_authorizations
        ),
    )


def validate_historical_provider_bindings(
    config: dict[str, Any],
    *,
    member_roles: dict[str, str],
    household_mapping: dict[str, Any],
    service_principal_ids: set[str] | None = None,
) -> tuple[HistoricalProviderBinding, ...]:
    """Validate active and retained member ownership solely for attribution.

    A disabled member row must retain its explicit ownership attestation and
    satisfy every ordinary binding rule. Incomplete optional rows grant no
    attribution; an attested malformed or conflicting claim fails closed.
    Validation never changes enabled flags or returns operational bindings.
    """
    bindings = _validate_provider_binding_inventory(
        config,
        member_roles=member_roles,
        household_mapping=household_mapping,
        service_principal_ids=service_principal_ids,
        include_retired_member_bindings=True,
    )
    return tuple(
        HistoricalProviderBinding(
            adapter_id=binding.adapter_id,
            kind=binding.kind,
            provider_family=binding.provider_family,
            owner_kind=binding.owner_kind,
            owner_id=binding.owner_id,
            member_id=binding.member_id,
            service_principal_id=binding.service_principal_id,
            provider_account_id=binding.provider_account_id,
            workspace=binding.workspace,
            binding_provenance=binding.binding_provenance,
        )
        for binding in bindings
    )


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


def public_service_source_readiness(
    bindings: Iterable[ProviderBinding],
    *,
    adapter_inventory: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Content-free permission, cursor, freshness, and runtime readiness."""

    inventory = adapter_inventory or {}
    rows: list[dict[str, Any]] = []
    for binding in bindings:
        if binding.owner_kind != "service_principal":
            continue
        runtime = inventory.get(binding.adapter_id) or {}
        extra = runtime.get("extra") if isinstance(runtime, Mapping) else {}
        extra = extra if isinstance(extra, Mapping) else {}
        durable = extra.get("durable_cursor")
        durable = durable if isinstance(durable, Mapping) else {}
        state = str(runtime.get("state") or "configured_not_running")
        rows.append(
            {
                "adapter_id": binding.adapter_id,
                "adapter_kind": binding.kind,
                "owner_kind": binding.owner_kind,
                "workspace": binding.workspace,
                "permission_scope": list(binding.permission_scope),
                "binding_provenance": binding.binding_provenance,
                "state": state,
                "freshness_at": runtime.get("last_cursor_advance_at"),
                "cursor_state": (
                    "present" if durable.get("provider_watermark") else "not_observed"
                ),
                "lease_state": extra.get("cursor_lease") or "not_reported",
                "ready": state == "running",
                "sending_enabled": False,
                "content_exposed": False,
            }
        )
    return {
        "contract": "service-source-readiness-v1",
        "state": (
            "not_configured"
            if not rows
            else "ready"
            if all(row["ready"] for row in rows)
            else "configured_not_ready"
        ),
        "sources": rows,
        "provider_effect_permitted": False,
        "content_exposed": False,
    }


__all__ = [
    "ACCOUNT_KEYS",
    "PROVENANCE_KINDS",
    "MEMBER_BRIDGE_ONLY_ADAPTER_KINDS",
    "SERVICE_OWNER_ADAPTER_KINDS",
    "SERVICE_OWNER_READONLY_SCOPES",
    "ProviderBinding",
    "HistoricalProviderBinding",
    "WORKSPACES",
    "adapter_owner_coordinate_id",
    "public_binding_inventory",
    "public_service_source_readiness",
    "validate_provider_bindings",
    "validate_historical_provider_bindings",
]
