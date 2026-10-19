"""Source-tree identity and release-materialization safety tests."""

from __future__ import annotations

import io
import fcntl
import os
import json
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import bootstrap.source_identity as source_identity
import bootstrap.update_controller_guardian as update_controller_guardian
import bootstrap.update_supervisor as update_supervisor
import bootstrap.upgrade as upgrade_module
import bootstrap.reviewed_launcher as reviewed_launcher
from bootstrap.source_identity import (
    GENERATED_EXTRA_POLICY,
    LEGACY_TREE_METHOD,
    SOURCE_MARKER_METHOD,
    SourceIdentityError,
    SourceProcessNotReaped,
    materialize_git_tree,
    verify_release_source_identity,
    write_source_marker,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.deployment_budget import (
    CONTROLLER_SECONDS,
    DEADLINE_ENV,
    START_ENV,
    DeploymentBudget,
    DeploymentBudgetError,
    deployment_clock,
)
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.unit.bootstrap.intake_fixture import stub_uninitialized_intake


REPO_ROOT = Path(__file__).resolve().parents[3]


def _private_acceptance_python(root: Path) -> Path:
    runtime = root / ".acceptance-runtime"
    bin_dir = runtime / "bin"
    bin_dir.mkdir(mode=0o700, parents=True)
    launcher = bin_dir / "python"
    launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    return launcher


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "--all")
    return _git(
        repository,
        "-c",
        "user.name=Source Identity Fixture",
        "-c",
        "user.email=source-identity-fixture.invalid",
        "commit",
        "-qm",
        message,
    ) or _git(repository, "rev-parse", "HEAD")


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "-q")
    (repository / "adminme").mkdir(mode=0o700)
    (repository / "adminme" / "sample.py").write_text(
        "VALUE = 'tracked'\n", encoding="utf-8"
    )
    (repository / "scripts").mkdir(mode=0o700)
    executable = repository / "scripts" / "run"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    first = _commit(repository, "first")
    (repository / "later.txt").write_text("later\n", encoding="utf-8")
    second = _commit(repository, "second")
    return repository, first, second


def _upgrade_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "upgrade-repository"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "-q")
    for relative in (
        "poetry.lock",
        "bootstrap/versions.json",
        "bootstrap/preparation_worker.py",
        "bootstrap/update_controller_guardian.py",
        "bootstrap/update_supervisor.py",
        "openclaw/administrateme/package.json",
        "openclaw/administrateme/openclaw.plugin.json",
    ):
        destination = repository / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / relative).read_bytes())
        destination.chmod(0o600)
    (repository / "bootstrap" / "prep_sample.py").write_text(
        "VALUE = 'tracked'\n",
        encoding="utf-8",
    )
    commit = _commit(repository, "candidate")
    (repository / ".env").write_text("must-not-copy\n", encoding="utf-8")
    return repository, commit


def _supervisor_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "supervisor-repository"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "-q")
    bootstrap = repository / "bootstrap"
    bootstrap.mkdir(mode=0o700)
    shutil.copy2(REPO_ROOT / "bootstrap" / "update_supervisor.py", bootstrap)
    shutil.copy2(REPO_ROOT / "bootstrap" / "update_controller_guardian.py", bootstrap)
    updater = bootstrap / "update.sh"
    updater.write_text(
        "#!/bin/bash -p\n"
        "set -Eeuo pipefail\n"
        '[[ "${ADMINME_UPDATE_SUPERVISED:-}" == 1 ]]\n'
        '[[ -n "${ADMINME_FIXED_CONTROLLER_ROOT:-}" ]]\n'
        'if [[ "${1:-}" == "--hold" ]]; then\n'
        '  printf \'%s %s\\n\' "$PPID" "$$" >"$ADMINME_SUPERVISOR_REPORT"\n'
        "  exec sleep 300\n"
        "fi\n"
        'printf \'%s\\n\' "$ADMINME_FIXED_CONTROLLER_ROOT" >"$ADMINME_SUPERVISOR_REPORT"\n',
        encoding="utf-8",
    )
    updater.chmod(0o755)
    bootstrap_installer = bootstrap / "bootstrap-macos.sh"
    bootstrap_installer.write_text(
        "#!/bin/bash -p\n"
        "set -Eeuo pipefail\n"
        '[[ "${ADMINME_BOOTSTRAP_SUPERVISED:-}" == 1 ]]\n'
        '[[ -n "${ADMINME_FIXED_CONTROLLER_ROOT:-}" ]]\n'
        'printf \'%s\\n\' "$ADMINME_FIXED_CONTROLLER_ROOT" >"$ADMINME_SUPERVISOR_REPORT"\n',
        encoding="utf-8",
    )
    bootstrap_installer.chmod(0o755)
    (bootstrap / "upgrade.py").write_text("FIXED = True\n", encoding="utf-8")
    (bootstrap / "cli.py").write_text("FIXED = True\n", encoding="utf-8")
    return repository, _commit(repository, "fixed supervisor")


def _supervisor_instance(tmp_path: Path) -> tuple[DeploymentPaths, str]:
    paths = DeploymentPaths.from_root(tmp_path / "supervisor-instance")
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    commit = "a" * 40
    release = paths.app_dir / "releases" / commit
    release.mkdir(mode=0o700, parents=True)
    _write_release_pointer(paths, {"commit": commit, "path": str(release)})
    return paths, commit


def _run_fixed_supervisor(
    repository: Path,
    commit: str,
    paths: DeploymentPaths,
    report: Path,
    *controller_arguments: str,
    instance_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    source = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "show",
            f"{commit}:bootstrap/update_supervisor.py",
        ],
        check=True,
        capture_output=True,
    ).stdout
    environment = os.environ.copy()
    environment["ADMINME_SUPERVISOR_REPORT"] = str(report)
    environment["ADMINME_ACCEPTANCE_PYTHON"] = str(
        _private_acceptance_python(report.parent)
    )
    return subprocess.run(
        [
            "/usr/bin/python3",
            "-I",
            "-",
            str(instance_root or paths.root),
            str(repository),
            commit,
            *(controller_arguments or (str(repository),)),
        ],
        input=source.decode("utf-8"),
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_supervisor_accepts_safe_virtualenv_link_to_system_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_home = tmp_path / "private-home"
    private_home.mkdir(mode=0o700)
    virtualenv = tmp_path / "acceptance-venv"
    bin_dir = virtualenv / "bin"
    bin_dir.mkdir(mode=0o700, parents=True)
    acceptance_python = bin_dir / "python"
    acceptance_python.symlink_to(Path("/usr/bin/python3"))
    monkeypatch.setenv("ADMINME_ACCEPTANCE_PYTHON", str(acceptance_python))

    environment = update_supervisor._controller_environment(  # noqa: SLF001
        private_home,
        test_mode=True,
    )

    assert environment["ADMINME_ACCEPTANCE_PYTHON"] == str(acceptance_python)
    assert environment["ADMINME_TEST_MODE"] == "1"


def test_supervisor_accepts_safe_root_owned_system_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_home = tmp_path / "private-home"
    private_home.mkdir(mode=0o700)
    acceptance_python = Path("/usr/bin/python3")
    monkeypatch.setenv("ADMINME_ACCEPTANCE_PYTHON", str(acceptance_python))

    environment = update_supervisor._controller_environment(  # noqa: SLF001
        private_home,
        test_mode=True,
    )

    assert environment["ADMINME_ACCEPTANCE_PYTHON"] == str(acceptance_python)
    assert environment["ADMINME_TEST_MODE"] == "1"


def test_supervisor_discards_ambient_deadline_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_home = tmp_path / "private-home"
    private_home.mkdir(mode=0o700)
    monkeypatch.setenv(START_ENV, "nan")
    monkeypatch.setenv(DEADLINE_ENV, "inf")
    environment = update_supervisor._controller_environment(  # noqa: SLF001
        private_home, test_mode=False
    )
    assert START_ENV not in environment
    assert DEADLINE_ENV not in environment
    assert CONTROLLER_SECONDS == update_supervisor.CONTROLLER_TIMEOUT_SECONDS
    assert CONTROLLER_SECONDS == update_controller_guardian.CONTROLLER_TIMEOUT_SECONDS
    assert CONTROLLER_SECONDS == 13 * 60 * 60


def test_deployment_clocks_share_explicit_os_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_clocks: list[int] = []

    def os_clock(clock_id: int) -> float:
        requested_clocks.append(clock_id)
        return 200_000.0

    def reject_process_clock() -> float:
        pytest.fail("inherited deadlines cannot use process-local clock origins")

    monkeypatch.setattr(time, "clock_gettime", os_clock)
    monkeypatch.setattr(time, "monotonic", reject_process_clock)
    assert deployment_clock() == 200_000.0
    assert update_supervisor._deployment_clock() == 200_000.0  # noqa: SLF001
    assert update_controller_guardian._deployment_clock() == 200_000.0  # noqa: SLF001
    assert requested_clocks == [time.CLOCK_MONOTONIC] * 3


@pytest.mark.parametrize(
    ("start", "deadline"),
    [
        (None, None),
        ("100", None),
        (None, "46900"),
        ("nan", "46900"),
        ("100", "inf"),
        ("-100", "46700"),
        ("201", "47001"),
        ("100", "46901"),
        ("100", "200"),
        ("invalid", "46900"),
    ],
)
def test_controller_deadline_rejects_missing_or_untrusted_budget(
    monkeypatch: pytest.MonkeyPatch, start: str | None, deadline: str | None
) -> None:
    environment: dict[str, str] = {}
    monkeypatch.setenv("ADMINME_UPDATE_SUPERVISED", "1")
    for name, value in ((START_ENV, start), (DEADLINE_ENV, deadline)):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            environment[name] = value
            monkeypatch.setenv(name, value)
    with pytest.raises(DeploymentBudgetError):
        DeploymentBudget.from_environment(environment, now=200, supervised=True)
    with pytest.raises(SystemExit):
        update_controller_guardian._controller_deadline(  # noqa: SLF001
            CONTROLLER_SECONDS, now=200
        )


def test_guardian_and_controller_share_deadline_after_delayed_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {START_ENV: "100", DEADLINE_ENV: str(100 + CONTROLLER_SECONDS)}
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ADMINME_UPDATE_SUPERVISED", "1")
    inherited = update_controller_guardian._controller_deadline(  # noqa: SLF001
        CONTROLLER_SECONDS, now=1000
    )
    budget = DeploymentBudget.from_environment(environment, now=1200, supervised=True)
    assert inherited == budget.deadline == 100 + CONTROLLER_SECONDS
    assert inherited < 1000 + CONTROLLER_SECONDS
    with pytest.raises(DeploymentBudgetError, match="untrusted"):
        DeploymentBudget.from_environment(environment, now=1200, supervised=False)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 46801])
