"""A supported app-only denial preserves grants, native tools and exact recovery."""

import copy
import json

import pytest

from bootstrap.native_account_policy import prepare_native_account_policy
from bootstrap.openclaw_activation import (
    prepare_service_topology,
    reconcile_service_topology,
)
from tests.unit.bootstrap.test_cos_service_topology import _fixture


@pytest.mark.parametrize(
    "native_apps",
    [None, {"enabled": True, "allow_all_plugins": True}, {"enabled": False}],
)
def test_supported_topology_changes_only_native_apps_and_preserves_source_config(
    tmp_path, native_apps
):
    paths, release, config_path, original = _fixture(tmp_path)
    manifest_path = release / "openclaw/administrateme/openclaw.plugin.json"
    manifest = json.loads(manifest_path.read_text())
    codex = {
        "enabled": True,
        "config": {"appServer": {"homeScope": "agent", "sandbox": "workspace-write"}},
    }
    if native_apps is not None:
        codex["config"]["codexPlugins"] = copy.deepcopy(native_apps)
    original["plugins"]["entries"]["codex"] = codex
    original["plugins"]["entries"]["administrateme"]["config"].update(
        {
            "standingSelfReplyBindings": ["retained-reply"],
            "scheduledSelfReminderBindings": ["retained-reminder"],
        }
    )
    original["mcp"] = {"servers": {"fixture-local": {"command": "fixture"}}}
    config_path.write_text(json.dumps(original))
    before = config_path.read_bytes()
    baseline = prepare_service_topology(original, paths, release)
    manifest["contracts"]["nativeAccountApps"] = "disabled"
    manifest_path.write_text(json.dumps(manifest))
    expected = copy.deepcopy(baseline)
    expected["plugins"]["entries"]["codex"]["config"].setdefault("codexPlugins", {})[
        "enabled"
    ] = False
    prepared = prepare_service_topology(original, paths, release)
    assert prepared == expected
    assert config_path.read_bytes() == before
    assert json.loads(before) == original
    reconcile_service_topology(paths, release)
    assert json.loads(config_path.read_text()) == expected
    assert prepare_service_topology(expected, paths, release) == expected
    # Native app exclusion neither grants nor hides a separately configured MCP server.
    assert expected["mcp"] == original["mcp"]
    assert expected["agents"] == baseline["agents"]
    assert expected["bindings"] == baseline["bindings"]
    # Targeting an older release does not silently re-enable ambient account access.
    del manifest["contracts"]["nativeAccountApps"]
    manifest_path.write_text(json.dumps(manifest))
    assert prepare_native_account_policy(expected, release) == expected


def test_native_policy_prepares_absent_config_but_never_enables_a_runtime(tmp_path):
    _, release, _, _ = _fixture(tmp_path)
    path = release / "openclaw/administrateme/openclaw.plugin.json"
    manifest = json.loads(path.read_text())
    manifest["contracts"]["nativeAccountApps"] = "disabled"
    path.write_text(json.dumps(manifest))
    original = {"plugins": {"entries": {"codex": {"enabled": True}}}}
    assert prepare_native_account_policy(original, release) == {
        "plugins": {
            "entries": {
                "codex": {
                    "enabled": True,
                    "config": {"codexPlugins": {"enabled": False}},
                }
            }
        }
    }
    assert original == {"plugins": {"entries": {"codex": {"enabled": True}}}}
    for value in ({}, {"plugins": {"entries": {"codex": {"enabled": False}}}}):
        assert prepare_native_account_policy(value, release) == value
    for value in ({"config": []}, {"config": {"codexPlugins": []}}):
        invalid = {"plugins": {"entries": {"codex": {"enabled": True, **value}}}}
        with pytest.raises(RuntimeError, match="malformed"):
            prepare_native_account_policy(invalid, release)


