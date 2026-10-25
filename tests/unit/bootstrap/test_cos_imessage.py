"""Pure CoS enrollment configuration and disposable-file integrity proofs."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest

from adminme.openclaw_roster import agent_rows, replace_agent_rows
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.cos_imessage import (
    ARCHIVE_SHA256,
    BINARY_SHA256,
    CONTRACT,
    DISABLED_ACTIONS,
    MEMBER_TOOLS,
    VERSION,
    prepare_channel,
    validate_prepared_channel,
    verify_guard_files,
)


ACCOUNT = "fixture-cos-imessage-account"
SENDERS = ["+12025550102", "+12025550101"]  # fixture:tenant_data:ok


def _guard(tmp_path: Path) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "account_id": ACCOUNT,
        "cli_version": VERSION,
        "cli_sha256": BINARY_SHA256,
        "archive_sha256": ARCHIVE_SHA256,
        "wrapper_sha256": "a" * 64,
        "cli_path": str(tmp_path / "reviewed-provider"),
        "wrapper_path": str(tmp_path / "reviewed-wrapper"),
        "db_path": str(tmp_path / "dedicated-messages.db"),
        "api_base_url": "http://127.0.0.1:3330/",
        "shared_secret_env": "ADMINME_OPENCLAW_SHARED_SECRET",
    }


def _adapters() -> dict[str, Any]:
    return {
        "service_identities": {
            "channels": [
                {
                    "provider": "apple_imessage",
                    "enrolled": True,
                    "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                    "provider_account_reference": "provider-account:"
                    + hashlib.sha256(ACCOUNT.encode()).hexdigest(),
                    "response_transport_contract": CONTRACT,
                    "intake_mode": "member_submission",
                    "openclaw_channel_id": "imessage",
                }
            ]
        },
        "openclaw_actor_identities": {
            f"imessage|{ACCOUNT}|{sender}": f"fixture-member-{index}"
            for index, sender in enumerate(SENDERS)
        },
    }


def _previous() -> dict[str, Any]:
    return {
        "unrelated": {"retained": True},
        "agents": {
            "list": [
                {"id": "adminme-fixture", "tools": {"allow": ["exec", "*"]}},
                {"id": "unrelated-agent", "tools": {"allow": ["memory_search"]}},
            ]
        },
        "channels": {
            "imessage": {
                "cliPath": "/unreviewed-provider",
                "accounts": {ACCOUNT: {"fallback": "must-be-removed"}},
            }
        },
        "plugins": {
            "entries": {
                "administrateme": {
                    "enabled": False,
                    "config": {"existing_preference": "preserved"},
                }
            }
        },
    }


@pytest.mark.parametrize("canonical", [False, True])
def test_preparation_is_pure_exact_guarded_and_preserves_unrelated_config(
    tmp_path: Path,
    canonical: bool,
) -> None:
    previous = _previous()
    if canonical:
        replace_agent_rows(previous, agent_rows(previous))
    original = copy.deepcopy(previous)
    guard = _guard(tmp_path)
    result = prepare_channel(previous, guard, allowed_senders=[*SENDERS, SENDERS[0]])
    assert previous == original
    assert result["unrelated"] == previous["unrelated"]
    assert ("entries" in result["agents"]) is canonical
    assert agent_rows(result)[1] == agent_rows(previous)[1]
    assert agent_rows(result)[0]["tools"] == {"allow": MEMBER_TOOLS}
    imessage = result["channels"]["imessage"]
    assert set(imessage) == {"enabled", "defaultAccount", "accounts"}
    assert set(imessage["accounts"]) == {ACCOUNT}
    account = imessage["accounts"][ACCOUNT]
    assert account["allowFrom"] == sorted(SENDERS)
    assert account["cliPath"] == guard["wrapper_path"]
    assert "fallback" not in account
    assert account["service"] == "imessage"
    assert account["sendTransport"] == "applescript"
    assert account["actions"] == dict.fromkeys(DISABLED_ACTIONS, False)
    assert account["includeAttachments"] is False
    assert account["sendReadReceipts"] is False
    assert account["catchup"] == {"enabled": False}
    assert account["groupPolicy"] == "disabled"
    assert (
        result["plugins"]["entries"]["administrateme"]["config"]["existing_preference"]
        == "preserved"
    )
    validate_prepared_channel(result, guard, adapter_config=_adapters())
    # Pure preparation has not created a database, executable, credential or config.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "senders",
    [
        [],
        ["*"],
        ["fixture@example.test"],
        ["+1 202 555 0101"],  # fixture:tenant_data:ok
        ["202-555-0101"],  # fixture:tenant_data:ok
        ["+02025550101"],  # fixture:tenant_data:ok
        [None],
    ],
)
def test_preparation_rejects_ambiguous_senders(
    tmp_path: Path, senders: list[Any]
) -> None:
    with pytest.raises(ValueError, match="exact E164"):
        prepare_channel(_previous(), _guard(tmp_path), allowed_senders=senders)


def test_preparation_refuses_a_second_messages_account(tmp_path: Path) -> None:
    previous = _previous()
    previous["channels"]["imessage"]["accounts"]["another-account"] = {}
    with pytest.raises(ValueError, match="cannot share"):
        prepare_channel(previous, _guard(tmp_path), allowed_senders=SENDERS)


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("contract", "unreviewed-contract"),
        ("cli_version", "0.0.0"),
        ("cli_sha256", "b" * 64),
        ("archive_sha256", "b" * 64),
        ("wrapper_sha256", "not-a-digest"),
        ("cli_path", "relative-provider"),
        ("wrapper_path", "relative-wrapper"),
        ("db_path", "relative-database"),
        ("api_base_url", "https://example.test/"),
        ("shared_secret_env", "UNRELATED_SECRET"),
        ("account_id", ""),
    ],
)
def test_validator_requires_exact_pins_and_local_coordinates(
    tmp_path: Path, key: str, invalid: Any
) -> None:
    guard = _guard(tmp_path)
    prepared = prepare_channel(_previous(), guard, allowed_senders=SENDERS)
    guard[key] = invalid
    with pytest.raises(ValueError, match="not pinned"):
        validate_prepared_channel(prepared, guard, adapter_config=_adapters())


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("cliPath", "/unguarded-provider"),
        ("dbPath", "/another-database"),
        ("service", "sms"),
        ("sendTransport", "private-api"),
        ("dmPolicy", "open"),
        ("groupPolicy", "open"),
        ("includeAttachments", True),
        ("sendReadReceipts", True),
        ("region", "ZZ"),
        ("reactionNotifications", "all"),
        ("enabled", False),
        ("unreviewedFallback", "/unguarded-provider"),
        ("catchup", {"enabled": True}),
        ("allowFrom", ["*"]),
        ("allowFrom", [SENDERS[0], SENDERS[0]]),
    ],
)
def test_validator_rejects_unguarded_or_broadened_account(
    tmp_path: Path, key: str, invalid: Any
) -> None:
    guard = _guard(tmp_path)
    prepared = prepare_channel(_previous(), guard, allowed_senders=SENDERS)
    prepared["channels"]["imessage"]["accounts"][ACCOUNT][key] = invalid
    with pytest.raises(ValueError, match="unguarded route"):
        validate_prepared_channel(prepared, guard, adapter_config=_adapters())


@pytest.mark.parametrize("canonical", [False, True])
def test_validator_checks_each_action_identity_route_and_member_tool(
    tmp_path: Path,
    canonical: bool,
) -> None:
    guard = _guard(tmp_path)
    prepared = prepare_channel(_previous(), guard, allowed_senders=SENDERS)
    if canonical:
        replace_agent_rows(prepared, agent_rows(prepared))
    for action in DISABLED_ACTIONS:
        changed = copy.deepcopy(prepared)
        changed["channels"]["imessage"]["accounts"][ACCOUNT]["actions"][action] = True
        with pytest.raises(ValueError, match="unguarded route"):
            validate_prepared_channel(changed, guard, adapter_config=_adapters())
    missing_identity = _adapters()
    missing_identity["openclaw_actor_identities"].pop(
        f"imessage|{ACCOUNT}|{SENDERS[0]}"
    )
    with pytest.raises(ValueError, match="member identity map"):
        validate_prepared_channel(prepared, guard, adapter_config=missing_identity)
    extra_identity = _adapters()
    extra_identity["openclaw_actor_identities"][
        f"imessage|{ACCOUNT}|+12025550103"  # fixture:tenant_data:ok
    ] = "fixture-other-member"
    with pytest.raises(ValueError, match="member identity map"):
        validate_prepared_channel(prepared, guard, adapter_config=extra_identity)
    unguarded = copy.deepcopy(prepared)
    rows = agent_rows(unguarded)
    rows[0]["tools"]["allow"].append("exec")
    replace_agent_rows(unguarded, rows, canonical=False)
    with pytest.raises(ValueError, match="bypass"):
        validate_prepared_channel(unguarded, guard, adapter_config=_adapters())
    route = copy.deepcopy(prepared)
    route["plugins"]["entries"]["administrateme"]["config"]["forwardIntakeAccounts"] = [
        {"channelId": "imessage", "accountId": "different-account"}
    ]
    with pytest.raises(ValueError, match="routing"):
        validate_prepared_channel(route, guard, adapter_config=_adapters())
    extra_action = copy.deepcopy(prepared)
    extra_action["channels"]["imessage"]["accounts"][ACCOUNT]["actions"][
        "unreviewedMutation"
    ] = True
    with pytest.raises(ValueError, match="unguarded route"):
        validate_prepared_channel(extra_action, guard, adapter_config=_adapters())
    plugin_disabled = copy.deepcopy(prepared)
    plugin_disabled["plugins"]["entries"]["imessage"]["enabled"] = False
    with pytest.raises(ValueError, match="unguarded route"):
        validate_prepared_channel(plugin_disabled, guard, adapter_config=_adapters())
    ambiguous = copy.deepcopy(prepared)
    ambiguous["channels"]["imessage"]["accounts"]["another-account"] = {}
    with pytest.raises(ValueError, match="ambiguous"):
        validate_prepared_channel(ambiguous, guard, adapter_config=_adapters())


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("enrolled", False),
        ("service_principal_id", "another-service"),
        ("provider_account_reference", "provider-account:" + "b" * 64),
        ("response_transport_contract", "unguarded"),
        ("intake_mode", "passive"),
        ("openclaw_channel_id", "another-channel"),
    ],
)
def test_validator_requires_exact_service_enrollment(
    tmp_path: Path, key: str, invalid: Any
) -> None:
    guard = _guard(tmp_path)
    prepared = prepare_channel(_previous(), guard, allowed_senders=SENDERS)
    adapters = _adapters()
    adapters["service_identities"]["channels"][0][key] = invalid
    with pytest.raises(ValueError, match="not exact"):
        validate_prepared_channel(prepared, guard, adapter_config=adapters)


def test_executable_verification_uses_synthetic_temp_files_only(tmp_path: Path) -> None:
    release = tmp_path.resolve() / "reviewed-release"
    wrapper = release / "openclaw/administrateme/bin/cos-imsg-guard.mjs"
    wrapper.parent.mkdir(parents=True)
    config: dict[str, Any] = {}
    for kind in ("cli", "wrapper"):
        path = wrapper if kind == "wrapper" else tmp_path / kind
        content = f"nonexecutable fixture {kind}".encode()
        path.write_bytes(content)
        path.chmod(0o600)
        config[f"{kind}_path"] = str(path)
        config[f"{kind}_sha256"] = hashlib.sha256(content).hexdigest()
    verify_guard_files(config, reviewed_release=release)
    for kind in ("cli", "wrapper"):
        path = Path(config[f"{kind}_path"])
        original = path.read_bytes()
        path.write_bytes(b"changed fixture")
        with pytest.raises(ValueError, match="CoS iMessage"):
            verify_guard_files(config, reviewed_release=release)
        path.write_bytes(original)
        path.chmod(0o622)
        with pytest.raises(ValueError, match="reviewed bytes"):
            verify_guard_files(config, reviewed_release=release)
        path.chmod(0o600)
        link = tmp_path / f"{kind}-symlink"
        link.symlink_to(path)
        with pytest.raises(ValueError):
            verify_guard_files(
                {**config, f"{kind}_path": str(link)}, reviewed_release=release
            )

    # An operator-supplied digest must not bless an external wrapper. Even an
    # identical copy is outside the selected immutable release's trusted path.
    impostor = tmp_path / "unreviewed-wrapper"
    for content in (b"unguarded fixture transport", wrapper.read_bytes()):
        impostor.write_bytes(content)
        impostor.chmod(0o600)
        with pytest.raises(ValueError):
            verify_guard_files(
                {
                    **config,
                    "wrapper_path": str(impostor),
                    "wrapper_sha256": hashlib.sha256(content).hexdigest(),
                },
                reviewed_release=release,
            )