def test_guardian_timeout_cannot_exceed_reviewed_ceiling(timeout: float) -> None:
    with pytest.raises(SystemExit):
        update_controller_guardian._controller_deadline(timeout, now=100)  # noqa: SLF001


def test_supervisor_rejects_writable_test_interpreter_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_home = tmp_path / "private-home"
    private_home.mkdir(mode=0o700)
    virtualenv = tmp_path / "acceptance-venv"
    bin_dir = virtualenv / "bin"
    bin_dir.mkdir(mode=0o700, parents=True)
    writable_target = tmp_path / "writable-python"
    writable_target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    writable_target.chmod(0o777)
    acceptance_python = bin_dir / "python"
    acceptance_python.symlink_to(writable_target)
    monkeypatch.setenv("ADMINME_ACCEPTANCE_PYTHON", str(acceptance_python))

    with pytest.raises(update_supervisor.SupervisorError, match="unsafe"):
        update_supervisor._controller_environment(  # noqa: SLF001
            private_home,
            test_mode=True,
        )


def test_fixed_blob_supervisor_owns_lock_controller_and_cleanup(tmp_path: Path) -> None:
    repository, commit = _supervisor_repository(tmp_path)
    paths, _active_commit = _supervisor_instance(tmp_path)
    report = tmp_path / "supervisor-report"

    completed = _run_fixed_supervisor(repository, commit, paths, report)

    assert completed.returncode == 0, completed.stderr
    controller_root = Path(report.read_text("utf-8").strip())
    assert controller_root.name == "source"
    assert not controller_root.parent.exists()
    lock = paths.app_dir / ".upgrade-operation.lock"
    assert lock.is_file() and not lock.is_symlink()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert not list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))


def test_fixed_supervisor_deadline_survives_production_shell_cli_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _old_commit = _supervisor_repository(tmp_path)
    bootstrap = repository / "bootstrap"
    # A real package keeps an installed editable checkout from replacing this
    # fixture's CLI and deadline module during the production -m handoff.
    (bootstrap / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(REPO_ROOT / "bootstrap" / "common.sh", bootstrap)
    shutil.copy2(REPO_ROOT / "bootstrap" / "deployment_budget.py", bootstrap)
    # Execute the exact production final shell-to-CLI block, after the real
    # common.sh environment setup. Preparation dependencies remain synthetic.
    production_shell = (REPO_ROOT / "bootstrap" / "update.sh").read_text("utf-8")
    handoff = production_shell[
        production_shell.rindex('(\n  cd "$fixed_controller_root"') :
    ]
    (bootstrap / "update.sh").write_text(
        "#!/bin/bash -p\nset -Eeuo pipefail\n"
        'fixed_controller_root="$ADMINME_FIXED_CONTROLLER_ROOT"\n'
        'source "$fixed_controller_root/bootstrap/common.sh"\n'
        "resolve_instance_root\n"
        'supervised_commit="$ADMINME_SUPERVISED_CANDIDATE_COMMIT"\n'
        'controller="${ADMINME_ACCEPTANCE_PYTHON%/bin/python}"\n'
        'node_bin_dir="/usr/bin"\npoetry_venv="$controller"\n'
        "upgrade_args=(deadline-proof)\n" + handoff,
        encoding="utf-8",
    )
    (bootstrap / "cli.py").write_text(
        "import json, os, time\nfrom pathlib import Path\n"
        "import bootstrap.deployment_budget as budget_module\n"
        "from bootstrap.deployment_budget import DeploymentBudget, START_ENV, DEADLINE_ENV\n"
        "now = time.clock_gettime(time.CLOCK_MONOTONIC)\n"
        "budget = DeploymentBudget.from_environment(os.environ, now=now, supervised=True)\n"
        "report = {name: os.environ[name] for name in (START_ENV, DEADLINE_ENV, "
        "'ADMINME_UPGRADE_CONTROLLER_COMMIT', 'ADMINME_UPGRADE_CONTROLLER_ROOT')}\n"
        "report.update(now=now, deadline=budget.deadline, started=budget.started_at)\n"
        "report.update(cli_file=__file__, budget_file=budget_module.__file__)\n"
        "Path(os.environ['ADMINME_SUPERVISOR_REPORT']).write_text(json.dumps(report))\n",
        encoding="utf-8",
    )
    commit = _commit(repository, "production deadline handoff")
    paths, _active_commit = _supervisor_instance(tmp_path)
    report = tmp_path / "deadline-report"
    monkeypatch.setenv(START_ENV, "nan")
    monkeypatch.setenv(DEADLINE_ENV, "inf")

    before_launch = time.clock_gettime(time.CLOCK_MONOTONIC)
    completed = _run_fixed_supervisor(
        repository, commit, paths, report, "--test-mode", str(repository)
    )
    after_exit = time.clock_gettime(time.CLOCK_MONOTONIC)

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(report.read_text("utf-8"))
    assert before_launch <= observed["started"] <= observed["now"] <= after_exit
    assert observed["started"] <= observed["now"] < observed["deadline"]
    assert observed["deadline"] - observed["started"] == CONTROLLER_SECONDS
    assert observed["ADMINME_UPGRADE_CONTROLLER_COMMIT"] == commit
    assert Path(observed["ADMINME_UPGRADE_CONTROLLER_ROOT"]).name == "source"
    controller_root = Path(observed["ADMINME_UPGRADE_CONTROLLER_ROOT"])
    assert Path(observed["cli_file"]).resolve() == controller_root / "bootstrap/cli.py"
    assert Path(observed["budget_file"]).resolve() == (
        controller_root / "bootstrap/deployment_budget.py"
    )
    assert float(observed[DEADLINE_ENV]) == observed["deadline"]
    assert float(observed[START_ENV]) == observed["started"]


def test_fixed_blob_supervisor_bootstrap_creates_private_instance_and_cleans(
    tmp_path: Path,
) -> None:
    repository, commit = _supervisor_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "fresh-supervised-instance")
    report = tmp_path / "bootstrap-supervisor-report"

    completed = _run_fixed_supervisor(
        repository,
        commit,
        paths,
        report,
        "--bootstrap",
    )

    assert completed.returncode == 0, completed.stderr
    controller_root = Path(report.read_text("utf-8").strip())
    assert controller_root.name == "source"
    assert not controller_root.parent.exists()
    assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.app_dir.stat().st_mode) == 0o700
    assert not list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))


def test_supervisor_preserves_controller_and_lock_when_guardian_is_killed(
    tmp_path: Path,
) -> None:
    repository, commit = _supervisor_repository(tmp_path)
    paths, _active_commit = _supervisor_instance(tmp_path)
    report = tmp_path / "held-controller-report"
    source = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "show",
            f"{commit}:bootstrap/update_supervisor.py",
        ],
        check=True,
        capture_output=True,
    ).stdout
    environment = os.environ.copy()
    environment["ADMINME_SUPERVISOR_REPORT"] = str(report)
    supervisor_error_path = tmp_path / "supervisor-error"
    supervisor_error = supervisor_error_path.open("wb")
    supervisor = subprocess.Popen(
        [
            "/usr/bin/python3",
            "-I",
            "-",
            str(paths.root),
            str(repository),
            commit,
            "--hold",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=supervisor_error,
        env=environment,
        text=False,
        start_new_session=True,
    )
    assert supervisor.stdin is not None
    supervisor.stdin.write(source)
    supervisor.stdin.close()
    deadline = time.monotonic() + 15
    payload = ""
    while time.monotonic() < deadline:
        if supervisor.poll() is not None:
            supervisor_error.close()
            pytest.fail(
                "supervisor exited before hold: "
                f"{supervisor_error_path.read_text('utf-8', errors='replace')}"
            )
        try:
            payload = report.read_text("ascii").strip()
        except FileNotFoundError:
            payload = ""
        if payload:
            break
        time.sleep(0.02)
    assert payload
    guardian_pid, controller_pid = (int(value) for value in payload.split())
    os.kill(guardian_pid, signal.SIGKILL)
    assert supervisor.wait(timeout=10) == 1
    supervisor_error.close()
    assert b"terminality is unknown" in supervisor_error_path.read_bytes()
    workspaces = list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))
    assert len(workspaces) == 1

    competing = _run_fixed_supervisor(repository, commit, paths, tmp_path / "other")
    assert competing.returncode == 1
    assert "already running" in competing.stderr

    os.killpg(controller_pid, signal.SIGKILL)
    lock = os.open(paths.app_dir / ".upgrade-operation.lock", os.O_RDWR)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    pytest.fail(
                        "controller did not release the inherited operation lock"
                    )
                time.sleep(0.02)
    finally:
        os.close(lock)


def test_update_controller_guardian_times_out_and_publishes_terminality(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".upgrade-operation.lock"
    lock_path.write_text("\n", encoding="ascii")
    lock_path.chmod(0o600)
    lock = os.open(lock_path, os.O_RDWR)
    lifecycle_path = tmp_path / ".controller-lifecycle.lock"
    lifecycle_path.write_text("\n", encoding="ascii")
    lifecycle_path.chmod(0o600)
    lifecycle = lifecycle_path.lstat()
    ready_read, ready_write = os.pipe()
    supervisor_read, supervisor_write = os.pipe()
    terminal_read, terminal_write = os.pipe()
    delayed = tmp_path / "delayed-controller-write"
    command = (
        "import sys, time; from pathlib import Path; "
        "time.sleep(1); Path(sys.argv[1]).write_text('unsafe', encoding='utf-8')"
    )
    guardian = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "bootstrap" / "update_controller_guardian.py"),
            str(ready_write),
            str(supervisor_read),
            str(terminal_write),
            str(lock),
            str(lifecycle_path),
            str(lifecycle.st_dev),
            str(lifecycle.st_ino),
            "0.2",
            "--",
            sys.executable,
            "-c",
            command,
            str(delayed),
        ],
        pass_fds=(ready_write, supervisor_read, terminal_write, lock),
        start_new_session=True,
    )
    os.close(ready_write)
    os.close(supervisor_read)
    os.close(terminal_write)
    try:
        assert os.read(ready_read, 1) == b"1"
        assert guardian.wait(timeout=10) == 75
        assert os.read(terminal_read, 2) == b"1"
        time.sleep(1.1)
        assert not delayed.exists()
    finally:
        os.close(ready_read)
        os.close(supervisor_write)
        os.close(terminal_read)
        os.close(lock)


