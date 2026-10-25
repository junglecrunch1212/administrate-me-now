"""Lossless agent roster reads and the reviewed OpenClaw entries transition.

Legacy list order can determine the default agent. Conversion makes that same
selection explicit; it never renames an agent, moves state or changes a binding.
Reading retained configuration does not rewrite it.
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


def replace_agent_rows(
    config: dict[str, Any], rows: list[dict[str, Any]], *, canonical: bool = True
) -> None:
    """Replace only the roster, preserving all other configuration coordinates.

    ``canonical=False`` retains legacy format for legacy-plugin cleanup. Normal
    target writes use entries. Callers retain ownership of their authorized row
    changes; this codec supplies no new tools, account routes or default choice.
    """
    existing = agent_rows(config)
    agents = copy.deepcopy(config.get("agents", {}))
    # Validate replacement identities without interpreting a legacy list's order
    # as a new owner when the current roster has explicit ownership.
    candidate: dict[str, Any] = {"agents": {"list": copy.deepcopy(rows)}}
    if agents.get("ownership") == "explicit":
        candidate["agents"]["ownership"] = "explicit"
    checked = agent_rows(candidate)
    if not canonical and "entries" not in agents:
        agents["list"] = checked
    else:
        if not checked:
            raise ValueError("OpenClaw current roster must contain an agent")
        if agents.get("ownership") != "explicit" and not any(
            row.get("default") is True for row in checked
        ):
            previous_default = next(
                (row["id"] for row in existing if row.get("default") is True),
                existing[0]["id"] if existing else None,
            )
            if previous_default is not None:
                match = next(
                    (row for row in checked if row["id"] == previous_default), None
                )
                if match is None:
                    raise ValueError(
                        "OpenClaw conversion would change the default agent"
                    )
                match["default"] = True
            elif len(checked) > 1:
                raise ValueError("OpenClaw new roster requires an explicit owner")
        agents.pop("list", None)
        agents["entries"] = {
            row["id"]: {key: value for key, value in row.items() if key != "id"}
            for row in checked
        }
        agent_rows({"agents": agents})
    config["agents"] = agents
