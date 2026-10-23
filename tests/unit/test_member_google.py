"""Member consent models, exact proposals and encrypted OAuth request state."""

from __future__ import annotations

import copy
import json
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError

from adminme.adapters.loader import load_builtin_adapters
from adminme.adapters.member_google_connections import (
    CONFIG_KEY,
    MAIL_CONTROLS,
    SCOPES,
    selected_entries,
)
from adminme.google_oauth_transport import requested_scopes
from adminme.member_google import (
    AuthorizationRequest,
    GoogleHandoffs,
    PermissionRequest,
    SERVICES,
    ServicePermission,
    ServiceSelection,
    account_id,
    oauth_scopes,
    owned_accounts,
    propose_config,
)
from adminme.settings_control import SettingsControlStore
from adminme.source_policy import source_coordinate_for_event
from bootstrap.member_google_transition import google_change_digest

MEMBER = "member-fixture-b"
OTHER = "member-fixture-a"
EMAIL = "member@example.invalid"
REFERENCE = "keychain://fixture/new-member-grant"
ROOT = Path(__file__).resolve().parents[2]


def selection(*, read: bool = True, write: bool = False) -> dict[str, Any]:
    return {name: {"read": read, "write": write} for name in SERVICES}


def blank() -> dict[str, Any]:
    return {"adapters": [], "cos_channel_acquisition": {}}


def propose(config: dict[str, Any], **kwargs: Any) -> tuple[dict[str, Any], str]:
    return propose_config(
        config,
        **{
            "member": MEMBER,
            "email": EMAIL,
            "workspace": "personal",
            "services": selection(),
            "credential_ref": REFERENCE,
            **kwargs,
        },
    )


@pytest.mark.parametrize(
    ("read", "write"), [(False, False), (True, False), (True, True)]
)
def test_service_permissions_are_explicit(read: bool, write: bool) -> None:
    assert ServicePermission(read=read, write=write).model_dump() == {
        "read": read,
        "write": write,
    }


