"""The retained acquisition overlay grants no implicit member source authority."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from adminme.adapters.cos_channel_acquisition import (
    MEMBER_BRIDGE_CAPABILITIES,
    MEMBER_BRIDGE_ACQUISITION_CONTRACT,
    CosAcquisitionPolicy,
    authorized_member_bridge_packs,
    member_bridge_capability_ceiling,
)
from adminme.adapters.disposable_test_acquisition import DisposableTestAcquisition
from adminme.bridge_enrollment import CAPABILITIES, capability_set_digest


def _config() -> dict[str, Any]:
    retained = DisposableTestAcquisition(
        "retained-mail", "member-fixture", "retained@example.invalid", ("old-id",)
    )
    account = "service@example.invalid"
    policy = CosAcquisitionPolicy(
        retained_test_policy_sha256=retained.receipt_digest(),
        gmail_adapter_id="cos-mail",
        gmail_channel_id="cos-mail-channel",
        gmail_provider_account_id=account,
    )
    return {
        "disposable_test_acquisition": retained.private_config(),
        "cos_channel_acquisition": policy.model_dump(mode="json"),
        "adapters": [
            {
                "id": "cos-mail",
                "kind": "messaging-gmail-api",
                "config": {
                    "service_principal_id": policy.service_principal_id,
                    "provider_account_id": account,
                    "user_email": account,
                    "channel_id": policy.gmail_channel_id,
                    "trusted_sender_routes": [{"route_key": "synthetic"}],
                },
            }
        ],
        "service_identities": {
            "channels": [
                {
                    "channel_id": policy.gmail_channel_id,
                    "service_principal_id": policy.service_principal_id,
                    "provider": "gmail",
                    "enrolled": True,
                    "state": "healthy",
                    "intake_mode": "member_submission",
                    "provider_account_reference": "provider-account:"
                    + hashlib.sha256(account.encode()).hexdigest(),
                }
            ]
        },
    }


def _authority() -> tuple[dict[str, Any], dict[str, Any]]:
    capabilities = sorted(MEMBER_BRIDGE_CAPABILITIES)
    packs = sorted(str(CAPABILITIES[key]["pack"]) for key in capabilities)
    common = {
        "request_id": "request-fixture",
        "enrollment_id": "enrollment-fixture",
        "member_id": "member-fixture",
        "release_commit": "a" * 40,
        "capability_set_sha256": capability_set_digest(capabilities),
        "device_identity_sha256": hashlib.sha256(b"node-fixture").hexdigest(),
        "observation_only": True,
        "acquisition_policy": MEMBER_BRIDGE_ACQUISITION_CONTRACT,
    }
    record = {
        **common,
        "role": "principal",
        "instance_id": "tenant-fixture",
        "owner_scope": "private:member-fixture",
        "status": "active",
        "bridge_node_id": "node-fixture",
        "requested_capabilities": capabilities,
        "allowed_packs": packs,
        "configured_packs": packs,
        "authorized_packs": packs,
        "enabled_packs": packs,
        "pack_config": {pack: {} for pack in packs},
    }
    wizard = {
        **common,
        "requested_by_member_id": "member-fixture",
        "writes_authorized": False,
        "state": "active_observation",
        "capabilities": capabilities,
    }
    return record, wizard


def _authorized(config, record, wizard, **kwargs):
    return authorized_member_bridge_packs(
        config,
        member_id="member-fixture",
        instance_id="tenant-fixture",
        record=record,
        wizard=wizard,
        **kwargs,
    )


def test_full_overlay_is_only_a_ceiling_and_preserves_retained_history() -> None:
    config = _config()
    before = copy.deepcopy(config)
    assert member_bridge_capability_ceiling({}) is None
    assert member_bridge_capability_ceiling(config) == MEMBER_BRIDGE_CAPABILITIES
    record, wizard = _authority()
    assert _authorized(config, record, wizard) == frozenset(record["allowed_packs"])
    assert config == before
    with pytest.raises(ValueError):
        _authorized(config, {}, {})
    config.pop("cos_channel_acquisition")
    assert member_bridge_capability_ceiling(config) == frozenset()
    with pytest.raises(ValueError):
        _authorized(config, record, wizard)


@pytest.mark.parametrize("damage", ["selection", "channel", "account", "extra"])
def test_overlay_must_validate_completely(damage: str) -> None:
    config = _config()
    if damage == "selection":
        config["disposable_test_acquisition"]["message_ids"].append("changed")
    elif damage == "channel":
        config["service_identities"]["channels"][0]["enrolled"] = False
    elif damage == "account":
        config["adapters"][0]["config"]["member_id"] = "member-fixture"
    else:
        config["cos_channel_acquisition"]["allow_all_bridges"] = True
    assert member_bridge_capability_ceiling(config) == frozenset()


@pytest.mark.parametrize(
    ("side", "key", "value"),
    [
        ("record", "member_id", "other-member"),
        ("wizard", "requested_by_member_id", "other-member"),
        ("record", "instance_id", "other-tenant"),
        ("record", "owner_scope", "shared:household"),
        ("record", "role", "child"),
        ("record", "request_id", "different-request"),
        ("wizard", "enrollment_id", "different-enrollment"),
        ("wizard", "release_commit", "b" * 40),
        ("record", "capability_set_sha256", "0" * 64),
        ("record", "observation_only", False),
        ("record", "acquisition_policy", None),
        ("wizard", "writes_authorized", True),
        ("record", "status", "revoked"),
        ("wizard", "state", "paused"),
        ("record", "bridge_node_id", "other-node"),
        ("wizard", "device_identity_sha256", "0" * 64),
        ("record", "enabled_packs", ["messaging-bluebubbles"]),
        ("record", "authorized_packs", ["contacts-apple-contacts-bridge"]),
        ("record", "requested_capabilities", ["apple_messages_read"]),
        ("record", "requested_capabilities", ["apple_notes", "apple_notes"]),
        ("record", "configured_packs", None),
    ],
)
def test_mismatched_or_widened_authority_fails_closed(side, key, value) -> None:
    record, wizard = _authority()
    (record if side == "record" else wizard)[key] = value
    with pytest.raises(ValueError):
        _authorized(_config(), record, wizard)


def test_content_free_progress_can_establish_but_not_bypass_device_binding() -> None:
    record, wizard = _authority()
    record["status"] = "package-exported"
    wizard["state"] = "awaiting_transfer"
    record.pop("device_identity_sha256")
    wizard.pop("device_identity_sha256")
    assert _authorized(_config(), record, wizard, require_device=False)
    with pytest.raises(ValueError, match="device_binding"):
        _authorized(_config(), record, wizard)


def test_selected_subset_remains_exact() -> None:
    record, wizard = _authority()
    selected = ["apple_notes"]
    pack = str(CAPABILITIES["apple_notes"]["pack"])
    record["requested_capabilities"] = wizard["capabilities"] = selected
    record["capability_set_sha256"] = wizard["capability_set_sha256"] = (
        capability_set_digest(selected)
    )
    for key in (
        "allowed_packs",
        "configured_packs",
        "authorized_packs",
        "enabled_packs",
    ):
        record[key] = [pack]
    record["pack_config"] = {pack: {}}
    assert _authorized(_config(), record, wizard) == frozenset({pack})


def test_private_exception_never_inherits_shared_container_authority() -> None:
    record, wizard = _authority()
    pack = str(CAPABILITIES["apple_calendar"]["pack"])
    record["pack_config"][pack]["shared_container_ids"] = ["container-fixture"]
    with pytest.raises(ValueError, match="private_sources_required"):
        _authorized(_config(), record, wizard)
