from __future__ import annotations

import copy

import pytest

from adminme.bridge_authorization import (
    authorization_from_record,
    configuration_from_record,
    validate_bridge_record_set,
    validate_configured_pack_config,
)


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