@pytest.mark.parametrize("failure", ["validate", "runtime"])
def test_failed_plugin_reconciliation_restores_exact_account_app_policy(
    tmp_path, monkeypatch, failure
):
    from bootstrap.openclaw_tools import reconcile_administrateme_plugin
    from tests.unit.bootstrap.test_openclaw_plugin_reconciliation import (
        _destructive_plugin_cli,
    )

    paths, release, config_path, original = _fixture(tmp_path)
    original["plugins"]["entries"]["administrateme"]["config"].pop("preserved")
    original["plugins"]["entries"]["codex"] = {
        "enabled": True,
        "config": {"codexPlugins": {"enabled": True, "allow_all_plugins": True}},
    }
    before = (json.dumps(original, indent=4) + "\n").encode()
    config_path.write_bytes(before)
    config_path.chmod(0o600)
    observed = []

    def validate(config):
        observed.append(config["plugins"]["entries"]["codex"]["config"]["codexPlugins"])
        assert observed[-1] == {"enabled": False, "allow_all_plugins": True}

    _destructive_plugin_cli(
        paths, release, monkeypatch, validate=validate, failure=failure
    )
    with pytest.raises(RuntimeError):
        reconcile_administrateme_plugin(paths, release)
    assert observed
    assert config_path.read_bytes() == before
    assert config_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("browser_enabled", [False, True])
def test_browser_topology_and_account_exclusion_compose_in_both_activation_paths(
    tmp_path, browser_enabled
):
    from datetime import datetime, timezone

    from adminme.openclaw_topology import SERVICE_PRIVATE_TOOLS, service_agent_id
    from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
    from adminme.workstation_browser import BROWSER_POLICY_FILE, prepare_browser_gateway
    from adminme.workstation_inventory import INVENTORY_FILE
    from tests.unit.bootstrap.test_workstation_browser import private_file
    from tests.unit.test_workstation_inventory import document

    paths, release, config_path, original = _fixture(tmp_path)
    manifest_path = release / "openclaw/administrateme/openclaw.plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["contracts"].update(
        {
            "nativeAccountApps": "disabled",
            "agentToolResultMiddleware": ["openclaw", "codex"],
        }
    )
    manifest["contracts"]["tools"].append("administrateme_workstation_work")
    manifest_path.write_text(json.dumps(manifest))
    config_directory = paths.instance_dir / "config"
    config_directory.chmod(0o700)
    private_file(
        config_directory / INVENTORY_FILE, document(datetime.now(timezone.utc))
    )
    private_file(
        config_directory / BROWSER_POLICY_FILE,
        {
            "revision": 1,
            "enabled": browser_enabled,
            "resources": [
                {
                    "resource_id": "planning",
                    "inventory_binding_id": "cos-browser",
                    "inventory_binding_revision": 1,
                    "native_target_id": "observed-tab",
                    "target_evidence_reference": "fixture:profile-tab",
                    "url": "https://work.example.invalid/planning",
                    "authorized_url_prefix": "https://work.example.invalid/",
                    "identity_probes": {
                        key: {"selector": f"#{key}"}
                        for key in ("account", "workspace", "role")
                    },
                    "audience_member_ids": ["member-a"],
                }
            ],
        },
    )
    original = prepare_browser_gateway(original)
    plugin = original["plugins"]["entries"]["administrateme"]
    plugin.update({"enabled": True, "hooks": {"allowConversationAccess": True}})
    plugin["config"].update(
        {
            "serviceWorkEnabled": True,
            "standingSelfReplyBindings": ["retained-reply"],
            "scheduledSelfReminderBindings": ["retained-reminder"],
        }
    )
    original["plugins"]["entries"]["codex"] = {
        "enabled": True,
        "config": {"codexPlugins": {"enabled": True}},
    }
    config_path.write_text(json.dumps(original))
    before = config_path.read_bytes()
    prepared = prepare_service_topology(original, paths, release)
    assert config_path.read_bytes() == before
    assert prepared["plugins"]["entries"]["codex"]["config"]["codexPlugins"] == {
        "enabled": False
    }
    assert prepared["plugins"]["entries"]["administrateme"]["hooks"] == {
        "allowConversationAccess": True,
        "allowPromptInjection": True,
    }
    assert (
        prepared["plugins"]["entries"]["administrateme"]["config"] == plugin["config"]
    )
    assert prepared["browser"] == original["browser"]
    assert prepared["agents"]["list"][:-1] == original["agents"]["list"]
    assert prepared["bindings"] == original["bindings"]
    service = prepared["agents"]["list"][-1]
    assert service["id"] == service_agent_id(COS_SERVICE_PRINCIPAL_ID)
    allowed = [*SERVICE_PRIVATE_TOOLS, *(["browser"] if browser_enabled else [])]
    assert service["tools"] == {"allow": allowed}
    assert reconcile_service_topology(paths, release)["service_tool_count"] == len(
        allowed
    )
    assert json.loads(config_path.read_text()) == prepared
    assert prepare_service_topology(prepared, paths, release) == prepared
