"""Additive, member-owned Apple write authority; enrollment remains read-only.

The immutable enrollment authorizes acquisition. A separate revisioned grant
authorizes selected provider actions, and every command must bind the grant
that existed when its member approval was made. A grant never releases an old
queue. The bridge obtains a fresh, command-bound proof immediately before use.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from adminme.bridge_authorization import (
    BRIDGE_PACK_POLICIES,
    BridgePackPolicy,
    authorization_from_record,
)


EVENT_TYPE = "settings.apple_service_permission_controlled"
CONTRACT = "administrateme-apple-service-permission-v1"
PROOF_TTL_SECONDS = 15
PROVIDERS = ("apple_calendar", "apple_reminders", "apple_notes")
PACKS = {
    "apple_calendar": "calendaring-apple-calendar-bridge",
    "apple_reminders": "reminders-apple-eventkit-bridge",
    "apple_notes": "knowledge-apple-notes",
}
OPERATIONS = {
    "apple_calendar": ["create_event", "delete_event", "move_event", "update_event"],
    "apple_reminders": [
        "complete_reminder",
        "create_reminder",
        "delete_reminder",
        "move_reminder",
        "uncomplete_reminder",
        "update_reminder",
    ],
    "apple_notes": ["append_note", "create_note", "delete_note", "update_note"],
}
NOTES_WRITE_POLICY = BridgePackPolicy(
    pack="knowledge-apple-notes",
    source_adapter="knowledge-apple-notes",
    family="knowledge",
    provider="apple_notes",
    provider_family="apple_notes",
    event_types=frozenset(),
    container_key="folder_external_id",
    write_operations=frozenset(OPERATIONS["apple_notes"]),
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def write_policy(provider: str) -> BridgePackPolicy | None:
    if provider == "apple_notes":
        return NOTES_WRITE_POLICY
    pack = PACKS.get(provider)
    return BRIDGE_PACK_POLICIES.get(pack) if pack else None


def _record(runtime: Any, member_id: str) -> dict[str, Any]:
    records = [
        record
        for record in runtime.settings.bridge_enrollments.values()
        if isinstance(record, dict)
        and record.get("status") == "active"
        and record.get("member_id") == member_id
    ]
    if len(records) != 1:
        raise ValueError("Your active Apple bridge is missing or ambiguous")
    record = records[0]
    if authorization_from_record(record) is None:
        raise ValueError("Your Apple bridge permissions need attention")
    if (
        re.fullmatch(r"[0-9a-f]{64}", str(record.get("device_identity_sha256") or ""))
        is None
    ):
        raise ValueError("Your Apple bridge device binding is unavailable")
    return record


def permission_options(runtime: Any, member_id: str, provider: str) -> dict[str, Any]:
    if provider not in PACKS:
        raise ValueError("Unknown Apple service")
    record = _record(runtime, member_id)
    pack = PACKS[provider]
    if any(
        pack not in record.get(field, [])
        for field in ("configured_packs", "enabled_packs", "authorized_packs")
    ):
        raise ValueError("This Apple source is not currently authorized and enabled")
    control = runtime.settings_control
    wizard = control.bridge_request_for(member_id)
    if (
        not isinstance(wizard, dict)
        or wizard.get("enrollment_id") != record["enrollment_id"]
        or wizard.get("state") in {"paused", "revoked", "expired"}
    ):
        raise ValueError("This Apple bridge enrollment is not available")
    capability = (wizard.get("capability_states") or {}).get(provider, {})
    if capability.get("state") not in {"observing", "settling", "settled", "active"}:
        raise ValueError("The Apple source has not established its native permission")
    config = record["pack_config"][pack]
    scopes: list[dict[str, Any]] = []
    tenant = runtime.settings.instance.tenant_id
    private = f"private:{member_id}"
    if provider == "apple_notes":
        if runtime.projections is None:
            raise ValueError("Notes folder state is unavailable")
        rows = (
            runtime.projections.connection("member_knowledge")
            .execute(
                "SELECT account_external_id, folder_external_id, folder_path FROM member_knowledge_folders "
                "WHERE tenant_id=? AND member_id=? AND source_kind='apple_notes' AND owner_scope=? AND visibility_scope=? "
                "ORDER BY account_external_id, folder_path",
                (tenant, member_id, private, private),
            )
            .fetchall()
        )
        for row in rows:
            account, container = row["account_external_id"], row["folder_external_id"]
            if not account or not container:
                continue
            account_id = "provider-account:" + digest(
                [member_id, record["enrollment_id"], account]
            )
            scopes.append(
                {
                    "id": digest([provider, account, container]),
                    "label": row["folder_path"],
                    "account_id": account,
                    "container_id": container,
                    "provider_account_id": account_id,
                }
            )
    else:
        for index, container in enumerate(config.get("allowed_container_ids", []), 1):
            label = f"Selected {'calendar' if provider == 'apple_calendar' else 'reminder list'} {index}"
            if runtime.projections is not None:
                projection, table, name, field = (
                    (
                        "calendars",
                        "calendar_sources",
                        "calendar_name",
                        "calendar_external_id",
                    )
                    if provider == "apple_calendar"
                    else ("lists", "lists", "list_name", "list_external_id")
                )
                row = (
                    runtime.projections.connection(projection)
                    .execute(
                        f"SELECT {name} FROM {table} WHERE tenant_id=? AND member_id=? AND provider=? "
                        f"AND provider_account_id=? AND {field}=? AND owner_scope=? AND visibility_scope=? LIMIT 1",
                        (
                            tenant,
                            member_id,
                            provider,
                            config["provider_account_id"],
                            container,
                            private,
                            private,
                        ),
                    )
                    .fetchone()
                )
                if row is not None:
                    label = row[name]
            scopes.append(
                {
                    "id": digest([provider, config["provider_account_id"], container]),
                    "label": label,
                    "account_id": config["provider_account_id"],
                    "container_id": container,
                    "provider_account_id": config["provider_account_id"],
                }
            )
    return {
        "scopes": scopes,
        "operations": list(OPERATIONS[provider]),
        "enrollment": {
            "enrollment_id": record["enrollment_id"],
            "device_identity_sha256": record["device_identity_sha256"],
            "configuration_sha256": digest(record["pack_config"]),
        },
    }


def permission_coordinates(
    runtime: Any, member_id: str, provider: str, *, scope_ids: list[str]
) -> dict[str, Any]:
    options = permission_options(runtime, member_id, provider)
    if not scope_ids or len(scope_ids) != len(set(scope_ids)):
        raise ValueError("Choose at least one distinct Apple destination")
    selected = [scope for scope in options["scopes"] if scope["id"] in scope_ids]
    if (
        len(selected) != len(scope_ids)
        or len({scope["provider_account_id"] for scope in selected}) != 1
    ):
        raise ValueError("Choose currently available destinations in one Apple account")
    return {
        "member_id": member_id,
        "provider": provider,
        **options["enrollment"],
        "provider_account_id": selected[0]["provider_account_id"],
        "account_external_id": selected[0]["account_id"]
        if provider == "apple_notes"
        else None,
        "allowed_container_ids": sorted(scope["container_id"] for scope in selected),
        "operations": list(options["operations"]),
    }


def permission_state(
    runtime: Any, member_id: str, provider: str
) -> dict[str, Any] | None:
    value = (
        runtime.settings_control.read()
        .get("apple_service_permissions", {})
        .get(member_id, {})
        .get(provider)
    )
    return dict(value) if isinstance(value, dict) else None


def service_permission(
    runtime: Any, member_id: str, provider: str
) -> dict[str, Any] | None:
    state = permission_state(runtime, member_id, provider)
    if not state or state.get("enabled") is not True or not state.get("event_id"):
        return None
    coordinates = state.get("coordinates")
    if not isinstance(coordinates, dict) or digest(coordinates) != state.get(
        "coordinates_sha256"
    ):
        return None
    try:
        options = permission_options(runtime, member_id, provider)
        chosen = [
            scope["id"]
            for scope in options["scopes"]
            if scope["provider_account_id"] == coordinates.get("provider_account_id")
            and scope["container_id"] in coordinates.get("allowed_container_ids", [])
        ]
        current = permission_coordinates(runtime, member_id, provider, scope_ids=chosen)
    except (ValueError, KeyError):
        return None
    if current != coordinates:
        return None
    return {
        **coordinates,
        "event_id": state["event_id"],
        "revision": state["revision"],
        "changed_at": state["changed_at"],
        "coordinates_sha256": state["coordinates_sha256"],
    }


def permission_marker(grant: dict[str, Any]) -> dict[str, Any]:
    return {key: grant[key] for key in ("event_id", "revision", "coordinates_sha256")}


def command_container(provider: str, payload: dict[str, Any]) -> str | None:
    names = {
        "apple_calendar": (
            "calendar_identifier",
            "calendar_external_id",
            "calendar_id",
        ),
        "apple_reminders": (
            "target_list_identifier",
            "target_list_id",
            "list_external_id",
            "list_identifier",
        ),
        "apple_notes": ("folder_external_id",),
    }.get(provider, ())
    values = {
        payload[key]
        for key in names
        if isinstance(payload.get(key), str) and payload[key]
    }
    return next(iter(values)) if len(values) == 1 else None


def validate_command_scope(grant: dict[str, Any], command: dict[str, Any]) -> None:
    provider, payload = str(command.get("provider") or ""), command.get("payload")
    if (
        not isinstance(payload, dict)
        or command.get("member_id") != grant["member_id"]
        or provider != grant["provider"]
        or command.get("provider_account_id") != grant["provider_account_id"]
        or command.get("operation") not in grant["operations"]
    ):
        raise ValueError("Apple action does not match its current service permission")
    allowed = grant["allowed_container_ids"]
    if command_container(provider, payload) not in allowed:
        raise ValueError(
            "Apple action destination is outside the selected service permission"
        )
    for key in (
        "destination_list_id",
        "destination_list_external_id",
        "destination_calendar_id",
        "destination_calendar_external_id",
        "target_calendar_id",
    ):
        if payload.get(key) is not None and payload[key] not in allowed:
            raise ValueError(
                "Apple move destination is outside the selected service permission"
            )
    if (
        provider == "apple_notes"
        and payload.get("account_external_id") != grant["account_external_id"]
    ):
        raise ValueError("Notes action does not match the selected Apple account")


def requires_service_permission(runtime: Any, member_id: str, provider: str) -> bool:
    if provider == "apple_notes":
        return True
    if provider not in PROVIDERS:
        return False
    records = [
        record
        for record in runtime.settings.bridge_enrollments.values()
        if isinstance(record, dict)
        and record.get("status") == "active"
        and record.get("member_id") == member_id
    ]
    return any(
        record.get("acquisition_policy") is not None
        or record.get("observation_only") is True
        or record.get("provider_writes") == "deny"
        for record in records
    )


def bind_command_permission(
    runtime: Any, member_id: str, command: dict[str, Any]
) -> None:
    if not requires_service_permission(
        runtime, member_id, str(command.get("provider") or "")
    ):
        return
    grant = service_permission(runtime, member_id, str(command["provider"]))
    if grant is None:
        raise ValueError(
            "Enable this Apple service's writing permission in Settings first"
        )
    validate_command_scope(grant, command)
    command["payload"]["apple_service_permission"] = permission_marker(grant)


def command_permission(
    runtime: Any, member_id: str, command: dict[str, Any]
) -> dict[str, Any]:
    grant = service_permission(runtime, member_id, str(command.get("provider") or ""))
    if grant is None:
        raise ValueError(
            "Apple service writing permission is disabled or its binding changed"
        )
    validate_command_scope(grant, command)
    if command["payload"].get("apple_service_permission") != permission_marker(grant):
        raise ValueError(
            "This action needs a new approval under the current Apple service permission"
        )
    return grant


def command_proof(
    runtime: Any,
    member_id: str,
    command: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    grant = command_permission(runtime, member_id, command)
    now = now or datetime.now(timezone.utc)
    return {
        "contract": CONTRACT,
        "grant": grant,
        "command_sha256": digest(
            {
                key: value
                for key, value in command.items()
                if key != "apple_service_authorization"
            }
        ),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=PROOF_TTL_SECONDS)).isoformat(),
    }


def validate_edge_proof(
    config: Any,
    command: dict[str, Any],
    *,
    device_identity_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    proof = command.get("apple_service_authorization")
    if (
        not isinstance(proof, dict)
        or proof.get("contract") != CONTRACT
        or not isinstance(proof.get("grant"), dict)
    ):
        raise ValueError(
            "The bridge remains read-only without a fresh Apple service authorization"
        )
    grant = proof["grant"]
    coordinates = {
        key: value
        for key, value in grant.items()
        if key not in {"event_id", "revision", "changed_at", "coordinates_sha256"}
    }
    if digest(coordinates) != grant.get("coordinates_sha256"):
        raise ValueError("Apple service authorization coordinates changed")
    now = now or datetime.now(timezone.utc)
    try:
        issued, expires = (
            datetime.fromisoformat(proof["issued_at"]),
            datetime.fromisoformat(proof["expires_at"]),
        )
        valid_time = (
            issued <= now < expires
            and (expires - issued).total_seconds() <= PROOF_TTL_SECONDS
        )
    except (KeyError, ValueError, TypeError):
        valid_time = False
    if (
        not valid_time
        or grant.get("member_id") != config.member_id
        or grant.get("enrollment_id") != config.enrollment_id
        or grant.get("configuration_sha256") != digest(config.pack_config)
        or PACKS.get(str(command.get("provider"))) not in config.enabled_packs
        or not device_identity_sha256
        or grant.get("device_identity_sha256") != device_identity_sha256
    ):
        raise ValueError(
            "Apple service authorization is expired or belongs to another bridge"
        )
    if proof.get("command_sha256") != digest(
        {
            key: value
            for key, value in command.items()
            if key != "apple_service_authorization"
        }
    ):
        raise ValueError(
            "Apple service authorization does not match this exact command"
        )
    validate_command_scope(grant, command)
    if command["payload"].get("apple_service_permission") != permission_marker(grant):
        raise ValueError("Apple service approval revision changed")
    return grant


def require_unexpired_native_authorization(payload: dict[str, Any]) -> None:
    if not payload.get("apple_service_permission"):
        return
    try:
        expiry = datetime.fromisoformat(
            payload["apple_service_authorization_expires_at"]
        )
        if expiry.tzinfo is None or datetime.now(timezone.utc) >= expiry:
            raise ValueError(
                "Apple service authorization expired before the provider effect"
            )
    except (KeyError, TypeError) as exc:
        raise ValueError("Apple service authorization expiry is unavailable") from exc
