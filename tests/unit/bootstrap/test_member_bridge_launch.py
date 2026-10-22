"""Inert launchctl/process simulations; never run provider code or launchd."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths
from bootstrap import member_bridge_launch as launch


NOTES = "knowledge-apple-notes"
CALENDAR = "calendaring-apple-calendar-bridge"
REMINDERS = "reminders-apple-eventkit-bridge"
SELECTED = [CALENDAR, REMINDERS, NOTES]


class FakeLaunchd:
    def __init__(self, installer: Any) -> None:
        self.installer = installer
        self.calls: list[list[str]] = []
        self.loaded = False
        self.pid: int | None = None
        self.runs: int | None = 1
        self.bootstrap_configs: list[dict[str, Any]] = []
        self.bootstrap_results: list[str] = []
        self.unload_ticks = 0
        self.unload_delay = 0
        self.stuck = False
        self.orphan = False
        self.disabled = False
        self.domain_exists = True
        self.print_code = 0
        self.foreign = False
        self.kickstart_fails = False
        self.start_without_pid = False
        self.clock = 0.0
        self.command_unknown: str | None = None
        self.post_bootstrap_print_unknown = False

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def _output(self) -> str:
        expected = launch._expected_plist(self.installer)
        args = "\n".join("\t\t" + arg for arg in expected["ProgramArguments"])
        pid = f"\tpid = {self.pid}\n" if self.pid is not None else ""
        runs = f"\truns = {self.runs}\n" if self.runs is not None else ""
        path = (
            "/unrelated.plist"
            if self.foreign
            else str(self.installer.paths.launch_agent_path)
        )
        return (
            f"{launch._target()} = {{\n"
            f"\tpath = {path}\n"
            f"\tprogram = {expected['ProgramArguments'][0]}\n"
            f"\targuments = {{\n{args}\n\t}}\n"
            f"\tstdout path = {expected['StandardOutPath']}\n"
            f"\tstderr path = {expected['StandardErrorPath']}\n"
            f"\tenvironment = {{\n\t\tADMINME_BRIDGE_CONFIG => {self.installer.paths.config_path}\n\t}}\n"
            f"\tstate = {'running' if self.pid else 'waiting'}\n{pid}{runs}}}\n"
        )

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        stdout, stderr, status = "", "", 0
        action = args[1]
        if args[0] == "/bin/ps":
            pids = ({self.pid} if self.pid is not None else set()) | (
                {900} if self.orphan else set()
            )
            stdout = "\n".join(
                f"{pid} {os.getuid()} {self.installer.root}/.venv/bin/python -m adminme_bridge.main"
                for pid in sorted(pids)
            )
        elif action == "print-disabled":
            stdout = f'{{\n "{launch._LABEL}" => {str(self.disabled).lower()}\n}}\n'
        elif action == "print" and args[2] == launch._domain():
            status = 0 if self.domain_exists else 113
        elif action == "print":
            if self.unload_ticks and not self.stuck:
                self.unload_ticks -= 1
                if not self.unload_ticks:
                    self.loaded, self.pid = False, None
            if self.print_code:
                status, stderr = self.print_code, "private diagnostic never exposed"
            elif self.loaded:
                if self.post_bootstrap_print_unknown and self.bootstrap_configs:
                    self.post_bootstrap_print_unknown = False
                    raise launch.LaunchFailure("launch_state_unknown", unknown=True)
                stdout = self._output()
            else:
                status, stderr = 113, "Could not find service"
        elif action == "bootout":
            if self.stuck:
                status = 5
            elif self.unload_delay:
                self.unload_ticks = self.unload_delay
            else:
                self.loaded, self.pid = False, None
        elif action == "bootstrap":
            assert not self.loaded, "bootstrap must wait for confirmed unload"
            self.bootstrap_configs.append(
                yaml.safe_load(self.installer.paths.config_path.read_bytes())
            )
            outcome = self.bootstrap_results.pop(0) if self.bootstrap_results else "ok"
            self.loaded = outcome != "absent-failure"
            self.pid = (
                None
                if self.start_without_pid or not self.loaded
                else 100 + len(self.bootstrap_configs)
            )
            status = 5 if outcome != "ok" else 0
            if outcome == "unknown-child":
                self.stuck = True
        elif action == "kickstart":
            assert "-k" not in args, "a kickstart must not restart an uncertain process"
            if self.kickstart_fails:
                status = 5
            else:
                self.pid = 100 + len(self.bootstrap_configs)
                self.runs = 1
        else:
            raise AssertionError(args)
        if action == self.command_unknown:
            self.command_unknown = None
            raise launch.LaunchFailure("launch_state_unknown", unknown=True)
        return subprocess.CompletedProcess(args, status, stdout, stderr)

    def actions(self, name: str) -> list[list[str]]:
        return [args for args in self.calls if args[1] == name]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeLaunchd]:
    paths = MemberBridgePaths.from_root(tmp_path / "member")
    paths.create()
    runtime = tmp_path / "retained runtime"
    python = runtime / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    config = {
        "requested_packs": SELECTED,
        "configured_packs": [NOTES],
        "enabled_packs": [],
        "pack_config": {NOTES: {"source": "fixture"}},
        "provider_writes": "deny",
        "observation_only": True,
    }
    paths.config_path.write_text(yaml.safe_dump(config))
    paths.config_path.chmod(0o600)
    installer = launch.compatible_installer_class(MemberBridgeInstaller)(
        paths, root=runtime
    )
    paths.launch_agent_path.write_bytes(
        plistlib.dumps(launch._expected_plist(installer))
    )
    paths.launch_agent_path.chmod(0o600)
    fake = FakeLaunchd(installer)
    monkeypatch.setattr(launch, "_command", fake)
    monkeypatch.setattr(launch.time, "monotonic", lambda: fake.clock)
    monkeypatch.setattr(launch.time, "sleep", fake.sleep)
    return installer, fake


def test_initial_recovery_is_read_only_and_allows_unconfigured_requested_sources(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    before = installer.paths.config_path.read_bytes()
    installer.verify_initial_start_recovery(SELECTED)
    assert installer.paths.config_path.read_bytes() == before
    assert not launch._pending_path(installer).exists()
    assert {call[1] for call in fake.calls} <= {"print", "print-disabled", "-Aww"}


@pytest.mark.parametrize(
    "condition", ["enabled", "loaded", "orphan", "disabled", "unrequested"]
)
def test_initial_recovery_requires_exact_quiescent_boundary(
    rig: tuple[Any, FakeLaunchd], condition: str
) -> None:
    installer, fake = rig
    selected = list(SELECTED)
    if condition == "enabled":
        config = yaml.safe_load(installer.paths.config_path.read_bytes())
        config["enabled_packs"] = [NOTES]
        installer.paths.config_path.write_text(yaml.safe_dump(config))
    elif condition == "loaded":
        fake.loaded = True
    elif condition == "orphan":
        fake.orphan = True
    elif condition == "disabled":
        fake.disabled = True
    else:
        selected.append("unrequested-fixture-pack")
    with pytest.raises(launch.LaunchFailure, match="launch_not_quiescent"):
        installer.verify_initial_start_recovery(selected)
    assert not fake.actions("bootout") and not fake.actions("bootstrap")


@pytest.mark.parametrize("condition", ["missing-domain", "unknown-print", "pending"])
def test_unknown_initial_state_cannot_trigger_retry(
    rig: tuple[Any, FakeLaunchd], condition: str
) -> None:
    installer, fake = rig
    if condition == "missing-domain":
        fake.domain_exists = False
    elif condition == "unknown-print":
        fake.print_code = 5
    else:
        launch._pending_path(installer).write_text('{"code":"launch_start_unknown"}')
    with pytest.raises(launch.LaunchFailure) as error:
        installer.verify_initial_start_recovery(SELECTED)
    assert error.value.unknown
    assert "private diagnostic" not in str(error.value)
    assert not fake.actions("bootout") and not fake.actions("bootstrap")


@pytest.mark.parametrize(
    "field",
    ["arguments", "configuration", "logs", "keepalive", "symlink", "foreign-job"],
)
def test_owned_definition_mismatch_has_no_lifecycle_effects(
    rig: tuple[Any, FakeLaunchd], field: str, tmp_path: Path
) -> None:
    installer, fake = rig
    plist = plistlib.loads(installer.paths.launch_agent_path.read_bytes())
    if field == "arguments":
        plist["ProgramArguments"][-1] = "unrelated.main"
    elif field == "configuration":
        plist["EnvironmentVariables"]["ADMINME_BRIDGE_CONFIG"] = "/unrelated.yaml"
    elif field == "logs":
        plist["StandardErrorPath"] = "/unrelated.log"
    elif field == "keepalive":
        plist["KeepAlive"] = True
    elif field == "foreign-job":
        fake.loaded, fake.pid, fake.foreign = True, 99, True
    installer.paths.launch_agent_path.write_bytes(plistlib.dumps(plist))
    if field == "symlink":
        real = tmp_path / "other.plist"
        installer.paths.launch_agent_path.rename(real)
        installer.paths.launch_agent_path.symlink_to(real)
    with pytest.raises(launch.LaunchFailure, match="launch_binding_mismatch"):
        installer.enable_pack(NOTES)
    assert not fake.actions("bootout") and not fake.actions("bootstrap")


def test_missing_job_starts_once_without_redundant_bootout(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    result = installer.enable_pack(NOTES)
    assert result["central_provider_authority"] is False
    assert yaml.safe_load(installer.paths.config_path.read_bytes())[
        "enabled_packs"
    ] == [NOTES]
    assert len(fake.actions("bootstrap")) == 1
    assert not fake.actions("bootout") and not fake.actions("kickstart")
    assert fake.loaded and fake.pid is not None


def test_delayed_unload_settles_before_one_bootstrap(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    fake.loaded, fake.pid, fake.unload_delay = True, 99, 4
    installer.enable_pack(NOTES)
    assert len(fake.actions("bootout")) == 1
    assert len(fake.actions("bootstrap")) == 1
    assert fake.clock >= 0.6


@pytest.mark.parametrize("bootstrap_outcome", ["absent-failure", "loaded-failure"])
def test_failed_bootstrap_stops_child_then_restores_exact_configuration(
    rig: tuple[Any, FakeLaunchd], bootstrap_outcome: str
) -> None:
    installer, fake = rig
    raw = installer.paths.config_path.read_bytes()
    fake.bootstrap_results = [bootstrap_outcome]
    with pytest.raises(launch.LaunchFailure, match="launch_start_failed") as error:
        installer.enable_pack(NOTES)
    assert not error.value.unknown
    assert installer.paths.config_path.read_bytes() == raw
    assert not fake.loaded and fake.pid is None
    assert not launch._pending_path(installer).exists()
    assert len(fake.actions("bootstrap")) == 1


def test_failed_new_start_restores_previously_running_siblings(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    raw = installer.paths.config_path.read_bytes()
    fake.loaded, fake.pid = True, 99
    fake.bootstrap_results = ["loaded-failure", "ok"]
    with pytest.raises(launch.LaunchFailure, match="launch_start_failed"):
        installer.enable_pack(NOTES)
    assert installer.paths.config_path.read_bytes() == raw
    assert fake.bootstrap_configs[0]["enabled_packs"] == [NOTES]
    assert fake.bootstrap_configs[1]["enabled_packs"] == []
    assert fake.loaded and fake.pid is not None


def test_failed_sibling_restoration_is_not_hidden(rig: tuple[Any, FakeLaunchd]) -> None:
    installer, fake = rig
    fake.loaded, fake.pid = True, 99
    fake.bootstrap_results = ["absent-failure", "absent-failure"]
    with pytest.raises(launch.LaunchFailure, match="launch_rollback_failed") as error:
        installer.enable_pack(NOTES)
    assert error.value.unknown and launch._pending_path(installer).is_file()
    assert not fake.loaded


def test_unknown_cleanup_preserves_current_config_and_blocks_new_process(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    fake.bootstrap_results = ["unknown-child"]
    with pytest.raises(launch.LaunchFailure, match="launch_start_unknown") as error:
        installer.enable_pack(NOTES)
    assert error.value.unknown
    assert yaml.safe_load(installer.paths.config_path.read_bytes())[
        "enabled_packs"
    ] == [NOTES]
    assert fake.loaded and len(fake.actions("bootstrap")) == 1
    pending = json.loads(launch._pending_path(installer).read_bytes())
    assert pending == {
        "contract": "member-bridge-launch-pending-v1",
        "code": "launch_start_unknown",
    }
    # A new instance/process sees the durable unknown, not merely an in-memory flag.
    fresh = launch.compatible_installer_class(MemberBridgeInstaller)(
        installer.paths, root=installer.root
    )
    with pytest.raises(launch.LaunchFailure, match="launch_recovery_pending"):
        fresh.enable_pack(NOTES)
    assert len(fake.actions("bootstrap")) == 1


def test_failed_unload_never_changes_config_or_bootstraps(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    raw = installer.paths.config_path.read_bytes()
    fake.loaded, fake.pid, fake.stuck = True, 99, True
    with pytest.raises(launch.LaunchFailure, match="launch_stop_unknown"):
        installer.enable_pack(NOTES)
    assert installer.paths.config_path.read_bytes() == raw
    assert not fake.actions("bootstrap")
    assert len(fake.actions("bootout")) == 1


@pytest.mark.parametrize("fails", [False, True])
def test_single_kickstart_only_for_job_that_has_never_run(
    rig: tuple[Any, FakeLaunchd], fails: bool
) -> None:
    installer, fake = rig
    fake.start_without_pid, fake.runs, fake.kickstart_fails = True, 0, fails
    if fails:
        with pytest.raises(launch.LaunchFailure, match="launch_start_failed"):
            installer.enable_pack(NOTES)
        assert not fake.loaded
    else:
        installer.enable_pack(NOTES)
        assert fake.loaded and fake.pid is not None
    assert len(fake.actions("kickstart")) == 1


@pytest.mark.parametrize("runs", [None, 1, 2])
def test_no_kickstart_after_possible_prior_execution(
    rig: tuple[Any, FakeLaunchd], runs: int | None
) -> None:
    installer, fake = rig
    fake.start_without_pid, fake.runs = True, runs
    with pytest.raises(launch.LaunchFailure, match="launch_start_failed"):
        installer.enable_pack(NOTES)
    assert not fake.actions("kickstart")
    assert not fake.loaded


def test_existing_enabled_pack_preserves_idempotent_contract(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    installer.enable_pack(NOTES)
    count = len(fake.calls)
    assert installer.enable_pack(NOTES)["idempotent"] is True
    assert len(fake.calls) == count


def test_composition_test_mode_uses_retained_synthetic_service(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    installer.test_mode = True
    installer.verify_initial_start_recovery(SELECTED)
    installer.enable_pack(NOTES)
    assert installer.status()["service"] == "running"
    assert not fake.calls


@pytest.mark.parametrize("action", ["bootstrap", "kickstart", "bootout"])
def test_uncertain_command_is_not_resolved_by_absent_readback(
    rig: tuple[Any, FakeLaunchd], action: str
) -> None:
    installer, fake = rig
    raw = installer.paths.config_path.read_bytes()
    fake.command_unknown = action
    if action == "bootout":
        fake.loaded, fake.pid = True, 99
    elif action == "kickstart":
        fake.start_without_pid, fake.runs = True, 0
    with pytest.raises(launch.LaunchFailure) as error:
        installer.enable_pack(NOTES)
    assert error.value.unknown
    assert launch._pending_path(installer).is_file()
    assert not fake.loaded
    if action == "bootout":
        assert installer.paths.config_path.read_bytes() == raw
        assert not fake.actions("bootstrap")
    else:
        assert yaml.safe_load(installer.paths.config_path.read_bytes())[
            "enabled_packs"
        ] == [NOTES]
        assert len(fake.actions("bootstrap")) == 1
    with pytest.raises(launch.LaunchFailure, match="launch_recovery_pending"):
        installer.enable_pack(NOTES)


@pytest.mark.parametrize("target", ["config", "plist", "root", "log-dir", "lock"])
def test_nonprivate_owned_boundary_fails_before_commands(
    rig: tuple[Any, FakeLaunchd], target: str
) -> None:
    installer, fake = rig
    path = {
        "config": installer.paths.config_path,
        "plist": installer.paths.launch_agent_path,
        "root": installer.paths.root,
        "log-dir": installer.paths.log_dir,
        "lock": installer.paths.root / "launch-lifecycle.lock",
    }[target]
    if target == "lock":
        path.touch()
    path.chmod(0o755 if path.is_dir() else 0o644)
    with pytest.raises(launch.LaunchFailure, match="launch_binding_mismatch"):
        installer.enable_pack(NOTES)
    assert not fake.calls


def test_post_start_readback_unknown_remains_unknown_after_cleanup(
    rig: tuple[Any, FakeLaunchd],
) -> None:
    installer, fake = rig
    fake.post_bootstrap_print_unknown = True
    with pytest.raises(launch.LaunchFailure, match="launch_start_unknown") as error:
        installer.enable_pack(NOTES)
    assert error.value.unknown
    assert not fake.loaded
    assert yaml.safe_load(installer.paths.config_path.read_bytes())[
        "enabled_packs"
    ] == [NOTES]
    assert launch._pending_path(installer).is_file()
    assert len(fake.actions("bootstrap")) == 1
