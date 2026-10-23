from __future__ import annotations

import os
from pathlib import Path

import pytest

from bootstrap.prepared_runtime import (
    PreparedRuntimeError,
    runtime_artifact_fingerprint,
)


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
    before = runtime_artifact_fingerprint(release)
    assert runtime_artifact_fingerprint(release) == before
    interpreter.write_bytes(b"changed interpreter")
    assert runtime_artifact_fingerprint(release) != before


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
