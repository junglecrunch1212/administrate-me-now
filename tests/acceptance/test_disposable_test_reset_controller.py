"""Hosted-only rehearsal of the supported reset controller and real backup path.

The existing bootstrap test mode supplies credential-free operating-system and
Doctor fixtures. State moves, encrypted backup authentication/replay, immutable
release publication, and selection persistence use their production code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.disposable_test_reset import build_manifest, select_messages
from bootstrap.services import ServiceManager
from bootstrap.upgrade import UpgradeManager
from bootstrap.wizard import BootstrapWizard
from tests.acceptance.test_macos_bootstrap_operations import (
    REPO_ROOT,
    _committed_candidate_from_worktree,
    _extract_git_archive,
)


def _json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, indent=2).encode())


def test_supported_reset_rehearses_fresh_backup_and_accepts_selection(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    candidate = tmp_path / "candidate"
    predecessor_commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    predecessor = paths.app_dir / "releases" / predecessor_commit
    predecessor.parent.mkdir(mode=0o700, exist_ok=True)
    _extract_git_archive(candidate, predecessor_commit, predecessor)
    BootstrapWizard(
        paths,
        root=predecessor,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    _json(
        paths.app_dir / "current-release.json",
        {"commit": predecessor_commit, "path": str(predecessor)},
    )
    # A distinct immutable candidate is required; never edit an installed release.
    atomic_write(
        candidate / "reset-candidate-fixture.txt", b"synthetic reviewed candidate\n"
    )
    subprocess.run(
        ["git", "-C", str(candidate), "add", "reset-candidate-fixture.txt"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Reset Fixture",
            "-c",
            "user.email=reset-fixture.invalid",
            "commit",
            "-qm",
            "reset candidate",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    config = paths.instance_dir / "config"
    mapping_bytes = (config / "member-mapping.json").read_bytes()
    mapping = json.loads(mapping_bytes)
    member = next(
        row["member_id"] for row in mapping["members"] if row["role"] == "principal"
    )
    policy = {
        "contract": "disposable-test-acquisition-v1",
        "adapter_id": "fixture-gmail",
        "member_id": member,
        "provider_account_id": "reset-fixture@example.invalid",
        "message_ids": [],
    }
    _json(
        config / "adapters.json",
        {
            "adapters": [
                {
                    "id": policy["adapter_id"],
                    "kind": "messaging-gmail-api",
                    "enabled": True,
                    "config": {
                        "member_id": member,
                        "provider_account_id": policy["provider_account_id"],
                        "user_email": policy["provider_account_id"],
                    },
                }
            ]
        },
    )
    _json(
        paths.app_dir / "activation" / "active.json",
        {"transaction_id": "fixture-abandoned-transaction", "phase": "recovering"},
    )
    original_journal = (paths.app_dir / "activation" / "active.json").read_bytes()
    services = ServiceManager(paths, test_mode=True)
    services.start()
    diagnostic = BackupManager(paths, root=candidate, test_mode=True).create()
    manifest = build_manifest(
        paths,
        candidate_commit=commit,
        acquisition=policy,
        diagnostic_backup=Path(diagnostic["path"]),
    )
    manifest_path = paths.root / "approved-fixture-manifest.json"
    _json(manifest_path, manifest)
    monkeypatch.setattr(
        UpgradeManager, "_attest_controller_provenance", lambda *args: None
    )
    upgrades = UpgradeManager(
        paths, root=candidate, test_mode=True, test_reset_manifest=manifest_path
    )
    result = upgrades.activate(candidate)
    assert result["phase"] == "accepted"
    assert result["commit"] == commit
    assert result["production_rollback_proven"] is False
    assert result["original_health_proven"] is False
    archive = Path(manifest["archive"])
    assert (archive / "original-activation.json").read_bytes() == original_journal
    assert not (paths.app_dir / "activation" / "active.json").exists()
    assert (config / "member-mapping.json").read_bytes() == mapping_bytes
    assert (
        archive / "original-state" / "config" / "member-mapping.json"
    ).read_bytes() == mapping_bytes
    assert result["clean_backup"]["encrypted"] is True
    assert result["clean_rehearsal_sha256"]
    assert services.status().status == "healthy"
    assert upgrades.activate(candidate) == result
    services.stop()
    selection = paths.root / "selection.json"
    _json(selection, {"message_ids": ["synthetic-message-one", "synthetic-sent-two"]})
    first = select_messages(upgrades, selection)
    second = select_messages(upgrades, selection)
    assert first == second
    assert first["lifetime_admitted_count"] == 2
    assert first["provider_calls"] == 0
    assert first["mailbox_coverage_complete"] is False
