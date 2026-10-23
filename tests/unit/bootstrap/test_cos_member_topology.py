"""Enrollment updates peer routes without provisioning or moving member state."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

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


def test_new_service_peer_bindings_are_exact_and_preserve_member_state(
    tmp_path: Path,
) -> None:
    root, previous, adapters = _fixture(tmp_path)
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


@pytest.mark.parametrize("prior_default", [None, A, B, "service", "operator"])
def test_repeated_reconciliation_preserves_or_recovers_member_default(
    tmp_path: Path, prior_default: str | None
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    root, config, adapters = _fixture(paths.instance_dir)
    members = config["agents"]["list"][1:]
    service = {
        "id": service_agent_id(COS_SERVICE_PRINCIPAL_ID),
        "workspace": "/fixture/service",
    }
    config["agents"]["list"] = [*members, service]
    if prior_default in {A, B}:
        for row, member in zip(members, (A, B), strict=True):
            row["default"] = member == prior_default
    elif prior_default == "service":
        service["default"] = True
    elif prior_default == "operator":
        config["agents"]["list"].insert(0, {"id": "operator", "default": True})
    gateway = root / "runtime/openclaw.json"
    gateway.parent.mkdir()
    gateway.write_text(json.dumps(config))
    adapter_path = paths.instance_dir / "config/adapters.json"
    adapter_path.parent.mkdir()
    adapter_path.write_text(json.dumps(adapters))
    expected = (
        service["id"]
        if prior_default == "service"
        else "operator"
        if prior_default == "operator"
        else member_agent_id(prior_default or A)
    )
    first = None
    # Exercise two successive installations, including the already-lost flag.
    for _ in range(2):
        reconcile_member_topology(paths)
        current = json.loads(gateway.read_text())
        assert [
            row["id"] for row in current["agents"]["list"] if row.get("default") is True
        ] == [expected]
        if first is not None:
            assert current == first
        first = current
    for member in (A, B):
        assert (
            member_workspace_path(root, member) / "private.txt"
        ).read_text() == "preserved private member state"