@pytest.mark.parametrize(
    "value",
    [
        {"read": False, "write": True},
        {"read": 1, "write": 0},
        {"read": "true", "write": False},
        {"read": True},
        {"read": True, "write": False, "share": True},
    ],
)
def test_invalid_permission_never_coerces_to_a_grant(value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ServicePermission.model_validate(value)


def test_authorization_binds_expected_email_uuid_and_all_service_choices() -> None:
    request_id = str(uuid4())
    body = AuthorizationRequest(
        request_id=request_id,
        expected_account_id=" Member@Example.Invalid ",
        workspace="personal",
        services=ServiceSelection.model_validate(selection()),
    )
    assert body.expected_account_id == EMAIL
    assert body.request_id == request_id
    assert body.services.model_dump() == selection()


@pytest.mark.parametrize(
    "email",
    [
        "member@",
        "@example.invalid",
        "member@example",
        "member example@invalid",
        "member@@example.invalid",
    ],
)
def test_invalid_email_fails_before_google_consent(email: str) -> None:
    with pytest.raises(ValidationError):
        AuthorizationRequest.model_validate(
            {
                "request_id": str(uuid4()),
                "expected_account_id": email,
                "workspace": "personal",
                "services": selection(),
            }
        )


@pytest.mark.parametrize(
    "request_id",
    [
        "not-a-uuid",
        "A" * 32,
        "00000000000000000000000000000000",
        "{00000000-0000-0000-0000-000000000000}",
    ],
)
def test_requests_require_canonical_uuid(request_id: str) -> None:
    with pytest.raises(ValidationError):
        PermissionRequest.model_validate(
            {
                "request_id": request_id,
                "services": selection(),
                "expected_revision": "a" * 64,
            }
        )


def test_zero_service_authorization_rejected_but_permission_revocation_allowed() -> (
    None
):
    services = selection(read=False)
    with pytest.raises(ValidationError, match="at least one"):
        AuthorizationRequest.model_validate(
            {
                "request_id": str(uuid4()),
                "expected_account_id": EMAIL,
                "workspace": "personal",
                "services": services,
            }
        )
    assert (
        PermissionRequest.model_validate(
            {
                "request_id": str(uuid4()),
                "services": services,
                "expected_revision": "a" * 64,
            }
        ).services.model_dump()
        == services
    )


def test_selected_oauth_scope_union_is_exact_and_supported() -> None:
    services = selection(read=False)
    services["calendar"] = {"read": True, "write": False}
    assert set(oauth_scopes(services)) == {
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/calendar.readonly",
    }
    services["gmail"] = {"read": True, "write": True}
    scopes = set(oauth_scopes(services))
    assert {
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
    } <= scopes
    assert "https://www.googleapis.com/auth/tasks" not in scopes
    assert set(requested_scopes(sorted(scopes))) == scopes


@pytest.mark.parametrize("write", [False, True])
def test_proposal_has_usable_reviewed_adapter_configs_and_manual_intake(
    write: bool,
) -> None:
    original = blank()
    config, proof = propose(original, services=selection(write=write))
    assert original == blank()
    assert proof == google_change_digest(config, MEMBER)
    selected = selected_entries(config)
    assert len(selected) == 3
    account = owned_accounts(config, MEMBER)[account_id(MEMBER, EMAIL)]
    assert account["services"] == selection(write=write)
    assert account["workspace"] == "personal"
    assert owned_accounts(config, OTHER) == {}
    # The production loader validates each provider schema and constructs its
    # real class. No credentials, polling or provider calls occur in construction.
    adapters = load_builtin_adapters(
        {"adapters": config["adapters"]}, repository_root=ROOT
    )
    assert len(adapters) == 3
    for adapter in adapters:
        assert set(adapter._scopes) <= set(oauth_scopes(selection(write=write)))
        if adapter.name == "messaging-gmail-api":
            assert adapter._config["enable_writes"] is write
            assert adapter._scopes == (
                [
                    "https://www.googleapis.com/auth/gmail.modify",
                    "https://www.googleapis.com/auth/gmail.send",
                ]
                if write
                else [SCOPES[adapter.name]]
            )
            for name, value in MAIL_CONTROLS.items():
                assert adapter._config[name] == value
        else:
            assert bool(adapter.write_capabilities) is write
        assert adapter._config["read_only"] is not write
    assert config[CONFIG_KEY]["automated_candidate_extraction"] is False
    assert config[CONFIG_KEY]["historical_expansion"] is False
    assert config[CONFIG_KEY]["writes_require_existing_governance"] is True


def test_revocation_retains_exact_rows_and_credentials_but_stops_all_read_write() -> (
    None
):
    prior, _ = propose(blank(), services=selection(write=True))
    saved = copy.deepcopy(prior)
    revoked, proof = propose(prior, services=selection(read=False), credential_ref=None)
    assert prior == saved
    assert {row["id"] for row in revoked["adapters"]} == {
        row["id"] for row in prior["adapters"]
    }
    assert all(row["enabled"] is False for row in revoked["adapters"])
    assert all(
        row["config"]["oauth_credentials_ref"] == REFERENCE
        for row in revoked["adapters"]
    )
    assert owned_accounts(revoked, MEMBER)[account_id(MEMBER, EMAIL)][
        "services"
    ] == selection(read=False)
    assert len(selected_entries(revoked)) == 3
    assert proof != google_change_digest(prior, MEMBER)
    assert (
        load_builtin_adapters({"adapters": revoked["adapters"]}, repository_root=ROOT)
        == []
    )


def test_unselected_service_rows_remain_disabled_without_requesting_their_scope() -> (
    None
):
    services = selection(read=False)
    services["calendar"] = {"read": True, "write": False}
    config, _ = propose(blank(), services=services)
    account = owned_accounts(config, MEMBER)[account_id(MEMBER, EMAIL)]
    assert len(account["rows"]) == 3
    assert account["rows"]["gmail"]["enabled"] is False
    assert account["rows"]["tasks"]["enabled"] is False
    assert account["rows"]["calendar"]["config"]["discover_calendars"] is True
    assert (
        len(
            load_builtin_adapters(
                {"adapters": config["adapters"]}, repository_root=ROOT
            )
        )
        == 1
    )


def test_other_member_and_other_account_are_preserved_and_independently_revisioned() -> (
    None
):
    base, _ = propose(
        blank(),
        member=OTHER,
        email="other@example.invalid",
        credential_ref="keychain://fixture/other",
    )
    current, _ = propose(base)
    before_other = copy.deepcopy(owned_accounts(current, OTHER))
    before_first = copy.deepcopy(owned_accounts(current, MEMBER))
    appended, _ = propose(
        current,
        email="second@example.invalid",
        credential_ref="keychain://fixture/second",
    )
    assert owned_accounts(appended, OTHER) == before_other
    assert (
        owned_accounts(appended, MEMBER)[account_id(MEMBER, EMAIL)]
        == before_first[account_id(MEMBER, EMAIL)]
    )
    assert len(owned_accounts(appended, MEMBER)) == 2
    assert appended["cos_channel_acquisition"] == base["cos_channel_acquisition"]


def test_account_cannot_change_owner_or_workspace_or_create_without_consent() -> None:
    current, _ = propose(blank())
    with pytest.raises(ValueError, match="another principal"):
        propose(current, member=OTHER)
    with pytest.raises(ValueError, match="workspace"):
        propose(current, workspace="work")
    with pytest.raises(ValueError, match="Connect this account"):
        propose(blank(), credential_ref=None)


@pytest.mark.parametrize(
    ("service", "event_type"),
    [
        ("gmail", "messaging.received"),
        ("calendar", "calendar.event_added"),
        ("tasks", "list_item.added"),
    ],
)
def test_read_off_removes_cos_context_for_retained_exact_source_after_restart(
    tmp_path: Path, service: str, event_type: str
) -> None:
    connected, _ = propose(blank())
    services = selection()
    services[service] = {"read": False, "write": False}
    revoked, _ = propose(connected, services=services, credential_ref=None)
    kind = SERVICES[service][0]
    coordinate = source_coordinate_for_event(
        {
            "type": event_type,
            "tenant_id": "fixture-tenant",
            "owner_scope": f"private:{MEMBER}",
            "source_adapter": kind,
            "source_account_id": EMAIL,
            "payload": {"member_id": MEMBER},
        }
    )
    for restart in range(2):
        store = SettingsControlStore(
            tmp_path / "settings.json", adapter_config=connected
        )
        assert (
            store.effective_source_policy_v2(MEMBER, coordinate)["policy"][
                "context_mode"
            ]
            != "none"
        )
        if restart == 0:
            store.rebind_google_config(revoked)
        else:
            store = SettingsControlStore(
                tmp_path / "settings.json", adapter_config=revoked
            )
        for instance in (kind, account_id(MEMBER, EMAIL) + "-" + service):
            exact = coordinate.model_copy(update={"adapter_instance_id": instance})
            policy = store.effective_source_policy_v2(MEMBER, exact)["policy"]
            assert policy["context_mode"] == "none"
            assert policy["triage_mode"] == "manual_only"
            assert policy["attention_mode"] == "manual"
        other = coordinate.model_copy(
            update={"provider_account_id": "unrelated@example.invalid"}
        )
        assert (
            store.effective_source_policy_v2(MEMBER, other)["policy"]["context_mode"]
            != "none"
        )


def record(*, state: str = "pending") -> dict[str, Any]:
    return {
        "request_id": str(uuid4()),
        "member_id": MEMBER,
        "tenant_id": "fixture-tenant",
        "state": state,
        "account_id": account_id(MEMBER, EMAIL),
        "fingerprint": "f" * 64,
        "verifier": "fixture-private-verifier",
        "authorization_url": "https://accounts.example.invalid/authorize?state=private-state",
        "expires_epoch": 9999999999,
        "expires_at": "2286-11-20T17:46:39+00:00",
    }


def test_vault_is_private_encrypted_and_owner_scoped_across_restart(
    tmp_path: Path,
) -> None:
    path, key = tmp_path / "handoffs.enc", b"v" * 32
    vault = GoogleHandoffs(path, key)
    request = record()
    vault.put(request)
    raw = path.read_bytes()
    assert request["verifier"].encode() not in raw
    assert b"private-state" not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    reopened = GoogleHandoffs(path, key)
    assert reopened.get(request["request_id"], MEMBER) == request
    assert reopened.get(request["request_id"], OTHER) is None
    with pytest.raises(InvalidTag):
        GoogleHandoffs(path, b"x" * 32).read()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(InvalidTag):
        reopened.read()


def test_vault_refuses_public_or_symlinked_file(tmp_path: Path) -> None:
    path = tmp_path / "handoffs.enc"
    vault = GoogleHandoffs(path, b"v" * 32)
    vault.put(record())
    path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        vault.read()
    path.chmod(0o600)
    link = tmp_path / "linked.enc"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="permissions"):
        GoogleHandoffs(link, b"v" * 32).read()


