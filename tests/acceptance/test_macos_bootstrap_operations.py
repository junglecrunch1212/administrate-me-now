"""Gate 5 credential-free process proof for bootstrap and recovery."""

from __future__ import annotations

import json
from pathlib import Path

from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.doctor import Doctor
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.wizard import BootstrapWizard

REPO_ROOT = Path(__file__).resolve().parents[2]


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
        check for check in before["checks"] if check["name"] == "canonical_synthetic_round_trip"
    )["evidence"]
    assert proof["external_side_effects"] == 0
    assert proof["review_suppressed_by_observation"] is True
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    assert backup["encrypted"] is True
    restored = manager.restore(Path(backup["path"]))
    assert restored["projections_rebuilt"] == 13
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
