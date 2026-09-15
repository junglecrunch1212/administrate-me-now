"""Hosted-only rehearsal of the supported reset controller and real backup path.

The existing bootstrap test mode supplies credential-free operating-system and
Doctor fixtures. State moves, encrypted backup authentication/replay, immutable
release publication, and selection persistence use their production code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.disposable_test_reset import build_manifest, select_messages
from bootstrap.services import ServiceManager
from bootstrap.secrets import secret_store
from bootstrap.upgrade import UpgradeManager
from bootstrap.wizard import BootstrapWizard, install_launch_agents
from tests.acceptance.test_macos_bootstrap_operations import (
    REPO_ROOT,
    _committed_candidate_from_worktree,
    _extract_git_archive,
)


def _json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, indent=2).encode())


def accepted_reset_fixture(tmp_path, monkeypatch, *, predecessor_pointer_depth=1):
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    candidate = tmp_path / "candidate"
    predecessor_commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    BootstrapWizard(
        paths,
        root=candidate,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    predecessor = paths.app_dir / "releases" / predecessor_commit
    predecessor.parent.mkdir(mode=0o700, exist_ok=True)
    _extract_git_archive(candidate, predecessor_commit, predecessor)
    install_launch_agents(paths, predecessor, test_mode=True)
    previous = None
    # Synthetic inert history is established before the reset manifest exists.
    # Only the real immediate predecessor is ever selected or executed.
    assert 1 <= predecessor_pointer_depth <= 62
    for index in range(predecessor_pointer_depth - 1):
        historical_commit = f"{index + 1:040x}"
        historical = predecessor.parent / historical_commit
        historical.mkdir(mode=0o700)
        atomic_write(
            historical / "retained-history.txt", b"synthetic retained history\n"
        )
        previous = {
            "commit": historical_commit,
            "path": str(historical),
            "previous": previous,
        }
    pointer: dict[str, object] = {
        "commit": predecessor_commit,
        "path": str(predecessor),
    }
    if previous is not None:
        pointer["previous"] = previous
    _json(
        paths.app_dir / "current-release.json",
        pointer,
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
    # Retain the same configured, credential-free Gateway boundary that the
    # ordinary candidate-pending composition requires after HTR-07.
    _json(
        config / "openclaw-activation.json",
        {"configured": True, "fixture_only": True},
    )
    _json(
        paths.instance_dir / "openclaw/runtime/openclaw.json",
        {"agents": {}, "bindings": []},
    )
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
    adapters = json.loads((config / "adapters.json").read_text())
    adapters["adapters"] = [
        {
            "id": policy["adapter_id"],
            "kind": "messaging-gmail-api",
            "enabled": False,
            "config": {
                "service_account_key_ref": "op://fixture/gmail/credentials",
                "member_id": member,
                "provider_account_id": policy["provider_account_id"],
                "user_email": policy["provider_account_id"],
                "workspace": "personal",
                "permission_scope": ["https://www.googleapis.com/auth/gmail.readonly"],
                "binding_provenance": "service-account-delegation",
                "ownership_attested": True,
            },
        }
    ]
    _json(config / "adapters.json", adapters)
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
    real_start = ServiceManager.start

    def start_fixture_runtime(self):
        # The host fixture supplies launchd ownership, while real composition
        # creates the new stores/checkpoints that full Doctor requires.
        if (
            self.paths.instance_dir / "config" / "disposable-test-dataset.json"
        ).exists():
            fixture_config = self.paths.instance_dir / "config"
            references = json.loads(
                (fixture_config / "secret-references.json").read_text()
            )["references"]
            key = secret_store(self.paths, test_mode=True).get(references["event_log"])
            members = json.loads((fixture_config / "member-mapping.json").read_text())
            active = json.loads(
                (self.paths.app_dir / "current-release.json").read_text()
            )
            settings = RuntimeSettings.credential_free(
                self.paths.instance_dir,
                encryption_key=key,
                repository_root=Path(active["path"]),
                adapter_config=json.loads(
                    (fixture_config / "adapters.json").read_text()
                ),
                household_mapping=members,
                member_identities={
                    row["tailscale_identity"]: row["member_id"]
                    for row in members["members"]
                },
                member_roles={
                    row["member_id"]: row["role"] for row in members["members"]
                },
                deterministic_openclaw_fixture=True,
            )
            with TestClient(create_application(settings)) as client:
                assert client.get("/api/system/live").json()["runtime_started"] is True
        return real_start(self)

    monkeypatch.setattr(ServiceManager, "start", start_fixture_runtime)
    real_doctor = UpgradeManager._post_switch_doctor

    def checked_doctor(self, release, **kwargs):
        result = real_doctor(self, release, **kwargs)
        assert result["status"] == "healthy", json.dumps(result, sort_keys=True)
        return result

    monkeypatch.setattr(UpgradeManager, "_post_switch_doctor", checked_doctor)
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
    return paths, candidate, manifest, original_journal


def test_supported_reset_rehearses_fresh_backup_and_accepts_selection(
    tmp_path, monkeypatch
):
    accepted_reset_fixture(tmp_path, monkeypatch)
