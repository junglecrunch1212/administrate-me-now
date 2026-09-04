"""Exact-release native OpenClaw plugin registration evidence."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from adminme.openclaw_topology import member_agent_id
from bootstrap.contracts import DeploymentPaths
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


def test_reconcile_replaces_stale_registration_and_proves_runtime_source(
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
                "plugins": {"entries": {"administrateme": {"config": {}}}},
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
    assert {row["id"] for row in stored["agents"]["list"]} == {
        "operator",
        member_agent_id("member-a"),
        member_agent_id("member-b"),
    }
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
        if command[1:3] == ["plugins", "install"]:
            preinstall = json.loads(target.read_text("utf-8"))
            assert preinstall["agents"]["list"] == [{"default": True, "id": "operator"}]
            assert preinstall["bindings"] == [
                {"agentId": "operator", "match": {"channel": "cli"}}
            ]
            assert preinstall["plugins"]["entries"]["administrateme"]["config"] == {
                "baseUrl": "http://127.0.0.1:3330"
            }
            preinstall["plugins"]["entries"]["administrateme"] = {"enabled": True}
            target.write_text(json.dumps(preinstall), encoding="utf-8")
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
    assert stored["plugins"]["entries"]["administrateme"] == {"enabled": True}
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
    (plugin / "dist" / "index.js").write_text(
        "export default {};", encoding="utf-8"
    )
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
