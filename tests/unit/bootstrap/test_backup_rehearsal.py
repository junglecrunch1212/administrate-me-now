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
    _BACKUP_V2_MAGIC,
    BackupError,
    BackupManager,
    _authenticate_backup,
    _authenticate_backup_to_path,
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


def test_backup_omits_only_agent_codex_cache_and_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.create()
    events = paths.instance_dir / "events"
    events.mkdir()
    (events / "events.db").write_bytes(b"encrypted-event-log")

    generated = (
        paths.instance_dir
        / "openclaw"
        / "runtime"
        / "agents"
        / "main"
        / "agent"
        / "codex-home"
        / "tmp"
        / "arg0"
        / "codex-generated"
    )
    generated.mkdir(parents=True)
    target = tmp_path / "launcher-target"
    target.write_text("reviewed launcher", encoding="utf-8")
    (generated / "apply_patch").symlink_to(target)
    (generated / "transient.txt").write_text("disposable", encoding="utf-8")
    codex_home = generated.parents[2]
    generated_dot_tmp = codex_home / ".tmp" / "launcher-cache"
    generated_dot_tmp.parent.mkdir()
    generated_dot_tmp.write_text("disposable", encoding="utf-8")
    for name in ("logs_2.sqlite", "logs_2.sqlite-wal", "logs_2.sqlite-shm"):
        (codex_home / name).write_bytes(b"disposable telemetry")
    durable_session = codex_home / "sessions" / "session.jsonl"
    durable_session.parent.mkdir()
    durable_session.write_text("durable session", encoding="utf-8")
    durable_state = codex_home / "state_5.sqlite"
    durable_state.write_bytes(b"durable state")

    manager = BackupManager(
        paths,
        root=Path(__file__).resolve().parents[3],
        test_mode=True,
    )
    _payload, manifest = manager._build_archive()
    archived = {str(item["path"]) for item in manifest["files"]}
    assert "state/events/events.db" in archived
    assert not any("/codex-home/tmp/" in name for name in archived)
    assert not any("/codex-home/.tmp/" in name for name in archived)
    assert not any("/codex-home/logs_2.sqlite" in name for name in archived)
    assert any("/codex-home/sessions/session.jsonl" in name for name in archived)
    assert any("/codex-home/state_5.sqlite" in name for name in archived)

    unsafe = paths.instance_dir / "config" / "unsafe-link"
    unsafe.parent.mkdir()
    unsafe.symlink_to(target)
    with pytest.raises(BackupError, match="backup refuses symlink: unsafe-link"):
        manager._build_archive()


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
) -> str:
    """Read one complete marker while failing early on controller death."""

    deadline = time.monotonic() + _NESTED_SPAWN_MARKER_TIMEOUT_S
    while True:
        try:
            marker_value = marker.read_text("ascii").strip()
        except FileNotFoundError:
            marker_value = ""
        if marker_value:
            return marker_value
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

    authenticated, decrypted, _artifact_sha256 = _authenticate_backup(source, key)
    assert authenticated["format"] == "administrateme-backup-v1"
    assert decrypted == payload

    with pytest.raises(BackupError, match="decryption or authentication failed"):
        _authenticate_backup(source, b"b" * 32)


def test_streaming_backup_round_trip_and_wrong_key_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.create()
    events = paths.instance_dir / "events"
    events.mkdir()
    event_payload = b"encrypted-event-log" * 100_000
    (events / "events.db").write_bytes(event_payload)
    manager = BackupManager(
        paths,
        root=Path(__file__).resolve().parents[3],
        test_mode=True,
    )
    entries, manifest = manager._inventory_archive()
    destination = paths.backup_dir / "streaming.ambackup"
    key = b"s" * 32

    manager._write_streaming_backup(
        destination,
        entries=entries,
        manifest=manifest,
        backup_key=key,
    )

    assert destination.read_bytes().startswith(_BACKUP_V2_MAGIC)
    envelope, archive_payload, _artifact_sha256 = _authenticate_backup(
        destination,
        key,
    )
    assert envelope["format"] == "administrateme-backup-v2"
    assert envelope["version"] == 2
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    extracted = _extract_and_validate_payload(archive_payload, staging)
    assert extracted["manifest_sha256"] == manifest["manifest_sha256"]
    assert (staging / "state" / "events" / "events.db").read_bytes() == (event_payload)

    plaintext = tmp_path / "wrong-key.tar"
    with pytest.raises(BackupError, match="decryption or authentication failed"):
        _authenticate_backup_to_path(destination, b"w" * 32, plaintext)
    assert not plaintext.exists()

    # Authentication-only never publishes plaintext, including on a bad key.
    inventory_before = set(tmp_path.rglob("*"))
    device_mode = os.stat(os.devnull).st_mode
    verified, digest = _authenticate_backup_to_path(destination, key, None)
    assert verified == envelope
    assert digest == _artifact_sha256
    with pytest.raises(BackupError, match="decryption or authentication failed"):
        _authenticate_backup_to_path(destination, b"w" * 32, None)
    assert set(tmp_path.rglob("*")) == inventory_before
    assert os.stat(os.devnull).st_mode == device_mode