def test_controller_lifecycle_exclusive_proof_waits_for_independent_descendant(
    tmp_path: Path,
) -> None:
    lifecycle = tmp_path / ".controller-lifecycle.lock"
    lifecycle.write_text("\n", encoding="ascii")
    lifecycle.chmod(0o600)
    guardian_descriptor = os.open(lifecycle, os.O_RDWR)
    child_descriptor = os.open(lifecycle, os.O_RDWR)
    fcntl.flock(guardian_descriptor, fcntl.LOCK_SH)
    fcntl.flock(child_descriptor, fcntl.LOCK_SH)
    ready_read, ready_write = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - assertions run in the parent
        try:
            os.close(guardian_descriptor)
            os.close(ready_read)
            os.setsid()
            os.write(ready_write, b"1")
            os.close(ready_write)
            time.sleep(300)
        finally:
            os._exit(0)
    os.close(child_descriptor)
    os.close(ready_write)
    try:
        assert os.read(ready_read, 1) == b"1"
        assert not update_controller_guardian._prove_lifecycle_exclusive(
            guardian_descriptor,
            timeout=0.2,
        )
        os.kill(child, signal.SIGKILL)
        os.waitpid(child, 0)
        assert update_controller_guardian._prove_lifecycle_exclusive(
            guardian_descriptor,
            timeout=1,
        )
    finally:
        os.close(ready_read)
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass
        os.close(guardian_descriptor)


def test_install_entry_ignores_shell_startup_and_ambient_path(tmp_path: Path) -> None:
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir(mode=0o700)
    install = bootstrap / "install.sh"
    install.write_bytes((REPO_ROOT / "bootstrap" / "install.sh").read_bytes())
    install.chmod(0o700)
    report = tmp_path / "install-report"
    target = bootstrap / "bootstrap-macos.sh"
    target.write_text(
        "#!/bin/bash -p\n"
        "set -Eeuo pipefail\n"
        '[[ -z "${BASH_ENV:-}" && -z "${PYTHONPATH:-}" ]]\n'
        f"printf safe > {report}\n",
        encoding="utf-8",
    )
    target.chmod(0o700)
    bash_canary = tmp_path / "bash-startup-ran"
    bash_environment = tmp_path / "bash-environment"
    bash_environment.write_text(
        f"printf unsafe > {bash_canary}\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(mode=0o700)
    path_canary = tmp_path / "ambient-path-ran"
    fake_dirname = fake_bin / "dirname"
    fake_dirname.write_text(
        f"#!/bin/sh\nprintf unsafe > {path_canary}\nexit 1\n",
        encoding="utf-8",
    )
    fake_dirname.chmod(0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "BASH_ENV": str(bash_environment),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "PYTHONPATH": str(tmp_path / "ambient-python"),
        }
    )

    completed = subprocess.run(
        [str(install)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert report.read_text("ascii") == "safe"
    assert not bash_canary.exists()
    assert not path_canary.exists()


def test_fixed_blob_supervisor_rejects_held_lock_before_controller(
    tmp_path: Path,
) -> None:
    repository, commit = _supervisor_repository(tmp_path)
    paths, _active_commit = _supervisor_instance(tmp_path)
    report = tmp_path / "supervisor-report"
    lock_path = paths.app_dir / ".upgrade-operation.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = _run_fixed_supervisor(repository, commit, paths, report)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert completed.returncode == 1
    assert "already running" in completed.stderr
    assert not report.exists()
    assert not (paths.app_dir / "tooling").exists()


def test_fixed_blob_supervisor_rejects_unsafe_parent_without_writing_lock(
    tmp_path: Path,
) -> None:
    repository, commit = _supervisor_repository(tmp_path)
    paths, _active_commit = _supervisor_instance(tmp_path)
    paths.app_dir.chmod(0o755)
    report = tmp_path / "supervisor-report"

    completed = _run_fixed_supervisor(repository, commit, paths, report)

    assert completed.returncode == 1
    assert "unsafe" in completed.stderr
    assert not report.exists()
    assert not (paths.app_dir / ".upgrade-operation.lock").exists()


def test_fixed_blob_supervisor_rejects_terminal_instance_symlink_without_writes(
    tmp_path: Path,
) -> None:
    repository, commit = _supervisor_repository(tmp_path)
    paths, _active_commit = _supervisor_instance(tmp_path)
    instance_link = tmp_path / "instance-link"
    instance_link.symlink_to(paths.root, target_is_directory=True)
    report = tmp_path / "supervisor-report"
    lock = paths.app_dir / ".upgrade-operation.lock"
    lock.unlink(missing_ok=True)

    completed = _run_fixed_supervisor(
        repository,
        commit,
        paths,
        report,
        instance_root=instance_link,
    )

    assert completed.returncode == 1
    assert "instance root is unsafe" in completed.stderr
    assert not report.exists()
    assert not lock.exists()
    assert not (paths.app_dir / "tooling").exists()


def test_supervisor_rejects_home_and_filesystem_root_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "poisoned-home"))
    account_home = Path(update_supervisor.pwd.getpwuid(os.getuid()).pw_dir)
    for broad_root in (account_home, Path(account_home.anchor)):
        with pytest.raises(update_supervisor.SupervisorError, match="too broad"):
            update_supervisor._physical_instance_root(broad_root)  # noqa: SLF001


def test_supervisor_terminal_instance_swap_remains_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = tmp_path / "instance"
    requested.mkdir(mode=0o700)
    original = tmp_path / "instance-original"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    real_lstat = Path.lstat
    swapped = False

    def swap_after_lstat(path: Path) -> os.stat_result:
        nonlocal swapped
        metadata = real_lstat(path)
        if path == requested and not swapped:
            requested.rename(original)
            requested.symlink_to(outside, target_is_directory=True)
            swapped = True
        return metadata

    monkeypatch.setattr(Path, "lstat", swap_after_lstat)
    physical = update_supervisor._physical_instance_root(requested)  # noqa: SLF001

    assert physical == requested
    with pytest.raises(update_supervisor.SupervisorError, match="unsafe"):
        update_supervisor._open_private_directory(physical)  # noqa: SLF001
    assert outside.is_dir()


def test_supervisor_rejects_parent_alias_of_account_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_home = tmp_path / "account-home"
    child = account_home / "existing-private-child"
    child.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(
        update_supervisor.pwd,
        "getpwuid",
        lambda _uid: SimpleNamespace(pw_dir=str(account_home)),
    )
    requested = child / ".."

    with pytest.raises(update_supervisor.SupervisorError, match="too broad"):
        update_supervisor._physical_instance_root(requested)  # noqa: SLF001

    assert not (account_home / "app").exists()


def _write_release_pointer(
    paths: DeploymentPaths,
    document: dict[str, object],
) -> Path:
    pointer = paths.app_dir / "current-release.json"
    pointer.write_text(json.dumps(document), encoding="utf-8")
    pointer.chmod(0o600)
    return pointer


def _archive_commit(repository: Path, commit: str, destination: Path) -> None:
    payload = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repository), "archive", commit],
        check=True,
        capture_output=True,
    ).stdout
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        archive.extractall(destination, filter="data")


def _legacy_release(tmp_path: Path) -> tuple[Path, Path, str]:
    repository, predecessor, _head = _repository(tmp_path)
    release = tmp_path / "release"
    _archive_commit(repository, predecessor, release)
    release.chmod(0o700)
    for directory in release.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o700)
        elif directory.is_file():
            directory.chmod(0o700 if os.access(directory, os.X_OK) else 0o600)
    (release / ".venv").mkdir(mode=0o700)
    (release / ".venv" / "runtime-link").symlink_to("bin/python")
    node_modules = release / "openclaw" / "administrateme" / "node_modules"
    node_modules.mkdir(mode=0o700, parents=True)
    (node_modules / "package-link").symlink_to("../package")
    (release / "openclaw" / "administrateme" / "dist").mkdir(mode=0o700)
    bytecode = release / "adminme" / "__pycache__"
    bytecode.mkdir(mode=0o700)
    cached = bytecode / "sample.cpython-314.pyc"
    cached.write_bytes(b"generated-bytecode")
    cached.chmod(0o600)
    return repository, release, predecessor


def _verify_legacy(
    repository: Path,
    release: Path,
    predecessor: str,
) -> dict[str, object]:
    return verify_release_source_identity(
        release,
        repository=repository,
        commit=predecessor,
        allow_legacy_tree=True,
        retained_by_commit=_git(repository, "rev-parse", "HEAD"),
    )


def test_markerless_legacy_release_matches_pointer_commit_not_head(
    tmp_path: Path,
) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)

    evidence = _verify_legacy(repository, release, predecessor)

    assert _git(repository, "rev-parse", "HEAD") != predecessor
    assert evidence["source_identity_method"] == LEGACY_TREE_METHOD
    assert evidence["source_marker_matches"] is False
    assert evidence["tracked_source_tree_matches"] is True
    assert evidence["generated_extra_policy"] == GENERATED_EXTRA_POLICY
    assert evidence["generated_extra_policy_matches"] is True
    assert evidence["generated_extra_count"] == 5
    assert evidence["tracked_source_file_count"] == 2
    assert len(str(evidence["tracked_source_manifest_sha256"])) == 64


