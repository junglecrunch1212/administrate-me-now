"""Opt-in hosted macOS proof using launchd and an inert synthetic daemon.

This creates a unique GUI-domain job and a disposable Python environment. It
loads the sealed component with runpy, as the real helper does. It does not load
Apple frameworks, contact the household runtime, or use any member identity,
receipt, credentials, provider, or enrollment. Once opted in, absence of a GUI
domain is a failed release prerequisite, never a skipped passing check.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
import uuid
from typing import Any

import pytest
import yaml

from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths
from bootstrap.member_bridge_helper_app import build_member_bridge_helper_app


ROOT = Path(__file__).resolve().parents[2]
NOTES = "knowledge-apple-notes"
pytestmark = pytest.mark.skipif(
    os.environ.get("ADMINME_RUN_BRIDGE_LAUNCH_RUNTIME") != "1",
    reason="real launchd requires the explicit isolated hosted macOS job",
)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=60, check=True)


@pytest.fixture(scope="module")
def sealed_component(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    assert os.environ.get("GITHUB_ACTIONS") == "true", "isolated hosted CI only"
    assert sys.platform == "darwin", "real macOS launchd required"
    assert os.uname().machine == "arm64", "affected Apple silicon toolchain required"
    assert sys.version_info[:2] == (3, 14)
    # This may fail on an unsuitable runner. The opted-in release job cannot
    # replace the real GUI launchd boundary with a simulation or a skip.
    _run(["/bin/launchctl", "print", f"gui/{os.getuid()}"])
    commit = _run(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    app = (
        tmp_path_factory.mktemp("sealed launch component") / "Fixture Bridge Helper.app"
    )
    build_member_bridge_helper_app(
        app,
        root=ROOT,
        expected_commit=commit,
    )
    _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    manifest = json.loads(
        (app / "Contents/Resources/helper-manifest.json").read_bytes()
    )
    source = app / manifest["launch_controller_path"]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert digest == manifest["launch_controller_sha256"]
    assert (
        source.read_bytes() == (ROOT / "bootstrap/member_bridge_launch.py").read_bytes()
    )
    return source, digest


@pytest.mark.parametrize("initial_state", ["absent", "running"])
def test_sealed_launch_component_real_gui_start_restart_and_stop(
    tmp_path: Path,
    sealed_component: tuple[Path, str],
    initial_state: str,
) -> None:
    source, digest = sealed_component
    component = runpy.run_path(str(source))
    # Only the isolated test's fresh namespace changes its service label. The
    # sealed production file has no label/environment override or fixture mode.
    globals_ = component["compatible_installer_class"].__globals__
    label = "com.administrateme.test-bridge-" + uuid.uuid4().hex
    globals_["_LABEL"] = label
    workspace = tmp_path / "inert runtime"
    workspace.mkdir(mode=0o700)
    _run([sys.executable, "-m", "venv", "--without-pip", str(workspace / ".venv")])
    python = workspace / ".venv/bin/python"
    purelib = Path(
        _run(
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ]
        ).stdout.strip()
    )
    package = purelib / "adminme_bridge"
    package.mkdir(mode=0o700)
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text(
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "print('inert bridge fixture pid=' + str(os.getpid()), flush=True)\n"
        "while True: time.sleep(1)\n"
    )
    root = tmp_path / "inert member"
    paths = MemberBridgePaths.from_root(root)
    # Only the exact disposable job is manipulated. The ordinary member label
    # and ~/Library/LaunchAgents are never used by this fixture.
    paths = MemberBridgePaths(
        root=paths.root,
        config_path=paths.config_path,
        receipt_path=paths.receipt_path,
        log_dir=paths.log_dir,
        launch_agent_path=paths.launch_agent_path.parent / f"{label}.plist",
        service_state_path=paths.service_state_path,
        device_proof_key_path=paths.device_proof_key_path,
    )
    paths.create()
    config = {
        "requested_packs": [NOTES],
        "configured_packs": [NOTES],
        "enabled_packs": [],
        "pack_config": {NOTES: {"source": "inert-fixture"}},
        "provider_writes": "deny",
        "observation_only": True,
    }
    paths.config_path.write_text(yaml.safe_dump(config))
    paths.config_path.chmod(0o600)
    installer = component["compatible_installer_class"](MemberBridgeInstaller)(
        paths, root=workspace
    )
    paths.launch_agent_path.write_bytes(
        plistlib.dumps(globals_["_expected_plist"](installer))
    )
    paths.launch_agent_path.chmod(0o600)
    target = f"gui/{os.getuid()}/{label}"
    first_pid: int | None = None
    final_pid: int | None = None
    try:
        installer.verify_initial_start_recovery([NOTES])
        if initial_state == "running":
            installer._start()
            first = component["launch_probe"](installer)
            assert first.present and first.running and first.pid is not None
            first_pid = first.pid
        result = installer.enable_pack(NOTES)
        final = component["launch_probe"](installer)
        assert final.present and final.running and final.pid is not None
        final_pid = final.pid
        assert first_pid is None or final_pid != first_pid
        assert result["central_provider_authority"] is False
        assert yaml.safe_load(paths.config_path.read_bytes())["enabled_packs"] == [
            NOTES
        ]
        installer._stop()
        assert not component["launch_probe"](installer).present
        assert not component["_daemon_pids"]()
        assert not paths.receipt_path.exists()
        assert not component["_pending_path"](installer).exists()
    finally:
        # If the test failed mid-start, remove only its unique inert job and
        # fail if launchd still reports it. Never clean the real member label.
        subprocess.run(
            ["/bin/launchctl", "bootout", target],
            capture_output=True,
            check=False,
            timeout=15,
        )
        absent = subprocess.run(
            ["/bin/launchctl", "print", target],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert absent.returncode == 113 and "Could not find service" in absent.stderr
    artifact_dir = Path(os.environ["ADMINME_BRIDGE_LAUNCH_ARTIFACT_DIR"])
    assert artifact_dir.is_absolute()
    artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "contract": "member-bridge-inert-launch-runtime-v1",
        "result": "passed",
        "initial_state": initial_state,
        "component_sha256": digest,
        "toolchain": sys.version.split()[0],
        "platform": "macOS-arm64",
        "sealed_component": True,
        "runpy_load": True,
        "real_gui_launchd": True,
        "stable_process": final_pid is not None,
        "prior_process_replaced": first_pid is None or first_pid != final_pid,
        "cleanup_absent": True,
        "provider_imports": False,
        "provider_effects": False,
        "member_credentials": False,
    }
    output = artifact_dir / f"launch-{initial_state}.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    output.chmod(0o600)
