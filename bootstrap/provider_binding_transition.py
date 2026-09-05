"""Deterministic transition for legacy attribution and read-only enrollment.

The predecessor may be unable to parse the strengthened provider-binding
fields, so production never writes the prepared document while it is active.
Candidate readiness hashes the pure result and the crash-journaled updater
publishes it only after the predecessor is stopped.  Recovery restores the
exact predecessor bytes before attempting to restart that release. One exact,
already stored principal-bound personal Google grant may add the three reviewed
read-only adapters in the same transaction.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from adminme.provider_bindings import validate_provider_bindings
from adminme.service_identities import validate_service_identity_config
from bootstrap.contracts import DeploymentPaths
from bootstrap.google_oauth_contract import (
    GOOGLE_READONLY_ADAPTER_SCOPES,
    credential_member_digest,
    member_google_secret_name,
)
from bootstrap.secrets import reference_for, secret_store


TRANSITION_CONTRACT = "provider-binding-config-transition-v1"
ENROLLMENT_TRANSITION_CONTRACT = "provider-binding-config-transition-v2"

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
_TRANSITION_COMMON_FIELDS = frozenset(
    {
        "contract",
        "source_sha256",
        "prepared_sha256",
        "migrated_adapter_count",
        "credentials_changed",
        "capabilities_changed",
        "content_exposed",
    }
)


class ProviderBindingTransitionError(RuntimeError):
    """The adapter configuration cannot be transitioned exactly."""


class NoEligibleProviderBindingTransitionError(ProviderBindingTransitionError):
    """The reviewed provider-binding migration is already fully applied."""


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def valid_provider_binding_transition(value: object) -> bool:
    """Validate the exact content-free v1 or v2 public transition contract."""

    if not isinstance(value, dict):
        return False
    if (
        not _is_sha256(value.get("source_sha256"))
        or not _is_sha256(value.get("prepared_sha256"))
        or value.get("source_sha256") == value.get("prepared_sha256")
        or type(value.get("migrated_adapter_count")) is not int
        or value["migrated_adapter_count"] < 0
        or value.get("credentials_changed") is not False
        or value.get("content_exposed") is not False
    ):
        return False
    if value.get("contract") == TRANSITION_CONTRACT:
        return (
            set(value) == _TRANSITION_COMMON_FIELDS
            and value["migrated_adapter_count"] > 0
            and value.get("capabilities_changed") is False
        )
    return (
        value.get("contract") == ENROLLMENT_TRANSITION_CONTRACT
        and set(value)
        == _TRANSITION_COMMON_FIELDS
        | {
            "enrolled_adapter_count",
            "enrollment_profile",
            "observation_required",
        }
        and type(value.get("enrolled_adapter_count")) is int
        and value["enrolled_adapter_count"] == 3
        and value.get("enrollment_profile") == "personal-google-readonly"
        and value.get("observation_required") is True
        and value.get("capabilities_changed") is True
    )


def _google_rows_for_member(
    adapters: list[dict[str, Any]], member_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in adapters
        if str(item.get("kind") or "").removeprefix("adapter:") in _GOOGLE_KINDS
        and isinstance(item.get("config"), dict)
        and item["config"].get("member_id") == member_id
    ]


def discover_readonly_google_enrollment(
    paths: DeploymentPaths,
    source: Mapping[str, Any],
    *,
    household_mapping: Mapping[str, Any],
    test_mode: bool = False,
) -> dict[str, Any] | None:
    """Find exactly one unattached principal-bound exact read-only Google grant."""

    adapters = source.get("adapters")
    members = household_mapping.get("members")
    if not isinstance(adapters, list) or any(
        not isinstance(item, dict) for item in adapters
    ):
        raise ProviderBindingTransitionError("adapter inventory is invalid")
    if not isinstance(members, list):
        raise ProviderBindingTransitionError("household member inventory is invalid")
    store = secret_store(paths, test_mode=test_mode)
    candidates: list[dict[str, Any]] = []
    for member in members:
        if (
            not isinstance(member, dict)
            or member.get("role") != "principal"
            or not isinstance(member.get("member_id"), str)
            or not member["member_id"].strip()
        ):
            continue
        member_id = member["member_id"]
        owned_rows = _google_rows_for_member(adapters, member_id)
        if owned_rows:
            kinds = {
                str(item.get("kind") or "").removeprefix("adapter:")
                for item in owned_rows
            }
            if kinds != _GOOGLE_KINDS or len(owned_rows) != len(_GOOGLE_KINDS):
                raise ProviderBindingTransitionError(
                    "existing member Google adapter inventory is incomplete or ambiguous"
                )
            continue
        reference = reference_for(
            paths,
            member_google_secret_name(member_id),
            test_mode=test_mode,
        )
        try:
            credential_payload = store.get(reference)
        except RuntimeError:
            continue
        try:
            document = json.loads(credential_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderBindingTransitionError(
                "member Google credential document is invalid"
            ) from exc
        if not isinstance(document, dict):
            raise ProviderBindingTransitionError(
                "member Google credential document is invalid"
            )
        account = document.get("provider_account_id")
        scopes = document.get("scopes")
        if (
            document.get("owner_member_digest") != credential_member_digest(member_id)
            or not isinstance(account, str)
            or not account.strip()
            or not isinstance(scopes, list)
            or any(not isinstance(scope, str) for scope in scopes)
            or set(scopes) != set(GOOGLE_READONLY_ADAPTER_SCOPES)
            or len(scopes) != len(GOOGLE_READONLY_ADAPTER_SCOPES)
            or any(
                not isinstance(document.get(key), str) or not str(document[key]).strip()
                for key in (
                    "client_id",
                    "client_secret",
                    "refresh_token",
                    "token_uri",
                )
            )
        ):
            raise ProviderBindingTransitionError(
                "member Google credential binding or grant is invalid"
            )
        candidates.append(
            {
                "member_id": member_id,
                "provider_account_id": account.strip().casefold(),
                "oauth_credentials_ref": reference,
                "permission_scope": sorted(scopes),
            }
        )
    if len(candidates) > 1:
        raise ProviderBindingTransitionError(
            "multiple unattached read-only Google grants require separate review"
        )
    return candidates[0] if candidates else None


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
            _CALENDAR_READONLY if config.get("read_only") is True else _CALENDAR_FULL
        ]
    if kind == "tasks-google-tasks":
        return [_TASKS_READONLY if config.get("read_only") is True else _TASKS_FULL]
    raise ProviderBindingTransitionError("unsupported provider-binding migration kind")


def _service_principal_ids(
    source: Mapping[str, Any], household_mapping: Mapping[str, Any]
) -> set[str]:
    member_ids = {
        str(member["member_id"])
        for member in household_mapping.get("members", [])
        if isinstance(member, dict) and isinstance(member.get("member_id"), str)
    }
    household_party_ids = set(member_ids)
    household_party_ids.update(
        str(party["party_id"])
        for party in household_mapping.get("parties", [])
        if isinstance(party, dict) and isinstance(party.get("party_id"), str)
    )
    principals, _channels = validate_service_identity_config(
        source.get("service_identities"),
        member_ids=member_ids,
        household_party_ids=household_party_ids,
    )
    return {principal.service_principal_id for principal in principals}


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


def _workspace_by_owner_account(
    adapters: list[dict[str, Any]],
) -> dict[tuple[str, str], str]:
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
        if _BINDING_FIELDS.issubset(config):
            # Already-bound adapters are validated as part of the complete
            # prepared inventory below.  In particular, service-owned Google
            # adapters intentionally have a service principal instead of a
            # member owner and are not legacy member bindings to migrate.
            continue
        member = config.get("member_id")
        if not isinstance(member, str) or not member.strip():
            raise ProviderBindingTransitionError(
                "legacy Google adapter has no member owner"
            )
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
        and not _BINDING_FIELDS.issubset(item["config"])
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
    enrolled_adapter_count: int = 0

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "contract": (
                ENROLLMENT_TRANSITION_CONTRACT
                if self.enrolled_adapter_count
                else TRANSITION_CONTRACT
            ),
            "source_sha256": hashlib.sha256(self.source_bytes).hexdigest(),
            "prepared_sha256": hashlib.sha256(self.prepared_bytes).hexdigest(),
            "migrated_adapter_count": self.migrated_adapter_count,
            "credentials_changed": False,
            "capabilities_changed": bool(self.enrolled_adapter_count),
            "content_exposed": False,
        }
        if self.enrolled_adapter_count:
            result.update(
                {
                    "enrolled_adapter_count": self.enrolled_adapter_count,
                    "enrollment_profile": "personal-google-readonly",
                    "observation_required": True,
                }
            )
        return result


def _readonly_google_adapter_entries(
    enrollment: Mapping[str, Any],
) -> list[dict[str, Any]]:
    member_id = enrollment.get("member_id")
    account = enrollment.get("provider_account_id")
    reference = enrollment.get("oauth_credentials_ref")
    scopes = enrollment.get("permission_scope")
    if (
        not isinstance(member_id, str)
        or not member_id.strip()
        or not isinstance(account, str)
        or not account.strip()
        or not isinstance(reference, str)
        or not reference.startswith(("keychain://", "test-secret://"))
        or not isinstance(scopes, list)
        or any(not isinstance(scope, str) for scope in scopes)
        or set(scopes) != set(GOOGLE_READONLY_ADAPTER_SCOPES)
        or len(scopes) != len(GOOGLE_READONLY_ADAPTER_SCOPES)
    ):
        raise ProviderBindingTransitionError(
            "read-only Google enrollment request is invalid"
        )
    member_id = member_id.strip()
    account = account.strip().casefold()
    digest = credential_member_digest(member_id)
    common = {
        "member_id": member_id,
        "provider_account_id": account,
        "user_email": account,
        "workspace": "personal",
        "binding_provenance": "oauth-subject",
        "ownership_attested": True,
        "oauth_credentials_ref": reference,
    }
    return [
        {
            "id": f"google-gmail-{digest}",
            "kind": "messaging-gmail-api",
            "enabled": True,
            "config": {
                **common,
                "permission_scope": [_GMAIL_READONLY],
                "account_label": "Personal Gmail",
                "enable_writes": False,
            },
        },
        {
            "id": f"google-calendar-{digest}",
            "kind": "calendaring-google-calendar",
            "enabled": True,
            "config": {
                **common,
                "permission_scope": [_CALENDAR_READONLY],
                "account_label": "Personal Google Calendar",
                "read_only": True,
                "calendar_id": "primary",
                "discover_calendars": True,
            },
        },
        {
            "id": f"google-tasks-{digest}",
            "kind": "tasks-google-tasks",
            "enabled": True,
            "config": {
                **common,
                "permission_scope": [_TASKS_READONLY],
                "account_label": "Personal Google Tasks",
                "read_only": True,
                "tasklist_id": "@default",
                "include_all_tasklists": False,
            },
        },
    ]


def prepare_provider_binding_transition(
    source: Mapping[str, Any],
    *,
    household_mapping: Mapping[str, Any],
    ownership_reviewed: bool,
    source_payload: bytes | None = None,
    readonly_google_enrollment: Mapping[str, Any] | None = None,
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
        if _BINDING_FIELDS.issubset(config):
            continue
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

    enrolled_entries: list[dict[str, Any]] = []
    if readonly_google_enrollment is not None:
        enrolled_entries = _readonly_google_adapter_entries(readonly_google_enrollment)
        member_id = str(readonly_google_enrollment["member_id"])
        roles = {
            str(member["member_id"]): str(member["role"])
            for member in household_mapping.get("members", [])
            if isinstance(member, dict)
            and isinstance(member.get("member_id"), str)
            and isinstance(member.get("role"), str)
        }
        if roles.get(member_id) != "principal":
            raise ProviderBindingTransitionError(
                "read-only Google enrollment owner is not a principal"
            )
        if _google_rows_for_member(prepared_adapters, member_id):
            raise ProviderBindingTransitionError(
                "read-only Google enrollment would duplicate an existing binding"
            )
        existing_ids = {
            str(item.get("id"))
            for item in prepared_adapters
            if isinstance(item.get("id"), str)
        }
        if existing_ids.intersection(str(item["id"]) for item in enrolled_entries):
            raise ProviderBindingTransitionError(
                "read-only Google enrollment adapter id already exists"
            )
        prepared_adapters.extend(enrolled_entries)

    if migrated == 0 and not enrolled_entries:
        raise NoEligibleProviderBindingTransitionError(
            "provider-binding transition has no eligible changes"
        )
    source_adapters = source_document["adapters"]
    if len(prepared_adapters) != len(source_adapters) + len(enrolled_entries):
        raise ProviderBindingTransitionError(
            "provider-binding transition changed the adapter inventory unexpectedly"
        )
    for before, after in zip(
        source_adapters,
        prepared_adapters[: len(source_adapters)],
        strict=True,
    ):
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
        service_principal_ids=_service_principal_ids(prepared, household_mapping),
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
        enrolled_adapter_count=len(enrolled_entries),
    )


def prepare_live_provider_binding_transition(
    paths: DeploymentPaths,
    source: Mapping[str, Any],
    *,
    household_mapping: Mapping[str, Any],
    ownership_reviewed: bool,
    source_payload: bytes | None = None,
    test_mode: bool = False,
) -> PreparedProviderBindingTransition:
    """Prepare legacy attribution plus one exact unattached read-only grant."""

    enrollment = discover_readonly_google_enrollment(
        paths,
        source,
        household_mapping=household_mapping,
        test_mode=test_mode,
    )
    return prepare_provider_binding_transition(
        source,
        household_mapping=household_mapping,
        ownership_reviewed=ownership_reviewed,
        source_payload=source_payload,
        readonly_google_enrollment=enrollment,
    )


__all__ = [
    "NoEligibleProviderBindingTransitionError",
    "PreparedProviderBindingTransition",
    "ProviderBindingTransitionError",
    "TRANSITION_CONTRACT",
    "ENROLLMENT_TRANSITION_CONTRACT",
    "discover_readonly_google_enrollment",
    "prepare_live_provider_binding_transition",
    "prepare_provider_binding_transition",
    "valid_provider_binding_transition",
]
