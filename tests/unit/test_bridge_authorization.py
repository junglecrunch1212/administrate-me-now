from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from adminme.bridge_authorization import (
    BRIDGE_PACK_POLICIES,
    authorization_from_record,
    configuration_from_record,
    validate_bridge_record_set,
    validate_configured_pack_config,
)

from adminme.bridge_enrollment import BridgeEnrollmentCoordinator, CAPABILITIES


CALENDAR_PACK = "calendaring-apple-calendar-bridge"
NOTES_PACK = "knowledge-apple-notes"


def _calendar_config(member_id: str, account: str) -> dict[str, object]:
    return {
        "member_id": member_id,
        "provider_account_id": account,
        "deployment_target": "bridge",
        "workspace": "personal",
        "permission_scope": [
            "eventkit.calendar.read",
            "eventkit.calendar.write",
        ],
        "calendar_identifier": "calendar-private",
        "allowed_container_ids": ["calendar-private", "calendar-shared"],
        "shared_container_ids": ["calendar-shared"],
        "binding_provenance": "member-bridge-enrollment",
        "ownership_attested": True,
    }


def _record(member_id: str, enrollment_id: str, account: str) -> dict[str, object]:
    return {
        "status": "active",
        "member_id": member_id,
        "enrollment_id": enrollment_id,
        "configured_packs": [CALENDAR_PACK, NOTES_PACK],
        "enabled_packs": [CALENDAR_PACK],
        "authorized_packs": [NOTES_PACK],
        "pack_config": {
            CALENDAR_PACK: _calendar_config(member_id, account),
            NOTES_PACK: {"member_id": member_id},
        },
        "pack_capability_states": {
            CALENDAR_PACK: "not_attested",
            NOTES_PACK: "not_required",
        },
        "provider_capability_state": "not_attested",
        "capability_attestations": {},
    }


def test_transport_configuration_does_not_grant_unattested_provider_pack() -> None:
    record = _record("member-a", "enrollment-a", "provider-account:" + "a" * 32)

    configured = configuration_from_record(record)
    authorized = authorization_from_record(record)

    assert configured is not None
    assert configured.packs == frozenset({CALENDAR_PACK, NOTES_PACK})
    assert authorized is not None
    assert authorized.packs == frozenset({NOTES_PACK})
    assert authorized.policy_for_provider("apple_calendar") is None


def test_exact_capability_receipt_promotes_only_the_attested_pack() -> None:
    record = _record("member-a", "enrollment-a", "provider-account:" + "a" * 32)
    record["authorized_packs"] = [CALENDAR_PACK, NOTES_PACK]
    record["pack_capability_states"] = {
        CALENDAR_PACK: "attested",
        NOTES_PACK: "not_required",
    }
    record["provider_capability_state"] = "attested"
    record["capability_attestations"] = {
        CALENDAR_PACK: {"event_id": "ev-calendar-authenticated"}
    }

    authorization = authorization_from_record(record)

    assert authorization is not None
    assert authorization.provider_account_for(CALENDAR_PACK) == (
        "provider-account:" + "a" * 32
    )
    assert authorization.capability_event_ids == {
        CALENDAR_PACK: "ev-calendar-authenticated"
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider_account_id", "member-a", "content-free provider account"),
        ("deployment_target", "central", "deployment_target=bridge"),
        ("workspace", "shared", "workspace=personal"),
        ("ownership_attested", False, "ownership_attested=true"),
        ("shared_container_ids", ["not-allowed"], "must be read-authorized"),
    ],
)
def test_provider_configuration_rejects_collapsed_or_unreviewed_coordinates(
    field: str,
    value: object,
    message: str,
) -> None:
    config = _calendar_config("member-a", "provider-account:" + "a" * 32)
    config[field] = value

    with pytest.raises(ValueError, match=message):
        validate_configured_pack_config(
            member_id="member-a",
            enrollment_id="enrollment-a",
            configured_packs=[CALENDAR_PACK],
            pack_config={CALENDAR_PACK: config},
        )


