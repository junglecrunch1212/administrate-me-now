"""Helper distribution is fixed to verified public bytes and the active release."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from adminme.bridge_helper_distribution import (
    ARCHIVE,
    CONTRACT,
    distribution_root,
    read_distribution,
)


COMMIT = "a" * 40


def publish_fixture(instance: Path, commit: str = COMMIT) -> bytes:
    root = distribution_root(instance) / commit
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.parent.chmod(0o700)
    root.parent.parent.chmod(0o700)
    payload = b"inert hosted helper archive; contains no executable or enrollment data"
    (root / ARCHIVE).write_bytes(payload)
    (root / ARCHIVE).chmod(0o600)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "contract": CONTRACT,
                "filename": ARCHIVE,
                "release_commit": commit,
                "architecture": "arm64",
                "minimum_macos": "15.0",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    )
    (root / "manifest.json").chmod(0o600)
    return payload


def test_exact_publication(tmp_path: Path) -> None:
    instance = tmp_path / "state"
    payload = publish_fixture(instance)
    manifest, result = read_distribution(instance, COMMIT)
    assert result == payload
    assert manifest["release_commit"] == COMMIT
    with pytest.raises(FileNotFoundError):
        read_distribution(instance, "b" * 40)


@pytest.mark.parametrize(
    "fault", ["tamper", "manifest_mismatch", "symlink", "writable", "oversize_metadata"]
)
def test_rejects_untrusted_publication(tmp_path: Path, fault: str) -> None:
    instance = tmp_path / "state"
    publish_fixture(instance)
    root = distribution_root(instance) / COMMIT
    archive = root / ARCHIVE
    if fault == "tamper":
        archive.write_bytes(b"changed")
    elif fault == "manifest_mismatch":
        path = root / "manifest.json"
        data = json.loads(path.read_text())
        data["release_commit"] = "b" * 40
        path.write_text(json.dumps(data))
    elif fault == "symlink":
        outside = tmp_path / "outside"
        archive.rename(outside)
        archive.symlink_to(outside)
    elif fault == "writable":
        archive.chmod(0o666)
    else:
        (root / "manifest.json").write_text(" " * 4097)
    with pytest.raises(ValueError):
        read_distribution(instance, COMMIT)


def test_tailscale_app_fallback_does_not_require_cli_shortcut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adminme.tailscale_local import tailscale_executable

    monkeypatch.setattr("adminme.tailscale_local.shutil.which", lambda _: None)
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: (
            str(path) == "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
        ),
    )
    monkeypatch.setattr("adminme.tailscale_local.os.access", lambda *_: True)
    assert (
        tailscale_executable() == "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    )
    monkeypatch.setattr("adminme.tailscale_local.os.access", lambda *_: False)
    assert tailscale_executable() == "tailscale"
