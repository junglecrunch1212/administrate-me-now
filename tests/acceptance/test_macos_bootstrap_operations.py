"""Gate 5 credential-free process proof for bootstrap and recovery."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap.backup_restore import BackupManager
from bootstrap.bridge_enrollment import (
    BridgeEnrollmentManager,
    MemberBridgeInstaller,
    MemberBridgePaths,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.doctor import Doctor
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.upgrade import UpgradeManager
from bootstrap.wizard import BootstrapWizard

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_member_bridge_helpers_execute_from_the_archived_release() -> None:
    installer = (REPO_ROOT / "bootstrap" / "install-member-bridge.sh").read_text(
        "utf-8"
    )
    helper = (REPO_ROOT / "bootstrap" / "member-bridge.sh").read_text("utf-8")
    assert 'cd "$release_dir"' in installer
    assert 'cd "$release_root"' in helper
    assert ".administrateme-source-commit" in installer
    assert ".administrateme-bridge-installed-commit" in installer
    assert 'poetry_venv="$BRIDGE_ROOT/app/tooling/poetry-2.4.1"' in installer
    assert '"$poetry_venv/bin/poetry" -C bridge sync' in installer


def test_credential_free_bootstrap_doctor_backup_restore_round_trip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    wizard = BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    )
    state = wizard.run()
    assert all(item["status"] == "complete" for item in state["sections"].values())
    ServiceManager(paths, test_mode=True).start()
    before = Doctor(paths, root=REPO_ROOT, test_mode=True).run()
    assert before["status"] == "healthy"
    proof = next(
        check
        for check in before["checks"]
        if check["name"] == "canonical_synthetic_round_trip"
    )["evidence"]
    assert proof["external_side_effects"] == 0
    assert proof["review_suppressed_by_observation"] is True
    plugin_skills = paths.instance_dir / "openclaw" / "runtime" / "plugin-skills"
    plugin_skills.mkdir(parents=True, exist_ok=True)
    (plugin_skills / "generated-skill").symlink_to(REPO_ROOT / "packs" / "skills")
    transient = paths.instance_dir / "events" / "events.db-shm"
    transient.write_bytes(b"transient sqlite state")
    original_read_bytes = Path.read_bytes

    def remove_transient_on_read(path: Path):
        if path == transient:
            path.unlink(missing_ok=True)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", remove_transient_on_read)
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    assert backup["encrypted"] is True
    restored = manager.restore(Path(backup["path"]))
    assert restored["projections_rebuilt"] == 15
    assert restored["workbooks_rebuilt"] is True
    assert Path(restored["rollback"]).is_dir()
    after = Doctor(
        paths,
        root=REPO_ROOT,
        test_mode=True,
    ).run(include_synthetic_proof=False)
    assert after["status"] == "healthy"
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    store = secret_store(paths, test_mode=True)
    backup_bytes = Path(backup["path"]).read_bytes()
    for reference in references.values():
        secret = store.get(reference)
        assert secret not in backup_bytes


def test_reviewed_candidate_controller_completes_guarded_upgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-upgrade")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("ADMINME_UPGRADE_CONTROLLER_COMMIT", commit)
    reconciled: list[tuple[Path, bool]] = []

    def reconcile(_paths, release: Path, *, test_mode: bool = False):
        reconciled.append((release, test_mode))
        return {"ok": True}

    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        reconcile,
    )

    result = UpgradeManager(
        paths,
        root=REPO_ROOT,
        test_mode=True,
    ).activate(REPO_ROOT)

    assert result["status"] == "upgraded"
    assert result["commit"] == commit
    assert result["upgrade_controller_attested"] is True
    pointer = json.loads((paths.app_dir / "current-release.json").read_text("utf-8"))
    assert pointer["commit"] == commit
    assert Path(pointer["path"]).is_dir()
    assert reconciled == [(Path(pointer["path"]), True)]


def test_doctor_fails_when_required_triage_pipeline_is_poisoned(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment-poisoned")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    checkpoint_path = paths.instance_dir / "events" / "bus_checkpoints.db"
    connection = sqlite3.connect(str(checkpoint_path))
    try:
        connection.execute(
            "UPDATE bus_consumer_offsets SET degraded=1, "
            "consecutive_failures=5, poison_event_id='fixture-poison' "
            "WHERE subscriber_id='pipeline:pipeline:noise_filtering'"
        )
        connection.commit()
    finally:
        connection.close()

    report = Doctor(paths, root=REPO_ROOT, test_mode=True).run(
        include_synthetic_proof=False
    )
    assert report["status"] == "failed"
    check = next(
        item
        for item in report["checks"]
        if item["name"] == "required_reactive_pipeline_delivery"
    )
    assert check["ok"] is False
    assert check["evidence"]["unhealthy"] == ["pipeline:pipeline:noise_filtering"]


def test_member_bridge_export_accept_finalize_uses_production_ingest_composition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "central")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    ServiceManager(paths, test_mode=True).start()
    manager = BridgeEnrollmentManager(paths, root=REPO_ROOT, test_mode=True)
    transfer_path = tmp_path / "principal-a.adminmebridge"
    exported = manager.export(
        "member_fixture_principal_a",
        transfer_path,
        central_url="https://central.example-tailnet.ts.net",
        transfer_key=b"p" * 32,
    )
    report = json.loads(paths.public_config_path.read_text("utf-8"))
    assert report["bootstrap_complete"] is False
    assert report["bridge_enrollment"]["credential_free_fixture"] is True

    def submitter(transfer: dict, envelope: dict) -> str:
        references = json.loads(
            (paths.instance_dir / "config" / "secret-references.json").read_text(
                "utf-8"
            )
        )["references"]
        bridge_records = json.loads(manager.index_path.read_text("utf-8"))
        settings = RuntimeSettings.credential_free(
            paths.instance_dir,
            encryption_key=secret_store(paths, test_mode=True).get(
                references["event_log"]
            ),
            repository_root=REPO_ROOT,
            member_identities={
                "principal-a@example.invalid": "member_fixture_principal_a"
            },
            bridge_enrollments=bridge_records,
            member_roles={"member_fixture_principal_a": "principal"},
        )
        with TestClient(create_application(settings)) as client:
            response = client.post(
                "/api/bridge/ingest",
                json=envelope,
                headers={"Tailscale-User-Login": transfer["tailscale_identity"]},
            )
        assert response.status_code == 202, response.text
        return response.json()["event_id"]

    receipt = MemberBridgeInstaller(
        MemberBridgePaths.from_root(tmp_path / "member"),
        root=REPO_ROOT,
        test_mode=True,
        submitter=submitter,
    ).accept(
        transfer_path,
        exported.transfer_code,
        identity_override="principal-a@example.invalid",
        node_override="node-principal-a",
    )
    assert receipt["status"] == "active"
    finalized = manager.finalize("member_fixture_principal_a")
    assert finalized["status"] == "active"
    assert finalized["bootstrap_material_revoked"] is True
    index = json.loads(manager.index_path.read_text("utf-8"))
    assert index["principal-a@example.invalid"]["status"] == "active"
    assert (
        json.loads(paths.public_config_path.read_text("utf-8"))["bootstrap_complete"]
        is False
    )

    manager.revoke("member_fixture_principal_b")
    manager.revoke("member_fixture_child")
    assert (
        json.loads(paths.public_config_path.read_text("utf-8"))["bootstrap_complete"]
        is True
    )
