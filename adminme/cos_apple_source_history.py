"""Exact retained CoS list ownership, independent of present operational grants.

The supported recovery controller verifies and seals the original activation
artifacts. This module only checks that its receipt refers to the exact existing
source/authentication pair; it never enrolls a source or authorizes later facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from adminme.adapters.cos_apple_reminders import (
    ADAPTER_KIND,
    READ_SCOPE,
    CoSRemindersSelection,
    digest,
)
from adminme.events.log import EventLog
from adminme.events.schemas.adapters import ServiceOSAuthenticatedV3
from adminme.events.schemas.cos_apple_source_history import (
    EVENT_TYPE,
    ServiceSourceOwnershipReconciledV1,
)
from adminme.events.schemas.lists import ListChangedV2
from adminme.provider_bindings import adapter_owner_coordinate_id
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID, service_private_scope

SOURCE_ADAPTER = "cos-apple-source-history"
_INVALID = "CoS Apple historical ownership evidence is invalid"


def receipt_idempotency_key(source_event_id: str) -> str:
    return (
        "cos-apple-source-history:"
        + hashlib.sha256(source_event_id.encode()).hexdigest()
    )


def event_fingerprint(event: dict[str, Any]) -> str:
    """Fingerprint every retained EventLog.get field, including legacy columns."""
    try:
        encoded = json.dumps(
            event, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, TypeError):
        raise ValueError(_INVALID) from None
    return hashlib.sha256(encoded).hexdigest()


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(_INVALID)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(_INVALID) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(_INVALID)
    return parsed


def _original_envelope(
    event: dict[str, Any], selection: CoSRemindersSelection, *, kind: str, version: int
) -> None:
    scope = service_private_scope(COS_SERVICE_PRINCIPAL_ID)
    if (
        event.get("type") != kind
        or type(event.get("schema_version")) is not int
        or event["schema_version"] != version
        or event.get("source_adapter") != ADAPTER_KIND
        or event.get("source_account_id") != selection.provider_account_id
        or event.get("owner_scope") != scope
        or event.get("visibility_scope") != scope
        or event.get("sensitivity") != "normal"
        or event.get("actor_identity") is not None
        or event.get("causation_id") is not None
        or event.get("raw_ref") is not None
        or not isinstance(event.get("tenant_id"), str)
        or not event["tenant_id"]
        or not isinstance(event.get("correlation_id"), str)
        or not event["correlation_id"]
        or type(event.get("event_at_ms")) is not int
        or event["event_at_ms"] <= 0
        or _time(event.get("occurred_at")) > _time(event.get("recorded_at"))
    ):
        raise ValueError(_INVALID)


async def validate_evidence(
    event_log: EventLog,
    selection: CoSRemindersSelection,
    source_event_id: str,
    authentication_event_id: str,
    through: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the original first-poll fact and its subsequent OS read receipt."""
    try:
        if (
            not isinstance(selection, CoSRemindersSelection)
            or not all(
                isinstance(value, str) and value and value == value.strip()
                for value in (source_event_id, authentication_event_id, through)
            )
            or not source_event_id < authentication_event_id <= through
        ):
            raise ValueError(_INVALID)
        source = await event_log.get(source_event_id)
        authentication = await event_log.get(authentication_event_id)
        if source is None or authentication is None:
            raise ValueError(_INVALID)
        _original_envelope(source, selection, kind="list.created", version=2)
        _original_envelope(
            authentication, selection, kind="adapter.authenticated", version=3
        )
        if (
            source.get("event_id") != source_event_id
            or authentication.get("event_id") != authentication_event_id
            or source["tenant_id"] != authentication["tenant_id"]
            or authentication.get("idempotency_key") is not None
        ):
            raise ValueError(_INVALID)
        fact = ListChangedV2.model_validate(source["payload"])
        auth = ServiceOSAuthenticatedV3.model_validate(
            authentication["payload"], strict=True
        )
        configuration = selection.adapter_config()
        coordinate = (
            adapter_owner_coordinate_id(ADAPTER_KIND, configuration)
            + ":"
            + digest(selection.list_id)
        )
        prefix = "cos-reminders:" + digest([coordinate, 1]) + ":"
        key = source.get("idempotency_key")
        if (
            fact.member_id is not None
            or fact.service_principal_id != COS_SERVICE_PRINCIPAL_ID
            or fact.workspace != "service"
            or fact.provider != "apple_reminders"
            or fact.provider_account_id != selection.provider_account_id
            or fact.list_external_id != selection.list_id
            or source["payload"].get("is_default") is not False
            or fact.color is not None
            or fact.provider_revision != digest(fact.list_name)
            or auth.configuration_sha256 != digest(configuration)
            or auth.device_identity_sha256 != selection.device_identity_sha256
            or auth.source_ownership_evidence_sha256
            != selection.source_ownership_evidence_sha256
            or auth.scopes != [READ_SCOPE]
            or not isinstance(key, str)
            or re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", key) is None
            or not (
                _time(source["payload"].get("changed_at"))
                <= _time(source["occurred_at"])
                <= _time(source["recorded_at"])
                <= _time(auth.authenticated_at)
                <= _time(authentication["occurred_at"])
                <= _time(authentication["recorded_at"])
            )
        ):
            raise ValueError(_INVALID)
        retained = await event_log.get_by_idempotency(source["tenant_id"], key)
        if retained is None or event_fingerprint(retained) != event_fingerprint(source):
            raise ValueError(_INVALID)
        return source, authentication
    except (ValidationError, KeyError, TypeError, ValueError):
        raise ValueError(_INVALID) from None