@pytest.mark.parametrize(
    "mutation",
    ("content", "mode", "missing", "unexpected", "group_write", "hardlink"),
)
def test_markerless_legacy_release_rejects_source_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)
    tracked = release / "adminme" / "sample.py"
    if mutation == "content":
        tracked.write_text("VALUE = 'changed'\n", encoding="utf-8")
    elif mutation == "mode":
        (release / "scripts" / "run").chmod(0o600)
    elif mutation == "missing":
        tracked.unlink()
    elif mutation == "unexpected":
        (release / "adminme" / "injected.py").write_text(
            "UNEXPECTED = True\n", encoding="utf-8"
        )
    elif mutation == "group_write":
        tracked.chmod(0o620)
    else:
        original = release / "adminme" / "sample-original.py"
        tracked.rename(original)
        os.link(original, tracked)

    with pytest.raises(SourceIdentityError):
        _verify_legacy(repository, release, predecessor)


def test_markerless_legacy_release_rejects_unavailable_commit(tmp_path: Path) -> None:
    repository, release, _predecessor = _legacy_release(tmp_path)

    with pytest.raises(SourceIdentityError, match="unavailable"):
        verify_release_source_identity(
            release,
            repository=repository,
            commit="f" * 40,
            allow_legacy_tree=True,
            retained_by_commit=_git(repository, "rev-parse", "HEAD"),
        )


def test_reviewed_tree_rejects_tracked_python_cache_cleanup_collision(
    tmp_path: Path,
) -> None:
    repository, _first, _second = _repository(tmp_path)
    cache = repository / "adminme" / "__pycache__"
    cache.mkdir(mode=0o700)
    (cache / "kept.pyc").write_bytes(b"tracked-cache")
    commit = _commit(repository, "tracked cache collision")

    with pytest.raises(SourceIdentityError, match="generated-runtime boundary"):
        materialize_git_tree(repository, commit, tmp_path / "destination")

    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize(
    "relative",
    (".env", "state.db", "runtime.log", ".pytest_cache/nodeids"),
)
def test_markerless_legacy_release_rejects_ignored_or_secret_shaped_residue(
    tmp_path: Path,
    relative: str,
) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)
    extra = release / relative
    extra.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    extra.write_text("unexpected residue must not be read\n", encoding="utf-8")

    with pytest.raises(SourceIdentityError, match="unexpected"):
        _verify_legacy(repository, release, predecessor)


def test_markerless_legacy_release_rejects_special_residue_without_opening_it(
    tmp_path: Path,
) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)
    os.mkfifo(release / "unexpected.pipe", mode=0o600)

    with pytest.raises(SourceIdentityError, match="unexpected"):
        _verify_legacy(repository, release, predecessor)


def test_markerless_legacy_release_rejects_nonancestor_commit(tmp_path: Path) -> None:
    repository, _release, _predecessor = _legacy_release(tmp_path)
    head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "checkout", "--orphan", "unrelated")
    for path in tuple(repository.iterdir()):
        if path.name != ".git":
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    (repository / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    unrelated = _commit(repository, "unrelated")
    release = tmp_path / "unrelated-release"
    _archive_commit(repository, unrelated, release)
    release.chmod(0o700)
    for path in release.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    _git(repository, "checkout", "-q", head)

    with pytest.raises(SourceIdentityError, match="not retained"):
        verify_release_source_identity(
            release,
            repository=repository,
            commit=unrelated,
            allow_legacy_tree=True,
            retained_by_commit=head,
        )


def test_valid_marker_fast_path_does_not_require_git_history(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir(mode=0o700)
    commit = "a" * 40
    write_source_marker(release, commit)

    evidence = verify_release_source_identity(
        release,
        repository=tmp_path / "not-a-repository",
        commit=commit,
        allow_legacy_tree=True,
    )

    assert evidence == {
        "source_identity_method": SOURCE_MARKER_METHOD,
        "source_marker_matches": True,
        "tracked_source_tree_matches": "not_evaluated",
        "generated_extra_policy_matches": "not_evaluated",
    }


@pytest.mark.parametrize(
    "kind",
    ("wrong", "public", "readonly", "owner_exec", "whitespace", "symlink"),
)
def test_invalid_present_marker_never_falls_back(tmp_path: Path, kind: str) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)
    marker = release / ".administrateme-source-commit"
    if kind == "symlink":
        marker.symlink_to(release / "adminme" / "sample.py")
    else:
        marker.write_text(
            (
                f" {'b' * 40 if kind == 'wrong' else predecessor}\n"
                if kind == "whitespace"
                else f"{'b' * 40 if kind == 'wrong' else predecessor}\n"
            ),
            encoding="ascii",
        )
        marker.chmod(
            0o644
            if kind == "public"
            else 0o400
            if kind == "readonly"
            else 0o700
            if kind == "owner_exec"
            else 0o600
        )

    with pytest.raises(SourceIdentityError, match="marker"):
        _verify_legacy(repository, release, predecessor)


def test_git_materialization_excludes_untracked_and_publishes_private_marker(
    tmp_path: Path,
) -> None:
    repository, _predecessor, commit = _repository(tmp_path)
    (repository / ".env").write_text("must-not-copy\n", encoding="utf-8")
    destination = tmp_path / "release"

    materialize_git_tree(repository, commit, destination)
    assert not (destination / ".env").exists()
    write_source_marker(destination, commit)

    evidence = verify_release_source_identity(
        destination,
        repository=repository,
        commit=commit,
        allow_legacy_tree=False,
        require_tracked_tree=True,
    )
    marker = destination / ".administrateme-source-commit"
    assert marker.read_text("ascii").strip() == commit
    assert marker.stat().st_mode & 0o077 == 0
    assert evidence["source_identity_method"] == SOURCE_MARKER_METHOD
    assert evidence["tracked_source_tree_matches"] is True


def test_updater_publishes_source_then_prepares_at_stable_final_coordinate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    observed: dict[str, object] = {}

    def prepare(release: Path) -> None:
        marker = release / ".administrateme-source-commit"
        virtualenv = release / ".venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(virtualenv)],
            check=True,
        )
        probe = virtualenv / "bin" / "stable-coordinate-probe"
        probe.write_text(
            f"#!{virtualenv / 'bin' / 'python'}\nprint('stable')\n",
            encoding="utf-8",
        )
        probe.chmod(0o700)
        observed.update(
            {
                "release": release,
                "release_exists": release.is_dir(),
                "marker": marker.read_text("ascii").strip(),
                "marker_mode": marker.stat().st_mode & 0o777,
                "probe": subprocess.run(
                    [str(probe)], check=True, capture_output=True, text=True
                ).stdout.strip(),
            }
        )

    monkeypatch.setattr(manager, "_prepare_release", prepare)
    release = manager._publish_release(repository, commit)  # noqa: SLF001

    assert observed["release"] == paths.app_dir / "releases" / commit
    assert observed["release_exists"] is True
    assert observed["marker"] == commit
    assert observed["marker_mode"] == 0o600
    assert observed["probe"] == "stable"
    assert release == paths.app_dir / "releases" / commit
    assert not (release / ".env").exists()
    assert (release / ".administrateme-source-commit").read_text("ascii").strip() == (
        commit
    )
    assert not list((paths.app_dir / "releases").glob(".staging-*"))


@pytest.mark.parametrize("failure", ("marker", "rename"))
def test_updater_publication_failure_leaves_no_release_or_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    if failure == "marker":

        def fail_marker(_release: Path, _commit: str) -> None:
            raise SourceIdentityError("fixture marker failure")

        monkeypatch.setattr("bootstrap.upgrade.write_source_marker_at", fail_marker)
        expected_error: type[BaseException] = UpgradeError
    else:

        def fail_rename(
            _source: str,
            _destination: str,
            **_kwargs: object,
        ) -> None:
            raise OSError("fixture rename failure")

        monkeypatch.setattr("bootstrap.upgrade.os.rename", fail_rename)
        expected_error = UpgradeError

    with pytest.raises(expected_error):
        manager._publish_release(repository, commit)  # noqa: SLF001

    releases = paths.app_dir / "releases"
    assert not (releases / commit).exists()
    assert not list(releases.glob(".staging-*"))
    assert not (paths.app_dir / "current-release.json").exists()


def test_interrupt_after_atomic_rename_retains_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    original_rename = os.rename

    def rename_then_interrupt(
        source: str,
        destination: str,
        **kwargs: object,
    ) -> None:
        original_rename(source, destination, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr("bootstrap.upgrade.os.rename", rename_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        manager._publish_release(repository, commit)  # noqa: SLF001

    release = paths.app_dir / "releases" / commit
    marker = release / ".administrateme-source-commit"
    source = release / "poetry.lock"
    marker_before = (marker.read_bytes(), marker.lstat().st_ino)
    source_before = (source.read_bytes(), source.lstat().st_ino)
    state_path = release.parent / f".release-state-{commit}.json"
    assert json.loads(state_path.read_text("utf-8"))["phase"] == "publishing"
    monkeypatch.setattr("bootstrap.upgrade.os.rename", original_rename)

    assert manager._publish_release(repository, commit) == release  # noqa: SLF001
    assert json.loads(state_path.read_text("utf-8"))["phase"] == "complete"
    assert (marker.read_bytes(), marker.lstat().st_ino) == marker_before
    assert (source.read_bytes(), source.lstat().st_ino) == source_before


def test_updater_refuses_to_repair_existing_markerless_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    paths.create()
    releases = paths.app_dir / "releases"
    releases.mkdir(mode=0o700)
    release = releases / commit
    materialize_git_tree(repository, commit, release)

    with pytest.raises(UpgradeError, match="source identity"):
        UpgradeManager(paths, root=repository, test_mode=True)._publish_release(  # noqa: SLF001
            repository,
            commit,
        )

    assert not (release / ".administrateme-source-commit").exists()


def test_legacy_proof_rejects_tracked_path_replacement_after_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)
    original = source_identity._read_tracked_file
    changed = False

    def replace_after_read(
        release_descriptor: int,
        entry: source_identity.GitTreeEntry,
        object_format: str,
    ) -> tuple[str, int, source_identity.FileCoordinate]:
        nonlocal changed
        result = original(release_descriptor, entry, object_format)
        if entry.path == "adminme/sample.py" and not changed:
            replacement = release / "adminme" / ".replacement"
            replacement.write_text("VALUE = 'changed'\n", encoding="utf-8")
            replacement.chmod(0o600)
            os.replace(replacement, release / entry.path)
            changed = True
        return result

    monkeypatch.setattr(source_identity, "_read_tracked_file", replace_after_read)
    with pytest.raises(SourceIdentityError, match="changed"):
        _verify_legacy(repository, release, predecessor)


def test_legacy_proof_rejects_marker_appearing_after_absence_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)
    original = source_identity._read_source_marker
    calls = 0

    def create_after_absence(candidate_release: Path, commit: str) -> bool:
        nonlocal calls
        result = original(candidate_release, commit)
        calls += 1
        if calls == 1 and result is False:
            write_source_marker(candidate_release, commit)
        return result

    monkeypatch.setattr(source_identity, "_read_source_marker", create_after_absence)
    with pytest.raises(SourceIdentityError, match="marker"):
        _verify_legacy(repository, release, predecessor)


