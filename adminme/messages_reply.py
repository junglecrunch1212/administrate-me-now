"""G11's member-owned, exact-chat Apple Messages reply contract.

This module contains only deterministic policy and fingerprints.  It never
contacts BlueBubbles and never grants authority by itself.  The provider call
remains in the separately supervised member bridge.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from adminme.bridge_authorization import authorization_from_record


REPLY_CONTRACT = "apple-messages-member-reply-v1"
REPLY_CAPABILITY_VERSION = "apple-messages-reply-v1"
REPLY_POLICY_VERSION = "governed-apple-messages-reply-v1"
REPLY_TRANSPORT = "bluebubbles-rest-apple-script-existing-chat-v1"
REPLY_CONTENT_GUARD = "apple-messages-plain-text-exact-v1"
REPLY_APPROVAL_TTL_SECONDS = 10 * 60
REPLY_MAX_BODY_CHARS = 20_000
REPLY_STATES = (
    "implementation_unavailable",
    "bridge_not_connected",
    "transport_unsupported",
    "read_observation_not_settled",
    "reply_unsupported",
    "local_permission_missing",
    "available_but_disabled",
    "enabled_ask_every_time",
    "paused",
    "degraded",
    "revoked",
)
TERMINAL_RESULT_STATES = frozenset({"accepted", "sent", "failed", "ambiguous"})
PROHIBITED_OPERATIONS = (
    "new conversation",
    "model-selected recipient",
    "autonomous CoS reply",
    "standing auto-reply",
    "attachment",
    "reaction",
    "edit",
    "unsend",
    "read receipt",
    "typing indicator",
    "group membership change",
    "broadcast or bulk send",
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def body_sha256(body: str) -> str:
    """Fingerprint the exact Unicode string without trimming or normalization."""

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def participant_ids(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return the source-proven endpoint set, including the local self endpoint."""

    values = {
        str(row.get("participant_id") or "")
        for row in rows
        if row.get("resolved") == "endpoint" and row.get("participant_id")
    }
    return sorted(values)


def validate_exact_body(body: Any) -> str:
    if not isinstance(body, str) or not body.strip():
        raise ValueError("reply body is empty")
    if len(body) > REPLY_MAX_BODY_CHARS:
        raise ValueError("reply body exceeds the plain-text safety limit")
    if "\x00" in body or any(
        ord(character) < 32 and character not in {"\n", "\r", "\t"}
        for character in body
    ):
        raise ValueError("reply body contains an unsafe control character")
    return body


