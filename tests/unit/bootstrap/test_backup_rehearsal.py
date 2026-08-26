"""Fail-closed unit coverage for disposable backup rehearsal boundaries."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import multiprocessing as mp
import os
import signal
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from adminme.projections.runner import DEFAULT_PROJECTION_CLASSES
from bootstrap.backup_restore import (
    BackupError,
    BackupManager,
    _authenticate_backup,
    _write_new_private_file,
    _extract_and_validate_payload,
)
from bootstrap.contracts import DeploymentPaths
from tests.fixtures.backup_rehearsal_processes import (
    guardian_hung_extract_fixture as _guardian_hung_extract_fixture,
)
from tests.fixtures.backup_rehearsal_processes import (
    guardian_replay_fixture as _guardian_replay_fixture,
)
from tests.fixtures.backup_rehearsal_processes import (
    run_guardian_from_disposable_parent as _run_guardian_from_disposable_parent,
)


_NESTED_SPAWN_MARKER_TIMEOUT_S = 30


def _manifest(files: list[dict[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": "administrateme-backup-manifest-v1",
        "created_at": "2026-08-26T00:00:00+00:00",
        "file_count": len(files),
        "files": files,
        "projection_names": [
            str(projection.name) for projection in DEFAULT_PROJECTION_CLASSES
        ],
        "event_log_sha256": next(
            item["sha256"] for item in files if item["path"] == "state/events/events.db"
        ),
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    document["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return document


def _archive(
    *,
    declared_payload: bytes = b"encrypted-event-log-fixture",
    archived_payload: bytes | None = None,
    extras: tuple[tuple[str, bytes], ...] = (),
) -> bytes:
    declared = {
        "path": "state/events/events.db",
        "size": len(declared_payload),
        "sha256": hashlib.sha256(declared_payload).hexdigest(),
    }
    manifest = _manifest([declared])
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        event_payload = (
            declared_payload if archived_payload is None else archived_payload
        )
        event_info = tarfile.TarInfo(declared["path"])
        event_info.size = len(event_payload)
        archive.addfile(event_info, io.BytesIO(event_payload))
        for name, payload in extras:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        encoded = json.dumps(manifest, sort_keys=True).encode()
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(encoded)
        archive.addfile(manifest_info, io.BytesIO(encoded))
    return buffer.getvalue()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_process_marker(
    controller: mp.Process,
    marker: Path,
    *,
    mode: str,
) -> None:
    """Wait through nested spawn startup while failing early on controller death."""

    deadline = time.monotonic() + _NESTED_SPAWN_MARKER_TIMEOUT_S
    while not marker.exists():
        if not controller.is_alive() or time.monotonic() >= deadline:
            controller.kill()
            controller.join()
            raise AssertionError(f"guardian did not reach {mode}")
        time.sleep(0.01)


def _expose_spawn_fixture_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the stable process-target module importable in fresh children."""

    repository_root = str(Path(__file__).resolve().parents[3])
    # ``multiprocessing.spawn`` serializes the parent's current ``sys.path``
    # into preparation data, so update that path as well as the environment
    # inherited by deeper guardian/worker interpreters.
    monkeypatch.syspath_prepend(repository_root)
    current = os.environ.get("PYTHONPATH", "")
    entries = [entry for entry in current.split(os.pathsep) if entry]
    if repository_root not in entries:
        entries.insert(0, repository_root)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(entries))


