"""Installed bridge enrollment uses exact, independently bound source identity."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from adminme.bridge_enrollment import BridgeEnrollmentError, reviewed_release
from bootstrap.bridge_enrollment import (
    BridgeEnrollmentError as InstallerError,
    _git_commit,
)
from bootstrap.source_identity import SOURCE_MARKER_NAME, write_source_marker


ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


def _release(tmp_path: Path) -> Path:
    release = tmp_path / "installed"
    release.mkdir(mode=0o700)
    write_source_marker(release, COMMIT)
    return release


def test_deployment_binding_wins_over_ambient_helper_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    monkeypatch.setenv("ADMINME_REVIEWED_COMMIT", "b" * 40)
    monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
    assert (
        reviewed_release(
            release, deployment_release_commit=COMMIT, require_deployment_release=True
        )
        == COMMIT
    )


@pytest.mark.parametrize("binding", [None, "", "not-a-commit", "b" * 40])
def test_production_binding_cannot_fall_back_to_environment_or_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding: str | None
) -> None:
    release = _release(tmp_path)
    monkeypatch.setenv("ADMINME_REVIEWED_COMMIT", COMMIT)
    with pytest.raises(BridgeEnrollmentError) as error:
        reviewed_release(
            release, deployment_release_commit=binding, require_deployment_release=True
        )
    assert error.value.code == "release_unavailable"


@pytest.mark.parametrize(
    "mutation", ["missing", "mismatch", "public", "symlink", "hardlink", "root-link"]
)
def test_installed_source_marker_is_mandatory_and_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    release = _release(tmp_path)
    marker = release / SOURCE_MARKER_NAME
    monkeypatch.setenv("ADMINME_REVIEWED_COMMIT", COMMIT)
    if mutation == "missing":
        marker.unlink()
    elif mutation == "mismatch":
        write_source_marker(release, "b" * 40)
    elif mutation == "public":
        marker.chmod(0o644)
    elif mutation == "symlink":
        original = tmp_path / "original-marker"
        marker.rename(original)
        marker.symlink_to(original)
    elif mutation == "hardlink":
        os.link(marker, tmp_path / "linked-marker")
    else:
        link = tmp_path / "linked-release"
        link.symlink_to(release, target_is_directory=True)
        release = link
    with pytest.raises(BridgeEnrollmentError) as error:
        reviewed_release(release, deployment_release_commit=COMMIT)
    assert error.value.code == "release_unavailable"


def test_packaged_helper_and_installer_validate_the_same_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    monkeypatch.setenv("ADMINME_REVIEWED_COMMIT", COMMIT)
    assert reviewed_release(release) == COMMIT
    assert _git_commit(release) == COMMIT
    write_source_marker(release, "b" * 40)
    with pytest.raises(BridgeEnrollmentError):
        reviewed_release(release)
    with pytest.raises(InstallerError):
        _git_commit(release)


def test_development_checkout_uses_git_without_ambient_repository_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADMINME_REVIEWED_COMMIT", raising=False)
    expected = subprocess.run(
        ["/usr/bin/git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing-repository"))
    assert reviewed_release(ROOT) == expected


def test_marker_alone_cannot_select_an_unbound_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    monkeypatch.delenv("ADMINME_REVIEWED_COMMIT", raising=False)
    with pytest.raises(BridgeEnrollmentError) as error:
        reviewed_release(release)
    assert error.value.code == "release_unavailable"
