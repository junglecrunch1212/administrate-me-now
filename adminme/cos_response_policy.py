"""Encrypted, event-sourced recipient ceiling for CoS channel responses.

Being listed never supplies provider approval, disclosure authority, service
account ownership, or permission to enter another member's private context.
Callers must read the policy again immediately before a provider effect.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID


POLICY_CONTRACT = "cos-response-allowlist-v1"
POLICY_EVENT_TYPE = "settings.cos_response_allowlist_changed"
MAX_RECIPIENT_ENTRIES = 100
_LOCAL = re.compile(r"^[A-Za-z0-9!#$%&'+/=^_`{|}~.-]+$")
_DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def normalize_recipient(channel: str, address: str) -> str:
    """Accept one exact bare endpoint; never parse display names or lists.

    Email domains are case-insensitive. Local parts remain exact, including
    case, dots and plus tags; provider-specific alias equivalence is forbidden.
    """

    if (
        not isinstance(channel, str)
        or channel not in {"email", "imessage"}
        or not isinstance(address, str)
    ):
        raise ValueError("choose email or iMessage and one bare recipient")
    if (
        not address
        or len(address) > 254
        or address != address.strip()
        or not address.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in address)
    ):
        raise ValueError("recipient must be one bare address without whitespace")
    if channel == "imessage" and _PHONE.fullmatch(address):
        return address
    if address.count("@") != 1:
        raise ValueError("use one email address or an international iMessage number")
    local, domain = address.split("@")
    labels = domain.split(".")
    if (
        len(local) > 64
        or not _LOCAL.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValueError("recipient must be one exact email address without wildcards")
    return f"{local}@{domain.lower()}"


def validate_recipient_entries(
    entries: Any, known_members: Collection[str] | Mapping[str, Any] | None
) -> list[dict[str, str]]:
    """Validate and canonicalize the complete replacement recipient list."""

    if not isinstance(entries, list) or len(entries) > MAX_RECIPIENT_ENTRIES:
        raise ValueError("CoS replies require a list of at most 100 recipients")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "member_id",
            "channel",
            "address",
        }:
            raise ValueError("each recipient needs a member, channel, and address")
        member_id = entry["member_id"]
        if (
            not isinstance(member_id, str)
            or not member_id
            or len(member_id) > 240
            or member_id != member_id.strip()
            or any(ord(char) < 32 for char in member_id)
            or (known_members is not None and member_id not in known_members)
        ):
            raise ValueError("recipient must belong to a known household member")
        channel = entry["channel"]
        address = normalize_recipient(channel, entry["address"])
        key = (channel, address)
        if key in seen:
            raise ValueError("each channel recipient must name exactly one member")
        seen.add(key)
        normalized.append(
            {"member_id": member_id, "channel": channel, "address": address}
        )
    return sorted(
        normalized, key=lambda row: (row["channel"], row["address"], row["member_id"])
    )


async def policy_from_event_log(log: Any, tenant_id: str) -> dict[str, Any]:
    """Replay this tenant's policy without a plaintext contact cache."""

    if log is None or not tenant_id:
        raise RuntimeError("CoS response policy event log is unavailable")
    policy: dict[str, Any] = {
        "contract": POLICY_CONTRACT,
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "revision": 0,
        "entries": [],
        "event_id": None,
    }
    async for event in log.read_since(types=[POLICY_EVENT_TYPE]):
        if event.get("tenant_id") != tenant_id:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("CoS response policy event is invalid")
        try:
            revision = payload["revision"]
            if (
                payload.get("contract") != POLICY_CONTRACT
                or payload.get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID
                or type(revision) is not int
                or revision != policy["revision"] + 1
                or not payload.get("changed_by_member_id")
                or payload.get("provider_effects") is not False
                or payload.get("grants_provider_authority") is not False
            ):
                raise ValueError("invalid policy revision or service coordinates")
            entries = validate_recipient_entries(payload["entries"], None)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("CoS response policy replay failed closed") from exc
        policy = {
            "contract": POLICY_CONTRACT,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "revision": revision,
            "entries": entries,
            "event_id": event["event_id"],
        }
    return policy


def recipient_decision(
    policy: Mapping[str, Any],
    *,
    channel: str,
    recipients: list[str],
    member_id: str,
    service_principal_id: str = COS_SERVICE_PRINCIPAL_ID,
) -> dict[str, Any]:
    """Fail closed unless every recipient belongs to the active member.

    The caller must supply every To/Cc/Bcc/group participant; the supported
    transport may impose the stricter one-recipient/no-group restriction.
    """

    result = {
        "allowed": False,
        "reason": "recipient_not_allowed",
        "policy_revision": policy.get("revision", 0),
        "policy_event_id": policy.get("event_id"),
    }
    if (
        policy.get("contract") != POLICY_CONTRACT
        or policy.get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID
        or service_principal_id != COS_SERVICE_PRINCIPAL_ID
        or type(policy.get("revision")) is not int
        or policy["revision"] < 1
        or not policy.get("event_id")
        or not member_id
        or not isinstance(recipients, list)
        or not recipients
        or len(recipients) > MAX_RECIPIENT_ENTRIES
    ):
        return {**result, "reason": "invalid_response_coordinates"}
    try:
        entries = validate_recipient_entries(policy.get("entries"), None)
        destinations = [normalize_recipient(channel, value) for value in recipients]
    except (TypeError, ValueError):
        return {**result, "reason": "invalid_recipient"}
    allowed = {
        row["address"]
        for row in entries
        if row["channel"] == channel and row["member_id"] == member_id
    }
    if any(value not in allowed for value in destinations):
        return result
    return {**result, "allowed": True, "reason": "recipients_allowed"}
