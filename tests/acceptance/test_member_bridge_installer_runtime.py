"""Hosted-only proof of the member's real, Git-free dependency installation.

The ordinary suite collects but skips this network/native-toolchain check. Its
explicit macOS job must opt in and reject skipped results. Host enrollment
prerequisites are synthetic; Python, Poetry, locked dependencies, the archived
installer and the production preview process are real. No Apple data, central
runtime, Keychain, launchd or member identity is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from adminme.bridge_enrollment import BridgeEnrollmentCoordinator
from bootstrap.source_identity import write_source_marker


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("ADMINME_RUN_BRIDGE_INSTALLER_RUNTIME") != "1",
    reason="real bridge installation requires its explicit isolated hosted job",
)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log: Path,
    diagnostic: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1200,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        log.write_text(output, encoding="utf-8")
        log.chmod(0o600)
        pytest.fail(f"{log.name}: installer stage timed out\n{output[-16000:]}")
    output = result.stdout + result.stderr
    log.write_text(output, encoding="utf-8")
    log.chmod(0o600)
    failure_detail = (
        diagnostic.read_text("utf-8", errors="replace")[-16000:]
        if result.returncode and diagnostic is not None and diagnostic.exists()
        else ""
    )
    assert result.returncode == 0, (
        f"{log.name}: exit {result.returncode}\n{output[-16000:]}\n{failure_detail}"
    )
    return result


def _shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\nset -eu\n" + body + "\n", encoding="utf-8")
    path.chmod(0o700)


def _handoff(source: Path, directory: Path, commit: str) -> tuple[Path, Path]:
    # Generate the signed package with the production coordinator, not a second
    # test implementation of its encryption/signature protocol.
    member = "member-installer-fixture"
    coordinator = BridgeEnrollmentCoordinator(
        instance_dir=directory / "synthetic central",
        encryption_key=b"i" * 32,
        repository_root=source,
        deployment_release_commit=commit,
        require_deployment_release=True,
        tenant_id="installer-fixture",
        member_identities={"installer-fixture.ts.net": member},
        member_roles={member: "principal"},
        bridge_enrollments={},
    )
    coordinator.prepare(
        request_id="installer-runtime-fixture",
        member_id=member,
        device_class="this_mac",
        capabilities=["apple_calendar", "apple_reminders", "apple_notes"],
        central_url="https://installer-central-fixture.ts.net",
    )
    payload, code = coordinator.download(
        request_id="installer-runtime-fixture", member_id=member
    )
    package = directory / "synthetic handoff.adminmebridge"
    package.write_bytes(payload)
    package.chmod(0o600)
    code_file = directory / "synthetic transfer code"
    code_file.write_text(code, encoding="utf-8")
    code_file.chmod(0o600)
    return package, code_file


def test_real_clean_and_interrupted_member_install_preview(tmp_path: Path) -> None:
    assert os.environ.get("GITHUB_ACTIONS") == "true", "hosted CI only"
    assert sys.platform == "darwin", "native macOS dependency build required"
    assert os.uname().machine == "arm64", "native Apple silicon required"
    python = Path(os.environ["ADMINME_BRIDGE_INSTALLER_PYTHON"]).resolve()
    assert python.is_file()
    # This interpreter is selected by the explicit CI job, independently of the
    # existing development environment that executes pytest.
    version = subprocess.run(
        [str(python), "-I", "-c", "import sys; print(sys.version.split()[0])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert version == "3.14.8"
    artifact_dir = Path(os.environ["ADMINME_BRIDGE_INSTALLER_ARTIFACT_DIR"])
    assert artifact_dir.is_absolute()
    artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace = tmp_path / "member installation fixture"
    workspace.mkdir(mode=0o700)
    home = workspace / "synthetic home"
    bridge_root = home / "Library/Application Support/AdministrateMe Bridge"
    bridge_root.parent.mkdir(mode=0o700, parents=True)
    temporary = workspace / "temporary files"
    temporary.mkdir(mode=0o700)
    shims = workspace / "host boundary shims"
    shims.mkdir(mode=0o700)
    python_prefix = workspace / "selected Python"
    (python_prefix / "bin").mkdir(mode=0o700, parents=True)
    (python_prefix / "bin/python3.14").symlink_to(python)
    _shim(shims, "fdesetup", "echo 'FileVault is On.'")
    _shim(shims, "tailscale", '[ "$*" = "status --json" ]; printf "{}\\n"')
    _shim(
        shims,
        "brew",
        'case "$*" in\n'
        "  'list --versions python@3.14') echo 'python@3.14 3.14.8' ;;\n"
        f"  '--prefix python@3.14') printf '%s\\n' {shlex.quote(str(python_prefix))} ;;\n"
        "  *) exit 71 ;;\nesac",
    )
    # Do not inherit a test-mode switch, developer venv, import path, package
    # index override, global Poetry environment, SDK override or central root.
    env = {
        "HOME": str(home),
        "PATH": f"{shims}:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": str(temporary) + "/",
        "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_CACHE_DIR": str(workspace / "pip cache"),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "POETRY_CACHE_DIR": str(workspace / "poetry cache"),
    }
    _run(
        ["/usr/bin/xcrun", "clang", "--version"],
        cwd=workspace,
        env=env,
        log=artifact_dir / "native-compiler.log",
    )
    _run(
        ["/usr/bin/xcrun", "--show-sdk-path"],
        cwd=workspace,
        env=env,
        log=artifact_dir / "native-sdk.log",
    )
    commit = _run(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"],
        cwd=workspace,
        env=env,
        log=artifact_dir / "candidate.log",
    ).stdout.strip()
    archive = workspace / "reviewed source.tar"
    _run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-C",
            str(ROOT),
            "archive",
            "--format=tar",
            f"--output={archive}",
            commit,
        ],
        cwd=workspace,
        env=env,
        log=artifact_dir / "archive.log",
    )
    source = workspace / "packaged helper source"
    source.mkdir(mode=0o700)
    with tarfile.open(archive) as package_archive:
        package_archive.extractall(source, filter="data")
    write_source_marker(source, commit)
    assert not (source / ".git").exists()
    package, code = _handoff(source, workspace, commit)
    command = [
        "/bin/bash",
        str(source / "bootstrap/install-member-bridge.sh"),
        "--expected-commit",
        commit,
        "--bridge-root",
        str(bridge_root),
        "--release-archive",
        str(archive),
        "--release-sha256",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
        "--preview-only",
        "--transfer-code-file",
        str(code),
        str(package),
    ]
    release = bridge_root / "app/releases" / commit
    receipt = release / ".venv/.administrateme-bridge-installed-commit"
    installed_python = release / ".venv/bin/python"
    assert not bridge_root.exists()
    original_package = package.read_bytes()
    outcomes = []
    for stage in ("fresh", "resumed"):
        if stage == "resumed":
            # Disposable fixture only: model an interrupted sync with the actual
            # missing distribution set. Keep installed third-party dependencies
            # and tooling, and let the supported installer repair the venv.
            _run(
                [
                    str(bridge_root / "app/tooling/poetry-2.4.1/bin/python"),
                    "-m",
                    "pip",
                    "--python",
                    str(installed_python),
                    "uninstall",
                    "-y",
                    "watchdog",
                    "adminme",
                    "adminme-bridge",
                ],
                cwd=workspace,
                env=env,
                log=artifact_dir / "simulate-interruption.log",
            )
            receipt.unlink()
        phases = artifact_dir / f"{stage}-phases.log"
        dependencies = artifact_dir / f"{stage}-dependencies.log"
        result = _run(
            [
                "/bin/bash",
                "-c",
                'umask 077; exec 3>"$1"; exec 4<>"$2"; shift 2; '
                'export ADMINME_BRIDGE_DIAGNOSTICS_FD=3; exec "$@"',
                "installer-fixture",
                str(phases),
                str(dependencies),
                *command,
            ],
            cwd=workspace,
            env=env,
            log=artifact_dir / f"{stage}-install.log",
            diagnostic=dependencies,
        )
        assert [line.split(":", 1)[0] for line in result.stdout.splitlines()] == [
            "Instance",
            "Member",
            "Device",
            "Read-only sources",
            "Reviewed release",
            "Expires",
            "Provider writes",
        ]
        assert phases.read_text("utf-8").splitlines() == [
            "preflight",
            "python_setup",
            "release_preparation",
            "tooling_setup",
            "environment_setup",
            "dependency_install",
            "package_preview",
        ]
        dependency_output = dependencies.read_bytes()
        assert len(dependency_output) <= 262144
        for private_preview in (
            code.read_bytes(),
            str(package).encode(),
            b"installer-central-fixture.ts.net",
            b"Read-only sources:",
            b"Provider writes: none",
        ):
            assert private_preview not in dependency_output
        for label in ("Apple Calendar", "Apple Reminders", "Apple Notes"):
            assert label in result.stdout
        assert "Provider writes: none" in result.stdout
        assert f"Reviewed release: {commit[:12]}" in result.stdout
        assert receipt.read_text("utf-8").strip() == commit
        assert not (release / ".git").exists()
        # Neither a preview nor dependency recovery is enrollment or consent.
        assert package.read_bytes() == original_package
        assert not (bridge_root / "enrollment-receipt.json").exists()
        assert not (bridge_root / "config.yaml").exists()
        assert not (home / "Library/LaunchAgents").exists()
        probe = _run(
            [
                str(installed_python),
                "-I",
                "-c",
                "import importlib.metadata as m, json, sys; "
                "import adminme, adminme_bridge, watchdog.observers.fsevents; "
                "print(json.dumps({'prefix': sys.prefix, 'versions': "
                "{p: m.version(p) for p in ('watchdog', 'adminme', 'adminme-bridge')}}))",
            ],
            cwd=workspace,
            env=env,
            log=artifact_dir / f"{stage}-runtime.log",
        )
        proof = json.loads(probe.stdout)
        assert Path(proof["prefix"]) == release / ".venv"
        outcomes.append({"stage": stage, "status": "passed", **proof})
    (artifact_dir / "installer-runtime-proof.json").write_text(
        json.dumps(
            {
                "release_commit": commit,
                "python_version": version,
                "architecture": os.uname().machine,
                "host_boundaries_shimmed": ["FileVault", "Tailscale", "Homebrew"],
                "production_preview": True,
                "git_free_release": True,
                "provider_access": False,
                "provider_writes": False,
                "outcomes": outcomes,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