def test_legacy_proof_requires_owner_execute_for_executable_source(
    tmp_path: Path,
) -> None:
    repository, release, predecessor = _legacy_release(tmp_path)
    (release / "scripts" / "run").chmod(0o601)

    with pytest.raises(SourceIdentityError, match="mode"):
        _verify_legacy(repository, release, predecessor)


@pytest.mark.parametrize(
    "variable",
    (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_INDEX_FILE",
        "GIT_EXEC_PATH",
        "GIT_CONFIG_GLOBAL",
    ),
)
def test_selected_repository_ignores_git_repository_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    repository, _predecessor, commit = _repository(tmp_path)
    unrelated_root = tmp_path / "unrelated"
    unrelated_root.mkdir(mode=0o700)
    unrelated, _unrelated_predecessor, _unrelated_commit = _repository(unrelated_root)
    poison = (
        unrelated / ".git" if variable in {"GIT_DIR", "GIT_COMMON_DIR"} else unrelated
    )
    monkeypatch.setenv(variable, str(poison))
    destination = tmp_path / "materialized"

    materialize_git_tree(repository, commit, destination)

    assert (destination / "later.txt").read_text("utf-8") == "later\n"
    assert (
        verify_release_source_identity(
            destination,
            repository=repository,
            commit=commit,
            allow_legacy_tree=True,
            retained_by_commit=commit,
        )["tracked_source_tree_matches"]
        is True
    )


def test_reviewed_source_ignores_ambient_git_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _predecessor, commit = _repository(tmp_path)
    fake_bin = tmp_path / "fake-git-bin"
    fake_bin.mkdir(mode=0o700)
    canary = tmp_path / "ambient-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\nprintf ran > {canary}\nexit 99\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    destination = tmp_path / "system-git-materialized"
    materialize_git_tree(repository, commit, destination)

    assert (destination / "later.txt").read_text("utf-8") == "later\n"
    assert not canary.exists()


@pytest.mark.parametrize("unsafe_mode", (0o755, 0o500))
def test_updater_rejects_unsafe_releases_directory_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_mode: int,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    releases = paths.app_dir / "releases"
    releases.mkdir(mode=unsafe_mode)
    before = releases.lstat()

    with pytest.raises(UpgradeError, match="unsafe"):
        UpgradeManager(paths, root=repository, test_mode=True)._publish_release(  # noqa: SLF001
            repository,
            commit,
        )

    after = releases.lstat()
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert not (releases / commit).exists()


def test_updater_rejects_releases_symlink_without_mutating_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    (paths.app_dir / "releases").symlink_to(outside, target_is_directory=True)
    before = outside.lstat()

    with pytest.raises(UpgradeError, match="unsafe"):
        UpgradeManager(paths, root=repository, test_mode=True)._publish_release(  # noqa: SLF001
            repository,
            commit,
        )

    after = outside.lstat()
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert sentinel.read_text("utf-8") == "unchanged\n"
    assert not (outside / commit).exists()


def test_prepare_interruption_retains_source_and_retry_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    calls: list[Path] = []
    outside = tmp_path / "outside-generated-target"
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged\n", encoding="utf-8")

    def interrupt(release: Path) -> None:
        calls.append(release)
        partial = release / ".venv" / "bin"
        partial.mkdir(mode=0o700, parents=True, exist_ok=True)
        poison = partial / "python"
        poison.write_text("#!/bin/sh\nprintf poisoned\n", encoding="utf-8")
        poison.chmod(0o700)
        (partial / "outside-link").symlink_to(sentinel)
        node_modules = release / "openclaw" / "administrateme" / "node_modules"
        node_modules.mkdir(mode=0o700)
        (node_modules / "partial").write_text("partial\n", encoding="utf-8")
        dist = release / "openclaw" / "administrateme" / "dist"
        dist.mkdir(mode=0o700)
        (dist / "poison.js").write_text("poison\n", encoding="utf-8")
        bytecode = release / "bootstrap" / "__pycache__"
        bytecode.mkdir(mode=0o700)
        (bytecode / "prep_sample.cpython-314.pyc").write_bytes(b"poison")
        raise KeyboardInterrupt

    monkeypatch.setattr(manager, "_prepare_release", interrupt)
    with pytest.raises(KeyboardInterrupt):
        manager._publish_release(repository, commit)  # noqa: SLF001

    release = paths.app_dir / "releases" / commit
    assert release.is_dir()
    marker = release / ".administrateme-source-commit"
    source = release / "poetry.lock"
    marker_before = marker.lstat()
    marker_payload = marker.read_bytes()
    source_before = source.lstat()
    source_payload = source.read_bytes()
    assert not list(release.parent.glob(".staging-*"))
    assert not (paths.app_dir / "current-release.json").exists()

    def finish(candidate_release: Path) -> None:
        calls.append(candidate_release)
        assert not (candidate_release / ".venv").exists()
        assert not (
            candidate_release / "openclaw" / "administrateme" / "node_modules"
        ).exists()
        assert not (candidate_release / "openclaw" / "administrateme" / "dist").exists()
        assert not (candidate_release / "bootstrap" / "__pycache__").exists()
        assert sentinel.read_text("utf-8") == "unchanged\n"
        (candidate_release / ".venv").mkdir(mode=0o700)
        (candidate_release / ".venv" / "prepared").write_text(
            "ready\n", encoding="utf-8"
        )

    monkeypatch.setattr(manager, "_prepare_release", finish)
    assert manager._publish_release(repository, commit) == release  # noqa: SLF001
    assert calls == [release, release]
    assert (release / ".venv" / "prepared").read_text("utf-8") == "ready\n"
    assert marker.read_bytes() == marker_payload
    assert marker.lstat().st_ino == marker_before.st_ino
    assert source.read_bytes() == source_payload
    assert source.lstat().st_ino == source_before.st_ino
    state = json.loads(
        (release.parent / f".release-state-{commit}.json").read_text("utf-8")
    )
    assert state["phase"] == "complete"


def test_complete_preparation_rebuilds_after_backup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    repository, commit = _upgrade_repository(tmp_path)
    (repository / ".env").unlink()
    paths = DeploymentPaths.from_root(tmp_path / "deployment-backup-retry")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    prepare_calls = 0
    backup_calls = 0

    def prepare(release: Path) -> None:
        nonlocal prepare_calls
        prepare_calls += 1
        canary = release / ".venv" / "retry-canary"
        if prepare_calls == 2:
            assert not canary.exists()
        canary.parent.mkdir(mode=0o700, exist_ok=True)
        canary.write_text(f"attempt-{prepare_calls}\n", encoding="utf-8")

    def backup_after_one_failure(_manager: object) -> dict[str, object]:
        nonlocal backup_calls
        backup_calls += 1
        if backup_calls == 1:
            raise RuntimeError("fixture backup failure")
        return {"path": str(tmp_path / "retry-backup.ambackup")}

    monkeypatch.setattr(manager, "_prepare_release", prepare)
    monkeypatch.setattr(
        "bootstrap.upgrade.BackupManager.create",
        backup_after_one_failure,
    )
    monkeypatch.setattr(
        manager,
        "_post_switch_doctor",
        lambda _release: {"status": "healthy", "checks": []},
    )
    with pytest.raises(RuntimeError, match="fixture backup failure"):
        manager.activate(repository)
    release = paths.app_dir / "releases" / commit
    state_path = release.parent / f".release-state-{commit}.json"
    assert json.loads(state_path.read_text("utf-8"))["phase"] == "complete"
    assert (release / ".venv" / "retry-canary").read_text("utf-8") == ("attempt-1\n")

    result = manager.activate(repository)
    assert prepare_calls == 2
    assert backup_calls == 2
    assert result["status"] == "upgraded"
    assert json.loads(state_path.read_text("utf-8"))["phase"] == "consumed"
    assert (release / ".venv" / "retry-canary").read_text("utf-8") == ("attempt-2\n")


def test_activation_consumes_preparation_before_first_selector_and_refuses_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    repository, commit = _upgrade_repository(tmp_path)
    (repository / ".env").unlink()
    paths = DeploymentPaths.from_root(tmp_path / "deployment-consumed-order")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    state_path = release.parent / f".release-state-{commit}.json"
    assert json.loads(state_path.read_text("utf-8"))["phase"] == "complete"
    monkeypatch.setattr(
        "bootstrap.upgrade.BackupManager.create",
        lambda _manager: {"path": str(tmp_path / "fixture.ambackup")},
    )

    def observe_first_selector(candidate_release: Path) -> None:
        assert candidate_release == release
        assert json.loads(state_path.read_text("utf-8"))["phase"] == "consumed"
        with pytest.raises(UpgradeError, match="already consumed"):
            manager._publish_release(repository, commit)  # noqa: SLF001
        raise RuntimeError("selector entry observed")

    monkeypatch.setattr(manager, "_install_reviewed_openclaw", observe_first_selector)
    with pytest.raises(RuntimeError, match="selector entry observed"):
        manager.activate(repository)

    assert json.loads(state_path.read_text("utf-8"))["phase"] == "consumed"
    with pytest.raises(UpgradeError, match="already consumed"):
        manager._publish_release(repository, commit)  # noqa: SLF001


def test_preparation_consumption_rejects_replaced_activity_before_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-consume-replacement")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    state_path = release.parent / f".release-state-{commit}.json"
    activity = release.parent / f".release-activity-{commit}.lock"
    activity.rename(release.parent / f".retained-activity-{commit}.lock")
    activity.write_text("replacement\n", encoding="ascii")
    activity.chmod(0o600)

    with pytest.raises(UpgradeError, match="activity lease is unsafe"):
        manager._consume_preparation_state(  # noqa: SLF001
            repository,
            commit,
            release,
        )

    assert json.loads(state_path.read_text("utf-8"))["phase"] == "complete"


