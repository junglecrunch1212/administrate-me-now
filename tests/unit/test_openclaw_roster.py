"""Exact native roster conversion; retained identity and domain boundaries."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.openclaw_roster import agent_rows, replace_agent_rows
from adminme.openclaw_topology import (
    binding_fingerprints,
    inspect_runtime_topology,
    member_agent_id,
    member_workspace_path,
)
from adminme.workstation_inventory import workstation_tools


def _legacy() -> dict[str, Any]:
    return {
        "agents": {
            "defaults": {
                "workspace": "/fixture/default",
                "model": {"primary": "fixture/model"},
            },
            "list": [
                {
                    "id": "member-a",
                    "workspace": "/fixture/a",
                    "tools": {"allow": ["administrateme"]},
                },
                {
                    "id": "cos",
                    "workspace": "/fixture/cos",
                    "default": False,
                    "tools": {"allow": ["memory_get"], "deny": ["exec"]},
                },
            ],
        },
        "bindings": [
            {
                "agentId": "member-a",
                "match": {"channel": "fixture", "accountId": "fixture-account"},
            }
        ],
        "session": {"dmScope": "per-account-channel-peer"},
    }


@pytest.mark.parametrize("selected", [None, "member-a", "cos"])
def test_conversion_preserves_native_coordinates_and_default(
    selected: str | None,
) -> None:
    config = _legacy()
    if selected:
        for row in config["agents"]["list"]:
            row["default"] = row["id"] == selected
    original = copy.deepcopy(config)
    rows = agent_rows(config)
    assert config == original
    replace_agent_rows(config, rows)
    assert "list" not in config["agents"]
    assert list(config["agents"]["entries"]) == ["member-a", "cos"]
    assert config["agents"]["defaults"] == original["agents"]["defaults"]
    assert config["bindings"] == original["bindings"]
    assert config["session"] == original["session"]
    normalized = agent_rows(config)
    assert [row["id"] for row in normalized if row.get("default") is True] == [
        selected or "member-a"
    ]
    for before, after in zip(rows, normalized, strict=True):
        assert {k: v for k, v in before.items() if k != "default"} == {
            k: v for k, v in after.items() if k != "default"
        }
    first = copy.deepcopy(config)
    replace_agent_rows(config, agent_rows(config))
    assert config == first
    # Detached reads cannot mutate a current grant or retained historical config.
    normalized[0]["tools"]["allow"].append("exec")
    assert config == first
    assert original["agents"]["list"] == rows


def test_explicit_ownership_does_not_invent_a_default() -> None:
    config = {
        "agents": {"ownership": "explicit", "entries": {"member-a": {}, "cos": {}}}
    }
    original = copy.deepcopy(config)
    replace_agent_rows(config, agent_rows(config))
    assert config == original


def test_legacy_cleanup_retains_exact_roster_format() -> None:
    config = _legacy()
    original = copy.deepcopy(config)
    replace_agent_rows(config, agent_rows(config), canonical=False)
    assert config == original


@pytest.mark.parametrize(
    "agents",
    [
        {"list": [], "entries": {}},
        {"entries": {}},
        {"list": [{"id": "a"}, {"id": "a"}]},
        {"list": [{"id": "A"}, {"id": "a"}]},
        {"list": [{"id": " a "}]},
        {"list": [{"id": "../a"}]},
        {"list": [{"id": "a", "default": 1}]},
        {"list": [{"id": "a", "default": True}, {"id": "b", "default": True}]},
        {"entries": {"a": {"id": "b"}}},
        {"entries": {"a": None}},
        {"entries": {"a": {}, "b": {}}},
        {"ownership": "explicit", "entries": {"a": {"default": True}}},
        {"ownership": "unknown", "entries": {"a": {}}},
    ],
)
def test_ambiguous_or_relabelled_identity_is_refused(agents: dict[str, Any]) -> None:
    config = {"agents": agents}
    original = copy.deepcopy(config)
    with pytest.raises(ValueError):
        agent_rows(config)
    assert config == original


def test_conversion_cannot_remove_implicit_default_or_create_an_empty_roster() -> None:
    config = _legacy()
    before = copy.deepcopy(config)
    with pytest.raises(ValueError, match="default"):
        replace_agent_rows(config, [agent_rows(config)[1]])
    assert config == before
    with pytest.raises(ValueError, match="contain"):
        replace_agent_rows(config, [])
    assert config == before


@pytest.mark.parametrize("canonical", [False, True])
def test_production_topology_and_inventory_read_both_formats_without_writes(
    tmp_path: Path, canonical: bool
) -> None:
    member = "member-roster-fixture"
    agent = member_agent_id(member)
    root = tmp_path / "openclaw"
    workspace = member_workspace_path(root, member)
    workspace.mkdir(parents=True)
    private = workspace / "memory.txt"
    private.write_text("retained fixture memory")
    config = {
        "agents": {
            "list": [
                {
                    "id": agent,
                    "workspace": str(workspace),
                    "tools": {"allow": ["administrateme"], "deny": ["exec"]},
                }
            ]
        },
        "bindings": [],
        "session": {"dmScope": "per-account-channel-peer"},
        "plugins": {
            "entries": {
                "administrateme": {
                    "config": {
                        "memberAgentIds": [agent],
                        "bindingFingerprints": list(
                            binding_fingerprints({}, frozenset({member}))
                        ),
                    }
                }
            }
        },
    }
    if canonical:
        replace_agent_rows(config, agent_rows(config))
    path = root / "runtime/openclaw.json"
    path.parent.mkdir()
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    before = path.read_bytes()
    assert (
        inspect_runtime_topology(
            path, actor_identities={}, member_chat_enabled_members=frozenset({member})
        )["state"]
        == "healthy"
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(openclaw_runtime_config_path=path)
    )
    assert workstation_tools(runtime, agent)["state"] == "policy_read"
    assert path.read_bytes() == before
    assert private.read_text() == "retained fixture memory"
    config["agents"] = {"list": [{"id": agent}], "entries": {agent: {}}}
    path.write_text(json.dumps(config))
    assert (
        inspect_runtime_topology(
            path, actor_identities={}, member_chat_enabled_members=frozenset({member})
        )["state"]
        == "blocked"
    )
    assert workstation_tools(runtime, agent)["state"] == "native_policy_unavailable"
