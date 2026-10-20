"""Content-free authorization for separately activated CoS channel operation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.adapters.cos_channel_acquisition import (
    policy_from_config,
    restrict_cos_adapter_config,
)


CONTRACT = "cos-channel-operation-v1"
RECEIPT_FILENAME = "cos-channel-operation.json"
ACTIVATED_EVENT = "cos.channels.activated"
DEACTIVATED_EVENT = "cos.channels.deactivated"


def aware_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


class SenderProof(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    channel: Literal["email", "imessage"]
    provider_account_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sender_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: str

    @model_validator(mode="after")
    def valid_time(self) -> SenderProof:
        if aware_time(self.observed_at) is None:
            raise ValueError("sender proof requires an aware observation timestamp")
        return self


class CosChannelActivation(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    contract: Literal["cos-channel-operation-v1"] = "cos-channel-operation-v1"
    activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    release_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    enabled_at: str
    acquisition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowlist_event_id: str = Field(min_length=1, max_length=240)
    allowlist_revision: int = Field(ge=1)
    channels: list[SenderProof] = Field(min_length=1, max_length=2)
    backup_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rehearsal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    observation_window_started_at: str
    suppressed_backlog_released: Literal[False] = False

    @model_validator(mode="after")
    def valid_boundary(self) -> CosChannelActivation:
        enabled = aware_time(self.enabled_at)
        started = aware_time(self.observation_window_started_at)
        if enabled is None or started is None or started >= enabled:
            raise ValueError("CoS activation requires an exact observation interval")
        if len({proof.channel for proof in self.channels}) != len(self.channels):
            raise ValueError("CoS activation channel proofs must be distinct")
        for proof in self.channels:
            observed = aware_time(proof.observed_at)
            if (
                observed is None
                or not 0 <= (enabled - observed).total_seconds() <= 86400
            ):
                raise ValueError("CoS activation sender proof is absent or expired")
        return self


def read_receipt(
    config_dir: Path, *, allow_verification: bool = False
) -> dict[str, Any]:
    journal = config_dir.parent.parent / "app" / "cos-channel-operation" / "active.json"
    if journal.exists() and not allow_verification:
        return {}
    path = config_dir / RECEIPT_FILENAME
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ValueError("CoS operation receipt cannot be a symlink")
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("CoS operation receipt must be an object")
    return value


async def live_authorization(
    event_log: Any,
    tenant_id: str,
    adapter_config: dict[str, Any],
    receipt: dict[str, Any],
    *,
    channel: str | None = None,
    provider_account_id: str | None = None,
    requested_at: str | None = None,
) -> dict[str, Any]:
    """Verify the receipt against durable facts and the current nonempty policy.

    Initial policy revision is provenance, not a permanent pin: subsequent
    Settings changes apply immediately and individual approvals remain bound
    to their exact revision in the response executor.
    """

    from adminme.cos_response_policy import policy_from_event_log

    refused = {"authorized": False, "reason": "cos_live_authorization_missing"}
    try:
        if set(receipt) != {"event_id", "activation"}:
            return refused
        activation = CosChannelActivation.model_validate(receipt["activation"])
        policy = policy_from_config(adapter_config)
        restrict_cos_adapter_config(adapter_config)
        if policy is None or policy.digest() != activation.acquisition_sha256:
            return refused
        latest: dict[str, Any] | None = None
        async for event in event_log.read_since(
            None, types=[ACTIVATED_EVENT, DEACTIVATED_EVENT]
        ):
            if event.get("tenant_id") == tenant_id:
                latest = event
        if (
            latest is None
            or latest.get("type") != ACTIVATED_EVENT
            or latest.get("event_id") != receipt["event_id"]
            or latest.get("payload") != activation.model_dump(mode="json")
        ):
            return refused
        current = await policy_from_event_log(event_log, tenant_id)
        if (
            not current.get("entries")
            or not current.get("event_id")
            or current.get("service_principal_id") != policy.service_principal_id
            or current.get("revision", 0) < activation.allowlist_revision
        ):
            return {"authorized": False, "reason": "cos_recipient_allowlist_empty"}
        enabled = aware_time(activation.enabled_at)
        if enabled is None or enabled > datetime.now(timezone.utc):
            return refused
        if channel is not None:
            matches = [
                proof for proof in activation.channels if proof.channel == channel
            ]
            if (
                len(matches) != 1
                or not provider_account_id
                or matches[0].provider_account_sha256
                != hashlib.sha256(provider_account_id.encode()).hexdigest()
            ):
                return {"authorized": False, "reason": "cos_sender_not_activated"}
            if (
                channel == "email"
                and provider_account_id != policy.gmail_provider_account_id
            ):
                return {"authorized": False, "reason": "cos_sender_not_activated"}
            if channel == "imessage" and policy.imessage_channel_id is None:
                return {"authorized": False, "reason": "cos_sender_not_activated"}
        if requested_at is not None:
            requested = aware_time(requested_at)
            if requested is None or requested < enabled:
                return {"authorized": False, "reason": "pre_activation_action_refused"}
        return {
            "authorized": True,
            "activation_id": activation.activation_id,
            "event_id": receipt["event_id"],
            "enabled_at": activation.enabled_at,
            "release_commit": activation.release_commit,
            "allowlist_revision": current["revision"],
            "activated_channels": [proof.channel for proof in activation.channels],
        }
    except Exception:
        return refused


async def require_cos_response_activation(
    runtime: Any,
    *,
    channel: str,
    provider_account_id: str,
    requested_at: str,
) -> dict[str, Any]:
    """Convenience seam for final outbound guards; never raises to fail open."""

    return await runtime_live_authorization(
        runtime,
        channel=channel,
        provider_account_id=provider_account_id,
        requested_at=requested_at,
    )


async def runtime_live_authorization(
    runtime: Any,
    *,
    channel: str | None = None,
    provider_account_id: str | None = None,
    requested_at: str | None = None,
    allow_verification: bool = False,
) -> dict[str, Any]:
    """Verify both the durable authorization and any native transport files."""

    try:
        config_dir = runtime.settings.instance.config_dir
        receipt = read_receipt(config_dir, allow_verification=allow_verification)
        acquisition = policy_from_config(runtime.settings.adapter_config)
        if acquisition is not None and acquisition.imessage_channel_id is not None:
            from bootstrap.cos_imessage import (
                validate_prepared_channel,
                verify_guard_files,
            )

            guard = json.loads((config_dir / "cos-imsg-guard.json").read_text("utf-8"))
            gateway = json.loads(
                (config_dir.parent / "openclaw/runtime/openclaw.json").read_text(
                    "utf-8"
                )
            )
            validate_prepared_channel(
                gateway, guard, adapter_config=runtime.settings.adapter_config
            )
            verify_guard_files(guard, reviewed_release=runtime.settings.repository_root)
    except Exception:
        return {"authorized": False, "reason": "cos_live_authorization_missing"}
    return await live_authorization(
        runtime.event_log,
        runtime.settings.instance.tenant_id,
        runtime.settings.adapter_config,
        receipt,
        channel=channel,
        provider_account_id=provider_account_id,
        requested_at=requested_at,
    )
