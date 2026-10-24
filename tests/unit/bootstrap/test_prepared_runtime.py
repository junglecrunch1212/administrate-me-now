from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bootstrap.prepared_runtime import (
    PreparedRuntimeError,
    runtime_artifact_fingerprint,
)
from bootstrap.upgrade import UpgradeManager


@pytest.mark.parametrize("ambient_parallel", [None, "true", "1", "false"])
def test_preparation_environment_always_serializes_poetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ambient_parallel: str | None
) -> None:
    if ambient_parallel is None:
        monkeypatch.delenv("POETRY_INSTALLER_PARALLEL", raising=False)
    else:
        monkeypatch.setenv("POETRY_INSTALLER_PARALLEL", ambient_parallel)
    environment = UpgradeManager._preparation_environment(
        tmp_path / "node-bin", tmp_path / "private-home"
    )
    assert environment["POETRY_INSTALLER_PARALLEL"] == "false"


def test_serialized_poetry_prevents_installer_umask_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hosted CI installs the pinned Poetry into the base interpreter. Keep this
    # process-wide umask experiment entirely outside pytest and its workers.
    child = textwrap.dedent(
        """
        import json
        import os
        import stat
        import sys
        from importlib.metadata import version
        from pathlib import Path
        from threading import Event

        from cleo.io.null_io import NullIO
        from installer.utils import make_file_executable
        from poetry.config.config import Config
        from poetry.installation.executor import Executor
        from poetry.repositories.repository_pool import RepositoryPool
        from poetry.utils.env import SystemEnv

        assert version("poetry") == sys.argv[2]
        config = Config()
        config.merge({"installer": {"max-workers": 2}})
        executor = Executor(
            SystemEnv(Path(sys.prefix)), RepositoryPool(config=config),
            config, NullIO(),
        )
        runtime = Path(sys.argv[1]) / ".venv"
        runtime.mkdir(mode=0o700, parents=True)
        script = runtime / "fixture-script"
        payload = runtime / "scrypt.py"
        real_umask = os.umask
        original_umask = real_umask(0o077)
        script.write_bytes(b"#!/usr/bin/env python3\\n")
        probe_entered = Event()
        resume_probe = Event()
        writer_started = Event()

        def paused_umask(mask):
            previous = real_umask(mask)
            if mask == 0:
                probe_entered.set()
                assert resume_probe.wait(10), "installer probe was not released"
            return previous

        def write_payload():
            writer_started.set()
            try:
                # The same unqualified file creation used by wheel extraction.
                payload.write_bytes(b"# synthetic wheel payload\\n")
            finally:
                resume_probe.set()

        try:
            # Pause the real installer probe after its process-wide umask(0),
            # without replacing its restoration or executable-mode calculation.
            os.umask = paused_umask
            script_future = executor._executor.submit(make_file_executable, script)
            assert probe_entered.wait(10), "installer did not query the umask"
            payload_future = executor._executor.submit(write_payload)
            if executor._max_workers == 1:
                assert not writer_started.is_set()
                resume_probe.set()
            script_future.result(timeout=10)
            payload_future.result(timeout=10)
            assert real_umask(0o077) == 0o077, "installer did not restore umask"
            print(json.dumps({
                "parallel": config.get("installer.parallel"),
                "workers": executor._max_workers,
                "mode": stat.S_IMODE(payload.stat().st_mode),
            }))
        finally:
            resume_probe.set()
            executor._executor.shutdown(wait=True, cancel_futures=True)
            os.umask = real_umask
            real_umask(original_umask)
        """
    )
    versions = json.loads(
        (Path(__file__).parents[3] / "bootstrap" / "versions.json").read_text()
    )
    monkeypatch.setenv("POETRY_INSTALLER_PARALLEL", "true")
    private_home = tmp_path / "private-home"
    private_home.mkdir(mode=0o700)
    environment = UpgradeManager._preparation_environment(
        tmp_path / "node-bin", private_home
    )
    unsafe_release = tmp_path / "parallel-release"
    safe_release = tmp_path / "serialized-release"
    for release, serialized in ((unsafe_release, False), (safe_release, True)):
        child_environment = environment.copy()
        if not serialized:
            # Reproduce the prior sanitized environment, which left Poetry's
            # parallel default enabled; do not inject an artificial worker mode.
            child_environment.pop("POETRY_INSTALLER_PARALLEL")
        result = subprocess.run(
            [
                sys._base_executable,
                "-c",
                child,
                str(release),
                versions["package_managers"]["poetry"],
            ],
            env=child_environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == {
            "parallel": not serialized,
            "workers": 1 if serialized else 2,
            "mode": 0o600 if serialized else 0o666,
        }
    assert (unsafe_release / ".venv" / "scrypt.py").read_bytes() == (
        safe_release / ".venv" / "scrypt.py"
    ).read_bytes()
    with pytest.raises(PreparedRuntimeError, match="entry is unsafe"):
        runtime_artifact_fingerprint(unsafe_release)
    fingerprint = runtime_artifact_fingerprint(safe_release)
    assert len(fingerprint) == 64
    assert runtime_artifact_fingerprint(safe_release) == fingerprint


def test_fingerprint_tracks_internal_links_and_pinned_interpreter_bytes(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    runtime = release / ".venv" / "bin"
    runtime.mkdir(mode=0o700, parents=True)
    interpreter = tmp_path / "pinned-python"
    interpreter.write_bytes(b"first interpreter")
    (runtime / "python").symlink_to(interpreter)
    (runtime / "python3").symlink_to("python")
    # Python 3.14's standard venv includes the pi-named interpreter alias.
    (runtime / "\U0001d70bthon").symlink_to("python")
    before = runtime_artifact_fingerprint(release)
    assert runtime_artifact_fingerprint(release) == before
    interpreter.write_bytes(b"changed interpreter")
    assert runtime_artifact_fingerprint(release) != before
    # Pinned system tools may be group-managed (including hosted macOS).
    interpreter.chmod(0o775)
    group_managed = runtime_artifact_fingerprint(release)
    assert runtime_artifact_fingerprint(release) == group_managed
    interpreter.chmod(0o755)
    assert runtime_artifact_fingerprint(release) != group_managed
    interpreter.chmod(0o777)
    with pytest.raises(PreparedRuntimeError, match="interpreter is unsafe"):
        runtime_artifact_fingerprint(release)


def test_fingerprint_rejects_external_dependency_link(tmp_path: Path) -> None:
    release = tmp_path / "release"
    runtime = release / ".venv"
    runtime.mkdir(mode=0o700, parents=True)
    external = tmp_path / "external-dependency.py"
    external.write_text("unbound code")
    (runtime / "dependency.py").symlink_to(external)
    with pytest.raises(PreparedRuntimeError, match="dependency link"):
        runtime_artifact_fingerprint(release)


@pytest.mark.parametrize("unsafe", ["root-link", "parent-link", "fifo", "writable"])
def test_fingerprint_rejects_unsafe_generated_entries(
    tmp_path: Path, unsafe: str
) -> None:
    runtime = tmp_path / ".venv"
    if unsafe == "root-link":
        runtime.symlink_to(tmp_path, target_is_directory=True)
    elif unsafe == "parent-link":
        (tmp_path / "openclaw").symlink_to(tmp_path, target_is_directory=True)
    else:
        runtime.mkdir(mode=0o700)
        if unsafe == "fifo":
            os.mkfifo(runtime / "pipe")
        else:
            item = runtime / "writable"
            item.write_text("changed")
            item.chmod(0o666)
    with pytest.raises(PreparedRuntimeError):
        runtime_artifact_fingerprint(tmp_path)


def test_fingerprint_rejects_alias_to_a_different_interpreter(tmp_path: Path) -> None:
    release = tmp_path / "release"
    runtime = release / ".venv" / "bin"
    runtime.mkdir(mode=0o700, parents=True)
    interpreter = tmp_path / "pinned-python"
    interpreter.write_bytes(b"pinned interpreter")
    other = tmp_path / "other-python"
    other.write_bytes(b"different interpreter")
    (runtime / "python").symlink_to(interpreter)
    (runtime / "python3").symlink_to(other)
    with pytest.raises(PreparedRuntimeError, match="dependency link"):
        runtime_artifact_fingerprint(release)
