"""Exact, separately authorized personal Google connections beside CoS intake.

Gmail, Calendar and Tasks may retain explicitly authorized read/write access
under existing write governance. Automatic candidate extraction
is disabled. The guarded configuration operation publishes each selection.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

CONFIG_KEY = "member_google_connections"
CONTRACT = "member-google-connections-v1"
SCOPES = {
    "messaging-gmail-api": "https://www.googleapis.com/auth/gmail.readonly",
    "calendaring-google-calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "tasks-google-tasks": "https://www.googleapis.com/auth/tasks.readonly",
}
SOURCES = {
    "messaging-gmail-api": "adapter:gmail",
    "calendaring-google-calendar": "adapter:google_calendar",
    "tasks-google-tasks": "adapter:google_tasks",
}
Access = Literal["read_only", "read_write"]
WRITE_SCOPES = {
    kind: scope.removesuffix(".readonly")
    for kind, scope in SCOPES.items()
    if kind != "messaging-gmail-api"
}
# Gmail needs two write scopes; it is normalized separately below.
MAIL_CONTROLS: dict[str, Any] = {
    "initial_backfill_days": 0,
    "initial_backfill_max_messages": 0,
    "historical_seed_queries": [],
    "historical_seed_max_messages_per_query": 0,
    "historical_related_max_participants": 0,
    "historical_related_messages_per_participant": 0,
}


class GoogleConnectionSelection(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    adapter_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=500)
    access: Access = "read_only"
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoogleConnectionPolicy(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract: Literal["member-google-connections-v1"] = "member-google-connections-v1"
    selections: list[GoogleConnectionSelection] = Field(max_length=30)
    writes_require_existing_governance: Literal[True] = True
    automated_candidate_extraction: Literal[False] = False
    historical_expansion: Literal[False] = False

    @model_validator(mode="after")
    def unique_adapters(self) -> GoogleConnectionPolicy:
        if len({row.adapter_id for row in self.selections}) != len(self.selections):
            raise ValueError("Google connection selections must be unique")
        return self


def policy_from_config(value: dict[str, Any]) -> GoogleConnectionPolicy | None:
    if CONFIG_KEY not in value:
        return None
    if "cos_channel_acquisition" not in value:
        raise ValueError(
            "member Google connection requires the CoS acquisition ceiling"
        )
    return GoogleConnectionPolicy.model_validate(value[CONFIG_KEY])


def connection_config(
    entry: dict[str, Any], *, access: Access = "read_only"
) -> dict[str, Any]:
    kind = str(entry.get("kind") or "").removeprefix("adapter:")
    if kind not in SCOPES:
        raise ValueError(
            "member Google connection only supports reviewed Google connections"
        )
    if access not in {"read_only", "read_write"}:
        raise ValueError("unsupported Google connection access")
    scope = (
        WRITE_SCOPES.get(kind, SCOPES[kind]) if access == "read_write" else SCOPES[kind]
    )
    config = copy.deepcopy(entry.get("config") or {})
    config.update(read_only=access == "read_only", permission_scope=[scope])
    if kind == "messaging-gmail-api":
        config["enable_writes"] = access == "read_write"
        if access == "read_write":
            config["permission_scope"] = [
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
            ]
        config.update(copy.deepcopy(MAIL_CONTROLS))
    else:
        config.pop("enable_writes", None)
    return config


def binding_digest(entry: dict[str, Any], *, access: Access = "read_only") -> str:
    """Bind every provider/config coordinate and the selected access.

    Pause/resume is deliberately outside this digest. Normalization is idempotent,
    so both the requested and installed configurations have the same binding.
    """
    document = {
        "id": entry.get("id"),
        "kind": str(entry.get("kind") or "").removeprefix("adapter:"),
        "config": connection_config(entry, access=access),
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def selected_entries(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policy = policy_from_config(value)
    if policy is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for selection in policy.selections:
        matches = [
            row
            for row in value.get("adapters", [])
            if isinstance(row, dict) and row.get("id") == selection.adapter_id
        ]
        if len(matches) != 1:
            raise ValueError("Google connection requires one exact existing adapter")
        entry = matches[0]
        config = entry.get("config") or {}
        if (
            config.get("member_id") != selection.member_id
            or config.get("provider_account_id") != selection.provider_account_id
            or config.get("user_email", selection.provider_account_id)
            != selection.provider_account_id
            or config.get("service_principal_id")
            or config.get("service_account_key_ref")
            or config.get("ownership_attested") is not True
            or config.get("binding_provenance") != "oauth-subject"
            or not isinstance(config.get("oauth_credentials_ref"), str)
            or not config["oauth_credentials_ref"].startswith("keychain://")
            or "disposable_test_acquisition" in config
            or config.get("b03_google_tasks_create") is not None
            or binding_digest(entry, access=selection.access)
            != selection.binding_sha256
        ):
            raise ValueError(
                "Google connection owner/account/configuration binding mismatch"
            )
        result[selection.adapter_id] = {
            **copy.deepcopy(entry),
            "config": connection_config(entry, access=selection.access),
        }
    return result


def manual_sources(value: dict[str, Any]) -> frozenset[tuple[str, str]]:
    """Keep the legacy source-family candidate seam closed for selected owners."""
    return frozenset(
        (entry["config"]["member_id"], SOURCES[entry["kind"].removeprefix("adapter:")])
        for entry in selected_entries(value).values()
    )


def selected_source_entry(
    entries: dict[str, dict[str, Any]],
    *,
    owner_id: str,
    account_id: str,
    adapter_id: str,
    kind: str,
) -> dict[str, Any] | None:
    """Match configured coordinates and existing Google pack event provenance.

    Current Google envelopes name the pack, not its configured instance. Bind
    that legacy form to one exact selected owner/account/kind without treating
    an unrelated explicit instance id as an alias.
    """
    matches = [
        entry
        for entry in entries.values()
        if entry["config"]["member_id"] == owner_id
        and entry["config"]["provider_account_id"] == account_id
        and entry["kind"].removeprefix("adapter:") == kind
        and adapter_id in {entry["id"], kind}
    ]
    return matches[0] if len(matches) == 1 else None


def validate_authorized_change(
    previous: dict[str, Any],
    requested: dict[str, Any],
    *,
    owner_member_id: str,
    owner_authorization_sha256: str,
) -> None:
    """A principal may add/revoke only their own exact selections."""
    before = policy_from_config(previous)
    after = policy_from_config(requested)
    old = {row.adapter_id: row for row in before.selections} if before else {}
    new = {row.adapter_id: row for row in after.selections} if after else {}
    selected_entries(requested)
    previous_entries = {
        entry.get("id"): entry for entry in previous.get("adapters", [])
    }
    requested_entries = {
        entry.get("id"): entry for entry in requested.get("adapters", [])
    }
    for adapter_id in old.keys() | new.keys():
        prior, current = old.get(adapter_id), new.get(adapter_id)
        enabled_changed = (
            previous_entries.get(adapter_id, {}).get("enabled") is not False
        ) != (requested_entries.get(adapter_id, {}).get("enabled") is not False)
        if prior == current and not enabled_changed:
            continue
        if prior is not None and prior.member_id != owner_member_id:
            raise ValueError(
                "cannot change another member's Google connection selection"
            )
        if current is not None and (
            current.member_id != owner_member_id
            or current.owner_authorization_sha256 != owner_authorization_sha256
        ):
            raise ValueError(
                "Google connection requires this member's exact authorization"
            )
