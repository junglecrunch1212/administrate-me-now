"""Service work owns an isolated native agent, never a synthetic member."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from adminme.openclaw_topology import (
    SERVICE_PRIVATE_IDENTITY,
    SERVICE_PRIVATE_INSTRUCTIONS,
    SERVICE_PRIVATE_TOOLS,
    member_agent_id,
    service_agent_id,
    service_session_key,
    service_session_valid,
    service_workspace_path,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.contracts import DeploymentPaths
from bootstrap.openclaw_activation import (
    prepare_service_topology,
    reconcile_service_topology,
)


def _fixture(tmp_path: Path) -> tuple[DeploymentPaths, Path, Path, dict]:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    release = tmp_path / "release"
    plugin = release / "openclaw" / "administrateme"
    source = plugin / "workspace" / "service-private" / "AGENTS.md"
    source.parent.mkdir(parents=True)
    source.write_text(SERVICE_PRIVATE_INSTRUCTIONS)
    (source.parent / "IDENTITY.md").write_text(SERVICE_PRIVATE_IDENTITY)
    (plugin / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "contracts": {
                    "tools": list(SERVICE_PRIVATE_TOOLS),
                    "serviceWorkspaceFiles": ["AGENTS.md", "IDENTITY.md"],
                }
            }
        )
    )
    config = {
        "agents": {
            "list": [
                {"id": "operator", "default": True, "workspace": "/fixture/operator"},
                {"id": member_agent_id("member-a"), "workspace": "/fixture/member"},
            ]
        },
        "bindings": [
            {"agentId": member_agent_id("member-a"), "match": {"peer": "fixture"}}
        ],
        "plugins": {
            "entries": {
                "administrateme": {
                    "config": {"preserved": True, "serviceWorkEnabled": False}
                }
            }
        },
    }
    config_path = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config))
    adapters = paths.instance_dir / "config" / "adapters.json"
    adapters.parent.mkdir(parents=True)
    adapters.write_text(json.dumps({"member_chat_enabled_members": ["member-a"]}))
    template = paths.instance_dir / "openclaw" / "workspace"
    template.mkdir()
    (template / "USER.md").write_text("private member overlay must not be copied")
    return paths, release, config_path, config


def test_service_binding_matches_native_plugin_fixture() -> None:
    principal = COS_SERVICE_PRINCIPAL_ID
    assert service_agent_id(principal) == "am-service-4f2e45be890cdc3f"
    assert service_session_key(principal, "fixture-secret-at-least-24-characters") == (
        "agent:am-service-4f2e45be890cdc3f:administrateme-service-"
        "8c142ef930b2dfb4313889945ebd5197"
    )
    assert service_agent_id(principal) != member_agent_id(principal)
    with pytest.raises(ValueError, match="transport secret"):
        service_session_key(principal, "short")
    root = Path(__file__).resolve().parents[3]
    assert (
        root / "openclaw/administrateme/workspace/service-private/AGENTS.md"
    ).read_text() == SERVICE_PRIVATE_INSTRUCTIONS
    assert (
        root / "openclaw/administrateme/workspace/service-private/IDENTITY.md"
    ).read_text() == SERVICE_PRIVATE_IDENTITY


def test_service_install_and_rollback_preserve_member_and_service_state(
    tmp_path: Path,
) -> None:
    paths, release, config_path, previous = _fixture(tmp_path)
    before = config_path.read_bytes()
    expected = prepare_service_topology(previous, paths, release)
    assert config_path.read_bytes() == before
    assert not (paths.instance_dir / "openclaw/service-workspaces").exists()
    result = reconcile_service_topology(paths, release)
    assert result["service_agent_count"] == 1
    assert result["provider_authority"] is False
    current = json.loads(config_path.read_text())
    assert current == expected
    assert current["agents"]["list"][:-1] == previous["agents"]["list"]
    assert current["bindings"] == previous["bindings"]
    assert current["plugins"] == previous["plugins"]
    agent = current["agents"]["list"][-1]
    assert agent["id"] == service_agent_id(COS_SERVICE_PRINCIPAL_ID)
    assert agent["tools"] == {"allow": list(SERVICE_PRIVATE_TOOLS)}
    workspace = service_workspace_path(
        paths.instance_dir / "openclaw", COS_SERVICE_PRINCIPAL_ID
    )
    assert agent["workspace"] == str(workspace)
    assert sorted(path.name for path in workspace.iterdir()) == [
        "AGENTS.md",
        "IDENTITY.md",
    ]
    assert (workspace / "IDENTITY.md").read_text() == SERVICE_PRIVATE_IDENTITY
    private_memory = workspace / "private-memory.txt"
    private_memory.write_text("retained operational context")
    assert reconcile_service_topology(paths, release) == result
    assert json.loads(config_path.read_text()) == current

    source = release / "openclaw/administrateme/workspace/service-private/AGENTS.md"
    source.write_text(
        SERVICE_PRIVATE_INSTRUCTIONS + "\nRevised internal instructions.\n"
    )
    reconcile_service_topology(paths, release)
    revised = json.loads(config_path.read_text())["agents"]["list"][-1]
    assert revised["workspace"] != agent["workspace"]
    assert (workspace / "AGENTS.md").read_text() == SERVICE_PRIVATE_INSTRUCTIONS
    source.write_text(SERVICE_PRIVATE_INSTRUCTIONS)
    reconcile_service_topology(paths, release)
    assert json.loads(config_path.read_text())["agents"]["list"][-1] == agent
    assert private_memory.read_text() == "retained operational context"

    identity = source.parent / "IDENTITY.md"
    identity.write_text(SERVICE_PRIVATE_IDENTITY + "\nEstablished role refinement.\n")
    reconcile_service_topology(paths, release)
    assert (
        json.loads(config_path.read_text())["agents"]["list"][-1]["workspace"]
        != agent["workspace"]
    )
    assert (workspace / "IDENTITY.md").read_text() == SERVICE_PRIVATE_IDENTITY
    identity.write_text(SERVICE_PRIVATE_IDENTITY)
    reconcile_service_topology(paths, release)
    assert json.loads(config_path.read_text())["agents"]["list"][-1] == agent

    (release / "openclaw/administrateme/openclaw.plugin.json").write_text("{}")
    assert prepare_service_topology(current, paths, release) == previous
    assert reconcile_service_topology(paths, release)["service_agent_count"] == 0
    assert json.loads(config_path.read_text()) == previous
    assert private_memory.read_text() == "retained operational context"


@pytest.mark.parametrize(
    "defect",
    [
        "source_symlink",
        "workspace_symlink",
        "instructions_changed",
        "identity_source_symlink",
        "identity_missing",
        "identity_changed",
        "identity_target_symlink",
        "invalid_declaration",
    ],
)
def test_service_install_rejects_unsafe_workspace_before_config_write(
    tmp_path: Path, defect: str
) -> None:
    paths, release, config_path, previous = _fixture(tmp_path)
    workspace = service_workspace_path(
        paths.instance_dir / "openclaw", COS_SERVICE_PRINCIPAL_ID
    )
    if defect in {"source_symlink", "identity_source_symlink"}:
        filename = "AGENTS.md" if defect == "source_symlink" else "IDENTITY.md"
        source = (
            release / "openclaw/administrateme/workspace/service-private" / filename
        )
        source.unlink()
        replacement = tmp_path / "unreviewed.md"
        replacement.write_text(SERVICE_PRIVATE_INSTRUCTIONS)
        source.symlink_to(replacement)
    elif defect == "workspace_symlink":
        workspace.parent.mkdir(parents=True)
        workspace.symlink_to(tmp_path, target_is_directory=True)
    elif defect in {
        "instructions_changed",
        "identity_changed",
        "identity_target_symlink",
    }:
        workspace.mkdir(parents=True)
        filename = "AGENTS.md" if defect == "instructions_changed" else "IDENTITY.md"
        target = workspace / filename
        if defect == "identity_target_symlink":
            replacement = tmp_path / "identity.md"
            replacement.write_text(SERVICE_PRIVATE_IDENTITY)
            target.symlink_to(replacement)
        else:
            target.write_text("Unreviewed replacement")
    elif defect == "identity_missing":
        (
            release / "openclaw/administrateme/workspace/service-private/IDENTITY.md"
        ).unlink()
    else:
        manifest = release / "openclaw/administrateme/openclaw.plugin.json"
        content = json.loads(manifest.read_text())
        content["contracts"]["serviceWorkspaceFiles"] = ["AGENTS.md", "USER.md"]
        manifest.write_text(json.dumps(content))
    snapshot = copy.deepcopy(previous)
    with pytest.raises(RuntimeError, match="instructions|workspace"):
        reconcile_service_topology(paths, release)
    assert json.loads(config_path.read_text()) == snapshot


def test_service_legacy_workspace_is_reselected_without_rewriting_history(
    tmp_path: Path,
) -> None:
    paths, release, config_path, previous = _fixture(tmp_path)
    plugin = release / "openclaw/administrateme"
    manifest = plugin / "openclaw.plugin.json"
    declared = json.loads(manifest.read_text())
    legacy = copy.deepcopy(declared)
    del legacy["contracts"]["serviceWorkspaceFiles"]
    manifest.write_text(json.dumps(legacy))
    identity = plugin / "workspace/service-private/IDENTITY.md"
    identity.unlink()
    reconcile_service_topology(paths, release)
    old_agent = json.loads(config_path.read_text())["agents"]["list"][-1]
    old_workspace = Path(old_agent["workspace"])
    assert (
        old_workspace.name
        == hashlib.sha256(SERVICE_PRIVATE_INSTRUCTIONS.encode()).hexdigest()
    )
    memory = old_workspace / "private-memory.txt"
    memory.write_text("retained legacy work")
    identity.write_text(SERVICE_PRIVATE_IDENTITY)
    manifest.write_text(json.dumps(declared))
    reconcile_service_topology(paths, release)
    current = json.loads(config_path.read_text())
    assert current["agents"]["list"][-1]["workspace"] != old_agent["workspace"]
    assert current["agents"]["list"][:-1] == previous["agents"]["list"]
    assert "defaults" not in current["agents"]
    assert not (old_workspace / "IDENTITY.md").exists()
    manifest.write_text(json.dumps(legacy))
    reconcile_service_topology(paths, release)
    assert json.loads(config_path.read_text())["agents"]["list"][-1] == old_agent
    assert memory.read_text() == "retained legacy work"


def test_service_request_session_is_signed_and_does_not_reuse_context() -> None:
    principal = COS_SERVICE_PRINCIPAL_ID
    secret = "fixture-secret-at-least-24-characters"
    canonical = service_session_key(principal, secret)
    first = service_session_key(principal, secret, "fixture-request")
    second = service_session_key(principal, secret, "different-request")
    assert first != second != canonical
    assert first == service_session_key(principal, secret, "fixture-request")
    assert service_session_valid(principal, secret, first)
    assert service_session_valid(principal, secret, second)
    assert service_session_valid(principal, secret, canonical)
    for invalid in (
        first + "x",
        first.replace("service-", "member-"),
        first[:-1],
        first.upper(),
        first + "\n",
    ):
        assert not service_session_valid(principal, secret, invalid)
    assert not service_session_valid(principal, secret + "different", first)
    assert not service_session_valid("service-principal:unknown", secret, first)


def test_supported_workstation_hook_changes_only_exact_reviewed_capability(
    tmp_path: Path,
) -> None:
    paths, release, config_path, previous = _fixture(tmp_path)
    plugin = previous["plugins"]["entries"]["administrateme"]
    plugin["enabled"] = True
    plugin["config"].update(
        {
            "serviceWorkEnabled": True,
            "standingSelfReplyBindings": ["retained-reply"],
            "scheduledSelfReminderBindings": ["retained-reminder"],
        }
    )
    plugin["hooks"] = {"allowConversationAccess": True, "allowPromptInjection": False}
    config_path.write_text(json.dumps(previous))
    baseline = prepare_service_topology(previous, paths, release)
    manifest_path = release / "openclaw/administrateme/openclaw.plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["contracts"]["tools"].append("administrateme_workstation_work")
    manifest_path.write_text(json.dumps(manifest))
    selected = prepare_service_topology(previous, paths, release)
    expected = copy.deepcopy(baseline)
    expected["plugins"]["entries"]["administrateme"]["hooks"][
        "allowPromptInjection"
    ] = True
    assert selected == expected
    assert json.loads(config_path.read_text()) == previous
    reconcile_service_topology(paths, release)
    assert json.loads(config_path.read_text()) == selected
    previous["plugins"]["entries"]["administrateme"]["config"]["serviceWorkEnabled"] = (
        False
    )
    assert (
        prepare_service_topology(previous, paths, release)["plugins"]
        == previous["plugins"]
    )
