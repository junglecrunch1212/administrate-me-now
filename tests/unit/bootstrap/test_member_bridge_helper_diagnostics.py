"""Hosted-only launcher processes with synthetic installer and native UI seams."""

from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
import sys
from pathlib import Path

import pytest

from bootstrap.member_bridge_helper_app import _helper_launcher, _mac_prerequisites


COMMIT = "a" * 40
COMPONENT_COMMIT = "c" * 40
CODE = "fixture-private-one-time-code"
POST_PREVIEW = "fixture-private-post-preview-output"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


@pytest.fixture
def launcher(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    home = tmp_path / "member home"
    home.mkdir(mode=0o700)
    temporary = home / "temporary"
    temporary.mkdir(mode=0o700)
    resources = home / "Fixture Helper.app" / "Contents" / "Resources"
    resources.mkdir(parents=True, mode=0o700)
    native_ui = home / "fixture-native-ui"
    _executable(
        native_ui,
        f"#!{sys.executable}\n"
        + """import json
import os
import sys
from pathlib import Path

screen = Path(sys.argv[1]).name
with (Path(os.environ["HOME"]) / "ui.jsonl").open("a") as stream:
    stream.write(json.dumps({"screen": screen, "arguments": sys.argv[2:]}) + "\\n")
if screen == "secure-code.applescript":
    print("fixture-private-one-time-code")
elif screen == "manage.applescript":
    print(os.environ.get("FIXTURE_MANAGE_ACTION", "Continue Enrollment"))
elif screen == "review.applescript":
    assert Path(sys.argv[2]).read_text() == "Safe synthetic enrollment preview\\n"
    if os.environ.get("FIXTURE_OUTCOME") == "review_cancel":
        raise SystemExit(1)
""",
    )
    controller = resources / "member_bridge_permission_controller.py"
    controller.write_text(
        """import json
import os
import sys

def validate_result(value, *, operation, exit_code):
    outcomes = {"ready": 0, "blocked": 2, "cancelled": 0, "unknown": 3}
    if not isinstance(value, dict) or set(value) != {"outcome", "operation"}:
        raise ValueError("invalid fixture result")
    if value["operation"] != operation or outcomes.get(value["outcome"]) != exit_code:
        raise ValueError("invalid fixture outcome")
    return value

if __name__ == "__main__":
    operation = sys.argv[sys.argv.index("--operation") + 1]
    assert sys.argv[sys.argv.index("--expected-central-commit") + 1] == "c" * 40
    assert sys.argv[sys.argv.index("--expected-runtime-commit") + 1] == "a" * 40
    outcome = os.environ.get("FIXTURE_OUTCOME", "success")
    print("fixture-private-post-preview-output", file=sys.stderr)
    if outcome == "permission_invalid":
        print(json.dumps({"secret": "fixture-private-one-time-code"}))
        raise SystemExit(0)
    if outcome == "permission_duplicate":
        print('{"outcome":"ready","outcome":"ready","operation":' + json.dumps(operation) + '}')
        raise SystemExit(0)
    if outcome == "permission_wrong_exit":
        print(json.dumps({"outcome": "blocked", "operation": operation}))
        raise SystemExit(0)
    if outcome == "permission_large":
        print("x" * 8193)
        raise SystemExit(0)
    if outcome == "permission_crash":
        raise SystemExit(57)
    selected = {"permission_blocked": "blocked", "permission_cancelled": "cancelled", "permission_unknown": "unknown"}.get(outcome, "ready")
    print(json.dumps({"outcome": selected, "operation": operation}))
    raise SystemExit({"ready": 0, "blocked": 2, "cancelled": 0, "unknown": 3}[selected])
""",
        encoding="utf-8",
    )
    controller.chmod(0o600)
    launch_controller = resources / "member_bridge_launch.py"
    launch_controller.write_text("# inert hosted launch fixture\n")
    launch_controller.chmod(0o600)
    # Replace only unavailable native UI in this generated hosted fixture. The
    # shipped launcher has no environment variable or arbitrary UI override.
    script = (
        _helper_launcher(
            COMMIT,
            "b" * 64,
            component_commit=COMPONENT_COMMIT,
            launch_controller_sha256=hashlib.sha256(
                launch_controller.read_bytes()
            ).hexdigest(),
            permission_controller_sha256=hashlib.sha256(
                controller.read_bytes()
            ).hexdigest(),
        )
        .decode()
        .replace("/usr/bin/osascript", f'"{native_ui}"')
    )
    prerequisite = b"#!/bin/bash\nexit 0\n"
    _executable(resources / "prepare-mac.sh", prerequisite.decode())
    script = script.replace(
        hashlib.sha256(_mac_prerequisites()).hexdigest(),
        hashlib.sha256(prerequisite).hexdigest(),
    )
    launcher_path = resources / "bridge-helper-launcher.sh"
    _executable(launcher_path, script)
    release = (
        home / "Library/Application Support/AdministrateMe Bridge/app/releases" / COMMIT
    )
    binary = release / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    (binary.parent.parent / ".administrateme-bridge-installed-commit").write_text(
        COMMIT
    )
    _executable(
        binary,
        f"#!{sys.executable}\n"
        + "import os, sys\n"
        + "assert os.getcwd() == os.environ['ADMINME_REPOSITORY_ROOT']\n"
        + "if sys.argv[1:3] == ['-m', 'bootstrap.cli']:\n"
        + "    assert sys.argv[3] == 'bridge-uninstall'\n"
        + "    print('fixture-private-post-preview-output', file=sys.stderr)\n"
        + "    raise SystemExit(57 if os.environ.get('FIXTURE_OUTCOME') == 'uninstall_fail' else 0)\n"
        + f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
    )
    _executable(
        resources / "install-member-bridge.sh",
        """#!/bin/bash
set -Eeuo pipefail
preview=false
for argument in "$@"; do
  [[ "$argument" != "--preview-only" ]] || preview=true
  [[ "$argument" != "--complete-wizard" ]] || exit 98
done
if [[ "${FIXTURE_OUTCOME:-}" == "native_setup_fail" ]]; then
  printf 'release_preparation\\nnative_build_setup\\n' >&3
  exit 78
fi
printf 'dependency_install\\n' >&3
printf 'synthetic dependency installation detail\\n' >&4
if [[ "${FIXTURE_OUTCOME:-}" == "dependency_fail" ]]; then
  exit 23
fi
if [[ "$preview" == true ]]; then
  printf 'package_preview\\n' >&3
  if [[ "${FIXTURE_OUTCOME:-}" == "preview_fail" ]]; then
    printf 'fixture-private-post-preview-output\\n' >&2
    exit 31
  fi
  printf 'Safe synthetic enrollment preview\\n'
else
  printf 'permission_setup\\n' >&3
  printf 'fixture-private-post-preview-output\\n' >&2
  if [[ "${FIXTURE_OUTCOME:-}" == "enrollment_fail" ]]; then
    exit 42
  fi
fi
""",
    )
    package = home / "synthetic.adminmebridge"
    package.write_text("synthetic encrypted handoff", encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"BASH_ENV", "ENV", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment.update({"HOME": str(home), "TMPDIR": str(temporary)})
    return launcher_path, environment, package


def _run(
    launcher: tuple[Path, dict[str, str], Path],
    *,
    outcome: str,
    manage: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[Path]]:
    executable, environment, package = launcher
    completed = subprocess.run(
        ["/bin/bash", str(executable), "--manage" if manage else str(package)],
        env={**environment, "FIXTURE_OUTCOME": outcome},
        capture_output=True,
        text=True,
        timeout=20,
    )
    home = Path(environment["HOME"])
    attempts = sorted(
        (home / "Library" / "Logs" / "AdministrateMe Bridge" / "Setup").glob(
            "attempt.*"
        )
    )
    assert not list(Path(environment["TMPDIR"]).iterdir())
    assert CODE not in completed.stdout + completed.stderr
    assert POST_PREVIEW not in completed.stdout + completed.stderr
    return completed, attempts


@pytest.mark.parametrize(
    ("outcome", "phase", "exit_code", "status"),
    [
        ("dependency_fail", "dependency_install", 23, "failed"),
        ("native_setup_fail", "native_build_setup", 78, "failed"),
        ("preview_fail", "package_preview", 31, "failed"),
        ("enrollment_fail", "permission_setup", 42, "failed"),
        ("review_cancel", "review", 0, "cancelled"),
        ("success", "complete", 0, "succeeded"),
    ],
)
def test_launcher_records_phase_without_retaining_enrollment_material(
    launcher: tuple[Path, dict[str, str], Path],
    outcome: str,
    phase: str,
    exit_code: int,
    status: str,
) -> None:
    completed, attempts = _run(launcher, outcome=outcome)
    assert completed.returncode == exit_code, completed.stderr
    assert len(attempts) == 1
    attempt = attempts[0]
    assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
    receipt = json.loads((attempt / "receipt.json").read_text())
    assert receipt["phase"] == phase
    assert receipt["exit_code"] == exit_code
    assert receipt["outcome"] == status
    assert receipt["release_commit"] == COMMIT
    assert receipt["helper_component_commit"] == COMPONENT_COMMIT
    assert receipt["operation"] == "enroll"
    assert receipt["dependency_log_limit_bytes"] == 262144
    assert (attempt / "dependency-install.log").read_text() == (
        ""
        if outcome == "native_setup_fail"
        else "synthetic dependency installation detail\n"
    )
    for file in attempt.iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
        content = file.read_text()
        assert CODE not in content
        assert POST_PREVIEW not in content
        assert "synthetic encrypted handoff" not in content
        assert launcher[1]["HOME"] not in content
    ui = [
        json.loads(line)
        for line in (Path(launcher[1]["HOME"]) / "ui.jsonl").read_text().splitlines()
    ]
    failures = [item for item in ui if item["screen"] == "failed.applescript"]
    assert failures == (
        [{"screen": "failed.applescript", "arguments": [phase]}]
        if status == "failed"
        else []
    )


def test_retry_preserves_previous_failure_and_uses_a_new_private_receipt(
    launcher: tuple[Path, dict[str, str], Path],
) -> None:
    failed, first_attempts = _run(launcher, outcome="dependency_fail")
    assert failed.returncode == 23
    previous = {file.name: file.read_bytes() for file in first_attempts[0].iterdir()}
    succeeded, attempts = _run(launcher, outcome="success")
    assert succeeded.returncode == 0
    assert len(attempts) == 2
    assert {
        file.name: file.read_bytes() for file in first_attempts[0].iterdir()
    } == previous
    current = next(attempt for attempt in attempts if attempt != first_attempts[0])
    assert json.loads((current / "receipt.json").read_text())["outcome"] == "succeeded"


@pytest.mark.parametrize("action", ["Continue Enrollment", "Uninstall Locally"])
@pytest.mark.parametrize("fails", [False, True])
def test_manage_uses_same_private_terminal_diagnostics_and_cleans_temporary_files(
    launcher: tuple[Path, dict[str, str], Path], action: str, fails: bool
) -> None:
    _, environment, _ = launcher
    environment["FIXTURE_MANAGE_ACTION"] = action
    permission = action == "Continue Enrollment"
    outcome = (
        ("permission_blocked" if permission else "uninstall_fail")
        if fails
        else "manage"
    )
    completed, attempts = _run(launcher, outcome=outcome, manage=True)
    assert completed.returncode == ((2 if permission else 57) if fails else 0)
    assert len(attempts) == 1
    receipt = json.loads((attempts[0] / "receipt.json").read_text())
    assert receipt["phase"] == (
        ("permission_setup" if fails else "complete") if permission else "uninstall"
    )
    assert receipt["operation"] == ("continue" if permission else "uninstall")
    assert receipt["outcome"] == (
        ("blocked" if permission else "failed") if fails else "succeeded"
    )
    assert (attempts[0] / "dependency-install.log").read_bytes() == b""


def test_manage_missing_release_records_failure_without_attempting_installation(
    launcher: tuple[Path, dict[str, str], Path],
) -> None:
    binary = (
        Path(launcher[1]["HOME"])
        / "Library/Application Support/AdministrateMe Bridge/app/releases"
        / COMMIT
        / ".venv/bin/python"
    )
    binary.unlink()
    completed, attempts = _run(launcher, outcome="manage", manage=True)
    assert completed.returncode == 1
    receipt = json.loads((attempts[0] / "receipt.json").read_text())
    assert receipt["operation"] == "manage"
    assert receipt["phase"] == "manage"
    assert receipt["outcome"] == "failed"
    assert (attempts[0] / "dependency-install.log").read_bytes() == b""


@pytest.mark.parametrize("unsafe", ["symlink", "writable_by_others"])
def test_unsafe_diagnostic_parent_cannot_redirect_writes(
    launcher: tuple[Path, dict[str, str], Path], tmp_path: Path, unsafe: str
) -> None:
    home = Path(launcher[1]["HOME"])
    logs = home / "Library/Logs"
    logs.mkdir(parents=True, mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    parent = logs / "AdministrateMe Bridge"
    if unsafe == "symlink":
        parent.symlink_to(outside, target_is_directory=True)
    else:
        parent.mkdir(mode=0o700)
        parent.chmod(0o777)
    completed, attempts = _run(launcher, outcome="success")
    assert completed.returncode != 0
    assert not attempts
    assert list(outside.iterdir()) == []
    assert not (parent / "Setup").exists()


@pytest.mark.parametrize("manage", [False, True])
@pytest.mark.parametrize(
    ("outcome", "expected_exit", "retained"),
    [
        ("permission_blocked", 2, True),
        ("permission_cancelled", 0, True),
        ("permission_unknown", 3, True),
        ("permission_invalid", 1, False),
        ("permission_large", 1, False),
        ("permission_duplicate", 1, False),
        ("permission_wrong_exit", 1, False),
        ("permission_crash", 1, False),
    ],
)
def test_permission_result_never_reports_false_completion_or_retains_raw_output(
    launcher: tuple[Path, dict[str, str], Path],
    manage: bool,
    outcome: str,
    expected_exit: int,
    retained: bool,
) -> None:
    completed, attempts = _run(launcher, outcome=outcome, manage=manage)
    assert completed.returncode == expected_exit
    attempt = attempts[0]
    result = attempt / "permission-result.json"
    assert result.exists() is retained
    if retained:
        assert json.loads(result.read_text())["operation"] == (
            "continue" if manage else "initial"
        )
        assert stat.S_IMODE(result.stat().st_mode) == 0o600
    for path in attempt.iterdir():
        text = path.read_text()
        assert CODE not in text and POST_PREVIEW not in text
    ui = [
        json.loads(line)
        for line in (Path(launcher[1]["HOME"]) / "ui.jsonl").read_text().splitlines()
    ]
    assert not any(item["screen"] == "complete.applescript" for item in ui)
    assert any(
        item["screen"]
        == ("permission-stopped.applescript" if retained else "failed.applescript")
        for item in ui
    )


@pytest.mark.parametrize("manage", [False, True])
@pytest.mark.parametrize(
    ("number", "code", "accepted"),
    [
        (-1743, "native_permission_denied", True),
        (-128, "native_cancelled", True),
        (-1712, "native_timeout", True),
        (-32768, "native_command_failed", True),
        (32767, "native_command_failed", True),
        (-32769, "native_command_failed", False),
        (32768, "native_command_failed", False),
        (True, "native_command_failed", False),
        (False, "native_command_failed", False),
        (-1743.0, "native_permission_denied", False),
        ("-1743", "native_permission_denied", False),
        (None, "native_command_failed", False),
        (CODE, "native_command_failed", False),
        (-1743, "source_probe_failed", False),
    ],
)
def test_launcher_retains_only_native_numbers_validated_by_production_controller(
    launcher: tuple[Path, dict[str, str], Path],
    manage: bool,
    number: object,
    code: str,
    accepted: bool,
) -> None:
    executable, environment, _ = launcher
    controller = executable.parent / "member_bridge_permission_controller.py"
    old_hash = hashlib.sha256(controller.read_bytes()).hexdigest()
    operation = "continue" if manage else "initial"
    result = {
        "contract": "administrateme-bridge-permission-result-v1",
        "operation": operation,
        "outcome": "blocked",
        "capabilities": [
            {
                "capability_id": "apple_notes",
                "status": "blocked",
                "stage": "probe",
                "code": code,
                "native_error_number": number,
            }
        ],
        "content_exposed": False,
        "provider_effects": False,
    }
    # This fixture replaces only result production. Validation is the real
    # candidate controller's strict contract, invoked by the shipped launcher.
    controller.write_text(
        "import runpy\n"
        + f"validate_result = runpy.run_path({str(REPO_ROOT / 'bootstrap/member_bridge_permission_controller.py')!r})['validate_result']\n"
        + "if __name__ == '__main__':\n"
        + f"    print({json.dumps(result)!r})\n"
        + "    raise SystemExit(2)\n"
    )
    new_hash = hashlib.sha256(controller.read_bytes()).hexdigest()
    executable.write_text(executable.read_text().replace(old_hash, new_hash))
    completed, attempts = _run(launcher, outcome="success", manage=manage)
    assert completed.returncode == (2 if accepted else 1)
    retained = attempts[0] / "permission-result.json"
    assert retained.exists() is accepted
    if accepted:
        assert json.loads(retained.read_text()) == result
        assert stat.S_IMODE(retained.stat().st_mode) == 0o600
    for path in attempts[0].iterdir():
        assert CODE not in path.read_text()
        assert POST_PREVIEW not in path.read_text()
    ui = [
        json.loads(line)
        for line in (Path(environment["HOME"]) / "ui.jsonl").read_text().splitlines()
    ]
    assert not any(item["screen"] == "complete.applescript" for item in ui)


@pytest.mark.parametrize("manage", [False, True])
@pytest.mark.parametrize("defect", ["bytes", "symlink", "missing"])
@pytest.mark.parametrize(
    "resource", ["member_bridge_permission_controller.py", "member_bridge_launch.py"]
)
def test_permission_controller_identity_is_checked_before_execution(
    launcher: tuple[Path, dict[str, str], Path],
    manage: bool,
    defect: str,
    resource: str,
) -> None:
    controller = launcher[0].parent / resource
    if defect == "bytes":
        controller.write_text("raise SystemExit('tampered controller executed')\n")
    elif defect == "symlink":
        target = controller.with_name("outside-controller.py")
        controller.rename(target)
        controller.symlink_to(target)
    else:
        controller.unlink()
    completed, attempts = _run(launcher, outcome="success", manage=manage)
    assert completed.returncode != 0
    assert not (attempts[0] / "permission-result.json").exists()
    assert "tampered controller executed" not in completed.stdout + completed.stderr
    assert json.loads((attempts[0] / "receipt.json").read_text())["outcome"] == "failed"


@dataclass
class _InstallerFixture:
    environment: dict[str, str]
    command: list[str]
    phases: Path
    log: Path
    receipt: Path
    developer: Path
    sdk: Path
    newer_sdk: Path


def _write_sdk(parent: Path, version: str, *, alias: bool) -> Path:
    sdk = parent / f"MacOSX{version}.sdk"
    sdk.mkdir(parents=True)
    (sdk / "SDKSettings.json").write_text(
        json.dumps({"Version": version, "CanonicalName": f"macosx{version}"})
    )
    if alias:
        (parent / "MacOSX.sdk").symlink_to(sdk, target_is_directory=True)
    return sdk


def _installer_fixture(tmp_path: Path) -> _InstallerFixture:
    """Exercise actual shell/collector; only OS prerequisites and tooling are fake."""
    tools = tmp_path / "fixture-tools"
    tools.mkdir()
    prefix = tmp_path / "python-prefix"
    (prefix / "bin").mkdir(parents=True)
    # Model the base interpreter toolchain settings independently of the hosted
    # runner; the real Homebrew/compiler integration has its own acceptance test.
    _executable(
        prefix / "bin/python3.14",
        f"#!{sys.executable}\n"
        + "import json, os, sys, sysconfig\n"
        + f"real_python = {sys.executable!r}\n"
        + "if len(sys.argv) > 2 and sys.argv[1] == '-c':\n"
        + "    values = json.loads(os.environ.get('FIXTURE_SYSCONFIG', '{}'))\n"
        + "    sysconfig.get_config_var = lambda key, fixture_values=values: fixture_values.get(key, '')\n"
        + "    code = sys.argv[2]\n"
        + "    sys.argv = ['-c', *sys.argv[3:]]\n"
        + "    exec(compile(code, '<fixture-python>', 'exec'))\n"
        + "else:\n"
        + "    os.execv(real_python, [real_python, *sys.argv[1:]])\n",
    )
    developer = tmp_path / "selected developer"
    sdk = _write_sdk(developer / "SDKs", "26.5", alias=True)
    newer_sdk = _write_sdk(developer / "SDKs", "27.0", alias=False)
    for name, script in {
        "uname": 'if [[ "$1" == -s ]]; then echo Darwin; else echo arm64; fi',
        "id": "echo 501",
        "fdesetup": "echo 'FileVault is On.'",
        "tailscale": "exit 0",
        "xcode-select": 'printf "%s\\n" "$FIXTURE_DEVELOPER_DIR"',
        "xcrun": 'printf "unqualified lookup used\\n" >"$FIXTURE_XCRUN_MARKER"; exit 99',
        "brew": '[[ "$1" != --prefix ]] || printf "%s\\n" "$FIXTURE_PYTHON_PREFIX"',
        "df": "printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\nfixture 99999999 1 99999998 1%% /\\n'",
    }.items():
        _executable(tools / name, f"#!/bin/bash\n{script}\n")
    root = tmp_path / "member bridge with spaces"
    release = root / "app/releases" / COMMIT
    (release / "bridge").mkdir(parents=True)
    (release / "bridge/pyproject.toml").touch()
    (release / "bridge/poetry.lock").touch()
    (release / ".administrateme-source-commit").write_text(COMMIT)
    (release / ".venv/bin").mkdir(parents=True)
    (release / ".venv/bin/python").symlink_to(sys.executable)
    (release / "bootstrap").mkdir()
    (release / "bootstrap/__init__.py").touch()
    (release / "bootstrap/cli.py").write_text(
        """import json
import os
from pathlib import Path
import sys

Path(os.environ["FIXTURE_RUNTIME_ENV"]).write_text(json.dumps({key: os.environ.get(key) for key in ("SDKROOT", "DEVELOPER_DIR", "CC", "CFLAGS", "LDFLAGS")}))
try:
    os.write(4, b"private-preview-output-must-not-be-persisted")
except OSError:
    pass
else:
    raise SystemExit("persistent diagnostic descriptor reached preview")
print("fixture-private-post-preview-output", file=sys.stderr)
print("Safe synthetic enrollment preview")
"""
    )
    poetry = root / "app/tooling/poetry-2.4.1/bin/poetry"
    poetry.parent.mkdir(parents=True)
    _executable(
        poetry,
        f"#!{sys.executable}\n"
        + """import json
import os
from pathlib import Path

Path(os.environ["FIXTURE_BUILD_ENV"]).write_text(json.dumps({key: os.environ.get(key) for key in ("SDKROOT", "DEVELOPER_DIR", "CC", "CFLAGS", "LDFLAGS")}))
os.write(1, b"early-dependency-marker\\n" + b"a" * 300000)
os.write(2, b"b" * 300000)
os.write(1, b"\\nlast-dependency-marker\\n")
raise SystemExit(int(os.environ["FIXTURE_INSTALL_EXIT"]))
""",
    )
    package = tmp_path / "fixture.adminmebridge"
    package.write_text("private synthetic encrypted handoff")
    code = tmp_path / "fixture-code"
    code.write_text(CODE)
    code.chmod(0o600)
    archive = tmp_path / "reviewed.tar"
    # The existing immutable release is retained for the resume case. Its
    # archive hash is still checked; extraction is not part of this fixture.
    archive.write_bytes(b"synthetic retained reviewed archive")
    phase_file = tmp_path / "phases"
    log_file = tmp_path / "dependency-install.log"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "BASH_ENV",
            "ENV",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "SDKROOT",
            "DEVELOPER_DIR",
            "CC",
            "CXX",
            "CFLAGS",
            "CPPFLAGS",
            "LDFLAGS",
            "LDSHARED",
            "LDCXXSHARED",
            "ARCHFLAGS",
            "ADMINME_BRIDGE_DIAGNOSTICS_FD",
        }
    }
    environment.update(
        {
            "PATH": f"{tools}:{os.defpath}",
            "FIXTURE_PYTHON_PREFIX": str(prefix),
            "PYTHONDONTWRITEBYTECODE": "1",
            "FIXTURE_DEVELOPER_DIR": str(developer),
            "FIXTURE_XCRUN_MARKER": str(tmp_path / "unqualified-sdk-lookup"),
            "FIXTURE_RUNTIME_ENV": str(tmp_path / "runtime-environment.json"),
            "FIXTURE_BUILD_ENV": str(tmp_path / "build-environment.json"),
        }
    )
    receipt = release / ".venv/.administrateme-bridge-installed-commit"
    command = [
        "/bin/bash",
        str(REPO_ROOT / "bootstrap/install-member-bridge.sh"),
        "--expected-commit",
        COMMIT,
        "--bridge-root",
        str(root),
        "--release-archive",
        str(archive),
        "--release-sha256",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
        "--preview-only",
        "--transfer-code-file",
        str(code),
        str(package),
    ]
    return _InstallerFixture(
        environment, command, phase_file, log_file, receipt, developer, sdk, newer_sdk
    )


def _run_installer(
    fixture: _InstallerFixture,
    *,
    exit_code: int = 0,
    diagnostics: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = fixture.command
    if diagnostics:
        command = [
            "/bin/bash",
            "-c",
            'exec 3>"$1"; exec 4<>"$2"; shift 2; ADMINME_BRIDGE_DIAGNOSTICS_FD=3 "$@"',
            "fixture-descriptors",
            str(fixture.phases),
            str(fixture.log),
            *command,
        ]
    return subprocess.run(
        command,
        env={**fixture.environment, "FIXTURE_INSTALL_EXIT": str(exit_code)},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_actual_installer_drains_large_dependency_failure_and_resumes_privately(
    tmp_path: Path,
) -> None:
    fixture = _installer_fixture(tmp_path)
    for exit_code in (23, 0):
        completed = _run_installer(fixture, exit_code=exit_code)
        assert completed.returncode == exit_code, completed.stderr
        dependency_log = fixture.log.read_bytes()
        assert len(dependency_log) == 262144
        assert dependency_log.endswith(b"last-dependency-marker\n")
        assert b"early-dependency-marker" not in dependency_log
        assert CODE.encode() not in dependency_log
        assert POST_PREVIEW.encode() not in dependency_log
        assert b"private-preview-output-must-not-be-persisted" not in dependency_log
        if exit_code:
            assert completed.stdout == completed.stderr == ""
            assert not fixture.receipt.exists()
            assert fixture.phases.read_text().splitlines()[-1] == "dependency_install"
        else:
            assert completed.stdout == "Safe synthetic enrollment preview\n"
            assert completed.stderr == f"{POST_PREVIEW}\n"
            assert fixture.receipt.read_text().strip() == COMMIT
            assert fixture.phases.read_text().splitlines()[-1] == "package_preview"


@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize(
    "selection", ["clt", "xcode_directory", "xcode_app", "explicit_sdk"]
)
def test_native_sdk_selection_is_build_only_and_preserves_explicit_choices(
    tmp_path: Path, diagnostics: bool, selection: str
) -> None:
    fixture = _installer_fixture(tmp_path)
    expected_sdk = fixture.sdk
    expected_developer = str(fixture.developer)
    if selection in {"xcode_directory", "xcode_app"}:
        app = tmp_path / "Selected Xcode.app"
        developer = app / "Contents/Developer"
        expected_sdk = _write_sdk(
            developer / "Platforms/MacOSX.platform/Developer/SDKs", "26.5", alias=True
        )
        expected_developer = str(app if selection == "xcode_app" else developer)
        fixture.environment["DEVELOPER_DIR"] = expected_developer
    elif selection == "explicit_sdk":
        expected_sdk = tmp_path / "explicit SDK alias"
        expected_sdk.symlink_to(fixture.newer_sdk, target_is_directory=True)
        fixture.environment["SDKROOT"] = str(expected_sdk)
        # Compatible explicit compiler choices remain byte-for-byte intact.
        fixture.environment["CC"] = f'custom-clang -isysroot "{fixture.newer_sdk}"'
        fixture.environment["LDFLAGS"] = f'-Wl,-syslibroot,"{fixture.newer_sdk}"'
    original = {
        key: fixture.environment.get(key)
        for key in ("SDKROOT", "DEVELOPER_DIR", "CC", "CFLAGS", "LDFLAGS")
    }
    completed = _run_installer(fixture, diagnostics=diagnostics)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "Safe synthetic enrollment preview\n"
    build = json.loads(Path(fixture.environment["FIXTURE_BUILD_ENV"]).read_text())
    runtime = json.loads(Path(fixture.environment["FIXTURE_RUNTIME_ENV"]).read_text())
    assert build["SDKROOT"] == str(expected_sdk)
    assert build["DEVELOPER_DIR"] == expected_developer
    assert build["CC"] == original["CC"]
    assert build["LDFLAGS"] == original["LDFLAGS"]
    assert runtime == original
    assert not Path(fixture.environment["FIXTURE_XCRUN_MARKER"]).exists()
    assert fixture.receipt.read_text().strip() == COMMIT


@pytest.mark.parametrize(
    "failure",
    [
        "missing_explicit_sdk",
        "empty_explicit_sdk",
        "missing_alias",
        "external_alias",
        "invalid_metadata",
        "symlink_metadata",
        "unsafe_sdk",
        "compiler_sysroot",
        "sysconfig_sysroot",
        "linker_syslibroot",
        "split_linker_syslibroot",
        "xlinker_syslibroot",
    ],
)
def test_native_sdk_setup_failure_cannot_complete_or_enter_enrollment(
    tmp_path: Path, failure: str
) -> None:
    fixture = _installer_fixture(tmp_path)
    metadata = fixture.sdk / "SDKSettings.json"
    alias = fixture.developer / "SDKs/MacOSX.sdk"
    if failure == "missing_explicit_sdk":
        fixture.environment["SDKROOT"] = str(tmp_path / "missing")
    elif failure == "empty_explicit_sdk":
        fixture.environment["SDKROOT"] = ""
    elif failure == "missing_alias":
        alias.unlink()
    elif failure == "external_alias":
        alias.unlink()
        alias.symlink_to(_write_sdk(tmp_path / "outside", "26.5", alias=False))
    elif failure == "invalid_metadata":
        metadata.write_text(
            json.dumps({"Version": "26.5", "CanonicalName": "iphoneos26.5"})
        )
    elif failure == "symlink_metadata":
        retained = fixture.sdk / "retained-metadata.json"
        metadata.rename(retained)
        metadata.symlink_to(retained)
    elif failure == "unsafe_sdk":
        fixture.sdk.chmod(0o777)
    elif failure == "compiler_sysroot":
        fixture.environment["CFLAGS"] = f'-isysroot "{fixture.newer_sdk}"'
    elif failure == "sysconfig_sysroot":
        fixture.environment["FIXTURE_SYSCONFIG"] = json.dumps(
            {"CFLAGS": f'-isysroot "{fixture.newer_sdk}"'}
        )
    elif failure == "linker_syslibroot":
        fixture.environment["LDFLAGS"] = f'-Wl,-syslibroot,"{fixture.newer_sdk}"'
    elif failure == "split_linker_syslibroot":
        fixture.environment["LDFLAGS"] = f'-Wl,-syslibroot -Wl,"{fixture.newer_sdk}"'
    else:
        fixture.environment["LDSHARED"] = (
            f'clang -Xlinker -syslibroot -Xlinker "{fixture.newer_sdk}"'
        )
    completed = _run_installer(fixture)
    assert completed.returncode == 78
    assert completed.stdout == completed.stderr == ""
    assert not fixture.receipt.exists()
    assert not Path(fixture.environment["FIXTURE_BUILD_ENV"]).exists()
    assert not Path(fixture.environment["FIXTURE_RUNTIME_ENV"]).exists()
    assert fixture.phases.read_text().splitlines()[-1] == "native_build_setup"
    diagnostic = fixture.log.read_text()
    assert "native build SDK setup failed" in diagnostic
    assert str(tmp_path) not in diagnostic
    assert CODE not in diagnostic


@pytest.mark.parametrize("diagnostics", [False, True])
def test_completed_install_does_not_require_or_change_native_build_configuration(
    tmp_path: Path, diagnostics: bool
) -> None:
    fixture = _installer_fixture(tmp_path)
    fixture.receipt.write_text(COMMIT)
    fixture.environment["SDKROOT"] = "unavailable-explicit-sdk"
    fixture.environment["DEVELOPER_DIR"] = "unavailable-explicit-developer"
    completed = _run_installer(fixture, diagnostics=diagnostics)
    assert completed.returncode == 0, completed.stderr
    assert not Path(fixture.environment["FIXTURE_BUILD_ENV"]).exists()
    runtime = json.loads(Path(fixture.environment["FIXTURE_RUNTIME_ENV"]).read_text())
    assert runtime["SDKROOT"] == "unavailable-explicit-sdk"
    assert runtime["DEVELOPER_DIR"] == "unavailable-explicit-developer"
    if diagnostics:
        assert "native_build_setup" not in fixture.phases.read_text()


def test_first_run_prepares_before_asking_for_a_secret(launcher) -> None:
    executable, environment, _ = launcher
    environment["FIXTURE_MANAGE_ACTION"] = "Prepare This Mac"
    completed, attempts = _run(launcher, outcome="success", manage=True)
    assert completed.returncode == 0
    receipt = json.loads((attempts[0] / "receipt.json").read_text())
    assert receipt["phase"] == "prepared"
    assert receipt["operation"] == "prepare"
    ui = (Path(environment["HOME"]) / "ui.jsonl").read_text()
    assert "prepared.applescript" in ui
    assert "secure-code.applescript" not in ui
    assert "complete.applescript" not in ui


def test_early_handoff_finishes_preparation_without_using_code(launcher) -> None:
    home = Path(launcher[1]["HOME"])
    receipt = (
        home
        / "Library/Application Support/AdministrateMe Bridge/app/releases"
        / COMMIT
        / ".venv/.administrateme-bridge-installed-commit"
    )
    receipt.unlink()
    completed, attempts = _run(launcher, outcome="success")
    assert completed.returncode == 0
    assert json.loads((attempts[0] / "receipt.json").read_text())["phase"] == "prepared"
    ui = (home / "ui.jsonl").read_text()
    assert "secure-code.applescript" not in ui
    assert "prepared.applescript" in ui


def test_first_preparation_failure_is_resumable_without_pairing(launcher) -> None:
    launcher[1]["FIXTURE_MANAGE_ACTION"] = "Prepare This Mac"
    failed, first = _run(launcher, outcome="dependency_fail", manage=True)
    assert failed.returncode == 23
    succeeded, attempts = _run(launcher, outcome="success", manage=True)
    assert succeeded.returncode == 0
    assert len(attempts) == 2
    assert json.loads((first[0] / "receipt.json").read_text())["outcome"] == "failed"
    assert (
        "secure-code.applescript"
        not in (Path(launcher[1]["HOME"]) / "ui.jsonl").read_text()
    )
