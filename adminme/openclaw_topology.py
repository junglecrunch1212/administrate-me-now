"""Deterministic, content-free OpenClaw member isolation coordinates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def member_agent_id(member_id: str) -> str:
    return "adminme-" + hashlib.sha256(member_id.encode()).hexdigest()[:16]


def member_workspace_path(openclaw_root: Path, member_id: str) -> Path:
    """Return a stable workspace owned by exactly one member agent."""

    return openclaw_root / "member-workspaces" / member_agent_id(member_id)


def binding_fingerprints(
    actor_identities: dict[str, str],
    member_chat_enabled_members: frozenset[str],
) -> tuple[str, ...]:
    values = {
        hashlib.sha256(f"actor|{route.casefold()}|{member}".encode()).hexdigest()
        for route, member in actor_identities.items()
    }
    values.update(
        hashlib.sha256(f"portal|{member}".encode()).hexdigest()
        for member in member_chat_enabled_members
    )
    return tuple(sorted(values))


def inspect_runtime_topology(
    path: Path | None,
    *,
    actor_identities: dict[str, str],
    member_chat_enabled_members: frozenset[str],
) -> dict[str, Any]:
    """Read the current OpenClaw config and verify real member isolation rows."""

    expected_members = set(actor_identities.values()) | set(member_chat_enabled_members)
    expected_agents = {member_agent_id(member) for member in expected_members}
    expected_bindings: set[tuple[str, str, str, str]] = set()
    for route, member in actor_identities.items():
        parts = route.split("|", 2)
        if len(parts) != 3 or not all(parts):
            return {"state": "blocked", "reason": "invalid_expected_actor_route"}
        channel, account, actor = parts
        expected_bindings.add((member_agent_id(member), channel, account, actor))
    if path is None or not path.is_file() or path.is_symlink():
        return {"state": "blocked", "reason": "runtime_config_unavailable"}
    try:
        config = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "blocked", "reason": "runtime_config_invalid"}
    if not isinstance(config, dict):
        return {"state": "blocked", "reason": "runtime_config_invalid"}
    raw_agents = (config.get("agents") or {}).get("list") or []
    raw_bindings = config.get("bindings") or []
    if not isinstance(raw_agents, list) or not isinstance(raw_bindings, list):
        return {"state": "blocked", "reason": "runtime_topology_invalid"}
    actual_agent_ids = [
        str(row.get("id"))
        for row in raw_agents
        if isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and str(row["id"]).startswith("adminme-")
    ]
    actual_agent_workspaces = {
        str(row.get("id")): str(row.get("workspace") or "")
        for row in raw_agents
        if isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and str(row["id"]).startswith("adminme-")
    }
    openclaw_root = path.parent.parent
    expected_workspaces = {
        member_agent_id(member): str(member_workspace_path(openclaw_root, member))
        for member in expected_members
    }
    workspace_directories_ready = all(
        Path(workspace).is_dir() and not Path(workspace).is_symlink()
        for workspace in actual_agent_workspaces.values()
    )
    actual_bindings: list[tuple[str, str, str, str]] = []
    malformed_member_binding = False
    for row in raw_bindings:
        if not isinstance(row, dict):
            continue
        agent_id = row.get("agentId")
        if not isinstance(agent_id, str) or not agent_id.startswith("adminme-"):
            continue
        match = row.get("match")
        peer = match.get("peer") if isinstance(match, dict) else None
        coordinate = (
            agent_id,
            match.get("channel") if isinstance(match, dict) else None,
            match.get("accountId") if isinstance(match, dict) else None,
            peer.get("id") if isinstance(peer, dict) else None,
        )
        if not all(isinstance(value, str) and value for value in coordinate):
            malformed_member_binding = True
            continue
        actual_bindings.append(coordinate)  # type: ignore[arg-type]
    plugin_config = (
        ((config.get("plugins") or {}).get("entries") or {}).get("administrateme") or {}
    ).get("config") or {}
    actual_plugin_agents = (
        plugin_config.get("memberAgentIds") if isinstance(plugin_config, dict) else None
    )
    actual_fingerprints = (
        plugin_config.get("bindingFingerprints")
        if isinstance(plugin_config, dict)
        else None
    )
    dm_scope = (config.get("session") or {}).get("dmScope")
    healthy = all(
        (
            len(actual_agent_ids) == len(set(actual_agent_ids)),
            set(actual_agent_ids) == expected_agents,
            actual_agent_workspaces == expected_workspaces,
            len(set(actual_agent_workspaces.values())) == len(expected_workspaces),
            workspace_directories_ready,
            not malformed_member_binding,
            len(actual_bindings) == len(set(actual_bindings)),
            set(actual_bindings) == expected_bindings,
            dm_scope == "per-account-channel-peer",
            isinstance(actual_plugin_agents, list),
            set(actual_plugin_agents or []) == expected_agents,
            len(actual_plugin_agents or []) == len(expected_agents),
            isinstance(actual_fingerprints, list),
            set(actual_fingerprints or [])
            == set(binding_fingerprints(actor_identities, member_chat_enabled_members)),
            len(actual_fingerprints or [])
            == len(binding_fingerprints(actor_identities, member_chat_enabled_members)),
        )
    )
    return {
        "state": "healthy" if healthy else "blocked",
        "reason": "current_runtime_topology_matches"
        if healthy
        else "current_runtime_topology_drift",
        "member_ids_with_agents": sorted(expected_members) if healthy else [],
        "member_ids_with_account_bindings": sorted(expected_members) if healthy else [],
        "unique_agents": healthy,
        "unique_accounts": healthy,
        "unique_workspaces": healthy,
        "dm_scope": "per_account_channel_peer"
        if dm_scope == "per-account-channel-peer"
        else "invalid",
        "content_exposed": False,
    }


__all__ = [
    "binding_fingerprints",
    "inspect_runtime_topology",
    "member_agent_id",
    "member_workspace_path",
]
