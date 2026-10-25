"""Lossless retained roster reads and writes after native Doctor migration.

Only native Doctor interprets ordered legacy lists and default markers. Keep
that input intact until it has materialized canonical ownership and routing.
Reading retained configuration never rewrites it or chooses a new owner.
"""

from __future__ import annotations

import copy
import re
from typing import Any

_AGENT_ID = re.compile(r"[a-z0-9_][a-z0-9_-]{0,63}")


def agent_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Read either exact roster format, refusing ambiguous/malformed identity."""
    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("OpenClaw agents configuration is invalid")
    if "list" in agents and "entries" in agents:
        raise ValueError("OpenClaw agent roster mixes legacy and current formats")
    ownership = agents.get("ownership")
    if ownership not in (None, "explicit"):
        raise ValueError("OpenClaw agent ownership is invalid")
    if "entries" in agents:
        entries = agents["entries"]
        if (
            not isinstance(entries, dict)
            or not entries
            or any(not isinstance(row, dict) or "id" in row for row in entries.values())
        ):
            raise ValueError("OpenClaw agent entries are invalid")
        rows = [{"id": key, **copy.deepcopy(row)} for key, row in entries.items()]
    else:
        rows = copy.deepcopy(agents.get("list", []))
    if not isinstance(rows, list):
        raise ValueError("OpenClaw agent roster is invalid")
    seen: set[str] = set()
    marked = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("OpenClaw agent row is invalid")
        identifier = row.get("id")
        if (
            not isinstance(identifier, str)
            or _AGENT_ID.fullmatch(identifier) is None
            or identifier in seen
        ):
            raise ValueError("OpenClaw agent identity is invalid or duplicated")
        seen.add(identifier)
        if "default" in row and type(row["default"]) is not bool:
            raise ValueError("OpenClaw default agent marker is invalid")
        marked += row.get("default") is True
    if marked > 1 or (ownership == "explicit" and marked):
        raise ValueError("OpenClaw default agent selection is ambiguous")
    if "entries" in agents and len(rows) > 1 and not marked and ownership != "explicit":
        raise ValueError("OpenClaw current roster has no explicit ownership")
    return rows


def canonical_agent_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Require native migration before a target-runtime topology write."""
    rows = agent_rows(config)
    agents = config.get("agents", {})
    if "entries" not in agents or any("default" in row for row in rows):
        raise ValueError("OpenClaw native Doctor roster migration is required")
    return rows


def replace_agent_rows(
    config: dict[str, Any], rows: list[dict[str, Any]], *, canonical: bool = True
) -> None:
    """Replace only the roster, preserving all other configuration coordinates.

    ``canonical=False`` retains legacy format for legacy-plugin cleanup. Target
    writes require already migrated entries and retain explicit ownership.
    This codec cannot replace native migration or assign implicit surface roles.
    """
    existing = agent_rows(config)
    agents = copy.deepcopy(config.get("agents", {}))
    candidate: dict[str, Any] = {"agents": {"list": copy.deepcopy(rows)}}
    if agents.get("ownership") == "explicit":
        candidate["agents"]["ownership"] = "explicit"
    checked = agent_rows(candidate)
    if not canonical and "entries" not in agents:
        agents["list"] = checked
    else:
        canonical_agent_rows(config)
        if not checked:
            raise ValueError("OpenClaw current roster must contain an agent")
        if any("default" in row for row in checked):
            raise ValueError("OpenClaw canonical roster forbids legacy default markers")
        if agents.get("ownership") != "explicit" and [row["id"] for row in checked] != [
            row["id"] for row in existing
        ]:
            raise ValueError(
                "OpenClaw roster change requires explicit native ownership"
            )
        removed = {row["id"] for row in existing} - {row["id"] for row in checked}
        defaults = agents.get("defaults", {})
        roles = [config.get("talk")]
        if isinstance(defaults, dict):
            roles.extend(
                defaults.get(name)
                for name in (
                    "systemAgent",
                    "heartbeat",
                    "authInheritance",
                    "sessionStore",
                )
            )
        if any(
            isinstance(role, dict) and role.get("agentId") in removed for role in roles
        ):
            raise ValueError(
                "OpenClaw roster change would remove an explicit native owner"
            )
        agents["entries"] = {
            row["id"]: {key: value for key, value in row.items() if key != "id"}
            for row in checked
        }
        agent_rows({"agents": agents})
    config["agents"] = agents