def test_streaming_backup_refuses_source_drift_without_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.create()
    events = paths.instance_dir / "events"
    events.mkdir()
    event_log = events / "events.db"
    event_log.write_bytes(b"first")
    manager = BackupManager(
        paths,
        root=Path(__file__).resolve().parents[3],
        test_mode=True,
    )
    entries, manifest = manager._inventory_archive()
    event_log.write_bytes(b"second")
    destination = paths.backup_dir / "drifted.ambackup"

    with pytest.raises(BackupError, match="changed before archive streaming"):
        manager._write_streaming_backup(
            destination,
            entries=entries,
            manifest=manifest,
            backup_key=b"d" * 32,
        )

    assert not destination.exists()
    assert not list(paths.backup_dir.glob(".drifted.ambackup.*"))


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
    marker_value = _wait_for_process_marker(controller, marker, mode=mode)

    guardian_pid = int((stage / ".guardian.pid").read_text("ascii"))
    worker_pid = int(marker_value) if marker.suffix == ".pid" else None
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
    marker_value = _wait_for_process_marker(controller, marker, mode=mode)

    guardian_pid = int((stage / ".guardian.pid").read_text("ascii"))
    worker_pid = int(marker_value)
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


def _reusable_recovery_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace
    from tests.unit.bootstrap.test_candidate_readiness import (
        NOW,
        _arguments,
        _private_write,
    )
    from bootstrap.backup_restore import _recovery_key_binding, _state_inventory_sha256
    from bootstrap.candidate_readiness import (
        _validate_rehearsal,
        capture_deployment_snapshot,
    )

    paths, candidate, commit, receipt_path, artifact, *_ = _arguments(
        tmp_path, monkeypatch
    )
    _private_write(
        paths.instance_dir / "events" / "events.db", b"encrypted-event-fixture"
    )
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    manager = BackupManager(paths, root=candidate, test_mode=True)
    keys = {"backup": b"b" * 32, "events": b"e" * 32}
    manager.store = SimpleNamespace(get=lambda name: keys[name])
    monkeypatch.setattr(
        manager, "_references", lambda: {"backup": "backup", "event_log": "events"}
    )
    calls: list[str] = []
    manager._services = SimpleNamespace(  # noqa: SLF001
        status=lambda: SimpleNamespace(status="healthy"),
        stop=lambda: calls.append("stop"),
        start=lambda: calls.append("start"),
    )
    _, manifest = manager._inventory_archive()  # noqa: SLF001
    receipt = json.loads(receipt_path.read_bytes())
    receipt["artifact"].update(
        manifest_sha256=manifest["manifest_sha256"],
        state_inventory_sha256=_state_inventory_sha256(manifest),
        key_binding_sha256=_recovery_key_binding(
            keys["backup"], keys["events"], receipt["artifact"]["sha256"]
        ),
    )
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _private_write(receipt_path, json.dumps(receipt, sort_keys=True).encode())
    _, receipt_sha, backup_sha, watermark_sha, _ = _validate_rehearsal(
        paths,
        receipt_path,
        artifact,
        candidate_commit=commit,
        snapshot=capture_deployment_snapshot(paths),
        now=NOW,
    )
    pending = {
        "recovery_inputs": {"receipt": receipt_path.name, "backup": artifact.name},
        "deployment_binding": {
            "preliminary_rehearsal_receipt_sha256": receipt_sha,
            "preliminary_backup_artifact_sha256": backup_sha,
            "preliminary_rehearsal_watermark_sha256": watermark_sha,
        },
    }
    monkeypatch.setattr(
        "bootstrap.backup_restore.datetime", SimpleNamespace(now=lambda _zone: NOW)
    )
    return (
        manager,
        paths,
        candidate,
        pending,
        receipt_path,
        artifact,
        keys,
        calls,
        receipt,
    )


