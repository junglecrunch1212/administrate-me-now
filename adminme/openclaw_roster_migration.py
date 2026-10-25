"""Validate native-produced ownership changes; never synthesize a migration.

The private receipts retain the input/result of the pinned public CLI. They are
not grants. Only exactly recorded fallback rows survive normal topology checks;
all member peer routes and disclosure/action policy remain independently checked.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from adminme.openclaw_roster import agent_rows, canonical_agent_rows

CONTRACT = "administrateme-native-roster-transition-v1"
RECEIPT_NAMES = ("native-roster-migration.json", "native-roster-expansion.json")
_ROLES = ("heartbeat", "systemAgent", "authInheritance", "sessionStore")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def classify(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    runtime: Path,
    requested_entries: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Admit only the pinned migration's ownership materialization.

    No provider, plugin, grant, model or arbitrary agent field may change. Public
    config-set expansion additionally binds exactly the entries we requested.
    """
    old_rows = agent_rows(before)
    old_agents = before.get("agents", {})
    old_defaults = old_agents.get("defaults", {})
    new_rows = canonical_agent_rows(after)
    new_agents = after["agents"]
    if not isinstance(old_defaults, dict):
        raise ValueError("native source defaults are invalid")
    if not old_rows:
        old_rows = [{"id": "main"}]
    old_ids = {row["id"] for row in old_rows}
    if requested_entries is not None:
        if len(old_rows) != 1 or old_agents.get("ownership") == "explicit":
            raise ValueError("native expansion requires one implicit owner")
        proposed = agent_rows(
            {"agents": {"entries": requested_entries, "ownership": "explicit"}}
        )
        if not old_ids <= {row["id"] for row in proposed}:
            raise ValueError("native expansion removed the retained owner")
        expected_rows = {row["id"]: row for row in proposed}
    else:
        expected_rows = {row["id"]: row for row in old_rows}
    if {row["id"] for row in new_rows} != set(expected_rows):
        raise ValueError("native migration changed the agent roster")
    owner = next(
        (row["id"] for row in old_rows if row.get("default") is True), old_rows[0]["id"]
    )
    ambient_owner = None if old_agents.get("ownership") == "explicit" else owner
    for row in new_rows:
        expected = {
            key: value
            for key, value in expected_rows[row["id"]].items()
            if key != "default"
        }
        if "workspace" not in expected and row.get("workspace") is not None:
            if row["id"] != owner or row["workspace"] != old_defaults.get(
                "workspace", str(runtime / "workspace")
            ):
                raise ValueError("native migration changed a workspace coordinate")
            expected["workspace"] = row["workspace"]
        if canonical(row) != canonical(expected):
            raise ValueError("native migration changed agent permissions or metadata")
    old_remaining = {
        key: value
        for key, value in old_agents.items()
        if key not in {"list", "entries", "defaults", "ownership"}
    }
    new_remaining = {
        key: value
        for key, value in new_agents.items()
        if key not in {"entries", "defaults", "ownership"}
    }
    if canonical(old_remaining) != canonical(new_remaining):
        raise ValueError("native migration changed unrelated agent configuration")
    if len(new_rows) > 1 and new_agents.get("ownership") != "explicit":
        raise ValueError("native migration did not pin ownership")
    new_defaults = copy.deepcopy(new_agents.get("defaults", {}))
    if not isinstance(new_defaults, dict):
        raise ValueError("native result defaults are invalid")
    for role in _ROLES:
        old = old_defaults.get(role)
        new = new_defaults.get(role)
        if canonical(old) == canonical(new):
            continue
        if (
            not isinstance(new, dict)
            or new.get("agentId") != ambient_owner
            or ambient_owner is None
        ):
            raise ValueError("native migration reassigned a default role")
        retained = {key: value for key, value in new.items() if key != "agentId"}
        if old is None:
            if retained:
                raise ValueError("native migration added role behavior")
            new_defaults.pop(role)
        elif (
            isinstance(old, dict)
            and "agentId" not in old
            and canonical(retained) == canonical(old)
        ):
            new_defaults[role] = old
        else:
            raise ValueError("native migration overwrote a role owner")
    if canonical(new_defaults) != canonical(old_defaults):
        raise ValueError("native migration changed unrelated defaults")
    old_talk, new_talk = before.get("talk"), after.get("talk")
    if canonical(old_talk) != canonical(new_talk):
        if (
            not isinstance(new_talk, dict)
            or ambient_owner is None
            or new_talk.get("agentId") != ambient_owner
            or canonical(
                {key: value for key, value in new_talk.items() if key != "agentId"}
            )
            != canonical(old_talk or {})
            or (isinstance(old_talk, dict) and "agentId" in old_talk)
        ):
            raise ValueError("native migration changed talk ownership")
    old_bindings, new_bindings = before.get("bindings", []), after.get("bindings", [])
    if not isinstance(old_bindings, list) or not isinstance(new_bindings, list):
        raise ValueError("native bindings are invalid")
    if canonical(new_bindings[: len(old_bindings)]) != canonical(old_bindings):
        raise ValueError("native migration changed existing routing")
    added = new_bindings[len(old_bindings) :]
    channels = before.get("channels", {})
    if not isinstance(channels, dict):
        raise ValueError("native source channels are invalid")
    seen: set[str] = set()
    for row in added:
        match = row.get("match") if isinstance(row, dict) else None
        channel = match.get("channel") if isinstance(match, dict) else None
        if (
            ambient_owner is None
            or not isinstance(channel, str)
            or channel not in channels
            or (
                isinstance(channels[channel], dict)
                and channels[channel].get("enabled") is False
            )
            or row
            != {
                "agentId": ambient_owner,
                "match": {"channel": channel, "accountId": "*"},
            }
            or channel in seen
            or any(
                isinstance(existing, dict) and existing.get("match") == match
                for existing in old_bindings
            )
        ):
            raise ValueError("native migration added an unbound or duplicate fallback")
        seen.add(channel)
    ignored = {"agents", "bindings", "talk", "meta"}
    if canonical(
        {key: value for key, value in before.items() if key not in ignored}
    ) != canonical({key: value for key, value in after.items() if key not in ignored}):
        raise ValueError(
            "native migration changed provider, policy or plugin configuration"
        )
    old_meta, new_meta = (
        copy.deepcopy(before.get("meta", {})),
        copy.deepcopy(after.get("meta", {})),
    )
    if not isinstance(old_meta, dict) or not isinstance(new_meta, dict):
        raise ValueError("native metadata is invalid")
    for value in (old_meta, new_meta):
        value.pop("lastTouchedAt", None)
        value.pop("lastTouchedVersion", None)
    if canonical(old_meta) != canonical(new_meta):
        raise ValueError("native migration changed unrelated metadata")
    return copy.deepcopy(added)


