"""Pinned CoS iMessage provider and pure guarded-channel enrollment contract.

Installation only stages an immutable official provider binary. The separately
authorized, journaled channel activation publishes configuration and restarts
services. This module never signs into Apple, changes TCC or sends a message.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Sequence
import urllib.request
import zipfile

from adminme.openclaw_roster import agent_rows, replace_agent_rows
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.contracts import DeploymentPaths, atomic_write

CONTRACT = "cos-imessage-guard-v1"
VERSION = "0.17.0"
ARCHIVE_URL = (
    "https://github.com/openclaw/imsg/releases/download/v0.17.0/imsg-macos.zip"
)
ARCHIVE_SHA256 = "ecb0a2b2e19ca9f4e459ed68b678f2fd2654c13b9b0a6f29062dab18a21f426f"
BINARY_SHA256 = "bfdab2f3c380386dc76c40f47e7dd10410ecf79254756fa15210473d99ca4bd6"
MEMBER_TOOLS = ["administrateme", "llm-task", "memory_search", "memory_get"]
DISABLED_ACTIONS = (
    "reactions",
    "edit",
    "unsend",
    "reply",
    "sendWithEffect",
    "renameGroup",
    "setGroupIcon",
    "addParticipant",
    "removeParticipant",
    "leaveGroup",
    "sendAttachment",
    "polls",
)
_PHONE = re.compile(r"\+[1-9][0-9]{7,14}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def prepare_channel(
    previous: dict[str, Any], guard: dict[str, Any], *, allowed_senders: list[str]
) -> dict[str, Any]:
    """Build a dedicated, direct-message-only account without publishing it."""
    if not allowed_senders or any(
        not isinstance(sender, str) or _PHONE.fullmatch(sender) is None
        for sender in allowed_senders
    ):
        raise ValueError("CoS iMessage senders must be exact E164 numbers")
    result = copy.deepcopy(previous)
    channel = result.setdefault("channels", {}).setdefault("imessage", {})
    account_id = guard["account_id"]
    existing = channel.get("accounts") or {}
    if any(key != account_id for key in existing):
        raise ValueError("dedicated CoS Messages cannot share a provider database")
    account = {
        "enabled": True,
        "cliPath": guard["wrapper_path"],
        "dbPath": guard["db_path"],
        "service": "imessage",
        "sendTransport": "applescript",
        "region": "US",
        "dmPolicy": "allowlist",
        "allowFrom": sorted(set(allowed_senders)),
        "groupPolicy": "disabled",
        "includeAttachments": False,
        "sendReadReceipts": False,
        "reactionNotifications": "off",
        "actions": dict.fromkeys(DISABLED_ACTIONS, False),
        "catchup": {"enabled": False},
    }
    # Eliminate inherited account fields that could introduce a fallback path.
    result["channels"]["imessage"] = {
        "enabled": True,
        "defaultAccount": account_id,
        "accounts": {account_id: account},
    }
    plugin = (
        result.setdefault("plugins", {})
        .setdefault("entries", {})
        .setdefault("administrateme", {"enabled": True})
    )
    plugin["enabled"] = True
    plugin.setdefault("config", {})["forwardIntakeAccounts"] = [
        {"channelId": "imessage", "accountId": account_id}
    ]
    result["plugins"]["entries"].setdefault("imessage", {})["enabled"] = True
    agents = agent_rows(result)
    for agent in agents:
        if str(agent.get("id", "")).startswith("adminme-"):
            agent["tools"] = {"allow": list(MEMBER_TOOLS)}
    if agents:
        replace_agent_rows(result, agents, canonical=False)
    return result


def validate_prepared_channel(
    openclaw_config: dict[str, Any],
    guard_config: dict[str, Any],
    *,
    adapter_config: dict[str, Any],
) -> None:
    """Fail closed unless every enrolled path uses the same guarded account."""
    guard = guard_config
    if (
        guard.get("contract") != CONTRACT
        or guard.get("cli_sha256") != BINARY_SHA256
        or guard.get("cli_version") != VERSION
        or guard.get("archive_sha256") != ARCHIVE_SHA256
        or guard.get("shared_secret_env") != "ADMINME_OPENCLAW_SHARED_SECRET"
        or guard.get("api_base_url") != "http://127.0.0.1:3330/"
        or not isinstance(guard.get("account_id"), str)
        or not guard["account_id"]
        or any(
            not isinstance(guard.get(key), str) or not Path(guard[key]).is_absolute()
            for key in ("cli_path", "wrapper_path", "db_path")
        )
        or not re.fullmatch(r"[a-f0-9]{64}", str(guard.get("wrapper_sha256", "")))
    ):
        raise ValueError("CoS iMessage guard configuration is not pinned")
    account_id = guard["account_id"]
    service = adapter_config.get("service_identities", {})
    channels = [
        row
        for row in service.get("channels", [])
        if row.get("provider") == "apple_imessage" and row.get("enrolled")
    ]
    if len(channels) != 1 or any(
        (
            channels[0].get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID,
            channels[0].get("provider_account_reference")
            != "provider-account:" + _digest(account_id),
            channels[0].get("response_transport_contract") != CONTRACT,
            channels[0].get("intake_mode") != "member_submission",
            channels[0].get("openclaw_channel_id") != "imessage",
        )
    ):
        raise ValueError("CoS iMessage service enrollment is not exact")
    imessage = openclaw_config.get("channels", {}).get("imessage", {})
    accounts = imessage.get("accounts", {})
    if (
        imessage.get("enabled") is not True
        or imessage.get("defaultAccount") != account_id
        or set(imessage) != {"enabled", "defaultAccount", "accounts"}
        or set(accounts) != {account_id}
    ):
        raise ValueError("CoS iMessage account configuration is ambiguous")
    account = accounts[account_id]
    senders = account.get("allowFrom")
    expected_keys = {
        "enabled",
        "cliPath",
        "dbPath",
        "service",
        "sendTransport",
        "region",
        "dmPolicy",
        "allowFrom",
        "groupPolicy",
        "includeAttachments",
        "sendReadReceipts",
        "reactionNotifications",
        "actions",
        "catchup",
    }
    if (
        set(account) != expected_keys
        or account.get("enabled") is not True
        or account.get("region") != "US"
        or account.get("reactionNotifications") != "off"
        or set(account.get("actions", {})) != set(DISABLED_ACTIONS)
        or openclaw_config.get("plugins", {})
        .get("entries", {})
        .get("imessage", {})
        .get("enabled")
        is not True
        or not isinstance(senders, list)
        or not senders
        or any(
            not isinstance(sender, str) or _PHONE.fullmatch(sender) is None
            for sender in senders
        )
        or len(set(senders)) != len(senders)
        or account.get("cliPath") != guard["wrapper_path"]
        or account.get("dbPath") != guard["db_path"]
        or account.get("service") != "imessage"
        or account.get("sendTransport") != "applescript"
        or account.get("dmPolicy") != "allowlist"
        or account.get("groupPolicy") != "disabled"
        or account.get("includeAttachments") is not False
        or account.get("sendReadReceipts") is not False
        or account.get("catchup") != {"enabled": False}
        or any(
            account.get("actions", {}).get(key) is not False for key in DISABLED_ACTIONS
        )
    ):
        raise ValueError("CoS iMessage channel has an unguarded route")
    actor_map = adapter_config.get("openclaw_actor_identities", {})
    prefix = f"imessage|{account_id}|"
    mapped = {key.removeprefix(prefix) for key in actor_map if key.startswith(prefix)}
    if mapped != set(senders):
        raise ValueError("CoS iMessage senders must match the member identity map")
    plugin = (
        openclaw_config.get("plugins", {}).get("entries", {}).get("administrateme", {})
    )
    if plugin.get("enabled") is not True or plugin.get("config", {}).get(
        "forwardIntakeAccounts"
    ) != [{"channelId": "imessage", "accountId": account_id}]:
        raise ValueError("CoS iMessage intake routing is not enabled")
    member_agents = [
        row
        for row in agent_rows(openclaw_config)
        if str(row.get("id", "")).startswith("adminme-")
    ]
    if not member_agents or any(
        row.get("tools") != {"allow": MEMBER_TOOLS} for row in member_agents
    ):
        raise ValueError("CoS member tools can bypass the guarded provider")


def verify_guard_files(config: dict[str, Any], *, reviewed_release: Path) -> None:
    """Bind the wrapper to immutable release bytes, then verify both executables."""
    expected_wrapper = (
        reviewed_release / "openclaw/administrateme/bin/cos-imsg-guard.mjs"
    )
    if (
        not reviewed_release.is_absolute()
        or expected_wrapper.resolve(strict=True) != expected_wrapper
        or Path(config["wrapper_path"]) != expected_wrapper
        or config["wrapper_sha256"]
        != hashlib.sha256(expected_wrapper.read_bytes()).hexdigest()
    ):
        raise ValueError("CoS iMessage wrapper is not the selected release guard")
    for path_key, digest_key in (
        ("cli_path", "cli_sha256"),
        ("wrapper_path", "wrapper_sha256"),
    ):
        path = Path(config[path_key])
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_mode & 0o022
            or hashlib.sha256(path.read_bytes()).hexdigest() != config[digest_key]
        ):
            raise ValueError("CoS iMessage executable does not match reviewed bytes")


def install_provider(paths: DeploymentPaths) -> Path:
    """Stage only the pinned binary/resources, excluding the private API helper."""
    target = paths.app_dir / "imsg" / VERSION
    if target.exists():
        binary = target / "imsg"
        if (
            binary.is_symlink()
            or not binary.is_file()
            or hashlib.sha256(binary.read_bytes()).hexdigest() != BINARY_SHA256
        ):
            raise RuntimeError("existing imsg installation differs from the pin")
        return binary
    with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response:
        archive = response.read(32 * 1024 * 1024)
    if hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("imsg archive integrity verification failed")
    allowed = {
        "imsg",
        "SQLite.swift_SQLite.bundle/PrivacyInfo.xcprivacy",
        "PhoneNumberKit_PhoneNumberKit.bundle/PhoneNumberMetadata.json",
        "PhoneNumberKit_PhoneNumberKit.bundle/PrivacyInfo.xcprivacy",
    }
    stage = target.with_name(f".{VERSION}-{os.getpid()}")
    stage.mkdir(parents=True, mode=0o700)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            if not allowed <= set(package.namelist()):
                raise RuntimeError("imsg archive is incomplete")
            for name in sorted(allowed):
                payload = package.read(name)
                if (
                    name == "imsg"
                    and hashlib.sha256(payload).hexdigest() != BINARY_SHA256
                ):
                    raise RuntimeError("imsg executable integrity verification failed")
                atomic_write(
                    stage / name, payload, mode=0o755 if name == "imsg" else 0o600
                )
        stage.rename(target)
    except BaseException:
        import shutil

        shutil.rmtree(stage)
        raise
    return target / "imsg"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-pinned-provider", action="store_true", required=True)
    args = parser.parse_args(argv)
    if args.install_pinned_provider:
        binary = install_provider(DeploymentPaths.default())
        print(
            json.dumps(
                {
                    "installed": True,
                    "version": VERSION,
                    "binary": str(binary),
                    "sha256": BINARY_SHA256,
                    "provider_effect_count": 0,
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
