"""Hosted actual old-writer → new Doctor → matched-generation recovery proof."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.openclaw_state_transition import (
    capture_generation,
    inspect_coverage,
    restore_generation,
)
from bootstrap.upgrade import UpgradeManager


def main() -> None:
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    repository, installed, old_source = map(Path, sys.argv[1:])
    paths = DeploymentPaths.from_root(installed)
    paths.create()
    native = paths.instance_dir / "openclaw"
    runtime = native / "runtime"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    home = installed / "fixture-home"
    home.mkdir(mode=0o700)
    rows = []
    for index, agent in enumerate(
        ("adminme-fixture-a", "adminme-fixture-b", "am-service-fixture")
    ):
        workspace = native / "workspaces" / agent
        workspace.mkdir(parents=True, mode=0o700)
        row = {
            "id": agent,
            "workspace": str(workspace),
            "tools": {"allow": ["session_status"]},
        }
        if index == 0:
            row["default"] = True
        rows.append(row)
    config = {
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "port": 19997,
            "auth": {"mode": "token", "token": "synthetic-native-upgrade-token-only"},
        },
        "agents": {"defaults": {"workspace": str(native / "workspace")}, "list": rows},
        "channels": {},
        "bindings": [],
        "session": {"dmScope": "per-account-channel-peer"},
        "update": {"checkOnStart": False, "auto": {"enabled": False}},
    }
    target = runtime / "openclaw.json"
    target.write_text(json.dumps(config))
    target.chmod(0o600)
    credential = runtime / "fixture-auth.json"
    credential.write_text('{"fixtureOnly":"retained-not-a-real-token"}')
    credential.chmod(0o600)
    central = paths.instance_dir / "central-event-history.fixture"
    central.write_bytes(b"append-only-central-history\n")
    original_event_hash = hashlib.sha256(central.read_bytes()).hexdigest()
    original_credential_hash = hashlib.sha256(credential.read_bytes()).hexdigest()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "TMPDIR", "LANG"}
    }
    environment.update(
        HOME=str(home),
        OPENCLAW_STATE_DIR=str(runtime),
        OPENCLAW_CONFIG_PATH=str(target),
        OPENCLAW_SERVICE_REPAIR_POLICY="external",
        OPENCLAW_NO_AUTO_UPDATE="1",
    )
    selected = paths.root / "bin/openclaw"
    new_source = (
        paths.app_dir / "openclaw" / load_versions(repository)["openclaw"]["commit"]
    )
    selected.unlink()
    selected.symlink_to(old_source / "openclaw.mjs")
    probe = repository / "tests/fixtures/openclaw_native_generation.mjs"
    subprocess.run(
        ["node", str(probe), str(old_source), "seed"],
        env=environment,
        check=True,
        timeout=180,
    )
    inspect_coverage(paths)
    tx = "0123456789abcdef0123456789abcdef"
    record = capture_generation(paths, tx)
    selected.unlink()
    selected.symlink_to(new_source / "openclaw.mjs")
    manager = object.__new__(UpgradeManager)
    manager.paths, manager.test_mode = paths, False
    manager._active_preparation_activity_descriptor = None
    manager._active_preparation_activity_path = None
    manager._active_preparation_activity_coordinate = None
    # Match the real controller's three independent lease coordinates. These
    # files are synthetic and created exclusively; an existing lease is never
    # removed or adopted. The production guardian retains its own descriptors.
    activity_path = repository.parent / manager._preparation_activity_name(
        repository.name
    )
    lease_paths = {
        "activity": activity_path,
        "lifecycle": installed / "fixture-controller-lifecycle.lock",
        "operation": installed / "fixture-controller-operation.lock",
    }
    descriptors: dict[str, int] = {}
    previous = dict(os.environ)
    settled = False
    try:
        for name, lease_path in lease_paths.items():
            descriptor = os.open(
                lease_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            descriptors[name] = descriptor
            fcntl.flock(
                descriptor, fcntl.LOCK_EX if name == "operation" else fcntl.LOCK_SH
            )
        activity_info = os.fstat(descriptors["activity"])
        lifecycle_info = os.fstat(descriptors["lifecycle"])
        manager._active_preparation_activity_descriptor = descriptors["activity"]
        manager._active_preparation_activity_path = activity_path
        manager._active_preparation_activity_coordinate = (
            activity_info.st_dev,
            activity_info.st_ino,
        )
        environment.update(
            ADMINME_CONTROLLER_LIFECYCLE_PATH=str(lease_paths["lifecycle"]),
            ADMINME_CONTROLLER_LIFECYCLE_DEVICE=str(lifecycle_info.st_dev),
            ADMINME_CONTROLLER_LIFECYCLE_INODE=str(lifecycle_info.st_ino),
            ADMINME_UPGRADE_OPERATION_LOCK_FD=str(descriptors["operation"]),
        )
        os.environ.clear()
        os.environ.update(environment)
        try:
            manager._migrate_openclaw_state(repository, record)
        finally:
            # Closing our shared descriptor does not release a guardian's own
            # lease. Recovery must independently acquire exclusive ownership.
            manager._active_preparation_activity_descriptor = None
            manager._active_preparation_activity_path = None
            manager._active_preparation_activity_coordinate = None
            os.close(descriptors.pop("activity"))
        manager._require_native_migration_settled(repository)
        settled = True
        stores = list(runtime.rglob("*.sqlite"))
        state_count, agent_count = 0, 0
        for store in stores:
            if store.name not in {"openclaw.sqlite", "openclaw-agent.sqlite"}:
                continue
            with sqlite3.connect(store.as_uri() + "?mode=ro", uri=True) as database:
                version = database.execute("PRAGMA user_version").fetchone()[0]
                if store.name == "openclaw.sqlite":
                    assert version == 20
                    state_count += 1
                else:
                    assert version == 25
                    retained = database.execute(
                        "SELECT entry_json FROM session_nodes"
                    ).fetchall()
                    assert any("Retained fixture" in row[0] for row in retained)
                    agent_count += 1
        assert (state_count, agent_count) == (1, 3)
        assert (
            hashlib.sha256(credential.read_bytes()).hexdigest()
            == original_credential_hash
        )
        print(
            "Actual dc252 Doctor: shared20 / three agent25 stores; retained sessions, authority and auth fixture PASS",
            flush=True,
        )
        # Simulate only a failed activation before normal work is released.
        failed = runtime / "failed-activation.fixture"
        failed.write_bytes(b"retain failed generation diagnostic")
        failed.chmod(0o600)
        manager._require_native_migration_settled(repository)
        restore_generation(paths, record, tx)
        selected.unlink()
        selected.symlink_to(old_source / "openclaw.mjs")
        assert (
            paths.rollback_dir
            / f"openclaw-{tx}/failed/runtime/failed-activation.fixture"
        ).is_file()
        assert hashlib.sha256(central.read_bytes()).hexdigest() == original_event_hash
        assert (
            hashlib.sha256(credential.read_bytes()).hexdigest()
            == original_credential_hash
        )
        subprocess.run(
            ["node", str(probe), str(old_source), "reopen"],
            env=environment,
            check=True,
            timeout=180,
        )
        print(
            "Matched-generation native recovery; failed generation retained; central history unchanged PASS",
            flush=True,
        )
    finally:
        os.environ.clear()
        os.environ.update(previous)
        for descriptor in descriptors.values():
            os.close(descriptor)
        if settled:
            # Only our exact now-unlocked synthetic checkout lease is cleaned;
            # unknown child liveness deliberately retains the evidence file.
            observed = activity_path.lstat()
            assert (observed.st_dev, observed.st_ino) == (
                activity_info.st_dev,
                activity_info.st_ino,
            )
            activity_path.unlink()


if __name__ == "__main__":
    main()
