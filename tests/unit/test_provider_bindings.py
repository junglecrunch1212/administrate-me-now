from __future__ import annotations

import pytest

from adminme.provider_bindings import (
    public_binding_inventory,
    validate_provider_bindings,
)


def _entry(
    *, member: str = "member-a", account: str = "account-a@example.invalid"
) -> dict[str, object]:
    return {
        "id": f"gmail-{member}",
        "kind": "messaging-gmail-api",
        "config": {
            "member_id": member,
            "user_email": account,
            "provider_account_id": account,
            "workspace": "personal",
            "permission_scope": ["gmail.read"],
            "binding_provenance": "oauth-subject",
            "ownership_attested": True,
            "oauth_credentials_ref": "keychain://fixture/gmail",
        },
    }


def test_explicit_provider_binding_is_accepted() -> None:
    [binding] = validate_provider_bindings(
        {"adapters": [_entry()]},
        member_roles={"member-a": "principal"},
        household_mapping={},
    )
    assert binding.member_id == "member-a"
    assert binding.workspace == "personal"
    assert binding.permission_scope == ("gmail.read",)
    assert public_binding_inventory((binding,))["owner_kind_counts"] == {"member": 1}


@pytest.mark.parametrize(
    "missing",
    [
        "member_id",
        "provider_account_id",
        "workspace",
        "permission_scope",
        "binding_provenance",
        "ownership_attested",
    ],
)
def test_missing_provider_binding_coordinate_fails_closed(missing: str) -> None:
    entry = _entry()
    config = entry["config"]
    assert isinstance(config, dict)
    config.pop(missing)
    if missing == "provider_account_id":
        config.pop("user_email")
    with pytest.raises(ValueError):
        validate_provider_bindings(
            {"adapters": [entry]},
            member_roles={"member-a": "principal"},
            household_mapping={},
        )


def test_unknown_member_and_cross_member_account_reuse_fail_closed() -> None:
    with pytest.raises(ValueError, match="unknown member"):
        validate_provider_bindings(
            {"adapters": [_entry(member="unknown")]},
            member_roles={"member-a": "principal"},
            household_mapping={},
        )
    second = _entry(member="member-b")
    second["id"] = "calendar-member-b"
    second["kind"] = "calendaring-google-calendar"
    with pytest.raises(ValueError, match="contradictory owner or workspace"):
        validate_provider_bindings(
            {"adapters": [_entry(), second]},
            member_roles={"member-a": "principal", "member-b": "principal"},
            household_mapping={},
        )

    second_config = second["config"]
    assert isinstance(second_config, dict)
    second_config["workspace"] = "work"
    with pytest.raises(ValueError, match="contradictory owner or workspace"):
        validate_provider_bindings(
            {"adapters": [_entry(), second]},
            member_roles={"member-a": "principal", "member-b": "principal"},
            household_mapping={},
        )


def test_disabled_optional_provider_may_remain_unconfigured() -> None:
    assert (
        validate_provider_bindings(
            {"adapters": [{"kind": "messaging-gmail-api", "enabled": False}]},
            member_roles={},
            household_mapping={},
        )
        == ()
    )


def test_provider_account_may_bind_to_a_distinct_service_principal() -> None:
    entry = _entry()
    config = entry["config"]
    assert isinstance(config, dict)
    config.pop("member_id")
    config["service_principal_id"] = "service-principal:chief-of-staff"
    [binding] = validate_provider_bindings(
        {"adapters": [entry]},
        member_roles={"member-a": "principal"},
        household_mapping={},
        service_principal_ids={"service-principal:chief-of-staff"},
    )
    assert binding.owner_kind == "service_principal"
    assert binding.member_id is None
    assert binding.service_principal_id == "service-principal:chief-of-staff"


def test_provider_binding_rejects_member_and_service_owner_collapse() -> None:
    entry = _entry()
    config = entry["config"]
    assert isinstance(config, dict)
    config["service_principal_id"] = "service-principal:chief-of-staff"
    with pytest.raises(ValueError, match="exactly one owner principal"):
        validate_provider_bindings(
            {"adapters": [entry]},
            member_roles={"member-a": "principal"},
            household_mapping={},
            service_principal_ids={"service-principal:chief-of-staff"},
        )