def _member_bridge(
    runtime: Any, member_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    matches = [
        (str(identity), record)
        for identity, record in getattr(
            runtime.settings, "bridge_enrollments", {}
        ).items()
        if isinstance(record, dict)
        and record.get("member_id") == member_id
        and "messaging-bluebubbles" in record.get("configured_packs", [])
    ]
    return matches[0] if len(matches) == 1 else (None, None)


def _has_exact_read_observation(
    runtime: Any,
    *,
    member_id: str,
    record: Mapping[str, Any],
    config: Mapping[str, Any],
) -> bool:
    projections = getattr(runtime, "projections", None)
    if projections is None:
        return False
    try:
        row = (
            projections.connection("interactions")
            .execute(
                "SELECT 1 FROM interactions WHERE tenant_id=? AND member_id=? "
                "AND provider='bluebubbles' AND provider_account_id=? "
                "AND bridge_enrollment_id=? AND protected_source_id=? LIMIT 1",
                (
                    runtime.settings.instance.tenant_id,
                    member_id,
                    config.get("provider_account_id"),
                    record.get("enrollment_id"),
                    config.get("protected_source_id"),
                ),
            )
            .fetchone()
        )
    except Exception:  # noqa: BLE001 - missing/stale read model fails closed
        return False
    return row is not None


def reply_control_for(
    runtime: Any,
    member_id: str,
    *,
    settings_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose the exact Settings/Comms capability state for one owner."""

    identity, record = _member_bridge(runtime, member_id)
    control_store = getattr(runtime, "settings_control", None)
    state = settings_state or (
        control_store.read() if control_store is not None else {}
    )
    controls = (
        state.get("message_reply_controls", {}) if isinstance(state, dict) else {}
    )
    desired = controls.get(member_id) if isinstance(controls, dict) else None
    desired = desired if isinstance(desired, dict) else None
    reason: str | None = None
    config: dict[str, Any] = {}
    authorization = (
        authorization_from_record(record) if isinstance(record, dict) else None
    )
    observation_settled = False

    if record is None:
        capability_state = "bridge_not_connected"
        reason = "No single member-owned Messages bridge is connected."
    else:
        raw_config = (record.get("pack_config") or {}).get("messaging-bluebubbles")
        config = dict(raw_config) if isinstance(raw_config, dict) else {}
        wizard = (
            state.get("bridge_requests", {}).get(member_id)
            if isinstance(state.get("bridge_requests"), dict)
            else None
        )
        wizard_state = wizard.get("state") if isinstance(wizard, dict) else None
        read_row = (
            (wizard.get("capability_states") or {}).get("apple_messages_read")
            if isinstance(wizard, dict)
            else None
        )
        read_state = read_row.get("state") if isinstance(read_row, dict) else None
        observation_settled = read_state in {"settled", "active"} or (
            _has_exact_read_observation(
                runtime,
                member_id=member_id,
                record=record,
                config=config,
            )
        )
        record_status = str(record.get("status") or "")
        if record_status == "revoked" or wizard_state == "revoked":
            capability_state = "revoked"
            reason = "The member bridge was revoked."
        elif record_status == "paused" or wizard_state == "paused":
            capability_state = "paused"
            reason = "The member bridge is paused."
        elif record_status != "active":
            capability_state = "bridge_not_connected"
            reason = "The exact member bridge is not active."
        elif (
            config.get("transport_version") != "1.9.9"
            or config.get("transport_commit")
            != "ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1"
        ):
            capability_state = "transport_unsupported"
            reason = "The bridge transport does not match the reviewed pin."
        elif not observation_settled:
            capability_state = "read_observation_not_settled"
            reason = "Messages read observation has not settled."
        elif (
            authorization is None
            or not {"messaging-bluebubbles"} <= authorization.packs
            or "messaging-bluebubbles" not in record.get("enabled_packs", [])
            or "messaging-bluebubbles" not in record.get("adapters_active", [])
        ):
            capability_state = "degraded"
            reason = "The Messages adapter is not independently authorized and active."
        elif (
            config.get("reply_transport") != REPLY_TRANSPORT
            or config.get("reply_capability_version") != REPLY_CAPABILITY_VERSION
        ):
            capability_state = "reply_unsupported"
            reason = "This enrolled connector has no reviewed reply capability."
        elif config.get("reply_permission_verified") is not True or not isinstance(
            config.get("reply_permission_evidence_sha256"), str
        ):
            capability_state = "local_permission_missing"
            reason = "Local reply permission has not been independently verified."
        elif desired and desired.get("state") == "revoked":
            capability_state = "revoked"
            reason = "Replies were revoked by the owning member."
        elif desired and desired.get("state") == "paused":
            capability_state = "paused"
            reason = "Replies are paused by the owning member."
        elif desired and desired.get("state") == "enabled_ask_every_time":
            coordinates = desired.get("coordinates")
            current_coordinates = {
                "member_id": member_id,
                "bridge_enrollment_id": record.get("enrollment_id"),
                "provider_account_id": config.get("provider_account_id"),
                "protected_source_id": config.get("protected_source_id"),
                "transport": REPLY_TRANSPORT,
                "capability_version": REPLY_CAPABILITY_VERSION,
                "policy_version": REPLY_POLICY_VERSION,
                "permission_evidence_sha256": config.get(
                    "reply_permission_evidence_sha256"
                ),
            }
            if coordinates != current_coordinates:
                capability_state = "degraded"
                reason = "Reply authority no longer matches the enrolled capability."
            else:
                capability_state = "enabled_ask_every_time"
        else:
            capability_state = "available_but_disabled"

    coordinates = {
        "member_id": member_id,
        "bridge_enrollment_id": record.get("enrollment_id") if record else None,
        "provider_account_id": config.get("provider_account_id"),
        "protected_source_id": config.get("protected_source_id"),
        "transport": REPLY_TRANSPORT,
        "capability_version": REPLY_CAPABILITY_VERSION,
        "policy_version": REPLY_POLICY_VERSION,
        "permission_evidence_sha256": config.get("reply_permission_evidence_sha256"),
    }
    return {
        "contract": REPLY_CONTRACT,
        "state": capability_state,
        "states": list(REPLY_STATES),
        "reason": reason,
        "member_id": member_id,
        "bridge_identity": identity,
        "bridge_enrollment_id": coordinates["bridge_enrollment_id"],
        "provider": "bluebubbles",
        "provider_account_id": coordinates["provider_account_id"],
        "protected_source_id": coordinates["protected_source_id"],
        "source_class": "member-private Apple Messages through BlueBubbles",
        "transport": coordinates["transport"],
        "transport_version": config.get("transport_version"),
        "transport_commit": config.get("transport_commit"),
        "observation_settled": observation_settled,
        "ask_every_time": True,
        "enabled": capability_state == "enabled_ask_every_time",
        "revision": int(desired.get("revision") or 0) if desired else 0,
        "coordinates": coordinates,
        "coordinates_sha256": canonical_sha256(coordinates),
        "permits": ["exact plain-text reply to one existing observed chat"],
        "prohibited": list(PROHIBITED_OPERATIONS),
        "actions": ["enable", "pause", "revoke"],
        "provider_effects": False,
    }


def approval_is_current(
    runtime: Any,
    *,
    member_id: str,
    command_payload: Mapping[str, Any],
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Revalidate mutable authority immediately before command creation/delivery."""

    if command_payload.get("contract") != REPLY_CONTRACT:
        return False, "reply contract changed"
    control = reply_control_for(runtime, member_id)
    if control["state"] != "enabled_ask_every_time":
        return False, f"reply setting is {control['state']}"
    if command_payload.get("capability_version") != REPLY_CAPABILITY_VERSION:
        return False, "reply capability changed"
    if command_payload.get("policy_version") != REPLY_POLICY_VERSION:
        return False, "reply policy changed"
    if command_payload.get("setting_revision") != control["revision"]:
        return False, "reply setting revision changed"
    for key in (
        "bridge_enrollment_id",
        "provider_account_id",
        "protected_source_id",
    ):
        if command_payload.get(key) != control.get(key):
            return False, f"reply {key} changed"
    body = command_payload.get("body")
    try:
        exact_body = validate_exact_body(body)
    except ValueError as exc:
        return False, str(exc)
    if command_payload.get("body_fingerprint") != body_sha256(exact_body):
        return False, "reply body changed"
    participants = command_payload.get("participant_ids")
    if not isinstance(participants, list) or command_payload.get(
        "participant_fingerprint"
    ) != canonical_sha256(participants):
        return False, "reply participants changed"
    expires_at = command_payload.get("approval_expires_at")
    try:
        expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False, "reply approval expiry is invalid"
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires.tzinfo is None or expires <= current:
        return False, "reply approval expired"
    return True, "current"


__all__ = [
    "PROHIBITED_OPERATIONS",
    "REPLY_APPROVAL_TTL_SECONDS",
    "REPLY_CAPABILITY_VERSION",
    "REPLY_CONTENT_GUARD",
    "REPLY_CONTRACT",
    "REPLY_MAX_BODY_CHARS",
    "REPLY_POLICY_VERSION",
    "REPLY_STATES",
    "REPLY_TRANSPORT",
    "TERMINAL_RESULT_STATES",
    "approval_is_current",
    "body_sha256",
    "canonical_sha256",
    "participant_ids",
    "reply_control_for",
    "validate_exact_body",
]