def test_vault_does_not_evict_terminal_uuids_at_capacity(tmp_path: Path) -> None:
    vault = GoogleHandoffs(tmp_path / "handoffs.enc", b"v" * 32)
    records = [record(state="configured") for _ in range(500)]
    vault.save({row["request_id"]: row for row in records})
    with pytest.raises(ValueError, match="history needs maintenance"):
        vault.put(record())
    changed = {**records[0], "message": "Permissions saved."}
    vault.put(changed)
    assert len(vault.read()) == 500
    assert vault.get(changed["request_id"], MEMBER) == changed


def test_public_state_never_returns_private_oauth_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adminme.member_google as module

    request = record()
    pending = GoogleHandoffs.public(request)
    assert "authorization_url" in pending
    assert "verifier" not in pending and "fingerprint" not in pending
    monkeypatch.setattr(module.time, "time", lambda: request["expires_epoch"] + 1)
    expired = GoogleHandoffs.public(request)
    assert expired["state"] == "expired"
    assert "authorization_url" not in expired
    for terminal in ("configured", "failed", "cancelled", "authorizing", "publishing"):
        value = GoogleHandoffs.public({**request, "state": terminal})
        assert "authorization_url" not in value
        assert "private-state" not in json.dumps(value)
        assert "fixture-private-verifier" not in json.dumps(value)
