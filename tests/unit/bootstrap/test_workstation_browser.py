"""Private provisioning changes only reviewed policy and a new preparation file."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from adminme.workstation_browser import BROWSER_POLICY_FILE
from adminme.workstation_inventory import INVENTORY_FILE
from bootstrap.workstation_browser import prepare_config, provision
from tests.unit.test_workstation_inventory import document


def private_file(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_private_browser_preparation_preserves_existing_config_and_credentials(
    tmp_path,
):
    previous = tmp_path / "gateway.json"
    original = {
        "gateway": {"auth": {"token": "synthetic-test-only"}},
        "agents": {"list": [{"id": "service", "tools": {"allow": ["fixture"]}}]},
    }
    private_file(previous, original)
    output = tmp_path / "prepared.json"
    assert prepare_config(previous, output)["state"] == "prepared_not_activated"
    prepared = json.loads(output.read_text())
    assert json.loads(previous.read_text()) == original
    assert prepared["gateway"]["auth"] == original["gateway"]["auth"]
    assert prepared["agents"] == original["agents"]
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        prepare_config(previous, output)
    link = tmp_path / "output-link"
    link.symlink_to(previous)
    with pytest.raises(FileExistsError):
        prepare_config(previous, link)


def test_verified_tab_refresh_uses_current_revision_and_can_revoke_without_evidence(
    tmp_path,
):
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    private_file(config / INVENTORY_FILE, document(datetime.now(timezone.utc)))
    policy = {
        "revision": 1,
        "enabled": True,
        "resources": [
            {
                "resource_id": "own-page",
                "inventory_binding_id": "cos-browser",
                "inventory_binding_revision": 1,
                "native_target_id": "actual-native-target",
                "target_evidence_reference": "operator-observation:profile-and-tab",
                "url": "https://work.example.invalid/planning",
                "authorized_url_prefix": "https://work.example.invalid/",
                "identity_probes": {
                    key: {"selector": f"#{key}"}
                    for key in ("account", "workspace", "role")
                },
                "audience_member_ids": ["member-a"],
            }
        ],
    }
    assert provision(config, policy, expected_revision=0)["enabled"] is True
    assert (config / BROWSER_POLICY_FILE).stat().st_mode & 0o777 == 0o600
    policy["revision"] = 2
    policy["resources"][0]["native_target_id"] = "replacement-native-target"
    with pytest.raises(ValueError, match="revision"):
        provision(config, policy, expected_revision=0)
    assert provision(config, policy, expected_revision=1)["revision"] == 2
    (config / INVENTORY_FILE).unlink()
    with pytest.raises(FileNotFoundError):
        provision(config, {**policy, "revision": 3}, expected_revision=2)
    assert (
        provision(
            config, {**policy, "revision": 3, "enabled": False}, expected_revision=2
        )["enabled"]
        is False
    )
    config.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        provision(config, {**policy, "revision": 4}, expected_revision=3)


@pytest.mark.parametrize("canonical", [False, True])
def test_native_patch_uses_hash_guard_and_only_browser_and_existing_cos_agent(
    tmp_path,
    canonical,
):
    from adminme.openclaw_topology import SERVICE_PRIVATE_TOOLS, service_agent_id
    from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
    from bootstrap.contracts import DeploymentPaths
    from bootstrap.workstation_browser import native_service_tools, prepare_native_patch
    from adminme.workstation_browser import prepare_browser_gateway

    paths = DeploymentPaths.from_root(tmp_path)
    directory = paths.instance_dir / "config"
    directory.mkdir(parents=True, mode=0o700)
    private_file(directory / INVENTORY_FILE, document(datetime.now(timezone.utc)))
    resource = {
        "resource_id": "planning",
        "inventory_binding_id": "cos-browser",
        "inventory_binding_revision": 1,
        "native_target_id": "observed-tab",
        "target_evidence_reference": "fixture:profile-tab",
        "url": "https://work.example.invalid/planning",
        "authorized_url_prefix": "https://work.example.invalid/",
        "identity_probes": {
            key: {"selector": f"#{key}"} for key in ("account", "workspace", "role")
        },
        "audience_member_ids": ["member-a"],
    }
    private_file(
        directory / BROWSER_POLICY_FILE,
        {"revision": 1, "enabled": True, "resources": [resource]},
    )
    release = tmp_path / "release"
    manifest = release / "openclaw" / "administrateme" / "openclaw.plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"contracts": {"agentToolResultMiddleware": ["openclaw", "codex"]}})
    )
    agent = service_agent_id(COS_SERVICE_PRINCIPAL_ID)
    original = {
        "plugins": {
            "entries": {
                "administrateme": {
                    "enabled": True,
                    "hooks": {
                        "allowPromptInjection": True,
                        "allowConversationAccess": True,
                    },
                }
            }
        },
        "agents": {
            "list": [
                {"id": agent, "tools": {"allow": list(SERVICE_PRIVATE_TOOLS)}},
                {"id": "member-agent", "tools": {"allow": ["administrateme_state"]}},
            ]
        },
        "gateway": {"auth": {"token": "synthetic-secret-never-in-patch"}},
    }
    if canonical:
        rows = original["agents"].pop("list")
        original["agents"]["ownership"] = "explicit"
        original["agents"]["entries"] = {
            row["id"]: {k: v for k, v in row.items() if k != "id"} for row in rows
        }
    snapshot = {"valid": True, "hash": "a" * 64, "config": original}
    params = prepare_native_patch(snapshot, paths, release)
    patch = json.loads(params["raw"])
    assert params["baseHash"] == snapshot["hash"]
    expected_tools = {"allow": [*SERVICE_PRIVATE_TOOLS, "browser"]}
    assert patch["agents"] == (
        {"entries": {agent: {"tools": expected_tools}}}
        if canonical
        else {"list": [{"id": agent, "tools": expected_tools}]}
    )
    assert "synthetic-secret" not in json.dumps(params)
    assert patch["browser"]["allowSystemProfileImport"] is False
    assert patch["plugins"] == {"entries": {"browser": {"enabled": True}}}
    assert "browser" not in original
    assert json.loads(
        prepare_native_patch(snapshot, paths, release, disable=True)["raw"]
    ) == {"browser": {"enabled": False}}
    with pytest.raises(ValueError, match="snapshot"):
        prepare_native_patch({**snapshot, "hash": ""}, paths, release)
    prepared = prepare_browser_gateway(original)
    prepared["plugins"]["entries"]["administrateme"]["hooks"][
        "allowPromptInjection"
    ] = False
    assert native_service_tools(prepared, paths, release) == []
    with pytest.raises(ValueError, match="finite worker policy hook"):
        prepare_native_patch({**snapshot, "config": prepared}, paths, release)
    assert json.loads(
        prepare_native_patch(
            {**snapshot, "config": prepared}, paths, release, disable=True
        )["raw"]
    ) == {"browser": {"enabled": False}}
    prepared["plugins"]["entries"]["administrateme"]["hooks"][
        "allowPromptInjection"
    ] = True
    prepared["plugins"]["entries"]["administrateme"]["hooks"][
        "allowConversationAccess"
    ] = False
    assert native_service_tools(prepared, paths, release) == []
    with pytest.raises(ValueError, match="finite worker policy hook"):
        prepare_native_patch({**snapshot, "config": prepared}, paths, release)
    prepared["plugins"]["entries"]["administrateme"]["enabled"] = False
    assert native_service_tools(prepared, paths, release) == []
    manifest.write_text(json.dumps({"contracts": {}}))
    with pytest.raises(ValueError, match="deployed release"):
        prepare_native_patch(snapshot, paths, release)
