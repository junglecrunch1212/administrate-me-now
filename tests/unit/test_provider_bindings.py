from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
import pytest

from adminme.adapters.cos_channel_acquisition import CosAcquisitionPolicy
from adminme.adapters.disposable_test_acquisition import DisposableTestAcquisition
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


def _cos_oauth_configuration(*, live: bool) -> dict[str, Any]:
    retained = DisposableTestAcquisition(
        adapter_id="personal-paused",
        member_id="member-a",
        provider_account_id="member-a@example.test",
        message_ids=("retained-message",),
    )
    account = "cos@example.test"
    channel = "channel:chief-of-staff:gmail"
    return {
        "disposable_test_acquisition": retained.private_config(),
        "cos_channel_acquisition": CosAcquisitionPolicy(
            retained_test_policy_sha256=retained.receipt_digest(),
            gmail_adapter_id="cos-oauth",
            gmail_channel_id=channel,
            gmail_provider_account_id=account,
        ).model_dump(mode="json"),
        "service_identities": {
            "channels": [
                {
                    "channel_id": channel,
                    "service_principal_id": "service-principal:chief-of-staff",
                    "provider": "gmail",
                    "enrolled": True,
                    "state": "healthy" if live else "observation",
                    "intake_mode": "member_submission",
                    "provider_account_reference": (
                        "provider-account:"
                        + hashlib.sha256(account.encode()).hexdigest()
                    ),
                }
            ]
        },
        "adapters": [
            {
                "id": "personal-paused",
                "enabled": False,
                "kind": "messaging-gmail-api",
                "config": {"member_id": "member-a"},
            },
            {
                "id": "cos-oauth",
                "kind": "messaging-gmail-api",
                "enabled": True,
                "config": {
                    "service_principal_id": "service-principal:chief-of-staff",
                    "provider_account_id": account,
                    "user_email": account,
                    "channel_id": channel,
                    "workspace": "service",
                    "permission_scope": [
                        "https://www.googleapis.com/auth/gmail.readonly",
                        "https://www.googleapis.com/auth/gmail.send",
                    ],
                    "binding_provenance": "oauth-subject",
                    "ownership_attested": True,
                    "oauth_credentials_ref": "keychain://fixture/cos-gmail",
                    "read_only": not live,
                    "enable_writes": live,
                    "trusted_sender_routes": [
                        {
                            "exact_from_identifier": "member-a@example.test",
                            "route_key": "route:" + "1" * 64,
                            "authentication_policy": "receiver_aligned_sender",
                        }
                    ],
                },
            },
        ],
    }


def _validate_cos_oauth(config: dict[str, Any]) -> Any:
    return validate_provider_bindings(
        config,
        member_roles={"member-a": "principal"},
        household_mapping={},
        service_principal_ids={"service-principal:chief-of-staff"},
    )


@pytest.mark.parametrize("live", [False, True])
def test_exact_enrolled_cos_oauth_binding_accepts_observation_and_governed_send(
    live: bool,
) -> None:
    config = _cos_oauth_configuration(live=live)
    original = copy.deepcopy(config)
    [binding] = _validate_cos_oauth(config)
    assert config == original
    assert binding.member_id is None
    assert binding.owner_scope == "service-private:service-principal:chief-of-staff"
    assert binding.permitted_operations == (("send",) if live else ())
    assert binding.write_binding_state == (
        "governed_cos_response" if live else "read_only"
    )
    assert binding.b03_dispatch_coordinate is None
    assert "cos@example.test" not in str(binding.public())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("provider_account_id", "other@example.test"),
        ("user_email", "other@example.test"),
        ("service_principal_id", "service-principal:other"),
        ("member_id", "member-a"),
        ("channel_id", "channel:other:gmail"),
        ("workspace", "personal"),
        ("permission_scope", ["https://www.googleapis.com/auth/gmail.readonly"]),
        (
            "permission_scope",
            [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        ),
        ("binding_provenance", "service-account-delegation"),
        ("ownership_attested", False),
        ("oauth_credentials_ref", "/tmp/unreviewed-credential"),
        ("service_account_key_ref", "op://fixture/ambiguous/credential"),
        ("read_only", True),
        ("enable_writes", False),
        ("b03_google_tasks_create", {}),
    ],
)
def test_cos_oauth_binding_rejects_inexact_account_grant_or_write_mode(
    key: str, value: Any
) -> None:
    config = _cos_oauth_configuration(live=True)
    config["adapters"][1]["config"][key] = value
    with pytest.raises(ValueError):
        _validate_cos_oauth(config)


@pytest.mark.parametrize(
    "mutation", ["no-overlay", "unselected", "unenrolled", "history"]
)
def test_cos_oauth_binding_requires_selected_enrolled_acquisition_contract(
    mutation: str,
) -> None:
    config = _cos_oauth_configuration(live=True)
    if mutation == "no-overlay":
        config.pop("cos_channel_acquisition")
    elif mutation == "unselected":
        config["cos_channel_acquisition"]["gmail_adapter_id"] = "other"
    elif mutation == "unenrolled":
        config["service_identities"]["channels"][0]["enrolled"] = False
    else:
        config["disposable_test_acquisition"]["message_ids"].append("expanded")
    with pytest.raises(ValueError):
        _validate_cos_oauth(config)


def _gmail_schema() -> Draft202012Validator:
    root = Path(__file__).resolve().parents[2]
    return Draft202012Validator(
        json.loads(
            (root / "packs/adapters/messaging/gmail/config.schema.json").read_text()
        )
    )


@pytest.mark.parametrize("live", [False, True])
def test_gmail_pack_schema_accepts_only_exact_cos_oauth_response_shape(
    live: bool,
) -> None:
    selected = _cos_oauth_configuration(live=live)["adapters"][1]["config"]
    _gmail_schema().validate(selected)
    # Provider grant order is irrelevant to the exact permission set.
    selected["permission_scope"].reverse()
    _gmail_schema().validate(selected)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("service_principal_id", "service-principal:other"),
        ("binding_provenance", "service-account-delegation"),
        ("service_account_key_ref", "op://fixture/ambiguous/credential"),
        ("read_only", True),
        ("enable_writes", False),
        ("trusted_sender_routes", []),
        (
            "permission_scope",
            [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        ),
        (
            "permission_scope",
            [
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.send",
            ],
        ),
    ],
)
def test_gmail_pack_schema_does_not_widen_other_service_accounts_or_grants(
    key: str, value: Any
) -> None:
    selected = _cos_oauth_configuration(live=True)["adapters"][1]["config"]
    selected[key] = value
    with pytest.raises(ValidationError):
        _gmail_schema().validate(selected)


def test_gmail_pack_schema_preserves_personal_and_delegated_readonly_shapes() -> None:
    schema = _gmail_schema()
    schema.validate(_entry()["config"])
    service = _service_entry(
        "messaging-gmail-api", "https://www.googleapis.com/auth/gmail.readonly"
    )["config"]
    assert isinstance(service, dict)
    service["channel_id"] = "channel:service:gmail"
    schema.validate(service)
    service["enable_writes"] = True
    service["read_only"] = False
    with pytest.raises(ValidationError):
        schema.validate(service)