def test_recovery_reuses_exact_state_and_original_proof_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        manager,
        _paths,
        candidate,
        pending,
        receipt_path,
        artifact,
        _keys,
        calls,
        receipt,
    ) = _reusable_recovery_fixture(tmp_path, monkeypatch)
    before = receipt_path.read_bytes(), artifact.read_bytes()
    reused = manager.reuse_recovery_evidence(pending=pending, candidate=candidate)
    assert reused is not None
    backup, proof, selected_path = reused
    assert backup["status"] == "reused"
    assert backup["path"] == str(artifact)
    assert selected_path == receipt_path
    assert proof["tested_at"] == receipt["tested_at"]
    assert before == (receipt_path.read_bytes(), artifact.read_bytes())
    assert calls == ["stop", "start"]


@pytest.mark.parametrize(
    "drift",
    [
        "state",
        "added",
        "removed",
        "config",
        "candidate",
        "receipt",
        "artifact",
        "expired",
        "backup-key",
        "event-key",
        "legacy",
    ],
)
def test_recovery_cache_miss_preserves_normal_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    from datetime import timedelta
    from types import SimpleNamespace
    from tests.unit.bootstrap.test_candidate_readiness import NOW

    manager, paths, candidate, pending, receipt, artifact, keys, calls, _ = (
        _reusable_recovery_fixture(tmp_path, monkeypatch)
    )
    if drift == "state":
        (paths.instance_dir / "events/events.db").write_bytes(b"changed-event-history")
    elif drift == "added":
        (paths.instance_dir / "new-state").write_bytes(b"new-state")
    elif drift == "removed":
        (paths.instance_dir / "events/events.db").unlink()
    elif drift == "config":
        (paths.instance_dir / "config/adapters.json").write_text('{"adapters":[{}]}')
    elif drift == "candidate":
        (candidate / "bootstrap/candidate_readiness.py").write_text("# changed")
    elif drift == "receipt":
        receipt.write_bytes(receipt.read_bytes() + b"\n")
    elif drift == "artifact":
        artifact.write_bytes(b"changed-backup")
    elif drift == "expired":
        monkeypatch.setattr(
            "bootstrap.backup_restore.datetime",
            SimpleNamespace(now=lambda _zone: NOW + timedelta(days=32)),
        )
    elif drift == "backup-key":
        keys["backup"] = b"x" * 32
    elif drift == "event-key":
        keys["events"] = b"x" * 32
    else:
        pending.pop("recovery_inputs")
    assert manager.reuse_recovery_evidence(pending=pending, candidate=candidate) is None
    assert calls == []


def test_recovery_rechecks_state_after_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, paths, candidate, pending, _receipt, _artifact, _keys, calls, _ = (
        _reusable_recovery_fixture(tmp_path, monkeypatch)
    )

    def stop() -> None:
        calls.append("stop")
        (paths.instance_dir / "events/events.db").write_bytes(b"last-in-flight-event")

    manager._services.stop = stop  # noqa: SLF001
    assert manager.reuse_recovery_evidence(pending=pending, candidate=candidate) is None
    assert calls == ["stop", "start"]


@pytest.mark.parametrize("key", ["backup", "events"])
def test_recovery_rechecks_keys_after_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    manager, _paths, candidate, pending, _receipt, _artifact, keys, calls, _ = (
        _reusable_recovery_fixture(tmp_path, monkeypatch)
    )

    def stop() -> None:
        calls.append("stop")
        keys[key] = b"r" * 32

    manager._services.stop = stop  # noqa: SLF001
    assert manager.reuse_recovery_evidence(pending=pending, candidate=candidate) is None
    assert calls == ["stop", "start"]
