"""Exact-release native OpenClaw plugin registration evidence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bootstrap.contracts import DeploymentPaths
from bootstrap.openclaw_tools import (
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
    (plugin / "openclaw.plugin.json").write_text("{}", encoding="utf-8")
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