def validate_record(value: Any, *, runtime: Path) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "contract",
        "kind",
        "source",
        "result",
        "source_sha256",
        "result_sha256",
        "fallbacks",
        "requested_entries",
        "native_commit",
        "process_receipt_sha256",
    }:
        raise ValueError("native roster receipt is invalid")
    if (
        value["contract"] != CONTRACT
        or not isinstance(value["kind"], str)
        or value["kind"]
        not in {
            "doctor",
            "sole-agent-expansion",
        }
    ):
        raise ValueError("native roster receipt contract is invalid")
    for key, size in (("native_commit", 40), ("process_receipt_sha256", 64)):
        raw = value[key]
        if (
            not isinstance(raw, str)
            or len(raw) != size
            or any(char not in "0123456789abcdef" for char in raw)
        ):
            raise ValueError("native roster receipt identity is invalid")
    if not isinstance(value["source"], dict) or not isinstance(value["result"], dict):
        raise ValueError("native roster receipt configuration is invalid")
    if value["source_sha256"] != digest(value["source"]) or value[
        "result_sha256"
    ] != digest(value["result"]):
        raise ValueError("native roster receipt configuration changed")
    requested = value["requested_entries"]
    if (value["kind"] == "doctor" and requested is not None) or (
        value["kind"] == "sole-agent-expansion" and not isinstance(requested, dict)
    ):
        raise ValueError("native roster receipt expansion is invalid")
    if (
        classify(
            value["source"],
            value["result"],
            runtime=runtime,
            requested_entries=requested,
        )
        != value["fallbacks"]
    ):
        raise ValueError("native roster receipt fallback rows changed")
    return value


def read_record(path: Path, *, runtime: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or metadata.st_nlink != 1
            or metadata.st_size > 32 * 1024 * 1024
        ):
            raise ValueError("native roster receipt is not a private bounded file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            payload = stream.read(metadata.st_size + 1)
        after = os.fstat(fd)
        public = path.lstat()
        if len(payload) != metadata.st_size or any(
            getattr(item, field) != getattr(metadata, field)
            for item in (after, public)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            raise ValueError("native roster receipt changed while reading")
    finally:
        os.close(fd)
    return validate_record(json.loads(payload), runtime=runtime)


def retained_fallbacks(config_path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    root = config_path.parent.parent
    for name in RECEIPT_NAMES:
        path = root / name
        if path.exists() or path.is_symlink():
            value = read_record(path, runtime=config_path.parent)
            for row in value["fallbacks"]:
                if row not in result:
                    result.append(row)
    return result