async def source_owner_matches(
    event_log: EventLog, source: dict[str, Any], through: str
) -> bool:
    """Match one retrospective receipt, with no fallback to current bindings."""
    try:
        source_id, tenant = source.get("event_id"), source.get("tenant_id")
        if not isinstance(source_id, str) or not isinstance(tenant, str):
            return False
        receipt = await event_log.get_by_idempotency(
            tenant, receipt_idempotency_key(source_id)
        )
        if receipt is None:
            return False
        payload = ServiceSourceOwnershipReconciledV1.model_validate(receipt["payload"])
        scope = service_private_scope(COS_SERVICE_PRINCIPAL_ID)
        receipt_id = receipt.get("event_id")
        if (
            receipt.get("type") != EVENT_TYPE
            or type(receipt.get("schema_version")) is not int
            or receipt["schema_version"] != 1
            or receipt.get("source_adapter") != SOURCE_ADAPTER
            or receipt.get("source_account_id") != payload.selection.provider_account_id
            or receipt.get("tenant_id") != tenant
            or receipt.get("owner_scope") != scope
            or receipt.get("visibility_scope") != scope
            or receipt.get("sensitivity") != "privileged"
            or receipt.get("actor_identity") != payload.operator_principal_id
            or receipt.get("causation_id") != source_id
            or receipt.get("idempotency_key") != receipt_idempotency_key(source_id)
            or receipt.get("raw_ref") is not None
            or receipt.get("correlation_id") != payload.activation_id
            or type(receipt.get("event_at_ms")) is not int
            or receipt["event_at_ms"] <= 0
            or not isinstance(receipt_id, str)
            or not payload.authentication_event_id < receipt_id <= through
            or payload.source_event_id != source_id
        ):
            return False
        retained, authentication = await validate_evidence(
            event_log,
            payload.selection,
            source_id,
            payload.authentication_event_id,
            through,
        )
        return bool(
            payload.source_fingerprint == event_fingerprint(retained)
            and event_fingerprint(source) == event_fingerprint(retained)
            and payload.authentication_fingerprint == event_fingerprint(authentication)
            and _time(authentication["recorded_at"])
            <= _time(payload.reconciled_at)
            <= _time(receipt["occurred_at"])
            <= _time(receipt["recorded_at"])
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        return False


__all__ = [
    "EVENT_TYPE",
    "SOURCE_ADAPTER",
    "event_fingerprint",
    "receipt_idempotency_key",
    "source_owner_matches",
    "validate_evidence",
]
