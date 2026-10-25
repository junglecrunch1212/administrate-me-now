"""Metadata-only transaction budgeting, including same-volume peak usage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bootstrap import storage_headroom as storage
from bootstrap.contracts import DeploymentPaths

GIB = 1024**3


def _state(tmp_path: Path) -> DeploymentPaths:
    paths = DeploymentPaths.from_root(tmp_path / "instance", production=False)
    paths.create()
    # Sparse metadata reproduces the production-size state without allocating
    # private data or gigabytes on the hosted worker.
    state = paths.instance_dir / "events.db"
    with state.open("wb") as handle:
        handle.truncate(232 * GIB // 100)
    return paths


def test_five_gib_doctor_floor_does_not_cover_backup_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _state(tmp_path)
    monkeypatch.setattr(storage, "_volume", lambda _path: (1, 6 * GIB))
    with pytest.raises(storage.StorageHeadroomError, match="headroom insufficient"):
        storage.require_deployment_headroom(paths, backup_copies=3)
    assert not list(paths.backup_dir.iterdir())
    assert not list(paths.app_dir.iterdir())


def test_shared_volume_accumulates_backups_tar_and_extraction_before_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _state(tmp_path)
    monkeypatch.setattr(storage, "_volume", lambda _path: (1, 30 * GIB))
    proof = storage.require_deployment_headroom(paths, backup_copies=3)
    check = proof["checks"][0]
    assert len(proof["checks"]) == 1
    assert check["required_bytes"] == 5 * GIB + 5 * proof["archive_estimate_bytes"]
    assert check["required_bytes"] > 16 * GIB
    assert check["purposes"] == ["runtime", "encrypted_backups", "restore_workspace"]
    assert str(tmp_path) not in json.dumps(proof)
    monkeypatch.setattr(
        storage, "_volume", lambda _path: (1, check["required_bytes"] - 1)
    )
    with pytest.raises(storage.StorageHeadroomError):
        storage.require_deployment_headroom(paths, backup_copies=3)
    monkeypatch.setattr(storage, "_volume", lambda _path: (1, check["required_bytes"]))
    storage.require_deployment_headroom(paths, backup_copies=3)


@pytest.mark.parametrize("short_device", [None, 1, 2, 3])
def test_each_separate_destination_requires_its_own_peak_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_device: int | None
) -> None:
    paths = _state(tmp_path)
    temp = tmp_path / "temporary-volume"
    temp.mkdir()
    monkeypatch.setattr(storage.tempfile, "gettempdir", lambda: str(temp))
    devices = {paths.root: 1, paths.backup_dir: 2, temp: 3}

    def volume(path: Path) -> tuple[int, int]:
        device = devices[path]
        return device, (4 if device == short_device else 30) * GIB

    monkeypatch.setattr(storage, "_volume", volume)
    if short_device:
        with pytest.raises(storage.StorageHeadroomError):
            storage.require_deployment_headroom(paths, backup_copies=3)
    else:
        proof = storage.require_deployment_headroom(paths, backup_copies=3)
        assert len(proof["checks"]) == 3
        assert sorted(check["required_bytes"] for check in proof["checks"]) == [
            5 * GIB,
            5 * GIB + 2 * proof["archive_estimate_bytes"],
            5 * GIB + 3 * proof["archive_estimate_bytes"],
        ]


def test_preparation_allowance_and_remaining_phase_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _state(tmp_path)
    source = tmp_path / "installed-release"
    source.mkdir()
    with (source / "dependencies").open("wb") as handle:
        handle.truncate(2 * GIB)
    monkeypatch.setattr(storage, "_volume", lambda _path: (1, 40 * GIB))
    prepared = storage.require_deployment_headroom(
        paths, backup_copies=2, release_sources=(source,)
    )
    remaining = storage.require_deployment_headroom(paths, backup_copies=2)
    assert prepared["preparation_estimate_bytes"] > 3 * GIB
    assert prepared["checks"][0]["required_bytes"] == (
        remaining["checks"][0]["required_bytes"]
        + 2 * prepared["preparation_estimate_bytes"]
    )
    restore = storage.require_deployment_headroom(paths, backup_copies=1)
    assert (
        remaining["checks"][0]["required_bytes"]
        - restore["checks"][0]["required_bytes"]
        == prepared["archive_estimate_bytes"]
    )


def test_scan_uses_metadata_and_backup_cache_exclusions_without_following_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance", production=False)
    paths.create()
    state = paths.instance_dir
    (state / "durable.db").write_bytes(b"durable")
    cache = state / "openclaw/runtime/agents/service/agent/codex-home/tmp"
    cache.mkdir(parents=True)
    (cache / "disposable").write_bytes(b"temporary" * 100)
    (cache.parent / "logs_1.sqlite").write_bytes(b"temporary")
    (cache.parent / "sessions.db").write_bytes(b"retained")
    skills = state / "openclaw/runtime/plugin-skills"
    skills.mkdir()
    (skills / "generated").symlink_to(tmp_path / "absent")
    monkeypatch.setattr(Path, "read_bytes", lambda _self: pytest.fail("content read"))
    assert storage._footprint(state, state=True)[0] == len(b"durableretained")
    (state / "unsafe").symlink_to(tmp_path)
    with pytest.raises(storage.StorageHeadroomError, match="state symlink"):
        storage._footprint(state, state=True)


def test_unavailable_destination_fails_before_any_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _state(tmp_path)

    def unavailable(_path: Path) -> tuple[int, int]:
        raise OSError("synthetic disk probe failure")

    monkeypatch.setattr(storage, "_volume", unavailable)
    with pytest.raises(storage.StorageHeadroomError, match="could not be inspected"):
        storage.require_deployment_headroom(paths, backup_copies=1)
    assert not list(paths.backup_dir.iterdir())
