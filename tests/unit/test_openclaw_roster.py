"""Native migration input is retained; canonical writers preserve ownership."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.openclaw_roster import agent_rows, canonical_agent_rows, replace_agent_rows
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
def test_legacy_migration_input_is_not_reinterpreted(selected: str | None) -> None:
    config = _legacy()
    if selected:
        for row in config["agents"]["list"]:
            row["default"] = row["id"] == selected
    original = copy.deepcopy(config)
    rows = agent_rows(config)
    with pytest.raises(ValueError, match="native Doctor"):
        replace_agent_rows(config, rows)
    assert config == original
    replace_agent_rows(config, rows, canonical=False)
    assert config == original
    rows[0]["tools"]["allow"].append("exec")
    assert config == original


def test_canonical_writes_preserve_native_roles_workspaces_and_routes() -> None:
    config = _legacy()
    rows = config["agents"].pop("list")
    config["agents"]["entries"] = {
        row["id"]: {k: v for k, v in row.items() if k not in {"id", "default"}}
        for row in rows
    }
    config["agents"]["ownership"] = "explicit"
    config["agents"]["defaults"].update(
        {
            role: {"agentId": "member-a"}
            for role in ("heartbeat", "systemAgent", "authInheritance", "sessionStore")
        }
    )
    config["talk"] = {"agentId": "member-a"}
    original = copy.deepcopy(config)
    for _ in range(2):
        replace_agent_rows(config, canonical_agent_rows(config))
        assert config == original
        assert not any("default" in row for row in agent_rows(config))


@pytest.mark.parametrize("marker", [False, True])
@pytest.mark.parametrize("canonical", [False, True])
def test_retired_entry_marker_requires_native_migration(
    marker: bool, canonical: bool
) -> None:
    config = {"agents": {"entries": {"main": {"default": marker}}}}
    original = copy.deepcopy(config)
    assert agent_rows(config) == [{"id": "main", "default": marker}]
    with pytest.raises(ValueError, match="native Doctor"):
        replace_agent_rows(config, [{"id": "main"}], canonical=canonical)
    assert config == original


@pytest.mark.parametrize("marker", [False, True])
def test_writer_cannot_add_retired_marker(marker: bool) -> None:
    config = {"agents": {"entries": {"main": {}}}}
    original = copy.deepcopy(config)
    with pytest.raises(ValueError, match="forbids legacy default"):
        replace_agent_rows(config, [{"id": "main", "default": marker}])
    assert config == original


def test_sole_native_owner_stays_implicit_without_silent_expansion() -> None:
    config = {"agents": {"entries": {"main": {"workspace": "/fixture/main"}}}}
    original = copy.deepcopy(config)
    replace_agent_rows(config, agent_rows(config))
    assert config == original
    for replacement in ([{"id": "other"}], [*agent_rows(config), {"id": "cos"}]):
        with pytest.raises(ValueError, match="explicit native ownership"):
            replace_agent_rows(config, replacement)
        assert config == original


@pytest.mark.parametrize(
    "agents", [{}, {"defaults": {"workspace": "/fixture/main"}}, {"list": []}]
)
def test_defaults_only_provisioning_remains_native(agents: dict[str, Any]) -> None:
    config = {"agents": agents}
    original = copy.deepcopy(config)
    with pytest.raises(ValueError, match="native Doctor"):
        replace_agent_rows(config, [{"id": "main"}])
    assert config == original


@pytest.mark.parametrize(
    "role", ["systemAgent", "heartbeat", "authInheritance", "sessionStore", "talk"]
)
def test_explicit_native_owner_cannot_be_removed(role: str) -> None:
    config: dict[str, Any] = {
        "agents": {"ownership": "explicit", "entries": {"member-a": {}, "cos": {}}}
    }
    if role == "talk":
        config["talk"] = {"agentId": "member-a"}
    else:
        config["agents"]["defaults"] = {role: {"agentId": "member-a"}}
    original = copy.deepcopy(config)
    with pytest.raises(ValueError, match="remove an explicit native owner"):
        replace_agent_rows(config, [{"id": "cos"}])
    assert config == original


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


def test_canonical_writer_cannot_create_empty_roster() -> None:
    config = {"agents": {"ownership": "explicit", "entries": {"member-a": {}}}}
    original = copy.deepcopy(config)
    with pytest.raises(ValueError, match="contain"):
        replace_agent_rows(config, [])
    assert config == original


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
        rows = config["agents"].pop("list")
        config["agents"]["entries"] = {
            row["id"]: {k: v for k, v in row.items() if k != "id"} for row in rows
        }
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
