"""Explicit private acquisition ceiling for disposable local test datasets.

This is an opt-in local restriction, never a provider enrollment or mailbox
mutation. Normal installations retain their existing acquisition behavior.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONTRACT = "disposable-test-acquisition-v1"
MAX_MESSAGES = 200
CONFIG_KEY = "disposable_test_acquisition"


@dataclass(frozen=True)
class DisposableTestAcquisition:
    adapter_id: str
    member_id: str = field(repr=False)
    provider_account_id: str = field(repr=False)
    message_ids: tuple[str, ...] = field(repr=False)

    @classmethod
    def parse(cls, value: Any) -> DisposableTestAcquisition:
        required = {
            "contract",
            "adapter_id",
            "member_id",
            "provider_account_id",
            "message_ids",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("disposable test acquisition requires the exact v1 fields")
        if value["contract"] != CONTRACT:
            raise ValueError("unsupported disposable test acquisition contract")
        for key in ("adapter_id", "member_id", "provider_account_id"):
            item = value[key]
            if (
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                or len(item) > 500
                or any(ord(character) < 32 for character in item)
            ):
                raise ValueError("disposable test acquisition requires exact ownership")
        ids = value["message_ids"]
        if not isinstance(ids, list) or len(ids) > MAX_MESSAGES:
            raise ValueError("disposable test selection must contain at most 200 IDs")
        if any(
            not isinstance(item, str)
            or not item
            or len(item) > 240
            or not all(
                character.isascii() and (character.isalnum() or character in "-_")
                for character in item
            )
            for item in ids
        ) or len(set(ids)) != len(ids):
            raise ValueError("disposable test selection requires distinct opaque IDs")
        return cls(
            adapter_id=value["adapter_id"],
            member_id=value["member_id"],
            provider_account_id=value["provider_account_id"],
            message_ids=tuple(ids),
        )

    def private_config(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "adapter_id": self.adapter_id,
            "member_id": self.member_id,
            "provider_account_id": self.provider_account_id,
            "message_ids": list(self.message_ids),
        }

    def receipt_digest(self) -> str:
        material = self.private_config()
        material["message_ids"] = sorted(self.message_ids)
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def coverage(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "coverage": "bounded_test_sample",
            "selected_message_count": len(self.message_ids),
            "distinct_message_cap": MAX_MESSAGES,
            "mailbox_coverage_complete": False,
            "thread_context": "selected_messages_only",
            "related_expansion": False,
            "selection": "private_explicit_message_ids",
        }


def policy_from_adapter_config(
    value: dict[str, Any],
) -> DisposableTestAcquisition | None:
    if CONFIG_KEY not in value:
        return None
    return DisposableTestAcquisition.parse(value[CONFIG_KEY])


def restrict_adapter_config(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one exact Gmail owner and pause every other local adapter.

    Applied after member controls and again by the production pack loader so a
    connection resume cannot widen this local acquisition restriction.
    """

    from adminme.adapters.cos_channel_acquisition import (
        CONFIG_KEY as COS_CONFIG_KEY,
        restrict_cos_adapter_config,
    )

    from adminme.adapters.member_google_connections import (
        policy_from_config as google_policy,
    )

    # Reject an orphaned connection grant, even without a test policy.
    google_policy(value)
    if COS_CONFIG_KEY in value:
        return restrict_cos_adapter_config(value)
    policy = policy_from_adapter_config(value)
    if policy is None:
        if any(
            isinstance(entry, dict)
            and isinstance(entry.get("config"), dict)
            and CONFIG_KEY in entry["config"]
            for entry in value.get("adapters") or []
        ):
            raise ValueError("disposable test acquisition requires its instance policy")
        return value
    result = copy.deepcopy(value)
    entries = result.get("adapters")
    if not isinstance(entries, list):
        raise ValueError("disposable test acquisition requires configured adapters")
    selected = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id") == policy.adapter_id
    ]
    if len(selected) != 1:
        raise ValueError("disposable test acquisition requires one exact adapter")
    entry = selected[0]
    config = entry.get("config")
    if (
        str(entry.get("kind") or "").removeprefix("adapter:") != "messaging-gmail-api"
        or not isinstance(config, dict)
        or config.get("member_id") != policy.member_id
        or config.get("provider_account_id") != policy.provider_account_id
        or config.get("user_email") != policy.provider_account_id
        or config.get("service_principal_id")
    ):
        raise ValueError("disposable test acquisition owner/account binding mismatch")
    for item in entries:
        if isinstance(item, dict) and item is not entry:
            item["enabled"] = False
    config[CONFIG_KEY] = policy.private_config()
    config["enable_writes"] = False
    config["read_only"] = True
    config["initial_backfill_days"] = 0
    config["initial_backfill_max_messages"] = 0
    config["historical_seed_queries"] = []
    config["historical_related_max_participants"] = 0
    config["historical_related_messages_per_participant"] = 0
    return result


def discover_test_message_selection(
    adapter_config: dict[str, Any], *, repository_root: Path, output: Path
) -> dict[str, Any]:
    """Prepare an exclusive private selection file through the existing Gmail seam.

    The operator must establish the accepted dataset and stopped-service
    boundary before calling. No provider/configuration/cursor mutation occurs.
    """

    from adminme.adapters.loader import load_builtin_adapters

    policy = policy_from_adapter_config(adapter_config)
    if policy is None:
        raise ValueError("Gmail test discovery requires the exact instance policy")
    parent = output.parent
    parent_metadata = parent.lstat()
    if (
        not output.is_absolute()
        or parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or parent_metadata.st_mode & 0o077
        or os.path.lexists(output)
    ):
        raise ValueError(
            "test selection output requires a new file in a private directory"
        )
    config = restrict_adapter_config(adapter_config)
    # Discovery is an explicit operator read. A persisted adapter pause remains
    # intact while this separate, bounded preparation uses the existing grant.
    config = copy.deepcopy(config)
    for entry in config["adapters"]:
        if isinstance(entry, dict) and entry.get("id") == policy.adapter_id:
            entry["enabled"] = True
    adapters = load_builtin_adapters(config, repository_root=repository_root)
    if len(adapters) != 1:
        raise ValueError("Gmail test discovery requires exactly one selected adapter")
    discover = getattr(adapters[0], "discover_test_message_ids", None)
    if not callable(discover):
        raise ValueError("selected Gmail adapter does not support bounded discovery")
    try:
        discovered = discover()
        if (
            not isinstance(discovered, tuple)
            or len(discovered) != 2
            or not isinstance(discovered[1], dict)
        ):
            raise ValueError("Gmail discovery returned an invalid selection result")
        message_ids, metadata = discovered
    except Exception as exc:
        raise RuntimeError("read-only Gmail test discovery failed") from exc
    selected = {**policy.private_config(), "message_ids": message_ids}
    verified = DisposableTestAcquisition.parse(selected)
    payload = json.dumps({"message_ids": list(verified.message_ids)}, indent=2).encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".test-selection-", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        # An exclusive hard link publishes the completed file without a race
        # that could overwrite an existing reviewed selection or symlink.
        os.link(temporary, output, follow_symlinks=False)
        temporary.unlink()
        directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "selection_discovered",
        "contract": CONTRACT,
        "selected_message_count": len(verified.message_ids),
        "distinct_message_cap": MAX_MESSAGES,
        "selection_sha256": hashlib.sha256(payload).hexdigest(),
        "output": str(output),
        **metadata,
    }
