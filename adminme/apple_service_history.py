"""Historical ownership of governed Apple commands without mutable bindings."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from adminme.apple_service_permissions import (
    EVENT_TYPE,
    PROVIDERS,
    permission_marker,
    validate_command_scope,
)
from adminme.events.log import EventLog
from adminme.events.schemas.apple_service_permissions import (
    AppleServicePermissionControlledV1,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID


def _private(event: dict[str, Any], *, tenant: str, member: str) -> bool:
    return bool(
        tenant
        and member
        and event.get("tenant_id") == tenant
        and event.get("owner_scope")
        == event.get("visibility_scope")
        == f"private:{member}"
    )


async def apple_command_owner_matches(
    event_log: EventLog, command: dict[str, Any], *, through: str
) -> bool:
    """Correlate the exact immutable grant, member decision and native approval.

    Later revocation changes future execution, never the attribution of a command
    already accepted. Terminal/provider-effect accounting remains a separate audit.
    """
    payload = command.get("payload") or {}
    nested = payload.get("payload") or {}
    marker = nested.get("apple_service_permission")
    member, tenant = (
        str(payload.get("member_id") or ""),
        str(command.get("tenant_id") or ""),
    )
    command_id = str(command.get("event_id") or "")
    if (
        command.get("type") != "external_sync.write_requested"
        or command.get("schema_version") != 2
        or command.get("source_adapter") != "openclaw-plugin"
        or command.get("actor_identity") != member
        or payload.get("provider") not in PROVIDERS
        or not _private(command, tenant=tenant, member=member)
        or not command_id
        or command_id > through
        or not isinstance(marker, dict)
    ):
        return False
    grant_id = marker.get("event_id")
    domain_id, native_id = (
        payload.get("domain_approval_event_id"),
        payload.get("openclaw_approval_event_id"),
    )
    if not all(
        isinstance(value, str) and value for value in (grant_id, domain_id, native_id)
    ):
        return False
    if not str(grant_id) < str(domain_id) < str(native_id) < command_id:
        return False
    grant = await event_log.get(str(grant_id))
    domain = await event_log.get(str(domain_id))
    native = await event_log.get(str(native_id))
    if grant is None or domain is None or native is None:
        return False
    if not all(
        _private(row, tenant=tenant, member=member) for row in (grant, domain, native)
    ):
        return False
    if (
        grant.get("type") != EVENT_TYPE
        or grant.get("schema_version") != 1
        or grant.get("source_adapter") != "member-settings-control-plane"
        or grant.get("source_account_id") != member
        or grant.get("actor_identity") != member
    ):
        return False
    try:
        permitted = AppleServicePermissionControlledV1.model_validate(grant["payload"])
        coordinates = {
            **permitted.coordinates,
            "event_id": grant_id,
            "revision": permitted.revision,
            "coordinates_sha256": permitted.coordinates_sha256,
        }
        validate_command_scope(coordinates, payload)
    except (ValidationError, ValueError, KeyError, TypeError):
        return False
    if not permitted.enabled or marker != permission_marker(coordinates):
        return False
    latest: dict[str, Any] | None = None
    async for event in event_log.read_since(
        through=command_id, owner_scope=f"private:{member}", types=[EVENT_TYPE]
    ):
        if (
            event.get("tenant_id") == tenant
            and event.get("payload", {}).get("provider") == payload["provider"]
        ):
            latest = event
    if latest is None or latest["event_id"] != grant_id:
        return False
    decision = domain.get("payload") or {}
    approval = native.get("payload") or {}
    exact_fields = {
        "member_id": member,
        "provider": payload["provider"],
        "provider_account_id": payload.get("provider_account_id"),
        "operation": payload.get("operation"),
        "target_external_id": payload.get("target_external_id"),
        "expected_provider_revision": payload.get("expected_provider_revision"),
        "request_fingerprint": payload.get("request_fingerprint"),
    }
    native_parameters = {"domain_approval_event_id": domain_id}
    if native.get("schema_version") == 2:
        grounding_id = approval.get("request_authorization_event_id")
        grounding = await event_log.get(str(grounding_id)) if grounding_id else None
        if (
            grounding is None
            or grounding.get("type") != "cos.action.request_grounded"
            or grounding.get("schema_version") != 1
            or not _private(grounding, tenant=tenant, member=member)
            or grounding.get("actor_identity") != member
        ):
            return False
        grounded = grounding.get("payload") or {}
        if (
            grounded.get("member_id") != member
            or grounded.get("action_id") != decision.get("action_id")
            or grounded.get("source_event_id") != domain.get("causation_id")
            or grounded.get("request_fingerprint")
            != decision.get("request_fingerprint")
            or approval.get("source_event_id") != grounded.get("source_event_id")
            or approval.get("domain_approval_event_id") != domain_id
            or approval.get("action_fingerprint") != grounded.get("action_fingerprint")
            or approval.get("rule_id") != grounded.get("rule_id")
            or approval.get("rule_id")
            != "current-human-request.apple-reminder-create.v1"
        ):
            return False
        native_parameters["request_authorization_event_id"] = grounding_id
    native_fingerprint = hashlib.sha256(
        json.dumps(
            native_parameters,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    try:
        issued, consumed, expires = (
            datetime.fromisoformat(str(approval.get(key)))
            for key in ("issued_at", "consumed_at", "expires_at")
        )
        timely = bool(
            issued.tzinfo
            and consumed.tzinfo
            and expires.tzinfo
            and issued <= consumed <= expires
        )
    except (ValueError, TypeError):
        timely = False
    return bool(
        domain.get("type") == "member.provider_action.confirmed"
        and domain.get("schema_version") == 1
        and domain.get("actor_identity") == domain.get("source_account_id") == member
        and all(decision.get(key) == value for key, value in exact_fields.items())
        and decision.get("action_id") == payload.get("command_id")
        and decision.get("command_payload") == nested
        and domain.get("raw_ref")
        == f"request-sha256:{payload.get('request_fingerprint')}"
        and native.get("type") == "openclaw.approval.received"
        and native.get("schema_version") in {1, 2}
        and native.get("source_adapter") == "openclaw-plugin"
        and native.get("actor_identity") in {member, COS_SERVICE_PRINCIPAL_ID}
        and native.get("causation_id") == domain_id
        and approval.get("member_id") == member
        and approval.get("tool_name")
        == "administrateme_execute_pending_provider_action"
        and approval.get("action") == payload.get("operation")
        and approval.get("approval_method")
        == (
            "authenticated_request"
            if native.get("schema_version") == 2
            else "allow_once"
        )
        and approval.get("single_use") is True
        and approval.get("request_fingerprint") == native_fingerprint
        and approval.get("resource_fingerprint") == native_fingerprint
        and timely
        and command.get("idempotency_key") == f"external-sync-approved:{domain_id}"
        and command.get("causation_id") == native_id
    )
