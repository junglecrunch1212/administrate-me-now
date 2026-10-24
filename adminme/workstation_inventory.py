"""Private workstation coordinates composed with current runtime authority.

The optional owner-maintained identity file supplements existing account and
grant sources. It never grants access, loads a credential or asserts execution.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

INVENTORY_FILE = "workstation-identities.json"
INVENTORY_CONTRACT = "administrateme-workstation-inventory-v1"
MAX_BYTES = 256 * 1024
Text = Annotated[
    str, Field(min_length=1, max_length=300, pattern=r"^[^\x00-\x1f\x7f]+$")
]


class StrictRecord(BaseModel):
    model_config = {"extra": "forbid"}


class AccountIdentity(StrictRecord):
    account: Text
    workspace: Text | None = None
    role: Text | None = None


class WorkstationRoute(StrictRecord):
    kind: Literal[
        "browser", "native_app", "integration", "member_bridge", "local_workspace"
    ]
    device: Text
    os_user: Text
    browser_profile: Text | None = None
    native_service: Text | None = None
    connection_id: Text | None = None

    @model_validator(mode="after")
    def exact_surface(self) -> WorkstationRoute:
        if self.kind == "browser" and self.browser_profile is None:
            raise ValueError("browser identity requires an exact profile")
        if self.kind == "native_app" and self.native_service is None:
            raise ValueError("native identity requires its separately verified service")
        return self


class IdentityVerification(StrictRecord):
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    binding_revision: int = Field(ge=1)
    evidence_reference: Text

    @model_validator(mode="after")
    def bounded_validity(self) -> IdentityVerification:
        if self.valid_until <= self.observed_at:
            raise ValueError("identity evidence requires a positive validity window")
        return self


class IdentityBinding(StrictRecord):
    binding_id: Text
    revision: int = Field(ge=1)
    operating_principal_id: Text
    resource_owner_principal_id: Text
    expected_identity: AccountIdentity
    observed_identity: AccountIdentity | None = None
    route: WorkstationRoute
    verification: IdentityVerification | None = None
    supported_operations: list[Text] = Field(default_factory=list, max_length=40)
    resource_boundaries: list[Text] = Field(default_factory=list, max_length=40)
    credential_store_references: list[
        Annotated[
            str,
            Field(
                pattern=r"^(?:keychain|apple-passwords|1password)-ref:[A-Za-z0-9_./:-]{1,200}$"
            ),
        ]
    ] = Field(default_factory=list, max_length=10)
    consent_references: list[Text] = Field(default_factory=list, max_length=20)
    conversation_routes: list[Text] = Field(default_factory=list, max_length=20)
    approval_routes: list[Text] = Field(default_factory=list, max_length=20)
    limitations: list[Text] = Field(default_factory=list, max_length=20)
    recovery: Text | None = None

    @model_validator(mode="after")
    def observed_evidence(self) -> IdentityBinding:
        if (self.observed_identity is None) != (self.verification is None):
            raise ValueError(
                "observed identity and its evidence must be supplied together"
            )
        return self


class IdentityInventory(StrictRecord):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=1)
    updated_at: AwareDatetime
    bindings: list[IdentityBinding] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_bindings(self) -> IdentityInventory:
        if len({row.binding_id for row in self.bindings}) != len(self.bindings):
            raise ValueError("identity binding IDs must be unique")
        return self


def read_private_document(path: Path) -> dict[str, Any]:
    """Bounded no-follow read; reject unsafe ownership and permissions."""
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_size > MAX_BYTES
        ):
            raise ValueError("inventory must be a bounded private regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ValueError("inventory changed while reading")
    finally:
        os.close(descriptor)
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("inventory root must be an object")
    return document


def state(
    value: bool | None, basis: str, observed_at: str | None = None
) -> dict[str, Any]:
    return {"value": value, "basis": basis, "observed_at": observed_at}


def states(**values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: values.get(key, state(None, "not_verified_by_this_read"))
        for key in (
            "configured",
            "connected",
            "authenticated",
            "authorized",
            "tool_accessible",
            "executable",
            "outcome_verified",
        )
    }


def identity_bindings(
    config_dir: Path, *, allowed_owners: set[str], now: datetime
) -> dict[str, Any]:
    path = config_dir / INVENTORY_FILE
    try:
        inventory = IdentityInventory.model_validate(read_private_document(path))
    except FileNotFoundError:
        return {"state": "not_provisioned", "bindings": []}
    except (OSError, ValueError):
        # Neither parser diagnostics nor an untrusted file's content is returned.
        return {"state": "invalid_private_inventory", "bindings": []}
    rows = []
    for binding in inventory.bindings:
        if binding.resource_owner_principal_id not in allowed_owners:
            continue
        verification = binding.verification
        matching = binding.observed_identity == binding.expected_identity
        fresh = bool(
            verification
            and verification.binding_revision == binding.revision
            and verification.observed_at <= now < verification.valid_until
        )
        identity_state = (
            "unverified"
            if verification is None
            else "identity_mismatch"
            if not matching
            else "verified_record"
            if fresh
            else "stale_evidence"
        )
        rows.append(
            {
                **binding.model_dump(mode="json"),
                "identity_state": identity_state,
                "states": states(
                    configured=state(True, "owner_private_inventory"),
                    authenticated=state(
                        False if verification and not matching else None,
                        "identity_mismatch"
                        if verification and not matching
                        else "recorded_identity_is_not_current_session_authentication",
                        verification.observed_at.isoformat() if verification else None,
                    ),
                    executable=state(
                        False if identity_state != "verified_record" else None,
                        "current_tool_and_authority_check_required",
                    ),
                ),
                "external_effect_requires_current_identity_check": True,
                "grants_authority": False,
            }
        )
    return {
        "state": "available",
        "revision": inventory.revision,
        "updated_at": inventory.updated_at.isoformat(),
        "bindings": rows,
    }


def provider_bindings(runtime: Any, allowed_owners: set[str]) -> list[dict[str, Any]]:
    inventory = runtime.adapters.inventory() if runtime.adapters else {}
    rows = []
    for binding in runtime.settings.provider_bindings:
        if binding.owner_id not in allowed_owners:
            continue
        live = inventory.get(binding.adapter_id) or {}
        running = live.get("state") == "running"
        acquired = live.get("last_cursor_advance_at")
        rows.append(
            {
                "binding_id": binding.adapter_id,
                "provider": binding.provider_family,
                "resource_owner_principal_id": binding.owner_id,
                "operating_principal_id": binding.owner_id,
                "provider_account": binding.provider_account_id,
                "workspace": binding.workspace,
                "route": "service_integration"
                if binding.owner_kind == "service_principal"
                else "member_authenticated_central_integration",
                "own_account_requirement": "service_owned"
                if binding.owner_kind == "service_principal"
                else "review_delegation_or_member_bridge_transition",
                "permission_scopes": list(binding.permission_scope),
                "configured_operations": list(binding.permitted_operations),
                "destination_id": binding.destination_id,
                "write_binding_state": binding.write_binding_state,
                "binding_provenance": binding.binding_provenance,
                "revision": binding.binding_revision,
                "runtime_state": live.get("state", "unknown"),
                "states": states(
                    configured=state(True, "runtime_provider_bindings"),
                    connected=state(
                        False if live.get("state") in {"stopped", "crashed"} else None,
                        "supervisor_state_does_not_prove_current_provider_connection",
                    ),
                    authenticated=state(
                        None,
                        "acquisition_is_historical_not_a_fresh_identity_check",
                        acquired,
                    ),
                    authorized=state(
                        True,
                        "current_owner_or_read_context_delegation_for_inventory_read",
                    ),
                    outcome_verified=state(
                        True if acquired else None,
                        "last_source_acquisition_only",
                        acquired,
                    ),
                ),
                "acquisition_running": running,
                "execution_authority": "evaluate_requested_operation_separately",
            }
        )
    return rows


def bridge_bindings(runtime: Any, allowed_owners: set[str]) -> list[dict[str, Any]]:
    from adminme.apple_service_permissions import permission_options, service_permission

    rows = []
    for member in sorted(allowed_owners - {COS_SERVICE_PRINCIPAL_ID}):
        request = runtime.settings_control.bridge_request_for(member) or {}
        for provider in ("apple_calendar", "apple_reminders", "apple_notes"):
            try:
                options = permission_options(runtime, member, provider)
            except ValueError:
                continue
            grant = service_permission(runtime, member, provider)
            capability = (request.get("capability_states") or {}).get(provider) or {}
            observed = capability.get("last_successful_observation")
            rows.append(
                {
                    "provider": provider,
                    "resource_owner_principal_id": member,
                    "operating_principal_id": member,
                    "worker_principal_id": COS_SERVICE_PRINCIPAL_ID,
                    "route": "authenticated_member_bridge",
                    "device_identity_sha256": options["enrollment"][
                        "device_identity_sha256"
                    ],
                    "enrollment_id": options["enrollment"]["enrollment_id"],
                    "available_resource_scopes": options["scopes"],
                    "allowed_write_scopes": (grant or {}).get(
                        "allowed_container_ids", []
                    ),
                    "allowed_write_operations": (grant or {}).get("operations", []),
                    "permission_event_id": (grant or {}).get("event_id"),
                    "permission_revision": (grant or {}).get("revision"),
                    "states": states(
                        configured=state(
                            True, "current_enrollment_and_native_permission"
                        ),
                        authenticated=state(
                            None, "paired_enrollment_does_not_prove_current_transport"
                        ),
                        authorized=state(
                            bool(grant), "current_provider_write_permission"
                        ),
                        executable=state(
                            False if not grant or runtime._observation_active else None,
                            "current_grant_and_observation_then_request_execution_check",
                        ),
                        outcome_verified=state(
                            True if observed else None,
                            "historical_source_observation_only",
                            observed,
                        ),
                    ),
                    "status_tool": "administrateme_apple_reminder_status"
                    if provider == "apple_reminders"
                    else None,
                }
            )
    return rows


GENERAL_TOOLS = {
    "browser": ["browser"],
    "native_apps": ["nodes", "exec"],
    "files": ["read", "write", "edit", "apply_patch"],
    "commands": ["exec", "process"],
    "search": ["web_search", "web_fetch"],
    "memory": ["memory_search", "memory_get"],
    "timers": ["automations"],
}


def workstation_tools(runtime: Any, agent_id: str) -> dict[str, Any]:
    """Report native policy without approximating OpenClaw's policy engine."""
    path = runtime.settings.openclaw_runtime_config_path
    try:
        config = read_private_document(path) if path else {}
        agents = config.get("agents", {}).get("list", [])
        matches = [row for row in agents if row.get("id") == agent_id]
        agent = matches[0] if len(matches) == 1 else {}
        tools = agent.get("tools") or {}
        allow = tools.get("allow")
        deny = tools.get("deny", [])
        global_deny = config.get("tools", {}).get("deny", [])
        also_allow = tools.get("alsoAllow", [])
        if any(
            not isinstance(value, list) or any(not isinstance(x, str) for x in value)
            for value in (deny, global_deny, also_allow)
        ) or (
            allow is not None
            and (
                not isinstance(allow, list)
                or any(not isinstance(x, str) for x in allow)
            )
        ):
            raise ValueError("malformed native tool policy")
        aliases = {"cron": "automations", "bash": "exec", "apply-patch": "apply_patch"}

        def normalized(values: list[str]) -> list[str]:
            return [
                aliases.get(value.strip().lower(), value.strip().lower())
                for value in values
            ]

        deny, global_deny = normalized(deny), normalized(global_deny)
        if isinstance(allow, list):
            allow = normalized([*allow, *also_allow])
    except (OSError, ValueError, AttributeError, TypeError):
        return {"state": "native_policy_unavailable", "capabilities": []}
    rows = []
    for capability, names in GENERAL_TOOLS.items():
        observations = []
        for name in names:
            explicit_deny = (
                name in deny or name in global_deny or "*" in deny or "*" in global_deny
            )
            # A literal closed allowlist can prove absence. Groups, globs and
            # provider policy remain OpenClaw's job; presence never proves load.
            closed = (
                isinstance(allow, list)
                and bool(allow)
                and not any(
                    "*" in entry or entry.startswith("group:") for entry in allow
                )
            )
            absent = explicit_deny or bool(closed and name not in (allow or []))
            observations.append(
                {
                    "tool": name,
                    "states": states(
                        configured=state(bool(agent), "native_agent_configuration"),
                        tool_accessible=state(
                            False if absent else None,
                            "explicit_native_tool_policy"
                            if absent
                            else "native_loaded_catalog_required",
                        ),
                        executable=state(
                            False if absent else None,
                            "tool_and_operation_authority_required",
                        ),
                    ),
                }
            )
        rows.append({"capability": capability, "tools": observations})
    return {
        "state": "policy_read",
        "agent_id": agent_id,
        "capabilities": rows,
        "loaded_catalog_verified": False,
        "policy_engine": "OpenClaw",
    }


def compose_inventory(
    runtime: Any, *, allowed_owners: set[str], agent_id: str
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "contract": INVENTORY_CONTRACT,
        "as_of": now.isoformat(),
        "worker_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "identity_inventory": identity_bindings(
            runtime.settings.instance.config_dir, allowed_owners=allowed_owners, now=now
        ),
        "provider_bindings": provider_bindings(runtime, allowed_owners),
        "member_bridges": bridge_bindings(runtime, allowed_owners),
        "workstation_tools": workstation_tools(runtime, agent_id),
        "observation_mode": bool(runtime._observation_active),
        "inventory_grants_authority": False,
        "provider_effect_count": 0,
        "provider_effect_count_scope": "this_inventory_read",
        "refresh": "Read again after account, permission, deployment or capability changes; recheck destination identity before external effects.",
    }
