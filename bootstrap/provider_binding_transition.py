"""Deterministic, additive migration for legacy central Google bindings.

The predecessor may be unable to parse the strengthened provider-binding
fields, so production never writes the prepared document while it is active.
Candidate readiness hashes the pure result and the crash-journaled updater
publishes it only after the predecessor is stopped.  Recovery restores the
exact predecessor bytes before attempting to restart that release.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from adminme.provider_bindings import validate_provider_bindings


TRANSITION_CONTRACT = "provider-binding-config-transition-v1"

_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
_GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
_CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
_CALENDAR_FULL = "https://www.googleapis.com/auth/calendar"
_TASKS_READONLY = "https://www.googleapis.com/auth/tasks.readonly"
_TASKS_FULL = "https://www.googleapis.com/auth/tasks"

_GOOGLE_KINDS = frozenset(
    {
        "messaging-gmail-api",
        "calendaring-google-calendar",
        "tasks-google-tasks",
    }
)
_BINDING_FIELDS = frozenset(
    {
        "provider_account_id",
        "workspace",
        "permission_scope",
        "binding_provenance",
        "ownership_attested",
    }
)


class ProviderBindingTransitionError(RuntimeError):
    """The legacy adapter configuration cannot be migrated exactly."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _account_coordinate(config: Mapping[str, Any]) -> str:
    values = [
        value.strip()
        for key in (
            "provider_account_id",
            "user_email",
            "subject_email",
            "source_account_id",
        )
        if isinstance((value := config.get(key)), str) and value.strip()
    ]
    normalized = {value.casefold() for value in values}
    if not values or len(normalized) != 1:
        raise ProviderBindingTransitionError(
            "legacy provider account coordinates are missing or contradictory"
        )
    return values[0]


def _permission_scope(kind: str, config: Mapping[str, Any]) -> list[str]:
    if kind == "messaging-gmail-api":
        return (
            [_GMAIL_MODIFY, _GMAIL_SEND]
            if config.get("enable_writes") is True
            else [_GMAIL_READONLY]
        )
    if kind == "calendaring-google-calendar":
        return [
            _CALENDAR_READONLY
            if config.get("read_only") is True
            else _CALENDAR_FULL
        ]
    if kind == "tasks-google-tasks":
        return [_TASKS_READONLY if config.get("read_only") is True else _TASKS_FULL]
    raise ProviderBindingTransitionError("unsupported provider-binding migration kind")


def _provenance(config: Mapping[str, Any]) -> str:
    oauth = config.get("oauth_credentials_ref")
    service = config.get("service_account_key_ref")
    if isinstance(oauth, str) and oauth.startswith(("keychain://", "op://")):
        if service is not None:
            raise ProviderBindingTransitionError(
                "legacy provider binding has multiple credential authorities"
            )
        return "oauth-subject"
    if isinstance(service, str) and service.startswith("op://"):
        return "service-account-delegation"
    raise ProviderBindingTransitionError(
        "legacy provider binding has no reviewed secure credential reference"
    )