def test_same_apple_account_may_be_reused_only_by_the_same_member() -> None:
    account = "provider-account:" + "a" * 32
    first = _record("member-a", "enrollment-a", account)
    duplicate_owner = _record("member-b", "enrollment-b", account)

    with pytest.raises(ValueError, match="contradictory member ownership"):
        validate_bridge_record_set(
            {
                "a@example.invalid": first,
                "b@example.invalid": duplicate_owner,
            }
        )

    same_member = copy.deepcopy(first)
    same_member["enrollment_id"] = "enrollment-b"
    with pytest.raises(ValueError, match="member bindings must be unique"):
        validate_bridge_record_set(
            {"a@example.invalid": first, "b@example.invalid": same_member}
        )


@pytest.mark.parametrize(
    ("existing", "configuring", "expected"),
    [
        ("apple_calendar", "apple_reminders", "partial"),
        ("apple_reminders", "apple_calendar", "partial"),
        ("apple_calendar", "apple_notes", "attested"),
        ("apple_notes", "apple_calendar", "not_attested"),
        (None, "apple_notes", "not_required"),
    ],
)
def test_configure_one_source_preserves_independent_attested_authority(
    tmp_path: Path, existing: str | None, configuring: str, expected: str
) -> None:
    member, request, enrollment = (
        "member-fixture",
        "configure-aggregate-fixture",
        "enrollment-fixture",
    )

    def config_for(capability: str) -> dict[str, Any]:
        policy = BRIDGE_PACK_POLICIES[str(CAPABILITIES[capability]["pack"])]
        if not policy.requires_provider_account:
            return {
                "member_id": member,
                "automation_mode": "notes_scripting",
                "emit_schema_version": 2,
            }
        assert policy.container_key is not None
        return {
            "member_id": member,
            "provider_account_id": "provider-account:" + "f" * 32,
            "deployment_target": "bridge",
            "workspace": "personal",
            "permission_scope": sorted(policy.permission_scopes),
            "binding_provenance": "member-bridge-enrollment",
            "ownership_attested": True,
            policy.container_key: "fixture-private-container",
            "allowed_container_ids": ["fixture-private-container"],
            "shared_container_ids": [],
        }

    previous_pack = str(CAPABILITIES[existing]["pack"]) if existing else None
    target_pack = str(CAPABILITIES[configuring]["pack"])
    previous = [previous_pack] if previous_pack else []
    states = {
        pack: "attested"
        if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
        else "not_required"
        for pack in previous
    }
    attestations = {
        pack: {"event_id": "ev-existing-authenticated", "content_exposed": False}
        for pack in previous
        if states[pack] == "attested"
    }
    record = {
        "status": "active",
        "member_id": member,
        "request_id": request,
        "enrollment_id": enrollment,
        "allowed_packs": [*previous, target_pack],
        "configured_packs": previous.copy(),
        "enabled_packs": previous.copy(),
        "authorized_packs": previous.copy(),
        "pack_config": {previous_pack: config_for(existing)} if existing else {},
        "pack_capability_states": states,
        "capability_attestations": copy.deepcopy(attestations),
        "provider_capability_state": "attested" if attestations else "not_required",
    }
    coordinator = BridgeEnrollmentCoordinator(
        instance_dir=tmp_path / "instance",
        encryption_key=b"x" * 32,
        repository_root=Path(__file__).resolve().parents[2],
        tenant_id="tenant-fixture",
        member_identities={"fixture@example.invalid": member},
        member_roles={member: "principal"},
        bridge_enrollments={"fixture@example.invalid": record},
    )
    coordinator.configure_capability(
        request_id=request,
        member_id=member,
        capability_id=configuring,
        pack_config=config_for(configuring),
    )
    assert record["provider_capability_state"] == expected
    assert record["authorized_packs"] == previous
    assert record["enabled_packs"] == previous
    assert record["capability_attestations"] == attestations
    assert record["pack_capability_states"][target_pack] == (
        "not_attested"
        if BRIDGE_PACK_POLICIES[target_pack].requires_capability_attestation
        else "not_required"
    )
    authorized = authorization_from_record(record)
    assert authorized is not None
    assert authorized.packs == frozenset(previous)
    assert target_pack not in authorized.packs
    stored = json.loads(coordinator.index_path.read_bytes())["fixture@example.invalid"]
    assert stored == record