@pytest.fixture(autouse=True)
def _stable_spawn_fixture_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every process fixture importable under pytest import modes."""

    _expose_spawn_fixture_module(monkeypatch)


def _marked_stage(
    parent: Path,
    *,
    fingerprint: str,
    nonce: str,
) -> Path:
    stage = parent / f"run-{nonce}"
    stage.mkdir(mode=0o700)
    (stage / "payload").mkdir(mode=0o700)
    marker = stage / ".administrateme-rehearsal-v1"
    marker.write_text(
        json.dumps(
            {
                "contract": "backup-rehearsal-v1",
                "instance_fingerprint": fingerprint,
                "nonce": nonce,
            }
        ),
        encoding="utf-8",
    )
    marker.chmod(0o600)
    (stage / ".activity.lock").touch(mode=0o600)
    return stage


def test_strict_archive_extracts_only_exact_manifest_members(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    manifest = _extract_and_validate_payload(_archive(), staging)

    assert manifest["file_count"] == 1
    assert (staging / "state" / "events" / "events.db").read_bytes() == (
        b"encrypted-event-log-fixture"
    )


def test_strict_archive_rejects_undeclared_member(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    with pytest.raises(BackupError, match="inventories differ"):
        _extract_and_validate_payload(
            _archive(extras=(("state/unlisted.bin", b"unexpected"),)),
            staging,
        )


def test_strict_archive_rejects_casefold_collision(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    with pytest.raises(BackupError, match="duplicate member names"):
        _extract_and_validate_payload(
            _archive(
                extras=(("state/events/Events.db", b"casefold-collision"),),
            ),
            staging,
        )


def test_strict_archive_rejects_content_that_differs_from_manifest(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    with pytest.raises(BackupError, match="size differs from manifest"):
        _extract_and_validate_payload(
            _archive(archived_payload=b"different"),
            staging,
        )


def test_strict_archive_cannot_escape_through_replaced_intermediate_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    original_open = os.open
    replaced = False

    def _replace_before_file_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        if path == "events.db" and dir_fd is not None and not replaced:
            replaced = True
            events = staging / "state" / "events"
            events.rename(staging / "state" / "original-events")
            events.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", _replace_before_file_open)

    with pytest.raises(BackupError, match="directory changed"):
        _extract_and_validate_payload(_archive(), staging)

    assert replaced is True
    assert not (outside / "events.db").exists()


def test_backup_authentication_rejects_distinct_wrong_key(tmp_path: Path) -> None:
    source = tmp_path / "wrong-key.ambackup"
    key = b"a" * 32
    nonce = b"n" * 12
    payload = b"authenticated-backup-payload"
    envelope = {
        "format": "administrateme-backup-v1",
        "version": 1,
        "created_at": "2026-08-26T00:00:00+00:00",
        "manifest_sha256": "0" * 64,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(
            AESGCM(key).encrypt(nonce, payload, b"administrateme-backup-v1")
        ).decode("ascii"),
    }
    source.write_text(json.dumps(envelope), encoding="utf-8")
    source.chmod(0o600)

    with pytest.raises(BackupError, match="decryption or authentication failed"):
        _authenticate_backup(source, b"b" * 32)


def test_failed_post_link_receipt_publish_removes_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "receipt.json"
    original_fsync = os.fsync
    calls = 0

    def _fail_directory_fsync_once(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", _fail_directory_fsync_once)

    with pytest.raises(OSError, match="simulated directory fsync failure"):
        _write_new_private_file(target, b'{"status":"healthy"}')

    assert not target.exists()


def test_failed_temporary_unlink_rolls_back_published_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "receipt.json"
    original_unlink = Path.unlink
    failed = False

    def _fail_temporary_unlink_once(
        path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        nonlocal failed
        if not failed and path.name.startswith(".receipt.json."):
            failed = True
            raise OSError("simulated temporary unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_temporary_unlink_once)

    with pytest.raises(OSError, match="simulated temporary unlink failure"):
        _write_new_private_file(target, b'{"status":"healthy"}')

    assert failed is True
    assert not target.exists()
    assert list(tmp_path.glob(".receipt.json.*")) == []


@pytest.mark.parametrize(
    ("mode", "active_marker"),
    (
        ("between-phases", ".extraction-complete"),
        ("hung-extraction", "payload/hung-extraction.pid"),
        ("hung-replay", "payload/state/hung-replay.pid"),
    ),
)
def test_guardian_removes_plaintext_after_controller_death_at_each_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    active_marker: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    manager = BackupManager(paths, root=tmp_path, test_mode=True)
    parent = tmp_path / "rehearsals"
    parent.mkdir(mode=0o700)
    fingerprint = "f" * 64
    nonce = "d" * 32
    stage = parent / f"run-{nonce}"
    stage.mkdir(mode=0o700)
    (stage / "payload").mkdir(mode=0o700)
    (stage / ".administrateme-rehearsal-v1").write_text(
        json.dumps(
            {
                "contract": "backup-rehearsal-v1",
                "instance_fingerprint": fingerprint,
                "nonce": nonce,
            }
        ),
        encoding="utf-8",
    )
    (stage / ".administrateme-rehearsal-v1").chmod(0o600)
    (stage / ".activity.lock").touch(mode=0o600)

    baseline_children = {process.pid for process in mp.active_children()}
    context = mp.get_context("spawn")
    controller = context.Process(
        target=_run_guardian_from_disposable_parent,
        args=(str(stage), mode),
    )
    controller.start()
    marker = stage / active_marker
    _wait_for_process_marker(controller, marker, mode=mode)

    guardian_pid = int((stage / ".guardian.pid").read_text("ascii"))
    worker_pid = int(marker.read_text("ascii")) if marker.suffix == ".pid" else None
    if mode == "between-phases":
        assert not (stage / "payload" / "state" / "replay-complete").exists()
    with pytest.raises(BackupError, match="orphan rehearsal child"):
        manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001

    controller.kill()
    controller.join(timeout=5)
    assert controller.exitcode is not None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        worker_stopped = worker_pid is None or not _pid_exists(worker_pid)
        if (
            not (stage / "payload").exists()
            and not _pid_exists(guardian_pid)
            and worker_stopped
        ):
            break
        time.sleep(0.02)
    assert not (stage / "payload").exists()
    assert not _pid_exists(guardian_pid)
    if worker_pid is not None:
        assert not _pid_exists(worker_pid)

    # Control evidence intentionally survives an orphan cleanup; the next
    # controller validates it under the inactive activity lock and removes it.
    assert stage.exists()
    manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001
    assert not stage.exists()
    assert {process.pid for process in mp.active_children()} == baseline_children


def test_guardian_phase_timeout_reaps_worker_before_payload_cleanup(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "rehearsals"
    parent.mkdir(mode=0o700)
    stage = _marked_stage(
        parent,
        fingerprint="f" * 64,
        nonce="e" * 32,
    )
    baseline_children = {process.pid for process in mp.active_children()}

    result, guardian_pid = BackupManager._run_rehearsal_guardian(  # noqa: SLF001
        "fixture-source",
        str(stage),
        "a" * 64,
        0.05,
        b"k" * 64,
        timeout_s=5,
        extraction_target=_guardian_hung_extract_fixture,
        replay_target=_guardian_replay_fixture,
    )

    assert result["status"] == "failed"
    assert guardian_pid > 0
    assert not _pid_exists(guardian_pid)
    assert not (stage / "payload").exists()
    assert {process.pid for process in mp.active_children()} == baseline_children


@pytest.mark.parametrize(
    ("mode", "active_marker"),
    (
        ("hung-extraction", "payload/hung-extraction.pid"),
        ("hung-replay", "payload/state/hung-replay.pid"),
    ),
)
def test_worker_lease_survives_guardian_death(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    active_marker: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    manager = BackupManager(paths, root=tmp_path, test_mode=True)
    parent = tmp_path / "rehearsals"
    parent.mkdir(mode=0o700)
    fingerprint = "f" * 64
    stage = _marked_stage(
        parent,
        fingerprint=fingerprint,
        nonce="a" * 32,
    )
    context = mp.get_context("spawn")
    controller = context.Process(
        target=_run_guardian_from_disposable_parent,
        args=(str(stage), mode),
    )
    controller.start()
    marker = stage / active_marker
    _wait_for_process_marker(controller, marker, mode=mode)

    guardian_pid = int((stage / ".guardian.pid").read_text("ascii"))
    worker_pid = int(marker.read_text("ascii"))
    os.kill(guardian_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while _pid_exists(guardian_pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(guardian_pid)
    assert _pid_exists(worker_pid)
    with pytest.raises(BackupError, match="orphan rehearsal child"):
        manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001
    assert (stage / "payload").exists()

    controller.kill()
    controller.join(timeout=5)
    os.kill(worker_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while _pid_exists(worker_pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(worker_pid)
    manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001
    assert not stage.exists()


def test_stale_cleanup_keeps_marker_when_payload_removal_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    manager = BackupManager(paths, root=tmp_path, test_mode=True)
    parent = tmp_path / "rehearsals"
    parent.mkdir(mode=0o700)
    fingerprint = "f" * 64
    stage = _marked_stage(
        parent,
        fingerprint=fingerprint,
        nonce="b" * 32,
    )
    (stage / "payload" / "plaintext").write_text("fixture", encoding="utf-8")
    original_rmtree = shutil.rmtree

    def _interrupt_payload(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path) == stage / "payload":
            raise OSError("simulated payload cleanup interruption")
        original_rmtree(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(shutil, "rmtree", _interrupt_payload)
        with pytest.raises(OSError, match="payload cleanup interruption"):
            manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001

    assert (stage / ".administrateme-rehearsal-v1").exists()
    assert (stage / "payload").exists()
    manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001
    assert not stage.exists()


@pytest.mark.parametrize(
    "interrupted_control",
    (".administrateme-rehearsal-v1", ".activity.lock"),
)
def test_markerless_control_residue_is_recoverable_only_after_payload_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted_control: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    manager = BackupManager(paths, root=tmp_path, test_mode=True)
    parent = tmp_path / "rehearsals"
    parent.mkdir(mode=0o700)
    fingerprint = "f" * 64
    stage = _marked_stage(
        parent,
        fingerprint=fingerprint,
        nonce="c" * 32,
    )
    (stage / "payload" / "plaintext").write_text("fixture", encoding="utf-8")
    original_unlink = Path.unlink

    def _interrupt_control(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == stage / interrupted_control:
            assert not (stage / "payload").exists()
            original_unlink(path, *args, **kwargs)
            raise OSError("simulated control cleanup interruption")
        original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", _interrupt_control)
        with pytest.raises(OSError, match="control cleanup interruption"):
            manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001

    assert stage.exists()
    assert not (stage / "payload").exists()
    assert not (stage / ".administrateme-rehearsal-v1").exists()
    manager._remove_validated_stale_stages(parent, fingerprint)  # noqa: SLF001
    assert not stage.exists()