def _workspace_by_owner_account(adapters: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    candidates: dict[tuple[str, str], set[str]] = {}
    for item in adapters:
        if item.get("enabled") is False:
            continue
        kind = str(item.get("kind") or "").removeprefix("adapter:")
        if kind not in _GOOGLE_KINDS:
            continue
        config = item.get("config")
        if not isinstance(config, dict):
            raise ProviderBindingTransitionError("legacy adapter config is malformed")
        member = config.get("member_id")
        if not isinstance(member, str) or not member.strip():
            raise ProviderBindingTransitionError("legacy Google adapter has no member owner")
        account = _account_coordinate(config).casefold()
        workspace = config.get("workspace")
        if workspace in {"personal", "work"}:
            candidates.setdefault((member, account), set()).add(str(workspace))
        elif workspace is not None:
            raise ProviderBindingTransitionError("legacy Google workspace is invalid")
    result: dict[tuple[str, str], str] = {}
    for coordinate in {
        (
            str(item["config"].get("member_id") or ""),
            _account_coordinate(item["config"]).casefold(),
        )
        for item in adapters
        if item.get("enabled") is not False
        and str(item.get("kind") or "").removeprefix("adapter:") in _GOOGLE_KINDS
        and isinstance(item.get("config"), dict)
    }:
        workspaces = candidates.get(coordinate, set())
        if len(workspaces) != 1:
            raise ProviderBindingTransitionError(
                "legacy Google workspace cannot be inherited unambiguously"
            )
        result[coordinate] = next(iter(workspaces))
    return result


@dataclass(frozen=True)
class PreparedProviderBindingTransition:
    source: dict[str, Any]
    prepared: dict[str, Any]
    source_bytes: bytes
    prepared_bytes: bytes
    migrated_adapter_count: int

    def public(self) -> dict[str, Any]:
        return {
            "contract": TRANSITION_CONTRACT,
            "source_sha256": hashlib.sha256(self.source_bytes).hexdigest(),
            "prepared_sha256": hashlib.sha256(self.prepared_bytes).hexdigest(),
            "migrated_adapter_count": self.migrated_adapter_count,
            "credentials_changed": False,
            "capabilities_changed": False,
            "content_exposed": False,
        }


def prepare_provider_binding_transition(
    source: Mapping[str, Any],
    *,
    household_mapping: Mapping[str, Any],
    ownership_reviewed: bool,
    source_payload: bytes | None = None,
) -> PreparedProviderBindingTransition:
    """Return the exact candidate-readable document without touching live state."""

    if ownership_reviewed is not True:
        raise ProviderBindingTransitionError(
            "provider ownership must be explicitly reviewed before migration"
        )
    if not isinstance(source, Mapping):
        raise ProviderBindingTransitionError("adapter configuration must be an object")
    source_document = deepcopy(dict(source))
    canonical_source = _canonical(source_document)
    if source_payload is not None:
        try:
            decoded_source = json.loads(source_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderBindingTransitionError(
                "adapter configuration payload is invalid"
            ) from exc
        if decoded_source != source_document:
            raise ProviderBindingTransitionError(
                "adapter configuration payload changed before migration"
            )
    adapters = source_document.get("adapters")
    if not isinstance(adapters, list):
        raise ProviderBindingTransitionError("adapter inventory must be a list")
    if any(not isinstance(item, dict) for item in adapters):
        raise ProviderBindingTransitionError("adapter inventory contains a non-object")

    prepared = deepcopy(source_document)
    prepared_adapters = prepared["adapters"]
    workspaces = _workspace_by_owner_account(prepared_adapters)
    migrated = 0
    for item in prepared_adapters:
        if item.get("enabled") is False:
            continue
        kind = str(item.get("kind") or "").removeprefix("adapter:")
        if kind not in _GOOGLE_KINDS:
            continue
        config = item.get("config")
        if not isinstance(config, dict):
            raise ProviderBindingTransitionError("legacy adapter config is malformed")
        member = str(config.get("member_id") or "")
        account = _account_coordinate(config)
        desired: dict[str, Any] = {
            "provider_account_id": account,
            "workspace": workspaces[(member, account.casefold())],
            "permission_scope": _permission_scope(kind, config),
            "binding_provenance": _provenance(config),
            "ownership_attested": True,
        }
        changed = False
        for key, value in desired.items():
            if key in config and config[key] != value:
                raise ProviderBindingTransitionError(
                    f"existing {key} contradicts the deterministic migration"
                )
            if key not in config:
                config[key] = value
                changed = True
        if changed:
            migrated += 1

    if migrated == 0:
        raise ProviderBindingTransitionError(
            "provider-binding migration has no eligible legacy adapters"
        )
    source_adapters = source_document["adapters"]
    for before, after in zip(source_adapters, prepared_adapters, strict=True):
        before_config = before.get("config") or {}
        after_config = after.get("config") or {}
        if any(after_config.get(key) != value for key, value in before_config.items()):
            raise ProviderBindingTransitionError(
                "provider-binding migration would change an existing field"
            )
        if set(after_config) - set(before_config) - _BINDING_FIELDS:
            raise ProviderBindingTransitionError(
                "provider-binding migration would add an unreviewed field"
            )

    roles = {
        str(member["member_id"]): str(member["role"])
        for member in household_mapping.get("members", [])
        if isinstance(member, dict)
        and isinstance(member.get("member_id"), str)
        and isinstance(member.get("role"), str)
    }
    bindings = validate_provider_bindings(
        prepared,
        member_roles=roles,
        household_mapping=dict(household_mapping),
    )
    enabled_google = sum(
        item.get("enabled") is not False
        and str(item.get("kind") or "").removeprefix("adapter:") in _GOOGLE_KINDS
        for item in prepared_adapters
    )
    if len(bindings) < enabled_google:
        raise ProviderBindingTransitionError(
            "prepared provider-binding inventory is incomplete"
        )
    return PreparedProviderBindingTransition(
        source=source_document,
        prepared=prepared,
        source_bytes=source_payload if source_payload is not None else canonical_source,
        prepared_bytes=_canonical(prepared),
        migrated_adapter_count=migrated,
    )


__all__ = [
    "PreparedProviderBindingTransition",
    "ProviderBindingTransitionError",
    "TRANSITION_CONTRACT",
    "prepare_provider_binding_transition",
]