def test_preparation_consumption_rejects_live_shared_activity_before_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-consume-live-lease")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    state_path = release.parent / f".release-state-{commit}.json"
    activity = release.parent / f".release-activity-{commit}.lock"
    held = os.open(activity, os.O_RDWR)
    try:
        fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(UpgradeError, match="worker is still active"):
            manager._consume_preparation_state(  # noqa: SLF001
                repository,
                commit,
                release,
            )
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)

    assert json.loads(state_path.read_text("utf-8"))["phase"] == "complete"


def test_controller_death_makes_guardian_reap_worker_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-worker-lease")
    stub_uninitialized_intake(monkeypatch, paths)
    ready = tmp_path / "preparation-worker-ready"
    controller_code = """
import sys
from pathlib import Path
import pytest
from bootstrap.contracts import DeploymentPaths
from bootstrap.upgrade import UpgradeManager
from tests.unit.bootstrap.intake_fixture import stub_uninitialized_intake

class HeldPreparationManager(UpgradeManager):
    def _prepare_release(self, release: Path) -> None:
        child = '''
import os
import sys
import time
from pathlib import Path
release = Path(sys.argv[1])
ready = Path(sys.argv[2])
(release / ".venv").mkdir(mode=0o700, exist_ok=True)
(release / ".venv" / "orphan-canary").write_text("alive\\\\n", encoding="utf-8")
ready.write_text(str(os.getpid()), encoding="ascii")
time.sleep(300)
'''
        self._run_preparation_command(
            [sys.executable, "-c", child, str(release), sys.argv[4]],
            release=release,
        )

fixture_paths = DeploymentPaths.from_root(Path(sys.argv[3]))
with pytest.MonkeyPatch.context() as fixture:
    stub_uninitialized_intake(fixture, fixture_paths)
    HeldPreparationManager(
        fixture_paths,
        root=Path(sys.argv[1]),
        test_mode=True,
    )._publish_release(Path(sys.argv[1]), sys.argv[2])
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPO_ROOT)
    controller = subprocess.Popen(
        [
            sys.executable,
            "-c",
            controller_code,
            str(repository),
            commit,
            str(paths.root),
            str(ready),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    payload = ""
    while time.monotonic() < deadline:
        if controller.poll() is not None:
            stdout, stderr = controller.communicate()
            pytest.fail(f"preparation controller exited early: {stdout}\n{stderr}")
        try:
            payload = ready.read_text("ascii").strip()
        except FileNotFoundError:
            payload = ""
        if payload:
            break
        time.sleep(0.02)
    assert payload
    worker_pid = int(payload)
    release = paths.app_dir / "releases" / commit
    assert (release / ".venv" / "orphan-canary").is_file()
    os.kill(controller.pid, signal.SIGKILL)
    controller.wait(timeout=5)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("orphaned preparation worker did not terminate")

    manager = UpgradeManager(paths, root=repository, test_mode=True)

    def finish(candidate_release: Path) -> None:
        assert not (candidate_release / ".venv").exists()
        (candidate_release / ".venv").mkdir(mode=0o700)

    monkeypatch.setattr(manager, "_prepare_release", finish)
    assert manager._publish_release(repository, commit) == release  # noqa: SLF001


def test_durable_activity_coordinate_rejects_replacement_before_retry_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-activity-coordinate")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)

    def initial_prepare(release: Path) -> None:
        (release / ".venv").mkdir(mode=0o700)
        (release / ".venv" / "must-remain").write_text("safe\n", encoding="utf-8")

    monkeypatch.setattr(manager, "_prepare_release", initial_prepare)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    activity = release.parent / f".release-activity-{commit}.lock"
    retained_activity = release.parent / f".retained-activity-{commit}.lock"
    activity.rename(retained_activity)
    activity.write_text("replacement\n", encoding="ascii")
    activity.chmod(0o600)

    with pytest.raises(UpgradeError, match="activity lease is unsafe"):
        manager._publish_release(repository, commit)  # noqa: SLF001
    assert (release / ".venv" / "must-remain").read_text("utf-8") == "safe\n"


def test_successful_preparation_leader_cannot_leave_background_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-background-process")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    delayed_write = tmp_path / "background-write"

    def prepare(release: Path) -> None:
        child = """
import subprocess
import sys

