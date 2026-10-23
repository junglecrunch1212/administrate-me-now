"""Contained bridge repair through the actual transaction and launch adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest
import yaml

from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths
from bootstrap import member_bridge_launch as launch
from bootstrap import member_bridge_runtime_repair as repair
from bootstrap.contracts import atomic_write
from tests.unit.bootstrap.test_member_bridge_launch import FakeLaunchd


OLD, NEW = "a" * 40, "b" * 40
OLD_DISPATCHER, NEW_DISPATCHER = b"retained dispatcher\n", b"write-aware dispatcher\n"


def _ledger(paths: MemberBridgePaths) -> sqlite3.Connection:
    connection = sqlite3.connect(paths.root / "adapter-cursors.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS bridge_command_attempts ("
        "command_id TEXT PRIMARY KEY, request_fingerprint TEXT NOT NULL, "
        "started_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS bridge_command_results ("
        "command_id TEXT PRIMARY KEY, envelope_json TEXT NOT NULL)"
    )
    connection.commit()
    return connection


def _attempt(paths: MemberBridgePaths) -> None:
    connection = _ledger(paths)
    try:
        connection.execute(
            "INSERT INTO bridge_command_attempts VALUES (?, ?, ?)",
            ("fixture-command", "f" * 64, "2034-01-01T00:00:00Z"),
        )
        connection.commit()
    finally:
        connection.close()


def _result(
    paths: MemberBridgePaths, *, called: bool = False, outcome: str = "suppressed"
) -> None:
    connection = _ledger(paths)
    try:
        connection.execute(
            "INSERT INTO bridge_command_results VALUES (?, ?)",
            (
                "fixture-command",
                json.dumps(
                    {
                        "type": "external_sync.write_completed",
                        "schema_version": 2,
                        "payload": {
                            "command_id": "fixture-command",
                            "provider": "apple_calendar",
                            "provider_called": called,
                            "request_fingerprint": "f" * 64,
                            "result": {"outcome": outcome},
                        },
                    }
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any]:
    monkeypatch.setattr(
        repair,
        "APPLE_SERVICE_WRITE_OVERLAY",
        {
            repair._DISPATCHER_PATH: (
                hashlib.sha256(OLD_DISPATCHER).hexdigest(),
                hashlib.sha256(NEW_DISPATCHER).hexdigest(),
            )
        },
    )
    paths = MemberBridgePaths.from_root(tmp_path / "bridge")
    paths.create()
    for commit in (OLD, NEW):
        root = paths.root / "app/releases" / commit
        for name in (
            *repair.UNCHANGED_FILES,
            *(tree + "/fixture.py" for tree in repair.UNCHANGED_TREES),
            repair.COLOR_SHIM,
            ".venv/bin/python",
        ):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        for name in (
            ".administrateme-source-commit",
            ".venv/.administrateme-bridge-installed-commit",
        ):
            (root / name).write_text(commit + "\n")
        (root / repair._DISPATCHER_PATH).write_bytes(
            OLD_DISPATCHER if commit == OLD else NEW_DISPATCHER
        )
    config = {
        "requested_packs": ["knowledge-apple-notes"],
        "configured_packs": ["knowledge-apple-notes"],
        "enabled_packs": ["knowledge-apple-notes"],
        "pack_config": {"knowledge-apple-notes": {"source": "fixture"}},
        "provider_writes": "deny",
        "observation_only": True,
        "member_id": "fixture-member",
        "enrollment_id": "fixture-enrollment",
        "request_id": "fixture-request",
        "instance_id": "fixture-instance",
        "release_commit": OLD,
        "cursor_db_path": str(paths.root / "adapter-cursors.db"),
    }
    _ledger(paths).close()
    atomic_write(paths.config_path, yaml.safe_dump(config).encode())
    repair._write(
        paths.receipt_path,
        {**config, "status": "disabled", "original_event": "preserved"},
    )
    old_root = paths.root / "app/releases" / OLD
    repair._write(
        paths.root / "app/current-release.json", {"path": str(old_root), "commit": OLD}
    )
    old = repair._installer(paths, old_root)
    old._install_launch_agent()
    fake = FakeLaunchd(old)
    monkeypatch.setattr(launch, "_command", fake)
    monkeypatch.setattr(launch.time, "monotonic", lambda: fake.clock)
    monkeypatch.setattr(launch.time, "sleep", fake.sleep)
    base = launch.compatible_installer_class(MemberBridgeInstaller)

    class Installer(base):
        def _start(self):
            fake.installer = self
            return super()._start()

        def _stop(self):
            fake.installer = self
            return super()._stop()

    monkeypatch.setattr(repair, "_installer", lambda p, r: Installer(p, root=r))
    return paths, fake, config


def test_repair_preserves_enrollment_and_configuration_then_rolls_back_stopped(rig):
    paths, fake, _ = rig
    original_config = paths.config_path.read_bytes()
    original_receipt = paths.receipt_path.read_bytes()
    original_plist = paths.launch_agent_path.read_bytes()
    result = repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert result["status"] == "activated"
    assert len(fake.actions("bootstrap")) == 1
    assert fake.loaded and fake.pid is not None
    assert paths.config_path.read_bytes() == original_config
    receipt = json.loads(paths.receipt_path.read_bytes())
    assert receipt["release_commit"] == OLD
    assert receipt["runtime_release_commit"] == NEW
    assert receipt["original_event"] == "preserved"
    assert receipt["provider_writes"] == "deny"
    assert (
        json.loads((paths.root / "app/current-release.json").read_bytes())["commit"]
        == NEW
    )
    operation = result["operation_id"]
    assert repair.rollback(paths, operation)["status"] == "rolled_back_stopped"
    assert not fake.loaded
    assert paths.receipt_path.read_bytes() == original_receipt
    assert paths.launch_agent_path.read_bytes() == original_plist
    assert paths.config_path.read_bytes() == original_config
    assert repair.rollback(paths, operation)["status"] == "rolled_back_stopped"


@pytest.mark.parametrize(
    "defect", ["running", "writable", "foreign-member", "interface", "not-prepared"]
)
def test_wrong_state_refuses_before_switch(rig, defect):
    paths, fake, config = rig
    if defect == "running":
        fake.loaded, fake.pid = True, 42
    elif defect == "writable":
        config["provider_writes"] = "allow"
        atomic_write(paths.config_path, yaml.safe_dump(config).encode())
    elif defect == "foreign-member":
        receipt = repair._json(paths.receipt_path)
        receipt["member_id"] = "other-member"
        repair._write(paths.receipt_path, receipt)
    elif defect == "interface":
        (paths.root / "app/releases" / NEW / "bridge/poetry.lock").write_text("changed")
    else:
        (
            paths.root
            / "app/releases"
            / NEW
            / ".venv/.administrateme-bridge-installed-commit"
        ).unlink()
    original_pointer = (paths.root / "app/current-release.json").read_bytes()
    with pytest.raises((ValueError, launch.LaunchFailure)):
        repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert (paths.root / "app/current-release.json").read_bytes() == original_pointer
    assert not fake.actions("bootstrap")


def test_known_failed_start_restores_original_disabled_state(rig):
    paths, fake, _ = rig
    original = paths.receipt_path.read_bytes()
    fake.bootstrap_results = ["fail"]
    with pytest.raises(launch.LaunchFailure):
        repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert not fake.loaded
    assert paths.receipt_path.read_bytes() == original
    assert repair._json(paths.root / "app/current-release.json")["commit"] == OLD
    journals = list((paths.root / "runtime-repairs").glob("*/journal.json"))
    assert len(journals) == 1
    assert repair._json(journals[0])["state"] == "rolled_back_stopped"


def test_unknown_start_retains_candidate_and_blocks_repeat(rig):
    paths, fake, _ = rig
    fake.command_unknown = "bootstrap"
    with pytest.raises(launch.LaunchFailure):
        repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert repair._json(paths.root / "app/current-release.json")["commit"] == NEW
    journals = list((paths.root / "runtime-repairs").glob("*/journal.json"))
    assert repair._json(journals[0])["state"] == "reconciliation_required"
    attempts = len(fake.actions("bootstrap"))
    with pytest.raises((ValueError, launch.LaunchFailure)):
        repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert len(fake.actions("bootstrap")) == attempts


def test_corrupt_snapshot_prevents_partial_pointer_restore(rig):
    paths, _, _ = rig
    result = repair.repair(paths, predecessor=OLD, candidate=NEW)
    operation = paths.root / "runtime-repairs" / result["operation_id"]
    atomic_write(operation / "receipt.json", b"corrupt")
    with pytest.raises(ValueError, match="snapshot mismatch"):
        repair.rollback(paths, result["operation_id"])
    assert repair._json(paths.root / "app/current-release.json")["commit"] == NEW


def test_successor_repair_preserves_original_enrollment_release(rig):
    paths, fake, config = rig
    enrolled = "c" * 40
    config["release_commit"] = enrolled
    atomic_write(paths.config_path, yaml.safe_dump(config).encode())
    receipt = repair._json(paths.receipt_path)
    receipt.update(release_commit=enrolled, runtime_release_commit=OLD)
    repair._write(paths.receipt_path, receipt)
    result = repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert result["status"] == "activated"
    assert repair._json(paths.receipt_path)["release_commit"] == enrolled
    assert repair._json(paths.receipt_path)["runtime_release_commit"] == NEW
    assert fake.loaded


@pytest.mark.parametrize("retained_digest", ["old", "current"])
def test_retained_schema_overlay_accepts_original_or_already_updated_release(
    rig, monkeypatch, retained_digest
):
    import hashlib

    paths, _, _ = rig
    relative = "adminme/events/schemas/cos_channels.py"
    old_bytes, new_bytes = b"original schema\n", b"current schema\n"
    monkeypatch.setattr(
        repair,
        "PERMISSION_RECOVERY_COS_OVERLAY",
        {
            relative: (
                hashlib.sha256(old_bytes).hexdigest(),
                hashlib.sha256(new_bytes).hexdigest(),
            )
        },
    )
    for commit, content in (
        (OLD, old_bytes if retained_digest == "old" else new_bytes),
        (NEW, new_bytes),
    ):
        path = paths.root / "app/releases" / commit / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert repair.repair(paths, predecessor=OLD, candidate=NEW)["status"] == "activated"


def test_acquisition_repair_does_not_allow_other_pack_changes(rig):
    paths, fake, _ = rig
    relative = "packs/adapters/reminders/apple-eventkit/config.schema.json"
    for commit, content in ((OLD, b"old"), (NEW, b"changed")):
        (paths.root / "app/releases" / commit / relative).write_bytes(content)
    with pytest.raises(ValueError, match="unsupported runtime interfaces"):
        repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert not fake.actions("bootstrap")


@pytest.mark.parametrize(
    "relative",
    [
        "adminme/apple_service_permissions.py",
        "adminme/events/schemas/cos_reminders.py",
        "adminme/tailscale_local.py",
    ],
)
def test_reviewed_write_overlay_requires_exact_bytes_without_changing_read_state(
    rig, monkeypatch, relative
):
    import hashlib

    paths, fake, _ = rig
    new_bytes = b"reviewed additive implementation\n"
    monkeypatch.setattr(
        repair,
        "APPLE_SERVICE_WRITE_OVERLAY",
        {
            **repair.APPLE_SERVICE_WRITE_OVERLAY,
            relative: (None, hashlib.sha256(new_bytes).hexdigest()),
        },
    )
    candidate_file = paths.root / "app/releases" / NEW / relative
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    candidate_file.write_bytes(new_bytes)
    before = paths.config_path.read_bytes()
    result = repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert result["status"] == "activated"
    assert paths.config_path.read_bytes() == before
    assert len(fake.actions("bootstrap")) == 1


@pytest.mark.parametrize(
    "relative",
    [
        "adminme/apple_service_permissions.py",
        "adminme/events/schemas/cos_reminders.py",
        "adminme/tailscale_local.py",
    ],
)
def test_unreviewed_write_overlay_refuses_before_switch(rig, monkeypatch, relative):
    paths, fake, _ = rig
    monkeypatch.setattr(
        repair,
        "APPLE_SERVICE_WRITE_OVERLAY",
        {**repair.APPLE_SERVICE_WRITE_OVERLAY, relative: (None, "a" * 64)},
    )
    candidate_file = paths.root / "app/releases" / NEW / relative
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    candidate_file.write_bytes(b"unreviewed\n")
    before = (paths.root / "app/current-release.json").read_bytes()
    with pytest.raises(ValueError, match="unreviewed Apple write overlay"):
        repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert (paths.root / "app/current-release.json").read_bytes() == before
    assert not fake.actions("bootstrap")


def test_candidate_write_overlay_pins_match_committed_files():
    import hashlib

    root = Path(__file__).resolve().parents[3]
    for relative, (_old, expected) in repair.APPLE_SERVICE_WRITE_OVERLAY.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected, (
            relative
        )


@pytest.mark.parametrize("cached_no_effect", [False, True])
def test_empty_or_exact_no_effect_history_keeps_pre_effect_rollback_available(
    rig, cached_no_effect
):
    paths, fake, _ = rig
    activated = repair.repair(paths, predecessor=OLD, candidate=NEW)
    if cached_no_effect:
        _result(paths)
    database = paths.root / "adapter-cursors.db"
    before = database.read_bytes()
    assert (
        repair.rollback(paths, activated["operation_id"])["status"]
        == "rolled_back_stopped"
    )
    assert not fake.loaded
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "history", ["attempt", "attempt-false-result", "called", "ambiguous", "unknown"]
)
def test_unsafe_history_refuses_unaware_target_before_stop_or_selector_change(
    rig, history
):
    paths, fake, _ = rig
    activated = repair.repair(paths, predecessor=OLD, candidate=NEW)
    if history.startswith("attempt"):
        _attempt(paths)
        if history == "attempt-false-result":
            _result(paths)
    else:
        _result(
            paths,
            called=history == "called",
            outcome="applied" if history == "called" else history,
        )
    pointer = paths.root / "app/current-release.json"
    before = pointer.read_bytes()
    stops = len(fake.actions("bootout"))
    with pytest.raises(ValueError, match="require a write-aware recovery target"):
        repair.rollback(paths, activated["operation_id"])
    assert pointer.read_bytes() == before
    assert len(fake.actions("bootout")) == stops
    assert fake.loaded


def test_aware_target_can_recover_without_restoring_attempt_history(rig):
    paths, fake, _ = rig
    (paths.root / "app/releases" / OLD / repair._DISPATCHER_PATH).write_bytes(
        NEW_DISPATCHER
    )
    activated = repair.repair(paths, predecessor=OLD, candidate=NEW)
    _attempt(paths)
    before = (paths.root / "adapter-cursors.db").read_bytes()
    assert (
        repair.rollback(paths, activated["operation_id"])["status"]
        == "rolled_back_stopped"
    )
    assert not fake.loaded
    assert (paths.root / "adapter-cursors.db").read_bytes() == before


def test_rollback_reads_committed_attempt_in_live_sqlite_wal(rig):
    paths, fake, _ = rig
    activated = repair.repair(paths, predecessor=OLD, candidate=NEW)
    connection = _ledger(paths)
    try:
        connection.execute(
            "INSERT INTO bridge_command_attempts VALUES (?, ?, ?)",
            ("fixture-command", "f" * 64, "2034-01-01T00:00:00Z"),
        )
        connection.commit()
        assert (paths.root / "adapter-cursors.db-wal").is_file()
        with pytest.raises(ValueError, match="require a write-aware recovery target"):
            repair.rollback(paths, activated["operation_id"])
        assert fake.loaded
        assert repair._json(paths.root / "app/current-release.json")["commit"] == NEW
    finally:
        connection.close()


def test_attempt_racing_stop_blocks_selector_restore_and_retains_current_history(
    rig, monkeypatch
):
    paths, fake, _ = rig
    activated = repair.repair(paths, predecessor=OLD, candidate=NEW)
    installer_factory = repair._installer

    def installer_with_race(p, root):
        installer = installer_factory(p, root)
        original_stop = installer._stop

        def stop():
            _attempt(paths)
            return original_stop()

        installer._stop = stop
        return installer

    monkeypatch.setattr(repair, "_installer", installer_with_race)
    with pytest.raises(ValueError, match="require a write-aware recovery target"):
        repair.rollback(paths, activated["operation_id"])
    assert not fake.loaded
    assert repair._json(paths.root / "app/current-release.json")["commit"] == NEW
    connection = sqlite3.connect(paths.root / "adapter-cursors.db")
    try:
        assert connection.execute(
            "SELECT count(*) FROM bridge_command_attempts"
        ).fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "unreadable",
        "redirected",
        "writable",
        "schema",
        "result",
        "cursor-coordinate",
        "dispatcher",
    ],
)
def test_unverifiable_history_or_recovery_coordinate_refuses_before_stop(rig, defect):
    paths, fake, config = rig
    activated = repair.repair(paths, predecessor=OLD, candidate=NEW)
    database = paths.root / "adapter-cursors.db"
    if defect == "missing":
        database.unlink()
    elif defect == "unreadable":
        database.write_bytes(b"not a SQLite ledger")
    elif defect == "redirected":
        other = paths.root / "redirected.db"
        database.rename(other)
        database.symlink_to(other)
    elif defect == "writable":
        database.chmod(0o666)
    elif defect == "schema":
        connection = sqlite3.connect(database)
        connection.execute("DROP TABLE bridge_command_attempts")
        connection.close()
    elif defect == "result":
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO bridge_command_results VALUES ('fixture-command', 'not json')"
        )
        connection.commit()
        connection.close()
    elif defect == "cursor-coordinate":
        config["cursor_db_path"] = str(paths.root / "other.db")
        atomic_write(paths.config_path, yaml.safe_dump(config).encode())
        operation = paths.root / "runtime-repairs" / activated["operation_id"]
        journal = repair._json(operation / "journal.json")
        # An authentic sealed config with an unsupported cursor path is still
        # refused; a different live config hash has its own earlier guard.
        journal["config_sha256"] = hashlib.sha256(
            paths.config_path.read_bytes()
        ).hexdigest()
        repair._write(operation / "journal.json", journal)
    else:
        (paths.root / "app/releases" / OLD / repair._DISPATCHER_PATH).write_bytes(
            b"unknown dispatcher"
        )
    stops = len(fake.actions("bootout"))
    with pytest.raises(
        ValueError, match="cannot be verified|unsupported bridge dispatcher"
    ):
        repair.rollback(paths, activated["operation_id"])
    assert len(fake.actions("bootout")) == stops
    assert fake.loaded
    assert repair._json(paths.root / "app/current-release.json")["commit"] == NEW


def test_previous_write_aware_dispatcher_remains_recoverable_without_history_loss(
    rig, monkeypatch
):
    paths, fake, _ = rig
    previous = b"previous write-aware dispatcher\n"
    monkeypatch.setattr(
        repair,
        "RETAINED_WRITE_AWARE_DISPATCHERS",
        frozenset({hashlib.sha256(previous).hexdigest()}),
    )
    (paths.root / "app/releases" / OLD / repair._DISPATCHER_PATH).write_bytes(previous)
    activated = repair.repair(paths, predecessor=OLD, candidate=NEW)
    _attempt(paths)
    before = (paths.root / "adapter-cursors.db").read_bytes()
    assert (
        repair.rollback(paths, activated["operation_id"])["status"]
        == "rolled_back_stopped"
    )
    assert not fake.loaded
    assert (paths.root / "adapter-cursors.db").read_bytes() == before


def test_previous_dispatcher_pin_does_not_authorize_it_as_the_new_candidate(
    rig, monkeypatch
):
    paths, fake, _ = rig
    previous = b"previous write-aware dispatcher\n"
    monkeypatch.setattr(
        repair,
        "RETAINED_WRITE_AWARE_DISPATCHERS",
        frozenset({hashlib.sha256(previous).hexdigest()}),
    )
    (paths.root / "app/releases" / NEW / repair._DISPATCHER_PATH).write_bytes(previous)
    with pytest.raises(ValueError, match="unreviewed Apple write overlay"):
        repair.repair(paths, predecessor=OLD, candidate=NEW)
    assert not fake.actions("bootstrap")
