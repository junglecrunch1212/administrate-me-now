"""Enrollment updates peer routes without provisioning or moving member state."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from adminme.openclaw_roster import agent_rows
from adminme.openclaw_topology import (
    inspect_runtime_topology,
    member_agent_id,
    member_workspace_path,
    service_agent_id,
)
from adminme.service_identities import (
    COS_SERVICE_PRINCIPAL_ID,
    DEFAULT_SERVICE_IDENTITY_CONFIG,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.openclaw_activation import (
    prepare_existing_member_topology,
    reconcile_member_topology,
)

A = "member-topology-fixture-a"  # fixture:tenant_data:ok
B = "member-topology-fixture-b"  # fixture:tenant_data:ok


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = tmp_path.resolve() / "openclaw"
    agents = []
    for member in (A, B):
        workspace = member_workspace_path(root, member)
        workspace.mkdir(parents=True)
        (workspace / "private.txt").write_text("preserved private member state")
        agents.append(
            {
                "id": member_agent_id(member),
                "workspace": str(workspace),
                "tools": {"allow": ["administrateme"]},
            }
        )
    config = {
        "agents": {
            "list": [{"id": "administrator", "workspace": "/fixture/admin"}, *agents]
        },
        "bindings": [
            {"agentId": "administrator", "match": {"channel": "fixture-admin"}},
            {"agentId": member_agent_id(A), "match": {"channel": "stale-channel"}},
        ],
        "plugins": {"entries": {"administrateme": {"config": {"preserved": True}}}},
    }
    services = copy.deepcopy(DEFAULT_SERVICE_IDENTITY_CONFIG)
    channel = services["channels"][0]
    channel.update(
        state="observation",
        enrolled=True,
        openclaw_channel_id="imessage",
        intake_mode="member_submission",
        provider_account_reference="provider-account:"
        + hashlib.sha256(b"fixture-cos").hexdigest(),
    )
    services["channels"] = [channel]
    adapters = {
        "service_identities": services,
        "openclaw_actor_identities": {
            "imessage|fixture-cos|fixture-peer-a": A,
            "imessage|fixture-cos|fixture-peer-b": B,
        },
        "member_chat_enabled_members": [A, B],
    }
    return root, config, adapters


def _canonical_fixture(config: dict[str, Any]) -> None:
    """Authored target shape, not a simulation/proof of native Doctor migration."""
    rows = config["agents"].pop("list")
    config["agents"].update(
        ownership="explicit",
        entries={
            row["id"]: {k: v for k, v in row.items() if k not in {"id", "default"}}
            for row in rows
        },
    )
    for binding in config["bindings"]:
        if binding["agentId"].startswith("adminme-"):
            binding["match"].update(
                accountId="stale-account", peer={"kind": "direct", "id": "stale-peer"}
            )


@pytest.mark.parametrize("canonical", [False, True])
def test_new_service_peer_bindings_are_exact_and_preserve_member_state(
    tmp_path: Path,
    canonical: bool,
) -> None:
    root, previous, adapters = _fixture(tmp_path)
    if canonical:
        _canonical_fixture(previous)
    original = copy.deepcopy(previous)
    prepared = prepare_existing_member_topology(
        previous, adapter_config=adapters, openclaw_root=root, known_members={A, B}
    )
    assert previous == original
    assert prepared["agents"] == previous["agents"]
    assert prepared["bindings"][0] == previous["bindings"][0]
    assert len(prepared["bindings"]) == 3
    assert prepared["plugins"]["entries"]["administrateme"]["config"]["preserved"]
    for member in (A, B):
        assert (member_workspace_path(root, member) / "private.txt").read_text() == (
            "preserved private member state"
        )
    path = root / "runtime/openclaw.json"
    path.parent.mkdir()
    path.write_text(json.dumps(prepared))
    assert (
        inspect_runtime_topology(
            path,
            actor_identities=adapters["openclaw_actor_identities"],
            member_chat_enabled_members=frozenset({A, B}),
        )["state"]
        == "healthy"
    )


@pytest.mark.parametrize(
    "defect",
    ["missing_agent", "changed_workspace", "missing_workspace", "unknown_member"],
)
def test_unprovisioned_topology_is_rejected_without_writes(
    tmp_path: Path, defect: str
) -> None:
    root, config, adapters = _fixture(tmp_path)
    if defect == "missing_agent":
        config["agents"]["list"].pop()
    elif defect == "changed_workspace":
        config["agents"]["list"][-1]["workspace"] = str(root / "other")
    elif defect == "missing_workspace":
        workspace = member_workspace_path(root, B)
        (workspace / "private.txt").unlink()
        workspace.rmdir()
    else:
        adapters["openclaw_actor_identities"]["imessage|fixture-cos|unknown"] = (
            "unknown-member"
        )
    original = copy.deepcopy(config)
    with pytest.raises(RuntimeError, match="member|workspace"):
        prepare_existing_member_topology(
            config, adapter_config=adapters, openclaw_root=root, known_members={A, B}
        )
    assert config == original
    assert not (root / "runtime/openclaw.json").exists()


@pytest.mark.parametrize("prior_owner", [A, B, "service", "operator"])
def test_repeated_reconciliation_preserves_canonical_native_roles(
    tmp_path: Path, prior_owner: str
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    root, config, adapters = _fixture(paths.instance_dir)
    service = {
        "id": service_agent_id(COS_SERVICE_PRINCIPAL_ID),
        "workspace": "/fixture/service",
    }
    config["agents"]["list"].extend([service, {"id": "operator"}])
    _canonical_fixture(config)
    expected = (
        service["id"]
        if prior_owner == "service"
        else "operator"
        if prior_owner == "operator"
        else member_agent_id(prior_owner)
    )
    config["agents"]["defaults"] = {
        role: {"agentId": expected}
        for role in ("systemAgent", "heartbeat", "authInheritance", "sessionStore")
    }
    config["talk"] = {"agentId": expected}
    member_row = config["agents"]["entries"][member_agent_id(A)]
    member_row["model"] = "fixture/retained-model"
    member_row["tools"] = {"allow": ["administrateme"], "deny": ["exec"]}
    original_roles = copy.deepcopy(config["agents"]["defaults"])
    gateway = root / "runtime/openclaw.json"
    gateway.parent.mkdir()
    gateway.write_text(json.dumps(config))
    adapter_path = paths.instance_dir / "config/adapters.json"
    adapter_path.parent.mkdir()
    adapter_path.write_text(json.dumps(adapters))
    first = None
    for _ in range(2):
        reconcile_member_topology(paths)
        current = json.loads(gateway.read_text())
        assert current["agents"]["defaults"] == original_roles
        assert current["talk"] == {"agentId": expected}
        assert current["agents"]["entries"][member_agent_id(A)] == member_row
        assert not any("default" in row for row in agent_rows(current))
        if first is not None:
            assert current == first
        first = current
    for member in (A, B):
        assert (
            member_workspace_path(root, member) / "private.txt"
        ).read_text() == "preserved private member state"


@pytest.mark.parametrize("defect", ["legacy", "marker", "fallback", "workspace"])
def test_reconciliation_refuses_unmigrated_or_unsupported_ownership_without_writes(
    tmp_path: Path, defect: str
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    root, config, adapters = _fixture(paths.instance_dir)
    if defect != "legacy":
        _canonical_fixture(config)
    if defect == "marker":
        config["agents"]["entries"][member_agent_id(A)]["default"] = False
    elif defect == "fallback":
        config["bindings"].append(
            {
                "agentId": member_agent_id(A),
                "match": {"channel": "imessage", "accountId": "*"},
            }
        )
    elif defect == "workspace":
        config["agents"]["entries"][member_agent_id(A)]["workspace"] = (
            "/fixture/retained-other-workspace"
        )
    gateway = root / "runtime/openclaw.json"
    gateway.parent.mkdir()
    gateway.write_text(json.dumps(config))
    original = gateway.read_bytes()
    adapter_path = paths.instance_dir / "config/adapters.json"
    adapter_path.parent.mkdir()
    adapter_path.write_text(json.dumps(adapters))
    with pytest.raises(RuntimeError, match="migrated|routing|coordinates"):
        reconcile_member_topology(paths)
    assert gateway.read_bytes() == original
    for member in (A, B):
        assert (
            member_workspace_path(root, member) / "private.txt"
        ).read_text() == "preserved private member state"
    if defect == "fallback":
        with pytest.raises(RuntimeError, match="routing"):
            prepare_existing_member_topology(
                config,
                adapter_config=adapters,
                openclaw_root=root,
                known_members={A, B},
            )
        assert gateway.read_bytes() == original


def test_current_explicit_ownership_is_not_replaced_with_a_member_default(
    tmp_path: Path,
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    root, config, adapters = _fixture(paths.instance_dir)
    config["agents"]["list"] = config["agents"]["list"][1:]
    config["agents"]["ownership"] = "explicit"
    config["bindings"] = []
    _canonical_fixture(config)
    gateway = root / "runtime/openclaw.json"
    gateway.parent.mkdir()
    gateway.write_text(json.dumps(config))
    adapter_path = paths.instance_dir / "config/adapters.json"
    adapter_path.parent.mkdir()
    adapter_path.write_text(json.dumps(adapters))
    for _ in range(2):
        reconcile_member_topology(paths)
        current = json.loads(gateway.read_text())
        assert current["agents"]["ownership"] == "explicit"
        assert not any(row.get("default") is True for row in agent_rows(current))
        assert {row["id"] for row in agent_rows(current)} == {
            member_agent_id(A),
            member_agent_id(B),
        }
        assert len(current["bindings"]) == 2
        assert {row["agentId"] for row in current["bindings"]} == {
            member_agent_id(A),
            member_agent_id(B),
        }
    for member in (A, B):
        assert (member_workspace_path(root, member) / "private.txt").read_text() == (
            "preserved private member state"
        )


def test_mixed_roster_is_rejected_before_member_state_is_changed(
    tmp_path: Path,
) -> None:
    root, config, adapters = _fixture(tmp_path)
    config["agents"]["entries"] = {"administrator": {}}
    original = copy.deepcopy(config)
    with pytest.raises(RuntimeError, match="member agents"):
        prepare_existing_member_topology(
            config, adapter_config=adapters, openclaw_root=root, known_members={A, B}
        )
    assert config == original
    assert not (root / "runtime/openclaw.json").exists()