payload = '''
import sys
import time
from pathlib import Path
time.sleep(0.5)
Path(sys.argv[1]).write_text("unexpected", encoding="utf-8")
'''
subprocess.Popen(
    [sys.executable, "-c", payload, sys.argv[1]],
    close_fds=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
"""
        manager._run_preparation_command(  # noqa: SLF001
            [sys.executable, "-c", child, str(delayed_write)],
            release=release,
        )

    monkeypatch.setattr(manager, "_prepare_release", prepare)
    with pytest.raises(UpgradeError, match="stable release path") as raised:
        manager._publish_release(repository, commit)  # noqa: SLF001
    assert isinstance(raised.value.__cause__, subprocess.CalledProcessError)
    assert raised.value.__cause__.returncode == 73
    time.sleep(0.7)
    assert not delayed_write.exists()


def test_guardian_reaps_command_when_ready_handoff_is_broken(tmp_path: Path) -> None:
    activity = tmp_path / ".release-activity.lock"
    activity.write_text("\n", encoding="ascii")
    activity.chmod(0o600)
    inherited = os.open(activity, os.O_RDWR)
    metadata = os.fstat(inherited)
    fcntl.flock(inherited, fcntl.LOCK_SH)
    lifecycle = tmp_path / ".controller-lifecycle.lock"
    lifecycle.write_text("\n", encoding="ascii")
    lifecycle.chmod(0o600)
    lifecycle_metadata = lifecycle.lstat()
    operation_lock_path = tmp_path / ".upgrade-operation.lock"
    operation_lock_path.write_text("\n", encoding="ascii")
    operation_lock_path.chmod(0o600)
    operation_lock = os.open(operation_lock_path, os.O_RDWR)
    ready_read, ready_write = os.pipe()
    controller_read, controller_write = os.pipe()
    os.close(ready_read)
    delayed_write = tmp_path / "broken-ready-write"
    command = (
        "import sys, time; from pathlib import Path; "
        "time.sleep(0.5); Path(sys.argv[1]).write_text('unsafe', encoding='utf-8')"
    )
    guardian = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "bootstrap" / "preparation_worker.py"),
            str(activity),
            str(inherited),
            str(ready_write),
            str(controller_read),
            str(metadata.st_dev),
            str(metadata.st_ino),
            str(lifecycle),
            str(lifecycle_metadata.st_dev),
            str(lifecycle_metadata.st_ino),
            str(operation_lock),
            "30",
            "--",
            sys.executable,
            "-c",
            command,
            str(delayed_write),
        ],
        start_new_session=True,
        pass_fds=(inherited, ready_write, controller_read, operation_lock),
        stderr=subprocess.PIPE,
        text=True,
    )
    os.close(inherited)
    os.close(ready_write)
    os.close(controller_read)
    os.close(operation_lock)
    try:
        _stdout, stderr = guardian.communicate(timeout=10)
        assert guardian.returncode == 77
        assert "invalid preparation-worker invocation" not in stderr
        time.sleep(0.7)
        assert not delayed_write.exists()
        proof = os.open(activity, os.O_RDWR)
        try:
            fcntl.flock(proof, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(proof)
    finally:
        os.close(controller_write)


def test_unreaped_materializer_retains_private_stage_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-unreaped-publication")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)

    def fail_without_terminality(
        _repository: Path,
        _commit: str,
        destination: Path,
        **_kwargs: object,
    ) -> tuple[int, int]:
        destination.mkdir(mode=0o700)
        (destination / "unreaped-canary").write_text(
            "retained\n",
            encoding="utf-8",
        )
        raise SourceProcessNotReaped("materializer process group was not reaped")

    monkeypatch.setattr(
        upgrade_module, "materialize_git_tree", fail_without_terminality
    )

    with pytest.raises(UpgradeError, match="terminality is unknown"):
        manager._publish_release(repository, commit)  # noqa: SLF001

    releases = paths.app_dir / "releases"
    state_path = releases / f".release-state-{commit}.json"
    state = json.loads(state_path.read_text("utf-8"))
    assert state["phase"] == "publishing_unreaped"
    stage = releases / str(state["stage_name"])
    assert stage.is_dir()
    assert (stage / "unreaped-canary").read_text("utf-8") == "retained\n"
    assert not (releases / commit).exists()
    assert not (paths.app_dir / "current-release.json").exists()
    assert not paths.backup_dir.exists()

    with pytest.raises(
        UpgradeError,
        match="release preparation state exists without its candidate",
    ):
        manager._publish_release(repository, commit)  # noqa: SLF001

    assert json.loads(state_path.read_text("utf-8"))["phase"] == ("publishing_unreaped")
    assert (stage / "unreaped-canary").read_text("utf-8") == "retained\n"


def test_publication_lock_excludes_concurrent_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    paths.create()
    releases = paths.app_dir / "releases"
    releases.mkdir(mode=0o700)
    lock = os.open(
        releases / ".publication.lock",
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(UpgradeError, match="already running"):
            UpgradeManager(paths, root=repository, test_mode=True)._publish_release(  # noqa: SLF001
                repository,
                commit,
            )
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def test_selected_release_is_verified_without_runtime_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    _write_release_pointer(paths, {"commit": commit, "path": str(release)})

    def must_not_prepare(_release: Path) -> None:
        raise AssertionError("selected release was prepared in place")

    monkeypatch.setattr(manager, "_prepare_release", must_not_prepare)
    assert manager._publish_release(repository, commit) == release  # noqa: SLF001


def test_retained_release_chain_is_read_only_without_preparation_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, first = _upgrade_repository(tmp_path)
    (repository / ".env").unlink()
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    first_release = manager._publish_release(repository, first)  # noqa: SLF001
    (repository / "second.txt").write_text("second\n", encoding="utf-8")
    second = _commit(repository, "second")
    second_release = manager._publish_release(repository, second)  # noqa: SLF001
    (repository / "third.txt").write_text("third\n", encoding="utf-8")
    third = _commit(repository, "third")
    third_release = manager._publish_release(repository, third)  # noqa: SLF001
    _write_release_pointer(
        paths,
        {
            "commit": third,
            "path": str(third_release),
            "previous": {
                "commit": second,
                "path": str(second_release),
                "previous": {
                    "commit": first,
                    "path": str(first_release),
                    "previous": None,
                },
            },
        },
    )
    (second_release.parent / f".release-state-{second}.json").unlink()
    tracked_before = (second_release / "poetry.lock").read_bytes()

    def must_not_prepare(_release: Path) -> None:
        raise AssertionError("retained release was prepared in place")

    monkeypatch.setattr(manager, "_prepare_release", must_not_prepare)
    with pytest.raises(UpgradeError, match="retained for rollback"):
        manager._publish_release(repository, second)  # noqa: SLF001

    assert (second_release / "poetry.lock").read_bytes() == tracked_before


@pytest.mark.parametrize(
    "document_factory",
    (
        lambda commit, release: {
            "commit": commit,
            "path": str(release),
            "unexpected": True,
        },
        lambda commit, release: {
            "commit": commit,
            "path": str(release),
            "previous": "not-an-object",
        },
        lambda commit, release: {
            "commit": commit,
            "path": str(release),
            "previous": {
                "commit": commit,
                "path": str(release),
                "previous": None,
            },
        },
        lambda commit, release: {
            "commit": commit.upper(),
            "path": str(release),
        },
        lambda commit, release: {
            "commit": commit,
            "path": str(release.parent / "wrong"),
        },
    ),
)
def test_release_pointer_chain_rejects_malformed_or_duplicated_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document_factory: Callable[[str, Path], dict[str, object]],
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    _write_release_pointer(paths, document_factory(commit, release))

    with pytest.raises(UpgradeError, match="pointer"):
        manager._release_pointer_snapshot()  # noqa: SLF001


def test_existing_unselected_release_without_pending_state_is_not_repaired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    (release.parent / f".release-state-{commit}.json").unlink()

    def must_not_prepare(_release: Path) -> None:
        raise AssertionError("marker-only release was prepared")

    monkeypatch.setattr(manager, "_prepare_release", must_not_prepare)
    with pytest.raises(UpgradeError, match="durable preparation ownership"):
        manager._publish_release(repository, commit)  # noqa: SLF001


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("candidate_controller_commit", "f" * 40, "state is invalid"),
        ("pointer_chain_sha256", "f" * 64, "stale pointer binding"),
        ("operation_nonce", "f" * 32, "state is invalid"),
        ("phase", "consumed", "already consumed"),
    ),
)
def test_preparation_state_tampering_fails_before_retry_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
    message: str,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    state_path = release.parent / f".release-state-{commit}.json"
    state = json.loads(state_path.read_text("utf-8"))
    state[field] = replacement
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state_path.chmod(0o600)

    def must_not_prepare(_release: Path) -> None:
        raise AssertionError("tampered preparation state authorized retry")

    monkeypatch.setattr(manager, "_prepare_release", must_not_prepare)
    with pytest.raises(UpgradeError, match=message):
        manager._publish_release(repository, commit)  # noqa: SLF001


def test_public_same_release_activation_stops_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    (repository / ".env").unlink()
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    release = manager._publish_release(repository, commit)  # noqa: SLF001
    pointer = _write_release_pointer(
        paths,
        {"commit": commit, "path": str(release), "previous": None},
    )
    before = pointer.read_bytes()

    def must_not_backup(_manager: object) -> dict[str, object]:
        raise AssertionError("same-release activation reached backup")

    monkeypatch.setattr("bootstrap.upgrade.BackupManager.create", must_not_backup)
    with pytest.raises(UpgradeError, match="already selected"):
        manager.activate(repository)

    assert pointer.read_bytes() == before


def test_activation_refuses_full_pointer_chain_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _commit_value = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    releases = paths.app_dir / "releases"
    releases.mkdir(mode=0o700)
    previous: dict[str, object] | None = None
    for index in range(64, 0, -1):
        commit = f"{index:040x}"
        release = releases / commit
        release.mkdir(mode=0o700)
        previous = {
            "commit": commit,
            "path": str(release),
            "previous": previous,
        }
    assert previous is not None
    pointer = _write_release_pointer(paths, previous)
    before = pointer.read_bytes()
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    monkeypatch.setattr(manager, "_candidate_commit", lambda _candidate: "f" * 40)
    monkeypatch.setattr(
        manager,
        "_attest_controller_provenance",
        lambda _candidate, _commit: None,
    )

    def must_not_backup(_manager: object) -> dict[str, object]:
        raise AssertionError("full pointer chain reached backup")

    monkeypatch.setattr("bootstrap.upgrade.BackupManager.create", must_not_backup)
    with pytest.raises(UpgradeError, match="retention limit"):
        manager.activate(repository)

    assert pointer.read_bytes() == before


def test_operation_lock_excludes_a_separate_upgrade_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _commit_value = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.root.mkdir(mode=0o700)
    paths.app_dir.mkdir(mode=0o700)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys; "
                "fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600); "
                "os.fchmod(fd,0o600); fcntl.flock(fd,fcntl.LOCK_EX); "
                "print('ready',flush=True); sys.stdin.buffer.read(1)"
            ),
            str(paths.app_dir / ".upgrade-operation.lock"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    called = False

    def must_not_activate(_candidate: Path) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    manager = UpgradeManager(paths, root=repository, test_mode=True)
    monkeypatch.setattr(manager, "_activate_locked", must_not_activate)
    try:
        with pytest.raises(UpgradeError, match="already running"):
            manager.activate(repository)
    finally:
        assert holder.stdin is not None
        holder.stdin.write("x")
        holder.stdin.flush()
        holder.wait(timeout=5)
    assert called is False


@pytest.mark.parametrize("retained_patch", [False, True])
def test_prepared_runtime_requires_exact_python_node_and_poetry_pins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retained_patch: bool,
) -> None:
    repository, _commit_value = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    manager = UpgradeManager(paths, root=repository, test_mode=False)
    release = tmp_path / "release-runtime"
    (release / ".venv" / "bin").mkdir(mode=0o700, parents=True)
    (release / "openclaw" / "administrateme" / "node_modules").mkdir(
        mode=0o700, parents=True
    )
    (release / "openclaw" / "administrateme" / "dist").mkdir(mode=0o700)
    tools = tmp_path / "tools"
    tools.mkdir(mode=0o700)

    def executable(name: str, version: str) -> Path:
        target = tools / name
        target.write_text(
            f"#!/bin/sh\nprintf '%s\\n' {version!r}\n",
            encoding="utf-8",
        )
        target.chmod(0o700)
        return target

    expected_version = "3.14.7" if retained_patch else "3.14.6"
    formula_python = executable("python3.14", f"Python {expected_version}")
    (release / ".venv" / "bin" / "python").symlink_to(formula_python)
    poetry = executable("poetry", "Poetry (version 2.4.1)")
    node_bin = tools / "node-bin"
    node_bin.mkdir(mode=0o700)
    node = node_bin / "node"
    node.write_text("#!/bin/sh\nprintf 'v24.19.0\\n'\n", encoding="utf-8")
    node.chmod(0o700)
    versions = {
        "python": {"preferred": "3.14.6"},
        "node": {"preferred": "24.19.0"},
        "package_managers": {"poetry": "2.4.1"},
    }
    monkeypatch.setattr(
        manager,
        "_runtime_coordinates",
        lambda _release: (
            versions,
            formula_python,
            release / ".venv" / "bin" / "python",
            poetry,
            node_bin,
        ),
    )

    def validate() -> None:
        if retained_patch:
            manager._validate_runtime(release, retained_python=expected_version)  # noqa: SLF001
        else:
            manager._validate_prepared_runtime(release)  # noqa: SLF001

    validate()
    formula_python.write_text(
        "#!/bin/sh\nprintf 'Python 3.14.5\\n'\n",
        encoding="utf-8",
    )
    formula_python.chmod(0o700)
    with pytest.raises(UpgradeError, match="wrong pinned Python formula"):
        validate()
    formula_python.write_text(
        f"#!/bin/sh\nprintf 'Python {expected_version}\\n'\n",
        encoding="utf-8",
    )
    formula_python.chmod(0o700)

    wrong_python = executable("wrong-python", f"Python {expected_version}")
    (release / ".venv" / "bin" / "python").unlink()
    (release / ".venv" / "bin" / "python").symlink_to(wrong_python)
    with pytest.raises(UpgradeError, match="outside the pinned Python"):
        validate()
    (release / ".venv" / "bin" / "python").unlink()
    (release / ".venv" / "bin" / "python").symlink_to(formula_python)

    poetry.write_text(
        "#!/bin/sh\nprintf 'Poetry (version 2.3.0)\\n'\n",
        encoding="utf-8",
    )
    poetry.chmod(0o700)
    with pytest.raises(UpgradeError, match="wrong pinned Poetry"):
        validate()
    poetry.write_text(
        "#!/bin/sh\nprintf 'Poetry (version 2.4.1)\\n'\n",
        encoding="utf-8",
    )
    poetry.chmod(0o700)

    node.write_text("#!/bin/sh\nprintf 'v24.18.0\\n'\n", encoding="utf-8")
    node.chmod(0o700)
    with pytest.raises(UpgradeError, match="wrong pinned Node"):
        validate()


def test_release_parent_swap_before_rename_cannot_touch_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    original_rename = os.rename
    swapped = False

    def swap_parent_then_rename(
        source: str,
        destination: str,
        **kwargs: object,
    ) -> None:
        nonlocal swapped
        releases = paths.app_dir / "releases"
        if not swapped:
            original_rename(releases, paths.app_dir / "releases-original")
            releases.symlink_to(outside, target_is_directory=True)
            swapped = True
        original_rename(source, destination, **kwargs)

    monkeypatch.setattr("bootstrap.upgrade.os.rename", swap_parent_then_rename)
    with pytest.raises(UpgradeError, match="directory changed"):
        manager._publish_release(repository, commit)  # noqa: SLF001

    assert sentinel.read_text("utf-8") == "unchanged\n"
    assert not (outside / commit).exists()
    assert not (paths.app_dir / "current-release.json").exists()


def test_release_parent_swap_during_materialization_cannot_touch_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=repository, test_mode=True)
    outside = tmp_path / "outside-during-materialization"
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    original = source_identity._materialize_git_tree_into
    swapped = False

    def swap_before_materialization(
        candidate_repository: Path,
        entries: tuple[source_identity.GitTreeEntry, ...],
        object_format: str,
        destination_descriptor: int,
    ) -> None:
        nonlocal swapped
        releases = paths.app_dir / "releases"
        if not swapped:
            os.rename(releases, paths.app_dir / "releases-original")
            releases.symlink_to(outside, target_is_directory=True)
            swapped = True
        original(
            candidate_repository,
            entries,
            object_format,
            destination_descriptor,
        )

    monkeypatch.setattr(
        source_identity,
        "_materialize_git_tree_into",
        swap_before_materialization,
    )
    with pytest.raises(UpgradeError, match="directory changed"):
        manager._publish_release(repository, commit)  # noqa: SLF001

    assert sentinel.read_text("utf-8") == "unchanged\n"
    assert not (outside / commit).exists()
    assert not list(outside.glob(".staging-*"))
    assert not (paths.app_dir / "current-release.json").exists()


def test_upgrade_cleanliness_ignores_fsmonitor_and_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    (repository / ".env").unlink()
    canary = tmp_path / "fsmonitor-ran"
    monitor = tmp_path / "monitor.sh"
    monitor.write_text(
        f"#!/bin/sh\nprintf ran > {canary}\n",
        encoding="utf-8",
    )
    monitor.chmod(0o700)
    _git(repository, "config", "core.fsmonitor", str(monitor))
    unrelated_root = tmp_path / "unrelated-cleanliness"
    unrelated_root.mkdir(mode=0o700)
    unrelated, _first, _second = _repository(unrelated_root)
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))

    manager = UpgradeManager(
        DeploymentPaths.from_root(tmp_path / "deployment"),
        root=repository,
        test_mode=True,
    )
    assert manager._candidate_commit(repository) == commit  # noqa: SLF001
    assert not canary.exists()


@pytest.mark.parametrize(
    "denied_signals",
    [(signal.SIGTERM,), (signal.SIGKILL,), (signal.SIGTERM, signal.SIGKILL)],
)
def test_materializer_permission_race_requires_reaped_leader_and_absent_group(
    monkeypatch: pytest.MonkeyPatch, denied_signals: tuple[int, ...]
) -> None:
    waits: list[float] = []
    signals: list[int] = []
    clock = [100.0]
    probes = 0
    absence_proven = False
    process = SimpleNamespace(
        pid=43210, wait=lambda *, timeout: waits.append(timeout) or 0
    )

    def killpg(group_id: int, signum: int) -> None:
        nonlocal probes, absence_proven
        assert group_id == process.pid
        signals.append(signum)
        if signum:
            if signum in denied_signals:
                raise PermissionError("synthetic group signal permission race")
            return
        probes += 1
        if probes == 3:
            assert waits == [5, 30]
            raise PermissionError("synthetic zombie-only group remains")
        if probes == 4:
            absence_proven = True
            raise ProcessLookupError("synthetic group is now absent")

    def pause(seconds: float) -> None:
        assert seconds == 0.01
        assert not absence_proven
        clock[0] += seconds

    monkeypatch.setattr(source_identity.os, "killpg", killpg)
    monkeypatch.setattr(
        source_identity,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], sleep=pause),
    )

    source_identity._reap_materializer_group(process)  # noqa: SLF001

    assert waits == [5, 30]
    assert signals == [0, signal.SIGTERM, 0, signal.SIGKILL, 0, 0]
    assert absence_proven
    assert clock[0] == 100.01


@pytest.mark.parametrize("probe_permission_denied", [False, True])
def test_materializer_persistent_permission_or_present_group_stays_unreaped(
    monkeypatch: pytest.MonkeyPatch, probe_permission_denied: bool
) -> None:
    waits: list[float] = []
    signals: list[int] = []
    clock = [0.0]
    process = SimpleNamespace(
        pid=43210, wait=lambda *, timeout: waits.append(timeout) or 0
    )

    def killpg(group_id: int, signum: int) -> None:
        assert group_id == process.pid
        signals.append(signum)
        if signum or probe_permission_denied:
            raise PermissionError("synthetic persistent group permission denial")

    def pause(seconds: float) -> None:
        assert seconds == 0.01
        clock[0] += seconds

    monkeypatch.setattr(source_identity.os, "killpg", killpg)
    monkeypatch.setattr(
        source_identity,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], sleep=pause),
    )

    with pytest.raises(SourceProcessNotReaped, match="descendants could not be reaped"):
        source_identity._reap_materializer_group(process)  # noqa: SLF001

    assert waits == [5, 30]
    assert [signum for signum in signals if signum] == [signal.SIGTERM, signal.SIGKILL]
    assert 5 <= clock[0] <= 5.02


def test_materializer_absent_group_cannot_replace_leader_reap_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    def wait(*, timeout: float) -> int:
        waits.append(timeout)
        raise subprocess.TimeoutExpired("synthetic owned materializer", timeout)

    process = SimpleNamespace(pid=43210, wait=wait)

    def killpg(group_id: int, signum: int) -> None:
        assert group_id == process.pid and signum == 0
        raise ProcessLookupError("synthetic group is absent")

    monkeypatch.setattr(source_identity.os, "killpg", killpg)

    with pytest.raises(
        SourceProcessNotReaped, match="materializer could not be reaped"
    ):
        source_identity._reap_materializer_group(process)  # noqa: SLF001

    assert waits == [5, 30]


def test_materializer_timeout_reaps_child_and_publisher_removes_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit = _upgrade_repository(tmp_path)
    real_git = shutil.which("git")
    assert real_git is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(mode=0o700)
    pid_marker = tmp_path / "hung-git-pid"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'cat-file --batch')\n"
        "    read oid\n"
        "    sleep 1000 & child=$!\n"
        f'    printf \'%s %s\' "$$" "$child" > {pid_marker}\n'
        "    printf '%s blob 4\\nx' \"$oid\"\n"
        '    wait "$child"\n'
        "    ;;\n"
        "  *)\n"
        f'    exec {real_git} "$@"\n'
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    monkeypatch.setattr(source_identity, "SYSTEM_GIT_EXECUTABLE", fake_git)
    monkeypatch.setattr(source_identity, "_MATERIALIZATION_TIMEOUT_SECONDS", 0.2)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    stub_uninitialized_intake(monkeypatch, paths)
    started = time.monotonic()

    with pytest.raises(UpgradeError, match="publication failed"):
        UpgradeManager(paths, root=repository, test_mode=True)._publish_release(  # noqa: SLF001
            repository,
            commit,
        )

    assert time.monotonic() - started < 5
    process_ids = [int(value) for value in pid_marker.read_text("ascii").split()]
    for process_id in process_ids:
        with pytest.raises(ProcessLookupError):
            os.kill(process_id, 0)
    releases = paths.app_dir / "releases"
    assert not (releases / commit).exists()
    assert not list(releases.glob(".staging-*"))


def test_public_reviewed_launcher_resolves_only_one_clean_commit(
    tmp_path: Path,
) -> None:
    repository, _first, head = _repository(tmp_path)

    resolved, commit = reviewed_launcher._reviewed_commit(repository)  # noqa: SLF001

    assert resolved == repository.resolve()
    assert commit == head
    (repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(
        reviewed_launcher.ReviewedLauncherError,
        match="local changes",
    ):
        reviewed_launcher._reviewed_commit(repository)  # noqa: SLF001


def test_public_reviewed_launcher_rejects_expected_commit_before_supervisor_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    commit = "a" * 40
    monkeypatch.setattr(
        reviewed_launcher,
        "_reviewed_commit",
        lambda _candidate: (candidate, commit),
    )

    def unexpected_git(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("supervisor source must not load after commit mismatch")

    monkeypatch.setattr(reviewed_launcher, "_run_git", unexpected_git)

    with pytest.raises(
        reviewed_launcher.ReviewedLauncherError,
        match="does not match reviewed commit",
    ):
        reviewed_launcher._execute_supervisor(  # noqa: SLF001
            "bootstrap",
            tmp_path / "instance",
            candidate,
            ["--expected-commit", "b" * 40],
        )


@pytest.mark.parametrize(
    "arguments",
    (
        ["--expected-commit"],
        ["--expected-commit", "invalid"],
        [
            "--expected-commit",
            "a" * 40,
            "--expected-commit",
            "a" * 40,
        ],
    ),
)
def test_public_reviewed_launcher_rejects_ambiguous_expected_commit(
    arguments: list[str],
) -> None:
    with pytest.raises(
        reviewed_launcher.ReviewedLauncherError,
        match="reviewed expected commit is invalid",
    ):
        reviewed_launcher._validate_expected_commit(  # noqa: SLF001
            "bootstrap", arguments, "a" * 40
        )


def test_public_reviewed_launcher_accepts_matching_or_absent_expected_commit() -> None:
    commit = "a" * 40
    reviewed_launcher._validate_expected_commit(  # noqa: SLF001
        "bootstrap", ["--expected-commit", commit], commit
    )
    reviewed_launcher._validate_expected_commit("bootstrap", [], commit)  # noqa: SLF001
    reviewed_launcher._validate_expected_commit(  # noqa: SLF001
        "update", ["--expected-commit", "invalid"], commit
    )


def test_public_reviewed_launcher_bounds_and_reaps_git_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_git = tmp_path / "hung-git"
    pid_marker = tmp_path / "hung-launcher-git-pids"
    fake_git.write_text(
        "#!/bin/sh\n"
        "sleep 300 & child=$!\n"
        f'printf \'%s %s\' "$$" "$child" > {pid_marker}\n'
        'wait "$child"\n',
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    monkeypatch.setattr(reviewed_launcher, "SYSTEM_GIT", str(fake_git))

    started = time.monotonic()
    with pytest.raises(
        reviewed_launcher.ReviewedLauncherError,
        match="timed out",
    ):
        reviewed_launcher._run_git(  # noqa: SLF001
            tmp_path,
            ["rev-parse", "HEAD"],
            max_output_bytes=64,
            timeout_seconds=1.0,
        )

    assert time.monotonic() - started < 5
    process_ids = [int(value) for value in pid_marker.read_text("ascii").split()]
    for process_id in process_ids:
        with pytest.raises(ProcessLookupError):
            os.kill(process_id, 0)
