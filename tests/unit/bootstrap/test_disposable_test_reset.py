"""Exact-manifest, interruption, and unchanged-diagnostic reset proof."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.disposable_test_reset import (
    OfflineTestReset,
    TestResetError as ResetError,
    build_manifest,
    validate_manifest,
)


def _json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, indent=2).encode())


@pytest.fixture
def reset_fixture(tmp_path: Path) -> tuple[DeploymentPaths, dict]:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.create()
    config = paths.instance_dir / "config"
    _json(config / "observation.json", {"active": True})
    _json(config / "member-mapping.json", {"members": ["fixture-member"]})
    _json(
        config / "adapters.json",
        {
            "adapters": [
                {
                    "id": "fixture-gmail",
                    "kind": "messaging-gmail-api",
                    "enabled": True,
                    "config": {
                        "member_id": "fixture-member",
                        "provider_account_id": "fixture-account",
                        "user_email": "fixture-account",
                        "credential_ref": "fixture:reference",
                    },
                }
            ]
        },
    )
    _json(
        config / "settings-control.json",
        {
            "schema_version": 6,
            "member_preferences": {"fixture-member": {"theme": "dark"}},
            "candidate_policies": {"fixture-member": {"old-test-rule": True}},
            "bridge_requests": {"fixture-member": {"state": "active"}},
        },
    )
    atomic_write(config / "openclaw-approvals.sqlite3", b"old-spent-approval")
    atomic_write(paths.instance_dir / "events" / "events.db", b"old-encrypted-history")
    atomic_write(paths.instance_dir / "data" / "seed-manifest.json", b"old-test-seed")
    atomic_write(paths.instance_dir / "projections" / "triage.db", b"old-candidates")
    _json(
        paths.instance_dir / "openclaw" / "runtime" / "openclaw.json",
        {"secret": "fixture-ref"},
    )
    _json(
        paths.app_dir / "current-release.json",
        {"commit": "a" * 40, "path": "fixture-release"},
    )
    _json(
        paths.app_dir / "activation" / "active.json",
        {
            "transaction_id": "b" * 32,
            "phase": "recovering",
            "failure": "fixture-failure",
        },
    )
    backup = paths.backup_dir / "retained.ambackup"
    atomic_write(
        backup, b"opaque-backup; authentication belongs to reviewed controller"
    )
    manifest = build_manifest(
        paths,
        candidate_commit="c" * 40,
        acquisition={
            "contract": "disposable-test-acquisition-v1",
            "adapter_id": "fixture-gmail",
            "member_id": "fixture-member",
            "provider_account_id": "fixture-account",
            "message_ids": [],
        },
        diagnostic_backup=backup,
    )
    return paths, manifest


def test_reset_retains_identity_and_failed_journal_without_claiming_recovery(
    reset_fixture,
):
    paths, manifest = reset_fixture
    identity = (paths.instance_dir / "config" / "member-mapping.json").read_bytes()
    journal = (paths.app_dir / "activation" / "active.json").read_bytes()
    operation = OfflineTestReset(paths, manifest)
    operation.prepare()
    operation.switch()
    assert (
        paths.instance_dir / "config" / "member-mapping.json"
    ).read_bytes() == identity
    assert (operation.archive / "original-activation.json").read_bytes() == journal
    assert not (paths.app_dir / "activation" / "active.json").exists()
    assert (
        operation.original / "events" / "events.db"
    ).read_bytes() == b"old-encrypted-history"
    assert not list((paths.instance_dir / "events").iterdir())
    assert not list((paths.instance_dir / "projections").iterdir())
    assert not list((paths.instance_dir / "data").iterdir())
    assert not (paths.instance_dir / "config" / "openclaw-approvals.sqlite3").exists()
    control = json.loads(
        (paths.instance_dir / "config" / "settings-control.json").read_text()
    )
    assert control["candidate_policies"] == {}
    assert control["member_preferences"]["fixture-member"]["theme"] == "dark"
    assert control["bridge_requests"] == {}
    result = operation.restore()
    assert result["phase"] == "original_restored_health_unproven"
    assert result["production_rollback_proven"] is False
    assert (paths.app_dir / "activation" / "active.json").read_bytes() == journal
    assert (
        paths.instance_dir / "events" / "events.db"
    ).read_bytes() == b"old-encrypted-history"


@pytest.mark.parametrize(
    "boundary", ["before_first_move", "between_moves", "after_state_moves"]
)
def test_interruptions_restore_original_coordinate_and_journal(
    reset_fixture, monkeypatch, boundary
):
    from bootstrap import disposable_test_reset as module

    paths, manifest = reset_fixture
    operation = OfflineTestReset(paths, manifest)
    operation.prepare()
    real_move = module._move
    count = 0

    def interrupted_move(source, target):
        nonlocal count
        count += 1
        if boundary == "before_first_move" and count == 1:
            raise KeyboardInterrupt
        real_move(source, target)
        if (boundary == "between_moves" and count == 1) or (
            boundary == "after_state_moves" and count == 2
        ):
            raise KeyboardInterrupt

    monkeypatch.setattr(module, "_move", interrupted_move)
    with pytest.raises(KeyboardInterrupt):
        operation.switch()
    monkeypatch.setattr(module, "_move", real_move)
    result = operation.restore()
    assert result["phase"] == "original_restored_health_unproven"
    assert (
        paths.instance_dir / "events" / "events.db"
    ).read_bytes() == b"old-encrypted-history"
    assert (
        json.loads((paths.app_dir / "activation" / "active.json").read_text())["phase"]
        == "recovering"
    )


@pytest.mark.parametrize(
    "relative", ["config/member-mapping.json", "config/adapters.json"]
)
def test_stale_identity_or_source_configuration_refuses_before_archive(
    reset_fixture, relative
):
    paths, manifest = reset_fixture
    _json(paths.instance_dir / relative, {"changed": True})
    with pytest.raises((ResetError, ValueError, KeyError)):
        validate_manifest(paths, manifest)
    assert not Path(manifest["archive"]).exists()


def test_manifest_cannot_expand_archive_or_clear_observation(reset_fixture):
    paths, manifest = reset_fixture
    manifest["archive"] = str(paths.root.parent)
    with pytest.raises(ResetError, match="archive coordinate"):
        OfflineTestReset(paths, manifest)
    manifest["archive"] = str(paths.rollback_dir / f"test-reset-{manifest['reset_id']}")
    _json(paths.instance_dir / "config" / "observation.json", {"active": False})
    with pytest.raises(ResetError, match="observation"):
        validate_manifest(paths, manifest)


def test_recovery_refuses_an_unrelated_replacement_dataset(reset_fixture):
    paths, manifest = reset_fixture
    operation = OfflineTestReset(paths, manifest)
    operation.prepare()
    operation.switch()
    paths.instance_dir.rename(paths.root / "unrelated-move")
    paths.instance_dir.mkdir(mode=0o700)
    with pytest.raises(ResetError, match="unrelated active dataset"):
        operation.restore()
    assert operation.original.exists()


def test_failed_initial_intent_publication_never_stops_services(
    reset_fixture, monkeypatch
):
    from bootstrap import disposable_test_reset as module

    paths, manifest = reset_fixture
    operation = OfflineTestReset(paths, manifest)
    original_move = module._move

    def fail_before_intent(source, target):
        if target == operation.archive:
            raise KeyboardInterrupt
        return original_move(source, target)

    monkeypatch.setattr(module, "_move", fail_before_intent)
    with pytest.raises(KeyboardInterrupt):
        operation.begin()
    assert not operation.archive.exists()
    assert (
        paths.instance_dir / "events" / "events.db"
    ).read_bytes() == b"old-encrypted-history"
    monkeypatch.setattr(module, "_move", original_move)
    assert operation.begin()["phase"] == "initialized"
    assert operation.restore()["phase"] == "original_restored_health_unproven"


@pytest.mark.parametrize("failure", ["prepare", "doctor", "stop"])
def test_controller_failure_reverses_real_state_moves_and_retains_failure(
    reset_fixture, monkeypatch, failure
):
    """Fake external service/build seams; real reset filesystem transaction.

    The acceptance companion covers actual SQLCipher/backup/replay/composition.
    This test deliberately injects failure instead of claiming healthy Doctor.
    """
    from types import SimpleNamespace

    from bootstrap import (
        backup_restore,
        candidate_readiness,
        openclaw_tools,
        upgrade,
        wizard,
    )
    from bootstrap import disposable_test_reset as module

    paths, manifest = reset_fixture
    manifest_path = paths.root / "approved-manifest.json"
    _json(manifest_path, manifest)
    trace = []
    running = True
    stopped_once = False

    class Services:
        def status(self):
            return SimpleNamespace(status="healthy" if running else "stopped")

        def stop(self):
            nonlocal running, stopped_once
            trace.append("stop")
            if failure == "stop" and not stopped_once:
                stopped_once = True
                raise RuntimeError("fixture-stop-failure")
            running = False

        def start(self):
            nonlocal running
            trace.append("start")
            running = True
            return SimpleNamespace(status="degraded")

    class Backups:
        def __init__(self, *args, **kwargs):
            self.store = SimpleNamespace(get=lambda reference: b"k" * 32)

        def _references(self):
            return {"backup": "fixture:backup", "event_log": "fixture:event"}

        def create(self, target):
            atomic_write(target, b"fixture encrypted snapshot seam")
            return {"path": str(target), "encrypted": True}

        def rehearse(self, backup, *, candidate, receipt):
            _json(receipt, {"status": "healthy", "fixture_seam": True})
            return {"status": "healthy"}

    monkeypatch.setattr(backup_restore, "BackupManager", Backups)
    monkeypatch.setattr(
        backup_restore,
        "_authenticate_backup_to_path",
        lambda *args: ({}, manifest["diagnostic_backup"]["sha256"]),
    )
    monkeypatch.setattr(upgrade, "validate_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        openclaw_tools, "configure_required_llm_tool", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        openclaw_tools, "reconcile_administrateme_plugin", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(wizard, "install_launch_agents", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        candidate_readiness,
        "capture_deployment_snapshot",
        lambda *args: SimpleNamespace(
            configuration_fingerprint="d" * 64,
            host_fingerprint="e" * 64,
            instance_fingerprint="f" * 64,
        ),
    )
    if failure == "prepare":

        def fail_prepare(self):
            assert not running
            raise RuntimeError("fixture-prepare-failure")

        monkeypatch.setattr(OfflineTestReset, "prepare", fail_prepare)

    def failed_doctor(release):
        assert running
        assert (
            paths.rollback_dir / f"test-reset-{manifest['reset_id']}" / "original-state"
        ).exists()
        trace.append("failed-doctor")
        return {"status": "failed"}

    # A real temporary descriptor exercises the consumed activity cleanup.
    import os

    descriptor = os.open(paths.root / "activity", os.O_CREAT | os.O_RDWR, 0o600)
    manager = SimpleNamespace(
        paths=paths,
        root=paths.root / "candidate",
        test_mode=True,
        approve_runtime_migration=False,
        approve_source_assessment_repair=False,
        approve_provider_binding_migration=False,
        pending_attestation_id=None,
        services=Services(),
        _candidate_commit=lambda candidate: "c" * 40,
        _attest_controller_provenance=lambda *args: trace.append("attest"),
        _active_release_root=lambda: paths.root / "predecessor",
        _preflight_keychain_access=lambda *args: None,
        _publish_release=lambda *args: paths.root / "candidate",
        _consume_preparation_state=lambda *args: descriptor,
        _install_reviewed_openclaw=lambda *args: trace.append("select-openclaw"),
        _post_switch_doctor=failed_doctor,
    )
    try:
        with pytest.raises((RuntimeError, ResetError)):
            module.execute_reset(manager, paths.root / "candidate", manifest_path)
        assert running
        assert trace[0] == "attest"
        assert trace.count("stop") >= 2
        assert (
            paths.instance_dir / "events" / "events.db"
        ).read_bytes() == b"old-encrypted-history"
        assert (
            json.loads((paths.app_dir / "activation" / "active.json").read_text())[
                "phase"
            ]
            == "recovering"
        )
        assert not (paths.app_dir / "disposable-test-reset-active.json").exists()
        record = json.loads(
            (
                paths.rollback_dir / f"test-reset-{manifest['reset_id']}" / "reset.json"
            ).read_text()
        )
        assert record["phase"] == "original_restored_health_unproven"
        assert record["original_health_proven"] is False
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
