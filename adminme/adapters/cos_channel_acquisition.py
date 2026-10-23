"""Exact CoS acquisition overlay; retained personal test history is immutable.

This policy never enrolls an account. Enrollment, transport validation and the
operational transition belong to the separately guarded bootstrap operation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

CONFIG_KEY = "cos_channel_acquisition"
CONTRACT = "cos-forwarded-acquisition-v1"
MEMBER_BRIDGE_ACQUISITION_CONTRACT = "cos-member-source-observation-v1"
MEMBER_BRIDGE_CAPABILITIES = frozenset(
    {"apple_calendar", "apple_reminders", "apple_notes"}
)


class CosAcquisitionPolicy(BaseModel):
    model_config = {"extra": "forbid", "frozen": True, "strict": True}

    contract: Literal["cos-forwarded-acquisition-v1"] = "cos-forwarded-acquisition-v1"
    service_principal_id: Literal["service-principal:chief-of-staff"] = (
        "service-principal:chief-of-staff"
    )
    retained_test_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gmail_adapter_id: str = Field(min_length=1, max_length=240)
    gmail_channel_id: str = Field(min_length=1, max_length=240)
    gmail_provider_account_id: str = Field(min_length=1, max_length=500)
    imessage_channel_id: str | None = Field(default=None, max_length=240)
    personal_channel_polling: Literal[False] = False
    historical_expansion: Literal[False] = False

    def digest(self) -> str:
        return configuration_digest(self.model_dump(mode="json"))


def configuration_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def policy_from_config(value: dict[str, Any]) -> CosAcquisitionPolicy | None:
    if CONFIG_KEY not in value:
        return None
    return CosAcquisitionPolicy.model_validate(value[CONFIG_KEY])


def restrict_cos_adapter_config(value: dict[str, Any]) -> dict[str, Any]:
    """Permit the service inbox and separately authorized read-only sources."""

    from adminme.adapters.disposable_test_acquisition import (
        CONFIG_KEY as TEST_CONFIG_KEY,
        DisposableTestAcquisition,
    )

    policy = policy_from_config(value)
    if policy is None:
        return value
    retained = DisposableTestAcquisition.parse(value.get(TEST_CONFIG_KEY))
    if retained.receipt_digest() != policy.retained_test_policy_sha256:
        raise ValueError("CoS acquisition requires the unchanged bounded test policy")
    result = copy.deepcopy(value)
    entries = result.get("adapters")
    channels = (result.get("service_identities") or {}).get("channels")
    if not isinstance(entries, list) or not isinstance(channels, list):
        raise ValueError("CoS acquisition requires exact adapter and channel inventory")
    selected = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id") == policy.gmail_adapter_id
    ]
    if len(selected) != 1:
        raise ValueError("CoS acquisition requires exactly one service Gmail adapter")
    entry = selected[0]
    config = entry.get("config")
    if (
        str(entry.get("kind") or "").removeprefix("adapter:") != "messaging-gmail-api"
        or not isinstance(config, dict)
        or config.get("member_id")
        or config.get("service_principal_id") != policy.service_principal_id
        or config.get("provider_account_id") != policy.gmail_provider_account_id
        or config.get("user_email") != policy.gmail_provider_account_id
        or config.get("channel_id") != policy.gmail_channel_id
        or TEST_CONFIG_KEY in config
    ):
        raise ValueError("CoS acquisition service owner/account binding mismatch")
    for channel_id, provider in (
        (policy.gmail_channel_id, "gmail"),
        (policy.imessage_channel_id, "apple_imessage"),
    ):
        if channel_id is None:
            continue
        matches = [
            item
            for item in channels
            if isinstance(item, dict) and item.get("channel_id") == channel_id
        ]
        if len(matches) != 1:
            raise ValueError("CoS acquisition requires one exact enrolled channel")
        channel = matches[0]
        if (
            channel.get("service_principal_id") != policy.service_principal_id
            or channel.get("provider")
            not in (
                {"apple_imessage", "imessage"}
                if provider == "apple_imessage"
                else {provider}
            )
            or channel.get("enrolled") is not True
            or channel.get("state") not in {"observation", "healthy"}
            or channel.get("intake_mode") != "member_submission"
            or not channel.get("provider_account_reference")
        ):
            raise ValueError("CoS acquisition channel enrollment is unproved")
        if provider == "gmail" and channel.get("provider_account_reference") != (
            "provider-account:"
            + hashlib.sha256(policy.gmail_provider_account_id.encode()).hexdigest()
        ):
            raise ValueError("CoS Gmail account reference is not exact")
        if provider == "apple_imessage" and channel.get("openclaw_channel_id") not in {
            "imessage",
            "bluebubbles",
        }:
            raise ValueError("CoS Messages requires its dedicated OpenClaw transport")
    if (
        not isinstance(config.get("trusted_sender_routes"), list)
        or not config["trusted_sender_routes"]
    ):
        raise ValueError("CoS Gmail requires reviewed member sender routes")
    from adminme.adapters.member_google_connections import (
        selected_entries,
    )

    from adminme.adapters.cos_google_workspace import (
        selected_entries as service_entries,
    )

    from adminme.adapters.cos_apple_reminders import (
        selected_entries as reminders_entries,
    )

    member_sources = selected_entries(result)
    service_sources = service_entries(result) | reminders_entries(result)
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("CoS acquisition adapter inventory is malformed")
        if item.get("id") in member_sources:
            item["config"] = member_sources[item["id"]]["config"]
        elif item.get("id") in service_sources:
            pass  # Preserve the exact selected service source and an explicit pause.
        elif item is not entry:
            item["enabled"] = False
    # A member's explicit connection pause wins. Settings cannot enable a
    # different adapter because this restriction is reapplied by the loader.
    config["initial_backfill_days"] = 0
    config["initial_backfill_max_messages"] = 0
    config["historical_seed_queries"] = []
    config["historical_related_max_participants"] = 0
    config["historical_related_messages_per_participant"] = 0
    return result


def member_bridge_capability_ceiling(
    value: dict[str, Any],
) -> frozenset[str] | None:
    """Return the additional read-only ceiling without granting any bridge.

    Ordinary installations retain their existing enrollment behavior. A bounded
    dataset alone stays closed. Only the fully validated CoS overlay may admit
    the three explicitly selected member source capabilities; retaining its
    history never re-enables a central member adapter or personal inbox.
    """

    from adminme.adapters.disposable_test_acquisition import CONFIG_KEY as TEST_KEY

    if TEST_KEY not in value and CONFIG_KEY not in value:
        return None
    try:
        if policy_from_config(value) is None:
            return frozenset()
        restrict_cos_adapter_config(value)
    except (ValueError, TypeError, AttributeError):
        return frozenset()
    return MEMBER_BRIDGE_CAPABILITIES


def authorized_member_bridge_packs(
    value: dict[str, Any],
    *,
    member_id: str,
    instance_id: str,
    record: Mapping[str, Any],
    wizard: Mapping[str, Any],
    require_device: bool = True,
) -> frozenset[str]:
    """Validate the existing member-owned enrollment, never invent authority.

    This is an additional acquisition ceiling. The caller must still enforce
    transport authentication, provisional proof, per-pack source/container
    contracts, local permission, attestation and enabled-state admission.
    Content-free signed progress may establish its device binding; content and
    the provisional proof must already have that binding.
    """

    from adminme.bridge_enrollment import CAPABILITIES, capability_set_digest

    ceiling = member_bridge_capability_ceiling(value)
    if not ceiling:
        raise ValueError("member_bridge_acquisition_not_enabled")
    selected = record.get("requested_capabilities")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) for item in selected)
        or len(set(selected)) != len(selected)
        or not set(selected) <= ceiling
        or wizard.get("capabilities") != sorted(selected)
    ):
        raise ValueError("member_bridge_capability_selection_mismatch")
    packs = frozenset(str(CAPABILITIES[item]["pack"]) for item in selected)
    if record.get("allowed_packs") != sorted(packs):
        raise ValueError("member_bridge_pack_selection_mismatch")
    digest = capability_set_digest(selected)
    release = record.get("release_commit")
    if (
        record.get("member_id") != member_id
        or wizard.get("member_id") != member_id
        or wizard.get("requested_by_member_id") != member_id
        or record.get("instance_id") != instance_id
        or record.get("owner_scope") != f"private:{member_id}"
        or record.get("role") != "principal"
        or record.get("observation_only") is not True
        or record.get("acquisition_policy") != MEMBER_BRIDGE_ACQUISITION_CONTRACT
        or wizard.get("observation_only") is not True
        or wizard.get("writes_authorized") is not False
        or not isinstance(release, str)
        or re.fullmatch(r"[0-9a-f]{40}", release) is None
        or record.get("capability_set_sha256") != digest
        or wizard.get("capability_set_sha256") != digest
        or any(
            not isinstance(record.get(key), str)
            or not record.get(key)
            or record.get(key) != wizard.get(key)
            for key in ("request_id", "enrollment_id", "release_commit")
        )
        or record.get("status") not in {"package-exported", "active"}
        or wizard.get("state")
        not in {
            "package_ready",
            "awaiting_transfer",
            "package_opened",
            "instance_verified",
            "member_confirmed",
            "device_identity_verified",
            "bridge_paired",
            "awaiting_permissions",
            "capabilities_testing",
            "settling",
            "active_observation",
            "partially_active",
            "degraded",
        }
    ):
        raise ValueError("member_bridge_enrollment_binding_mismatch")
    for key in ("configured_packs", "authorized_packs", "enabled_packs"):
        entries = record.get(key)
        if (
            not isinstance(entries, list)
            or any(not isinstance(item, str) for item in entries)
            or len(set(entries)) != len(entries)
            or not set(entries) <= packs
        ):
            raise ValueError("member_bridge_pack_selection_mismatch")
    pack_config = record.get("pack_config")
    if (
        not isinstance(pack_config, dict)
        or set(pack_config) != set(record["configured_packs"])
        or any(
            not isinstance(config, dict) or config.get("shared_container_ids", []) != []
            for config in pack_config.values()
        )
    ):
        raise ValueError("member_bridge_private_sources_required")
    if require_device:
        device_digest = record.get("device_identity_sha256")
        if (
            not isinstance(device_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", device_digest) is None
            or wizard.get("device_identity_sha256") != device_digest
            or (
                record.get("status") == "active"
                and (
                    not isinstance(record.get("bridge_node_id"), str)
                    or hashlib.sha256(record["bridge_node_id"].encode()).hexdigest()
                    != device_digest
                )
            )
        ):
            raise ValueError("member_bridge_device_binding_mismatch")
    return packs
