"""Hosted-only launcher processes with synthetic installer and native UI seams."""

from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from bootstrap.member_bridge_helper_app import _helper_launcher


COMMIT = "a" * 40
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
    # Replace only the unavailable native UI boundary in the generated fixture;
    # the shipped helper has no environment variable or arbitrary UI override.
    script = (
        _helper_launcher(COMMIT, "b" * 64)
        .decode()
        .replace("/usr/bin/osascript", f'"{native_ui}"')
    )
    launcher_path = resources / "bridge-helper-launcher.sh"
    _executable(launcher_path, script)
    _executable(
        resources / "install-member-bridge.sh",
        """#!/bin/bash
set -Eeuo pipefail
preview=false
for argument in "$@"; do
  [[ "$argument" != "--preview-only" ]] || preview=true
done
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
    assert receipt["operation"] == "enroll"
    assert receipt["dependency_log_limit_bytes"] == 262144
    assert (attempt / "dependency-install.log").read_text() == (
        "synthetic dependency installation detail\n"
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
    release = (
        Path(environment["HOME"])
        / "Library/Application Support/AdministrateMe Bridge/app/releases"
        / COMMIT
    )
    binary = release / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    _executable(
        binary,
        "#!/bin/bash\n"
        '[[ "$PWD" == "$ADMINME_REPOSITORY_ROOT" ]] || exit 99\n'
        'printf "fixture-private-post-preview-output\\n" >&2\n'
        + ("exit 57\n" if fails else "exit 0\n"),
    )
    completed, attempts = _run(launcher, outcome="manage", manage=True)
    assert completed.returncode == (57 if fails else 0)
    assert len(attempts) == 1
    receipt = json.loads((attempts[0] / "receipt.json").read_text())
    assert receipt["phase"] == (
        "continue_enrollment" if action == "Continue Enrollment" else "uninstall"
    )
    assert receipt["operation"] == (
        "continue" if action == "Continue Enrollment" else "uninstall"
    )
    assert receipt["outcome"] == ("failed" if fails else "succeeded")
    assert (attempts[0] / "dependency-install.log").read_bytes() == b""


def test_manage_missing_release_records_failure_without_attempting_installation(
    launcher: tuple[Path, dict[str, str], Path],
) -> None:
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


def test_actual_installer_drains_large_dependency_failure_and_resumes_privately(
    tmp_path: Path,
) -> None:
    """Exercise actual shell/collector; only OS prerequisites and tooling are fake."""
    tools = tmp_path / "fixture-tools"
    tools.mkdir()
    prefix = tmp_path / "python-prefix"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python3.14").symlink_to(sys.executable)
    for name, script in {
        "uname": 'if [[ "$1" == -s ]]; then echo Darwin; else echo arm64; fi',
        "id": "echo 501",
        "fdesetup": "echo 'FileVault is On.'",
        "tailscale": "exit 0",
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
        """import os
import sys

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
        + """import os

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
        if key not in {"BASH_ENV", "ENV", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment.update(
        {
            "PATH": f"{tools}:{os.defpath}",
            "FIXTURE_PYTHON_PREFIX": str(prefix),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    receipt = release / ".venv/.administrateme-bridge-installed-commit"
    for exit_code in (23, 0):
        completed = subprocess.run(
            [
                "/bin/bash",
                "-c",
                'exec 3>"$1"; exec 4<>"$2"; shift 2; ADMINME_BRIDGE_DIAGNOSTICS_FD=3 "$@"',
                "fixture-descriptors",
                str(phase_file),
                str(log_file),
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
            ],
            env={**environment, "FIXTURE_INSTALL_EXIT": str(exit_code)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == exit_code, completed.stderr
        dependency_log = log_file.read_bytes()
        assert len(dependency_log) == 262144
        assert dependency_log.endswith(b"last-dependency-marker\n")
        assert b"early-dependency-marker" not in dependency_log
        assert CODE.encode() not in dependency_log
        assert POST_PREVIEW.encode() not in dependency_log
        assert b"private-preview-output-must-not-be-persisted" not in dependency_log
        if exit_code:
            assert completed.stdout == completed.stderr == ""
            assert not receipt.exists()
            assert phase_file.read_text().splitlines()[-1] == "dependency_install"
        else:
            assert completed.stdout == "Safe synthetic enrollment preview\n"
            assert completed.stderr == f"{POST_PREVIEW}\n"
            assert receipt.read_text().strip() == COMMIT
            assert phase_file.read_text().splitlines()[-1] == "package_preview"
