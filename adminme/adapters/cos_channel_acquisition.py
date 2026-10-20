"""Exact CoS acquisition overlay; retained personal test history is immutable.

This policy never enrolls an account. Enrollment, transport validation and the
operational transition belong to the separately guarded bootstrap operation.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

CONFIG_KEY = "cos_channel_acquisition"
CONTRACT = "cos-forwarded-acquisition-v1"


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
    """Permit one enrolled service inbox; never load a member-owned adapter."""

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
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("CoS acquisition adapter inventory is malformed")
        if item is not entry:
            item["enabled"] = False
    # A member's explicit connection pause wins. Settings cannot enable a
    # different adapter because this restriction is reapplied by the loader.
    config["initial_backfill_days"] = 0
    config["initial_backfill_max_messages"] = 0
    config["historical_seed_queries"] = []
    config["historical_related_max_participants"] = 0
    config["historical_related_messages_per_participant"] = 0
    return result
