from __future__ import annotations

import os
from pathlib import Path

import pytest

from bootstrap.prepared_runtime import (
    PreparedRuntimeError,
    runtime_artifact_fingerprint,
)


def test_fingerprint_tracks_link_targets_without_following_them(tmp_path: Path) -> None:
    runtime = tmp_path / ".venv"
    runtime.mkdir(mode=0o700)
    link = runtime / "python"
    link.symlink_to("/not-opened/first-python")
    before = runtime_artifact_fingerprint(tmp_path)
    assert runtime_artifact_fingerprint(tmp_path) == before
    link.unlink()
    link.symlink_to("/not-opened/second-python")
    assert runtime_artifact_fingerprint(tmp_path) != before


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
