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


def _apple_entry(
    *,
    kind: str = "calendaring-apple-calendar",
    deployment_target: str = "bridge",
) -> dict[str, object]:
    return {
        "id": f"{kind}-member-a",
        "kind": kind,
        "config": {
            "member_id": "member-a",
            "provider_account_id": "provider-account:" + "a" * 32,
            "deployment_target": deployment_target,
            "workspace": "personal",
            "permission_scope": ["eventkit.read", "eventkit.write"],
            "binding_provenance": "member-bridge-enrollment",
            "ownership_attested": True,
        },
    }


def _service_entry(kind: str, scope: str) -> dict[str, object]:
    config: dict[str, object] = {
        "service_principal_id": "service-principal:chief-of-staff",
        "user_email": "cos-service@example.invalid",
        "provider_account_id": "cos-service@example.invalid",
        "workspace": "service",
        "permission_scope": [scope],
        "binding_provenance": "service-account-delegation",
        "ownership_attested": True,
        "service_account_key_ref": "op://Fixture/cos-google/service-account-json",
        "read_only": True,
    }
    if kind == "calendaring-google-calendar":
        config["calendar_id"] = "primary"
    if kind == "tasks-google-tasks":
        config["tasklist_id"] = "@default"
    return {"id": f"cos-{kind}", "kind": kind, "config": config}


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


def test_enabled_unbound_provider_never_becomes_a_fixture_exemption() -> None:
    with pytest.raises(ValueError, match="requires binding evidence"):
        validate_provider_bindings(
            {"adapters": [{"kind": "calendaring-fixture-provider"}]},
            member_roles={"member-a": "principal"},
            household_mapping={},
        )


def test_planned_service_principal_cannot_gain_authority_from_call_site_allowlist() -> (
    None
):
    entry = _entry()
    entry["id"] = "reviewed-service-channel"
    entry["kind"] = "messaging-reviewed-service-channel"
    config = entry["config"]
    assert isinstance(config, dict)
    config.pop("member_id")
    config["service_principal_id"] = "service-principal:chief-of-staff"
    with pytest.raises(ValueError, match="does not support service ownership"):
        validate_provider_bindings(
            {"adapters": [entry]},
            member_roles={"member-a": "principal"},
            household_mapping={},
            service_principal_ids={"service-principal:chief-of-staff"},
            service_principal_provider_authorizations={
                "service-principal:chief-of-staff": {
                    "messaging-reviewed-service-channel"
                }
            },
        )


@pytest.mark.parametrize(
    "kind",
    [
        "calendaring-apple-calendar",
        "contacts-apple-contacts",
        "reminders-apple-eventkit",
    ],
)
def test_current_member_apple_adapters_never_enter_central_inventory(kind: str) -> None:
    for entry in (
        _apple_entry(kind=kind, deployment_target="central"),
        _apple_entry(kind=kind, deployment_target="bridge"),
    ):
        with pytest.raises(ValueError, match="bridge-only"):
            validate_provider_bindings(
                {"adapters": [entry]},
                member_roles={"member-a": "principal"},
                household_mapping={},
            )


@pytest.mark.parametrize(
    ("kind", "scope"),
    [
        ("messaging-gmail-api", "https://www.googleapis.com/auth/gmail.readonly"),
        (
            "calendaring-google-calendar",
            "https://www.googleapis.com/auth/calendar.readonly",
        ),
        ("tasks-google-tasks", "https://www.googleapis.com/auth/tasks.readonly"),
    ],
)
def test_reviewed_google_sources_accept_exact_service_ownership(
    kind: str, scope: str
) -> None:
    [binding] = validate_provider_bindings(
        {"adapters": [_service_entry(kind, scope)]},
        member_roles={"member-a": "principal"},
        household_mapping={},
        service_principal_ids={"service-principal:chief-of-staff"},
    )
    assert binding.owner_kind == "service_principal"
    assert binding.member_id is None
    assert binding.workspace == "service"
    assert binding.permission_scope == (scope,)
    assert binding.owner_scope == ("service-private:service-principal:chief-of-staff")


def test_service_workspace_cannot_fake_member_ownership() -> None:
    entry = _entry()
    config = entry["config"]
    assert isinstance(config, dict)
    config["workspace"] = "service"
    with pytest.raises(ValueError, match="cannot fake member ownership"):
        validate_provider_bindings(
            {"adapters": [entry]},
            member_roles={"member-a": "principal"},
            household_mapping={},
        )


def test_full_service_owner_account_adapter_collision_fails_closed() -> None:
    first = _service_entry(
        "messaging-gmail-api", "https://www.googleapis.com/auth/gmail.readonly"
    )
    second = _service_entry(
        "messaging-gmail-api", "https://www.googleapis.com/auth/gmail.readonly"
    )
    second["id"] = "cos-gmail-duplicate"
    with pytest.raises(ValueError, match="full owner/account/adapter coordinate"):
        validate_provider_bindings(
            {"adapters": [first, second]},
            member_roles={"member-a": "principal"},
            household_mapping={},
            service_principal_ids={"service-principal:chief-of-staff"},
        )


def test_provider_account_cannot_equal_service_principal() -> None:
    entry = _entry()
    config = entry["config"]
    assert isinstance(config, dict)
    config.pop("member_id")
    config["service_principal_id"] = "service-principal:chief-of-staff"
    config["provider_account_id"] = "service-principal:chief-of-staff"
    config["user_email"] = "service-principal:chief-of-staff"
    config["workspace"] = "service"
    with pytest.raises(ValueError, match="collapses owner and provider account"):
        validate_provider_bindings(
            {"adapters": [entry]},
            member_roles={"member-a": "principal"},
            household_mapping={},
            service_principal_ids={"service-principal:chief-of-staff"},
            service_principal_provider_authorizations={
                "service-principal:chief-of-staff": {"messaging-gmail-api"}
            },
        )


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
