"""Hosted-only proof of the member's real, Git-free dependency installation.

The ordinary suite collects but skips this network/native-toolchain check. Its
explicit macOS job must opt in and reject skipped results. FileVault/Tailscale
enrollment prerequisites are synthetic; Python, Poetry, locked dependencies,
the archived installer and the production preview process are real. Both
setup-python and real Homebrew runtimes must pass. No Apple data, central
runtime, Keychain, launchd or member identity is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from adminme.bridge_enrollment import BridgeEnrollmentCoordinator
from bootstrap.source_identity import write_source_marker
from bootstrap.member_bridge_component import (
    PERMISSION_RECOVERY_COMPATIBILITY,
    PERMISSION_RECOVERY_PROTOCOL,
    runtime_interface_fingerprint,
)
from bootstrap.member_bridge_helper_app import build_member_bridge_helper_app


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
    expected_failure: str | None = None,
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
    if expected_failure is None:
        assert result.returncode == 0, (
            f"{log.name}: exit {result.returncode}\n{output[-16000:]}\n{failure_detail}"
        )
    else:
        assert result.returncode != 0 and expected_failure in output, (
            f"{log.name}: expected {expected_failure!r}, got exit "
            f"{result.returncode}\n{output[-16000:]}"
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


def _sdk_version(sdk: Path) -> str:
    with (sdk / "SDKSettings.plist").open("rb") as stream:
        return str(plistlib.load(stream)["Version"])


def _toolchain_evidence(
    workspace: Path, artifact_dir: Path, env: dict[str, str]
) -> dict[str, str]:
    def inspect(arguments: list[str], name: str) -> str:
        result = _run(
            arguments, cwd=workspace, env=env, log=artifact_dir / f"{name}.log"
        )
        return (result.stdout + result.stderr).strip()

    developer = Path(
        env.get("DEVELOPER_DIR")
        or inspect(["/usr/bin/xcode-select", "-p"], "selected-developer")
    ).resolve(strict=True)
    default_sdk = Path(
        inspect(["/usr/bin/xcrun", "--show-sdk-path"], "native-sdk")
    ).resolve(strict=True)
    canonical_candidates = [
        path
        for path in (
            developer / "SDKs/MacOSX.sdk",
            developer / "Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk",
        )
        if path.is_dir()
    ]
    assert len(canonical_candidates) == 1
    canonical_sdk = canonical_candidates[0].resolve(strict=True)
    compiler = inspect(["/usr/bin/xcrun", "--find", "clang"], "compiler-path")
    linker = inspect(["/usr/bin/xcrun", "--find", "ld"], "linker-path")
    evidence = {
        "developer_directory": str(developer),
        "compiler_path": compiler,
        "compiler_version": inspect([compiler, "--version"], "native-compiler"),
        "linker_path": linker,
        "linker_and_tapi_version": inspect([linker, "-v"], "native-linker"),
        "default_sdk_resolved": str(default_sdk),
        "default_sdk_version": _sdk_version(default_sdk),
        "canonical_sdk_resolved": str(canonical_sdk),
        "canonical_sdk_version": _sdk_version(canonical_sdk),
    }
    (artifact_dir / "native-toolchain.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    return evidence


@pytest.mark.parametrize(
    "distribution", ["setup-python", "homebrew", "homebrew-older-toolchain"]
)
def test_real_clean_and_interrupted_member_install_preview(
    tmp_path: Path, distribution: str
) -> None:
    assert os.environ.get("GITHUB_ACTIONS") == "true", "hosted CI only"
    assert sys.platform == "darwin", "native macOS dependency build required"
    assert os.uname().machine == "arm64", "native Apple silicon required"
    interpreter_variable = {
        "setup-python": "ADMINME_BRIDGE_INSTALLER_PYTHON",
        "homebrew": "ADMINME_BRIDGE_INSTALLER_HOMEBREW_PYTHON",
        "homebrew-older-toolchain": "ADMINME_BRIDGE_INSTALLER_HOMEBREW_PYTHON",
    }[distribution]
    python = Path(os.environ[interpreter_variable]).resolve()
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
    artifact_dir = (
        Path(os.environ["ADMINME_BRIDGE_INSTALLER_ARTIFACT_DIR"]) / distribution
    )
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
    _shim(shims, "fdesetup", "echo 'FileVault is On.'")
    _shim(shims, "tailscale", '[ "$*" = "status --json" ]; printf "{}\\n"')
    if distribution == "setup-python":
        python_prefix = workspace / "selected Python"
        (python_prefix / "bin").mkdir(mode=0o700, parents=True)
        (python_prefix / "bin/python3.14").symlink_to(python)
        _shim(
            shims,
            "brew",
            'case "$*" in\n'
            "  'list --versions python@3.14') echo 'python@3.14 3.14.8' ;;\n"
            f"  '--prefix python@3.14') printf '%s\\n' {shlex.quote(str(python_prefix))} ;;\n"
            "  *) exit 71 ;;\nesac",
        )
    else:
        assert Path("/opt/homebrew/bin/brew").is_file()
    # Do not inherit a test-mode switch, developer venv, import path, package
    # index override, global Poetry environment, SDK override or central root.
    env = {
        "HOME": str(home),
        "PATH": (
            f"{shims}:"
            + ("/opt/homebrew/bin:" if distribution != "setup-python" else "")
            + "/usr/bin:/bin:/usr/sbin:/sbin"
        ),
        "TMPDIR": str(temporary) + "/",
        "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_CACHE_DIR": str(workspace / "pip cache"),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "POETRY_CACHE_DIR": str(workspace / "poetry cache"),
        "HOMEBREW_NO_AUTO_UPDATE": "1",
        "HOMEBREW_NO_ANALYTICS": "1",
        "HOMEBREW_NO_INSTALL_CLEANUP": "1",
    }
    if distribution == "homebrew-older-toolchain":
        env["DEVELOPER_DIR"] = os.environ["ADMINME_BRIDGE_INSTALLER_OLDER_DEVELOPER"]
    formula_version = None
    if distribution != "setup-python":
        # Exercise the real package-manager lookup used on a member Mac. The
        # explicit CI setup installs the formula; no substitute reports success.
        formula_version = _run(
            ["/opt/homebrew/bin/brew", "list", "--versions", "python@3.14"],
            cwd=workspace,
            env=env,
            log=artifact_dir / "homebrew-python-version.log",
        ).stdout.strip()
        assert formula_version.startswith("python@3.14 3.14.8")
        formula_prefix = _run(
            ["/opt/homebrew/bin/brew", "--prefix", "python@3.14"],
            cwd=workspace,
            env=env,
            log=artifact_dir / "homebrew-python-prefix.log",
        ).stdout.strip()
        assert (Path(formula_prefix) / "bin/python3.14").resolve() == python
    toolchain = _toolchain_evidence(workspace, artifact_dir, env)
    compatibility_probe = None
    if distribution == "homebrew-older-toolchain":
        # Real mixed-toolchain failure, separately from the installer's default
        # selection: an explicit override must never be disguised as absent.
        assert "clang-2100.1.1.101" in toolchain["compiler_version"]
        newer_sdk = Path(os.environ["ADMINME_BRIDGE_INSTALLER_NEWER_SDK"]).resolve(
            strict=True
        )
        assert _sdk_version(newer_sdk) == "27.0"
        assert toolchain["canonical_sdk_version"] == "26.5"
        probe_source = workspace / "native link probe.c"
        probe_source.write_text("int adminme_native_probe(void) { return 0; }\n")
        probe_command = [
            toolchain["compiler_path"],
            "-v",
            "-bundle",
            "-undefined",
            "dynamic_lookup",
            str(probe_source),
            "-lm",
            "-framework",
            "CoreFoundation",
            "-framework",
            "CoreServices",
            "-o",
            str(workspace / "native link probe.so"),
        ]
        _run(
            probe_command,
            cwd=workspace,
            env={**env, "SDKROOT": str(newer_sdk)},
            log=artifact_dir / "older-compiler-newer-sdk-failure.log",
            expected_failure="arm64e.x1",
        )
        _run(
            probe_command,
            cwd=workspace,
            env={**env, "SDKROOT": toolchain["canonical_sdk_resolved"]},
            log=artifact_dir / "older-compiler-canonical-sdk-success.log",
        )
        assert "SDKROOT" not in env
        compatibility_probe = {
            "incompatible_sdk": str(newer_sdk),
            "incompatible_result": "arm64e.x1 link failure",
            "canonical_sdk": toolchain["canonical_sdk_resolved"],
            "canonical_result": "linked successfully; binary not executed",
            "installer_sdkroot_override": False,
        }
    commit = _run(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"],
        cwd=workspace,
        env=env,
        log=artifact_dir / "candidate.log",
    ).stdout.strip()
    candidate_commit = commit
    candidate_archive = workspace / "candidate source.tar"
    _run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-C",
            str(ROOT),
            "archive",
            "--format=tar",
            f"--output={candidate_archive}",
            candidate_commit,
        ],
        cwd=workspace,
        env=env,
        log=artifact_dir / "candidate-archive.log",
    )
    # Isolated mixed-release fixture: A has the exact candidate edge code, an
    # older central protocol constant and no controller. B restores the current
    # central route plus controller/installer. Real install/preview must keep A
    # while executing sealed component B; no archive overlay is permitted.
    # Production central+retained-edge handshake is covered by its composition
    # gate; this process proves actual packaging/installation/import boundaries.
    fixture_repository = workspace / "component compatibility repository"
    fixture_repository.mkdir(mode=0o700)
    with tarfile.open(candidate_archive) as package_archive:
        package_archive.extractall(fixture_repository, filter="data")
    installer = fixture_repository / "bootstrap/install-member-bridge.sh"
    candidate_installer = installer.read_bytes()
    fixture_controller = (
        fixture_repository / "bootstrap/member_bridge_permission_controller.py"
    )
    controller_payload = fixture_controller.read_bytes()
    fixture_controller.unlink()
    fixture_route = fixture_repository / "adminme/products/bridge/routers/ingest.py"
    candidate_route = fixture_route.read_bytes()
    legacy_route = candidate_route.replace(
        PERMISSION_RECOVERY_PROTOCOL.encode(), b"synthetic-legacy-bridge-protocol"
    )
    assert legacy_route != candidate_route
    fixture_route.write_bytes(legacy_route)
    installer.write_bytes(
        candidate_installer + b"\n# Synthetic retained helper revision.\n"
    )
    git_steps = [
        ["init", "-b", "fixture"],
        ["config", "user.name", "Component fixture"],
        ["config", "user.email", "component-fixture@example.invalid"],
        ["add", "--force", "--all"],
        ["commit", "-m", "Synthetic retained runtime with older helper"],
    ]
    for index, arguments in enumerate(git_steps):
        _run(
            ["/usr/bin/git", "-C", str(fixture_repository), *arguments],
            cwd=workspace,
            env=env,
            log=artifact_dir / f"component-fixture-{index}.log",
        )
    commit = _run(
        ["/usr/bin/git", "-C", str(fixture_repository), "rev-parse", "HEAD"],
        cwd=workspace,
        env=env,
        log=artifact_dir / "retained-runtime-commit.log",
    ).stdout.strip()
    installer.write_bytes(candidate_installer)
    fixture_controller.write_bytes(controller_payload)
    fixture_route.write_bytes(candidate_route)
    for index, arguments in enumerate(
        [
            [
                "add",
                "--",
                "bootstrap/install-member-bridge.sh",
                "bootstrap/member_bridge_permission_controller.py",
                "adminme/products/bridge/routers/ingest.py",
            ],
            ["commit", "-m", "Exact candidate helper with retained runtime"],
        ]
    ):
        _run(
            ["/usr/bin/git", "-C", str(fixture_repository), *arguments],
            cwd=workspace,
            env=env,
            log=artifact_dir / f"component-helper-{index}.log",
        )
    component_commit = _run(
        ["/usr/bin/git", "-C", str(fixture_repository), "rev-parse", "HEAD"],
        cwd=workspace,
        env=env,
        log=artifact_dir / "helper-component-commit.log",
    ).stdout.strip()
    assert commit != component_commit != candidate_commit
    runtime_fingerprint = runtime_interface_fingerprint(fixture_repository, commit)
    component_fingerprint = runtime_interface_fingerprint(
        fixture_repository, component_commit
    )
    assert runtime_fingerprint != component_fingerprint
    assert component_fingerprint == runtime_interface_fingerprint(
        ROOT, candidate_commit
    )
    archive = workspace / "retained runtime.tar"
    _run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-C",
            str(fixture_repository),
            "archive",
            "--format=tar",
            f"--output={archive}",
            commit,
        ],
        cwd=workspace,
        env=env,
        log=artifact_dir / "archive.log",
    )
    source = workspace / "packaged retained runtime"
    source.mkdir(mode=0o700)
    with tarfile.open(archive) as package_archive:
        package_archive.extractall(source, filter="data")
    write_source_marker(source, commit)
    assert not (source / ".git").exists()
    assert not (source / "bootstrap/member_bridge_permission_controller.py").exists()
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    helper_app = workspace / "Current Bridge Helper.app"
    built = build_member_bridge_helper_app(
        helper_app,
        root=fixture_repository,
        expected_commit=component_commit,
        runtime_commit=commit,
        expected_runtime_archive_sha256=archive_sha256,
        runtime_compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
    )
    manifest = json.loads(
        (helper_app / "Contents/Resources/helper-manifest.json").read_text()
    )
    bundled_installer = helper_app / manifest["installer_path"]
    bundled_archive = helper_app / manifest["release_archive_path"]
    bundled_controller = helper_app / manifest["permission_controller_path"]
    candidate_controller = (
        ROOT / "bootstrap/member_bridge_permission_controller.py"
    ).read_bytes()
    assert bundled_controller.read_bytes() == candidate_controller
    assert (
        manifest["permission_controller_sha256"]
        == hashlib.sha256(candidate_controller).hexdigest()
    )
    info = plistlib.loads((helper_app / "Contents/Info.plist").read_bytes())
    for key in (
        "NSCalendarsFullAccessUsageDescription",
        "NSRemindersFullAccessUsageDescription",
        "NSAppleEventsUsageDescription",
    ):
        assert info[key]
    assert (
        built.helper_component_commit
        == manifest["helper_component_commit"]
        == component_commit
    )
    assert (
        built.release_commit
        == manifest["release_commit"]
        == manifest["runtime_commit"]
        == commit
    )
    assert (
        manifest["runtime_compatibility"]["runtime_input_sha256"] == runtime_fingerprint
    )
    assert manifest["runtime_compatibility"]["runtime_inputs_unchanged"] is False
    assert manifest["runtime_compatibility"]["retained_edge_inputs_unchanged"] is True
    assert (
        manifest["runtime_compatibility"]["central_candidate_runtime_input_sha256"]
        == component_fingerprint
    )
    assert manifest["required_central_protocol"] == PERMISSION_RECOVERY_PROTOCOL
    assert manifest["required_central_release_commit"] == component_commit
    assert manifest["runtime_compatibility"]["central_differences"] == [
        {
            "path": "adminme/products/bridge/routers/ingest.py",
            "retained": {
                "mode": "100644",
                "sha256": hashlib.sha256(legacy_route).hexdigest(),
            },
            "central_candidate": {
                "mode": "100644",
                "sha256": hashlib.sha256(candidate_route).hexdigest(),
            },
        }
    ]
    assert (
        built.release_archive_sha256
        == manifest["release_archive_sha256"]
        == archive_sha256
    )
    assert bundled_archive.read_bytes() == archive.read_bytes()
    assert bundled_installer.read_bytes() == candidate_installer
    assert (
        manifest["installer_sha256"] == hashlib.sha256(candidate_installer).hexdigest()
    )
    assert (
        source / "bootstrap/install-member-bridge.sh"
    ).read_bytes() != candidate_installer
    assert not (helper_app / ".git").exists()
    _run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(helper_app)],
        cwd=workspace,
        env=env,
        log=artifact_dir / "sealed-helper-verification.log",
    )
    (artifact_dir / "sealed-helper-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    package, code = _handoff(source, workspace, commit)
    command = [
        "/bin/bash",
        str(bundled_installer),
        "--expected-commit",
        commit,
        "--bridge-root",
        str(bridge_root),
        "--release-archive",
        str(bundled_archive),
        "--release-sha256",
        archive_sha256,
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
            "native_build_setup",
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
    # Real installed dependency ABI and sealed resource loading, without
    # requesting/granting TCC or reading any real calendar/reminder records.
    native_probe = _run(
        [
            str(installed_python),
            "-I",
            "-c",
            "import hashlib,json,runpy,sys; from pathlib import Path; import EventKit; "
            "controller=Path(sys.argv[1]); "
            "assert hashlib.sha256(controller.read_bytes()).hexdigest()==sys.argv[2]; "
            "namespace=runpy.run_path(str(controller)); assert callable(namespace['validate_result']); "
            "runtime_root=Path(sys.prefix).parent; "
            "assert all(hashlib.sha256((runtime_root/path).read_bytes()).hexdigest()==expected for path,expected in namespace['RUNTIME_INTERFACE_HASHES'].items()); "
            "from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper; "
            "from adminme_bridge.enrollment_ui import MacOSEnrollmentInteraction; "
            "from bootstrap.bridge_enrollment import MemberBridgePaths; "
            "assert callable(MemberBridgeEnrollmentHelper.resume) and callable(MacOSEnrollmentInteraction) and callable(MemberBridgePaths.from_root); "
            "store=EventKit.EKEventStore.alloc().init(); "
            "assert callable(store.requestFullAccessToEventsWithCompletion_); "
            "assert callable(store.requestFullAccessToRemindersWithCompletion_); "
            "statuses=[int(EventKit.EKEventStore.authorizationStatusForEntityType_(kind)) for kind in (EventKit.EKEntityTypeEvent,EventKit.EKEntityTypeReminder)]; "
            "assert all(status in (0,1,2) for status in statuses), 'hosted runner must not have real granted store access'; "
            "calendar=EventKit.EKCalendar.calendarForEntityType_eventStore_(EventKit.EKEntityTypeEvent,store); "
            "calendar.setTitle_('Synthetic unsaved calendar'); "
            "assert calendar.title()=='Synthetic unsaved calendar'; "
            "assert int(calendar.allowedEntityTypes()) & 1; "
            "assert callable(store.calendarWithIdentifier_); "
            "interaction=namespace['native_interaction'](object,object); "
            "failure=namespace['PermissionFailure']\n"
            "try:\n"
            "    interaction._run_native(['/usr/bin/osascript','-e','error \"synthetic authorization denial\" number -1743'])\n"
            "except failure as exc:\n"
            "    assert exc.code=='native_permission_denied'\n"
            "else:\n"
            "    raise AssertionError('native denial was not classified')\n"
            "print(json.dumps({'sealed_controller_import':True,'eventkit_abi':True,'authorization_states':statuses,'unsaved_metadata':True,'permission_requested':False,'provider_records_read':False,'provider_writes':False,'osascript_native_error_classification':'native_permission_denied'}))",
            str(bundled_controller),
            manifest["permission_controller_sha256"],
        ],
        cwd=workspace,
        env=env,
        log=artifact_dir / "permission-native-abi.log",
    )
    native_permission_proof = json.loads(native_probe.stdout)
    (artifact_dir / "installer-runtime-proof.json").write_text(
        json.dumps(
            {
                "release_commit": commit,
                "candidate_source_commit": candidate_commit,
                "synthetic_helper_component_commit": component_commit,
                "runtime_input_sha256": runtime_fingerprint,
                "central_candidate_runtime_input_sha256": component_fingerprint,
                "retained_edge_inputs_unchanged": True,
                "required_central_protocol": PERMISSION_RECOVERY_PROTOCOL,
                "installer_sha256": hashlib.sha256(candidate_installer).hexdigest(),
                "permission_controller_sha256": manifest[
                    "permission_controller_sha256"
                ],
                "native_permission_smoke": native_permission_proof,
                "mixed_component_and_retained_runtime": True,
                "sealed_combined_package": True,
                "runtime_archive_sha256": archive_sha256,
                "python_distribution": distribution,
                "python_version": version,
                "homebrew_formula_version": formula_version,
                "native_toolchain": toolchain,
                "compatibility_probe": compatibility_probe,
                "architecture": os.uname().machine,
                "host_boundaries_shimmed": ["FileVault", "Tailscale"]
                + (["Homebrew"] if distribution == "setup-python" else []),
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
