"""Exact-release native OpenClaw plugin registration evidence."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable

import jsonschema
import pytest

from adminme.openclaw_topology import (
    SERVICE_PRIVATE_INSTRUCTIONS,
    SERVICE_PRIVATE_TOOLS,
    member_agent_id,
    service_agent_id,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.openclaw_activation import remove_unsupported_member_topology
from bootstrap.openclaw_tools import (
    OPENCLAW_PLUGIN_COMMAND_TIMEOUT_SECONDS,
    administrateme_plugin_status,
    reconcile_administrateme_plugin,
)


def _inspection(plugin: Path) -> str:
    return json.dumps(
        {
            "plugin": {
                "id": "administrateme",
                "status": "loaded",
                "activated": True,
                "source": str(plugin / "dist" / "index.js"),
            },
            "install": {
                "sourcePath": str(plugin),
                "installPath": str(plugin),
            },
        }
    )


def test_plugin_status_rejects_a_loaded_stale_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    release = tmp_path / "releases" / "candidate"
    expected = release / "openclaw" / "administrateme"
    stale = tmp_path / "releases" / "stale" / "openclaw" / "administrateme"
    monkeypatch.setattr(
        "bootstrap.openclaw_tools.resolve_reviewed_openclaw",
        lambda _paths: "/reviewed/openclaw",
    )
    monkeypatch.setattr(
        "bootstrap.openclaw_tools.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["openclaw"],
            0,
            _inspection(stale),
            "",
        ),
    )

    status = administrateme_plugin_status(paths, release)

    assert status == {
        "ok": False,
        "discovered": True,
        "loaded": True,
        "release_root_valid": True,
        "release_path_current": False,
        "runtime_source_current": False,
    }
    assert expected != stale


@pytest.mark.parametrize("standing_supported", [False, True])
@pytest.mark.parametrize("permitted", [False, True])
@pytest.mark.parametrize("service_supported", [False, True])
def test_reconcile_replaces_stale_registration_and_proves_runtime_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    standing_supported: bool,
    permitted: bool,
    service_supported: bool,
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    release = tmp_path / "releases" / "candidate"
    plugin = release / "openclaw" / "administrateme"
    (plugin / "dist").mkdir(parents=True)
    (plugin / "dist" / "index.js").write_text("export default {};", encoding="utf-8")
    (plugin / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "contracts": {
                    "tools": list(SERVICE_PRIVATE_TOOLS) if service_supported else []
                },
                "configSchema": {
                    "type": "object",
                    "properties": {
                        "bindingFingerprints": {},
                        "dmScope": {},
                        "memberAgentIds": {},
                        "forwardIntakeAccounts": {},
                        "scheduledSelfReminderBindings": {},
                        **({"serviceWorkEnabled": {}} if service_supported else {}),
                        **(
                            {"standingSelfReplyBindings": {}}
                            if standing_supported
                            else {}
                        ),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    if service_supported:
        source = plugin / "workspace/service-private/AGENTS.md"
        source.parent.mkdir(parents=True)
        source.write_text(SERVICE_PRIVATE_INSTRUCTIONS)
    config_dir = paths.instance_dir / "config"
    runtime_dir = paths.instance_dir / "openclaw" / "runtime"
    config_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    actors = {
        "discord|account-a|actor-a": "member-a",
        "discord|account-b|actor-b": "member-b",
    }
    (config_dir / "adapters.json").write_text(
        json.dumps(
            {
                "openclaw_actor_identities": actors,
                "member_chat_enabled_members": ["member-a", "member-b"],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "openclaw.json").write_text(
        json.dumps(
            {
                "agents": {
                    "list": [
                        {"id": "operator", "default": True},
                        {"id": "adminme-stale0000000000"},
                    ]
                },
                "bindings": [
                    {"agentId": "operator", "match": {"channel": "cli"}},
                    {
                        "agentId": "adminme-stale0000000000",
                        "match": {"channel": "stale"},
                    },
                ],
                "plugins": {
                    "entries": {
                        "administrateme": {
                            "enabled": True,
                            "hooks": {"allowConversationAccess": permitted},
                            "config": {
                                **(
                                    {"serviceWorkEnabled": False}
                                    if service_supported
                                    else {}
                                ),
                                "forwardIntakeAccounts": [
                                    {"channelId": "imessage", "accountId": "fixture"}
                                ],
                                "standingSelfReplyBindings": ["f" * 64]
                                if permitted
                                else [],
                                "scheduledSelfReminderBindings": ["f" * 64]
                                if permitted
                                else [],
                            },
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "openclaw.json").chmod(0o600)
    monkeypatch.setattr(
        "bootstrap.openclaw_tools.resolve_reviewed_openclaw",
        lambda _paths: "/reviewed/openclaw",
    )
    commands: list[list[str]] = []
    inspect_count = 0

    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        commands.append(command)
        if command[1:4] == ["plugins", "inspect", "administrateme"]:
            inspect_count += 1
            if "--runtime" in command:
                return subprocess.CompletedProcess(command, 0, _inspection(plugin), "")
            if inspect_count == 1:
                return subprocess.CompletedProcess(command, 0, "{}", "")
            return subprocess.CompletedProcess(command, 1, "", "not installed")
        if command[1:3] in (["plugins", "uninstall"], ["plugins", "install"]):
            target = runtime_dir / "openclaw.json"
            current = json.loads(target.read_bytes())
            entries = current["plugins"]["entries"]
            entries.pop("administrateme", None)
            if command[2] == "install":
                entries["administrateme"] = {"enabled": True}
            atomic_write(target, json.dumps(current).encode())
        if command[1:3] == ["config", "validate"]:
            entry = json.loads((runtime_dir / "openclaw.json").read_bytes())["plugins"][
                "entries"
            ]["administrateme"]
            assert entry["hooks"] == {"allowConversationAccess": permitted}
            assert entry["config"]["forwardIntakeAccounts"] == [
                {"channelId": "imessage", "accountId": "fixture"}
            ]
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr("bootstrap.openclaw_tools.subprocess.run", run)

    status = reconcile_administrateme_plugin(paths, release)

    assert status["ok"] is True
    assert status["member_topology"] == {
        "member_agent_count": 2,
        "binding_count": 4,
        "dm_scope": "per_account_channel_peer",
        "content_exposed": False,
    }
    stored = json.loads((runtime_dir / "openclaw.json").read_text("utf-8"))
    assert stored["plugins"]["entries"]["administrateme"]["config"].get(
        "standingSelfReplyBindings", []
    ) == (["f" * 64] if standing_supported and permitted else [])
    entry = stored["plugins"]["entries"]["administrateme"]
    assert entry["enabled"] is True
    assert entry["hooks"] == {"allowConversationAccess": permitted}
    assert entry["config"]["scheduledSelfReminderBindings"] == (
        ["f" * 64] if permitted else []
    )
    expected_agents = {
        "operator",
        member_agent_id("member-a"),
        member_agent_id("member-b"),
    }
    if service_supported:
        expected_agents.add(service_agent_id(COS_SERVICE_PRINCIPAL_ID))
    assert {row["id"] for row in stored["agents"]["list"]} == expected_agents
    assert status["service_topology"]["service_agent_count"] == int(service_supported)
    if service_supported:
        assert entry["config"]["serviceWorkEnabled"] is False
    assert all(
        row["agentId"] != "adminme-stale0000000000" for row in stored["bindings"]
    )
    assert any(command[1:3] == ["plugins", "uninstall"] for command in commands)
    assert [
        "/reviewed/openclaw",
        "plugins",
        "install",
        "--link",
        "--force",
        str(plugin.resolve()),
    ] in commands
    assert ["/reviewed/openclaw", "config", "validate"] in commands
    if service_supported:
        manifest_path = plugin / "openclaw.plugin.json"
        older_manifest = json.loads(manifest_path.read_text())
        older_manifest["contracts"]["tools"] = []
        manifest_path.write_text(json.dumps(older_manifest))
        rolled_back = reconcile_administrateme_plugin(paths, release)
        assert rolled_back["service_topology"]["service_agent_count"] == 0
        restored = json.loads((runtime_dir / "openclaw.json").read_text())
        assert restored["plugins"]["entries"] == stored["plugins"]["entries"]
        assert all(
            not row["id"].startswith("am-service-")
            for row in restored["agents"]["list"]
        )


def test_reconcile_legacy_plugin_removes_only_unsupported_candidate_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    release = tmp_path / "releases" / "predecessor"
    plugin = release / "openclaw" / "administrateme"
    (plugin / "dist").mkdir(parents=True)
    (plugin / "dist" / "index.js").write_text("export default {};", encoding="utf-8")
    (plugin / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "configSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"baseUrl": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_dir = paths.instance_dir / "openclaw" / "runtime"
    runtime_dir.mkdir(parents=True)
    target = runtime_dir / "openclaw.json"
    target.write_text(
        json.dumps(
            {
                "agents": {
                    "list": [
                        {"id": "operator", "default": True},
                        {"id": member_agent_id("member-a")},
                    ]
                },
                "bindings": [
                    {"agentId": "operator", "match": {"channel": "cli"}},
                    {
                        "agentId": member_agent_id("member-a"),
                        "match": {"channel": "discord"},
                    },
                ],
                "session": {"dmScope": "per-account-channel-peer"},
                "plugins": {
                    "entries": {
                        "administrateme": {
                            "config": {
                                "baseUrl": "http://127.0.0.1:3330",
                                "bindingFingerprints": ["a" * 64],
                                "dmScope": "per_account_channel_peer",
                                "memberAgentIds": [member_agent_id("member-a")],
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    target.chmod(0o600)
    monkeypatch.setattr(
        "bootstrap.openclaw_tools.resolve_reviewed_openclaw",
        lambda _paths: "/reviewed/openclaw",
    )
    commands: list[list[str]] = []
    inspect_count = 0

    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        commands.append(command)
        if command[1:3] == ["plugins", "uninstall"]:
            preinstall = json.loads(target.read_text("utf-8"))
            assert preinstall["agents"]["list"] == [{"default": True, "id": "operator"}]
            assert preinstall["bindings"] == [
                {"agentId": "operator", "match": {"channel": "cli"}}
            ]
            assert preinstall["plugins"]["entries"]["administrateme"]["config"] == {
                "baseUrl": "http://127.0.0.1:3330"
            }
            preinstall["plugins"]["entries"].pop("administrateme")
            atomic_write(target, json.dumps(preinstall).encode())
        if command[1:3] == ["plugins", "install"]:
            current = json.loads(target.read_bytes())
            current["plugins"]["entries"]["administrateme"] = {"enabled": True}
            atomic_write(target, json.dumps(current).encode())
        if command[1:4] == ["plugins", "inspect", "administrateme"]:
            inspect_count += 1
            if "--runtime" in command:
                return subprocess.CompletedProcess(command, 0, _inspection(plugin), "")
            if inspect_count == 1:
                return subprocess.CompletedProcess(command, 0, "{}", "")
            return subprocess.CompletedProcess(command, 1, "", "not installed")
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr("bootstrap.openclaw_tools.subprocess.run", run)

    status = reconcile_administrateme_plugin(paths, release)

    assert status["member_topology"] == {
        "member_agent_count": 0,
        "binding_count": 0,
        "dm_scope": "legacy_plugin_unmanaged",
        "content_exposed": False,
    }
    stored = json.loads(target.read_text("utf-8"))
    assert stored["agents"]["list"] == [{"default": True, "id": "operator"}]
    assert stored["bindings"] == [{"agentId": "operator", "match": {"channel": "cli"}}]
    assert stored["plugins"]["entries"]["administrateme"] == {
        "config": {"baseUrl": "http://127.0.0.1:3330"}
    }
    assert stored["session"]["dmScope"] == "per-account-channel-peer"
    assert ["/reviewed/openclaw", "config", "validate"] in commands


def test_legacy_cleanup_rejects_an_explicitly_invalid_plugin_config(
    tmp_path: Path,
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    runtime_dir = paths.instance_dir / "openclaw" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "openclaw.json").write_text(
        json.dumps(
            {
                "agents": {"list": []},
                "bindings": [],
                "plugins": {
                    "entries": {"administrateme": {"enabled": True, "config": None}}
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="plugin configuration is invalid"):
        remove_unsupported_member_topology(paths)


def test_reconcile_rejects_a_plugin_path_outside_the_release(tmp_path: Path) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    release = tmp_path / "releases" / "candidate"
    plugin = release / "openclaw" / "administrateme"
    outside = tmp_path / "outside-plugin"
    outside.mkdir()
    plugin.parent.mkdir(parents=True)
    plugin.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="escapes"):
        reconcile_administrateme_plugin(paths, release)


def test_reconcile_timeout_restores_exact_private_openclaw_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    release = tmp_path / "releases" / "candidate"
    plugin = release / "openclaw" / "administrateme"
    (plugin / "dist").mkdir(parents=True)
    (plugin / "dist" / "index.js").write_text("export default {};", encoding="utf-8")
    (plugin / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "configSchema": {
                    "type": "object",
                    "properties": {
                        "bindingFingerprints": {},
                        "dmScope": {},
                        "memberAgentIds": {},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    config_dir = paths.instance_dir / "config"
    runtime_dir = paths.instance_dir / "openclaw" / "runtime"
    config_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (config_dir / "adapters.json").write_text(
        '{"member_chat_enabled_members":[],"openclaw_actor_identities":{}}',
        encoding="utf-8",
    )
    target = runtime_dir / "openclaw.json"
    original = b'{"plugins":{"entries":{"administrateme":{"enabled":true}}}}\n'
    target.write_bytes(original)
    target.chmod(0o600)
    monkeypatch.setattr(
        "bootstrap.openclaw_tools.resolve_reviewed_openclaw",
        lambda _paths: "/reviewed/openclaw",
    )
    inspect_count = 0
    observed_timeouts: list[int] = []

    def run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_count
        observed_timeouts.append(int(kwargs["timeout"]))
        if command[1:4] == ["plugins", "inspect", "administrateme"]:
            inspect_count += 1
            return subprocess.CompletedProcess(
                command,
                0 if inspect_count == 1 else 1,
                "{}" if inspect_count == 1 else "",
                "",
            )
        if command[1:3] == ["plugins", "uninstall"]:
            target.write_bytes(b'{"plugins":{"entries":{}}}\n')
            target.chmod(0o600)
            return subprocess.CompletedProcess(command, 0, "{}", "")
        if command[1:3] == ["plugins", "install"]:
            raise subprocess.TimeoutExpired(
                command,
                OPENCLAW_PLUGIN_COMMAND_TIMEOUT_SECONDS,
            )
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr("bootstrap.openclaw_tools.subprocess.run", run)

    with pytest.raises(RuntimeError, match="plugin command timed out"):
        reconcile_administrateme_plugin(paths, release)

    assert target.read_bytes() == original
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert observed_timeouts
    assert set(observed_timeouts) == {OPENCLAW_PLUGIN_COMMAND_TIMEOUT_SECONDS}


def _destructive_plugin_cli(
    paths: DeploymentPaths,
    release: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    validate: Callable[[dict[str, Any]], None] | None = None,
    failure: str | None = None,
) -> list[list[str]]:
    """Model native registration: uninstall removes the entry, install defaults it."""
    root = Path(__file__).resolve().parents[3]
    plugin = release / "openclaw/administrateme"
    atomic_write(plugin / "dist/index.js", b"export default {};")
    for relative in (
        "openclaw.plugin.json",
        "bin/cos-imsg-guard.mjs",
        "workspace/service-private/AGENTS.md",
    ):
        atomic_write(
            plugin / relative,
            (root / "openclaw/administrateme" / relative).read_bytes(),
        )
    config_path = paths.instance_dir / "openclaw/runtime/openclaw.json"
    installed = True
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal installed
        commands.append(command)
        config = json.loads(config_path.read_bytes())
        entries = config["plugins"]["entries"]
        output = "{}"
        result = 0
        if command[1:3] == ["plugins", "uninstall"]:
            entries.pop("administrateme", None)
            atomic_write(config_path, json.dumps(config).encode())
            installed = False
        elif command[1:3] == ["plugins", "install"]:
            entries["administrateme"] = {"enabled": True}
            config["plugins"]["installs"] = {
                "administrateme": {
                    "sourcePath": str(plugin),
                    "installPath": str(plugin),
                }
            }
            atomic_write(config_path, json.dumps(config).encode())
            installed = True
            result = int(failure == "install")
        elif command[1:3] == ["plugins", "inspect"]:
            result = 0 if installed else 1
            output = _inspection(plugin)
            if (
                "--runtime" in command
                and entries["administrateme"].get("enabled") is False
            ):
                document = json.loads(output)
                document["plugin"].update(status="disabled", activated=False)
                output = json.dumps(document)
            if "--runtime" in command and failure == "runtime":
                result = 1
        elif command[1:3] == ["config", "validate"]:
            jsonschema.validate(
                entries["administrateme"].get("config", {}),
                json.loads((plugin / "openclaw.plugin.json").read_bytes())[
                    "configSchema"
                ],
            )
            if validate is not None:
                validate(config)
            result = int(failure == "validate")
        return subprocess.CompletedProcess(command, result, output, "")

    monkeypatch.setattr(
        "bootstrap.openclaw_tools.resolve_reviewed_openclaw",
        lambda _paths: "/reviewed/openclaw",
    )
    monkeypatch.setattr("bootstrap.openclaw_tools.subprocess.run", run)
    return commands


@pytest.mark.parametrize("failure", ["install", "validate", "runtime", "disabled"])
def test_destructive_reconciliation_failure_restores_exact_permissions_and_denials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    release = tmp_path / "candidate"
    config_path = paths.instance_dir / "openclaw/runtime/openclaw.json"
    original = (
        json.dumps(
            {
                "plugins": {
                    "entries": {
                        "administrateme": {
                            "enabled": failure != "disabled",
                            "hooks": {"allowConversationAccess": False},
                            "config": {
                                "forwardIntakeAccounts": [],
                                "standingSelfReplyBindings": [],
                                "scheduledSelfReminderBindings": [],
                            },
                        }
                    }
                },
                "unrelated": {"retained": True},
            },
            indent=4,
        ).encode()
        + b"\n"
    )
    atomic_write(config_path, original)
    atomic_write(paths.instance_dir / "config/adapters.json", b"{}")

    def validate(config: dict[str, Any]) -> None:
        entry = config["plugins"]["entries"]["administrateme"]
        assert entry["enabled"] is (failure != "disabled")
        assert entry["hooks"] == {"allowConversationAccess": False}
        assert entry["config"]["forwardIntakeAccounts"] == []
        assert entry["config"]["standingSelfReplyBindings"] == []
        assert entry["config"]["scheduledSelfReminderBindings"] == []

    _destructive_plugin_cli(
        paths, release, monkeypatch, validate=validate, failure=failure
    )
    with pytest.raises(RuntimeError):
        reconcile_administrateme_plugin(paths, release)
    assert config_path.read_bytes() == original
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
