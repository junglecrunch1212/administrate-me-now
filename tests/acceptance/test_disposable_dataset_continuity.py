"""Hosted composition: bounded state survives ordinary release transitions.

Only provider, OS service, human-evidence and controller-location fixtures are
synthetic. Runtime ingestion, durable cursors, encrypted backup/replay, pending
proofs, publication, activation journals and reversal execute production code.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.loader import load_builtin_adapters
from adminme.deployment_readiness import evaluate_deployment_readiness
from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap import (
    backup_restore,
    candidate_readiness,
    disposable_test_reset,
    reviewed_launcher,
)
from bootstrap.backup_restore import BackupManager
from bootstrap.candidate_readiness import (
    capture_deployment_snapshot,
    record_pending_attestation,
)
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.disposable_test_reset import (
    TestResetError as ResetError,
    _accepted_dataset,
    select_messages,
)
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.acceptance.test_disposable_test_acquisition import (
    ROOT,
    _Gmail,
    _message,
    _settled_skill_receipts,
    _source_events,
)
from tests.acceptance.test_disposable_test_reset_controller import (
    accepted_reset_fixture,
)
from tests.acceptance.test_macos_bootstrap_operations import _write_htr10_fixture
from tests.unit.test_disposable_reset_readiness import _inputs


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _write(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, indent=2).encode())


def _commit(candidate: Path, label: str) -> str:
    atomic_write(candidate / "continuity-fixture.txt", label.encode())
    subprocess.run(
        ["git", "-C", str(candidate), "add", "continuity-fixture.txt"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Continuity Fixture",
            "-c",
            "user.email=continuity-fixture.invalid",
            "commit",
            "-qm",
            label,
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _pending(paths: DeploymentPaths, candidate: Path, commit: str, monkeypatch) -> str:
    # The committed candidate is the same source being exercised in-process.
    monkeypatch.setattr(
        candidate_readiness,
        "__file__",
        str(candidate / "bootstrap/candidate_readiness.py"),
    )
    monkeypatch.setattr(
        backup_restore, "__file__", str(candidate / "bootstrap/backup_restore.py")
    )
    manager = BackupManager(paths, root=candidate, test_mode=True)
    backup = manager.create(paths.backup_dir / f"preliminary-{commit}.ambackup")
    receipt = paths.backup_dir / f"preliminary-{commit}.json"
    result = manager.rehearse(
        Path(backup["path"]), candidate=candidate, receipt=receipt
    )
    assert result["status"] == "healthy"
    now = datetime.now(timezone.utc)
    htr10 = _write_htr10_fixture(paths, candidate=candidate, now=now)
    names = ("htr_01", "htr_02", "htr_03", "htr_04", "htr_06", "htr_07")
    pending = record_pending_attestation(
        paths,
        candidate=candidate,
        expected_candidate_commit=commit,
        rehearsal_receipt=receipt,
        backup_artifact=Path(backup["path"]),
        htr10_review=htr10,
        confirmations={name: True for name in (*names, "htr_10")},
        observed_at={name: (now - timedelta(minutes=20)).isoformat() for name in names},
    )
    return pending["attestation_id"]


def _assert_backup_dimension(paths: DeploymentPaths, commit: str) -> None:
    snapshot = capture_deployment_snapshot(paths)
    values = _inputs()
    values["host_topology"]["deployment_binding"] = {
        "release_commit": commit,
        "instance_fingerprint": snapshot.instance_fingerprint,
        "host_fingerprint": snapshot.host_fingerprint,
        "configuration_fingerprint": snapshot.configuration_fingerprint,
    }
    values["deployment_evidence"] = _read(
        paths.instance_dir / "config/deployment-readiness.json"
    )
    values["disposable_test_dataset"] = _read(
        paths.instance_dir / "config/disposable-test-dataset.json"
    )
    result = evaluate_deployment_readiness(**values)
    assert result["dimensions"]["attestation_binding"]["state"] == "healthy"
    assert result["dimensions"]["backup_rollback"]["state"] == "healthy"
    assert result["production_rollback_proven"] is False


def _runtime_snapshot(paths: DeploymentPaths, service: _Gmail, monkeypatch):
    adapters = _read(paths.instance_dir / "config/adapters.json")
    selected = adapters["disposable_test_acquisition"]
    # The controller's service fixture keeps providers paused. This separate
    # real runtime uses the same persisted policy with a fake selected provider.
    next(row for row in adapters["adapters"] if row["id"] == selected["adapter_id"])[
        "enabled"
    ] = True
    adapter_type = type(load_builtin_adapters(adapters, repository_root=ROOT)[0])
    monkeypatch.setattr(
        adapter_type, "_connect_service", lambda *args, **kwargs: service
    )
    monkeypatch.setattr(adapter_type, "POLL_INTERVAL_S", 3600)
    mapping = _read(paths.instance_dir / "config/member-mapping.json")
    references = _read(paths.instance_dir / "config/secret-references.json")[
        "references"
    ]
    key = secret_store(paths, test_mode=True).get(references["event_log"])
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=key,
        repository_root=ROOT,
        member_identities={
            row["tailscale_identity"]: row["member_id"] for row in mapping["members"]
        },
        member_roles={row["member_id"]: row["role"] for row in mapping["members"]},
        household_mapping=mapping,
        adapter_config=adapters,
        deterministic_openclaw_fixture=True,
        human_triage_management_enabled=True,
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime

        async def settled():
            adapter = runtime.adapters._managed[selected["adapter_id"]].adapter
            deadline = asyncio.get_running_loop().time() + 30
            while (
                adapter.status().extra.get("initial_backfill", {}).get("state")
                != "complete"
            ):
                assert asyncio.get_running_loop().time() < deadline, adapter.status()
                await asyncio.sleep(0.01)
            receipts = await _settled_skill_receipts(runtime)
            events = await _source_events(runtime)
            return ({event["event_id"] for event in events}, receipts, adapter._cursor)

        assert client.portal is not None
        state = client.portal.call(settled)
        public = client.get("/api/system/ready")
        assert selected["provider_account_id"] not in public.text
        for row in mapping["members"]:
            response = client.get(
                "/api/member/v1/surfaces/settings",
                headers={"Tailscale-User-Login": row["tailscale_identity"]},
            )
            assert response.status_code == 200
            if row["member_id"] != selected["member_id"]:
                assert selected["provider_account_id"] not in response.text
    return state


def test_accepted_bounded_dataset_survives_upgrade_rollback_and_forward_reactivation(
    tmp_path, monkeypatch
):
    paths, candidate, manifest, abandoned_journal = accepted_reset_fixture(
        tmp_path, monkeypatch
    )
    archive = Path(manifest["archive"])
    accepted_archive_bytes = (archive / "reset.json").read_bytes()
    first_commit = manifest["candidate_commit"]
    policy = _read(paths.instance_dir / "config/adapters.json")[
        "disposable_test_acquisition"
    ]
    ids = policy["message_ids"]
    provider = _Gmail()
    provider.messages_by_id = {
        value: _message(value, sent=index == 1) for index, value in enumerate(ids)
    }
    provider.messages_by_id["outside-selection"] = _message("outside-selection")
    provider.next_history = {
        "historyId": "100",
        "history": [{"messagesAdded": [{"message": {"id": "outside-selection"}}]}],
    }
    initial = _runtime_snapshot(paths, provider, monkeypatch)
    assert len(initial[0]) == 2
    assert set(provider.get_ids) == set(ids)
    baseline_gets = list(provider.get_ids)
    readiness_path = paths.instance_dir / "config/deployment-readiness.json"
    evidence = _read(readiness_path)
    evidence["provider_effect_window"] = {
        "started_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    }
    _write(readiness_path, evidence)
    selection = paths.root / "continuity-selection.json"
    _write(selection, {"message_ids": ids})
    original_doctor = UpgradeManager._post_switch_doctor

    def checked_doctor(manager, release, **kwargs):
        pointer = _read(paths.app_dir / "current-release.json")
        _assert_backup_dimension(paths, pointer["commit"])
        return original_doctor(manager, release, **kwargs)

    monkeypatch.setattr(UpgradeManager, "_post_switch_doctor", checked_doctor)
    services = ServiceManager(paths, test_mode=True)
    second_commit = _commit(candidate, "bounded consumer behavior B")
    pending = _pending(paths, candidate, second_commit, monkeypatch)
    services.start()
    manager = UpgradeManager(
        paths, root=candidate, test_mode=True, pending_attestation_id=pending
    )
    upgraded = manager.activate(candidate)
    assert upgraded["commit"] == second_commit
    services.stop()
    # A supported encrypted restore gives state a new directory coordinate.
    # Its logical accepted marker and current activation proof must still work.
    restore_manager = BackupManager(paths, root=candidate, test_mode=True)
    snapshot_backup = restore_manager.create(
        paths.backup_dir / "bounded-current.ambackup"
    )
    _write(selection, {"message_ids": [*ids, "spent-before-restore"]})
    assert select_messages(manager, selection)["lifetime_admitted_count"] == 3
    admission_path = Path(manifest["lifetime_admissions"]["path"])
    admission_bytes = admission_path.read_bytes()
    previous_inode = paths.instance_dir.stat().st_ino
    assert (
        restore_manager.restore(Path(snapshot_backup["path"]))["status"] == "restored"
    )
    assert paths.instance_dir.stat().st_ino != previous_inode
    assert admission_path.read_bytes() == admission_bytes
    # Current state has only two IDs again. The third slot remains spent in
    # the logical dataset, so a different 200-ID selection must be refused.
    _write(
        selection, {"message_ids": [*ids, *(f"other-{index}" for index in range(198))]}
    )
    with pytest.raises(ResetError, match="lifetime admission cap"):
        select_messages(manager, selection)
    assert admission_path.read_bytes() == admission_bytes
    _write(selection, {"message_ids": ids})
    assert (
        _accepted_dataset(paths, second_commit)["activation_evidence"][
            "candidate_live_doctor"
        ]
        is True
    )
    assert select_messages(manager, selection)["lifetime_admitted_count"] == 3
    assert _runtime_snapshot(paths, provider, monkeypatch) == initial
    services.start()
    rolled_back = manager.rollback(recovery_repository=candidate)
    assert rolled_back["commit"] == first_commit
    services.stop()
    assert (
        _accepted_dataset(paths, first_commit)["disposable_test_reset"][
            "release_commit"
        ]
        == first_commit
    )
    assert select_messages(manager, selection)["lifetime_admitted_count"] == 3
    assert _runtime_snapshot(paths, provider, monkeypatch) == initial
    with pytest.raises(UpgradeError, match="already consumed"):
        manager._publish_release(candidate, second_commit)
    third_commit = _commit(candidate, "bounded consumer behavior B reactivated as C")
    pending = _pending(paths, candidate, third_commit, monkeypatch)
    services.start()
    manager = UpgradeManager(
        paths, root=candidate, test_mode=True, pending_attestation_id=pending
    )
    assert manager.activate(candidate)["commit"] == third_commit
    services.stop()
    assert select_messages(manager, selection)["lifetime_admitted_count"] == 3
    assert _runtime_snapshot(paths, provider, monkeypatch) == initial
    assert provider.get_ids == baseline_gets
    assert "outside-selection" not in provider.get_ids
    _write(selection, {"message_ids": [*ids, "synthetic-later-admission"]})
    real_write = disposable_test_reset.atomic_write
    adapter_path = paths.instance_dir / "config/adapters.json"

    def interrupted_selection(path, payload, **kwargs):
        if path == adapter_path:
            raise OSError("synthetic interruption after readiness publication")
        return real_write(path, payload, **kwargs)

    with monkeypatch.context() as interruption:
        interruption.setattr(
            disposable_test_reset, "atomic_write", interrupted_selection
        )
        with pytest.raises(OSError, match="synthetic interruption"):
            select_messages(manager, selection)
    with pytest.raises(ResetError, match="activation binding is stale"):
        _accepted_dataset(paths, third_commit)
    assert select_messages(manager, selection)["lifetime_admitted_count"] == 4
    _accepted_dataset(paths, third_commit)
    assert provider.get_ids == baseline_gets
    assert (archive / "reset.json").read_bytes() == accepted_archive_bytes
    assert (archive / "original-activation.json").read_bytes() == abandoned_journal
    assert not (paths.app_dir / "activation/active.json").exists()
    retained_admissions = admission_path.with_suffix(".retained")
    admission_path.rename(retained_admissions)
    with pytest.raises(ResetError, match="admission receipt is unavailable"):
        select_messages(manager, selection)
    assert not admission_path.exists(), "missing lifetime evidence was recreated"
    retained_admissions.rename(admission_path)


def test_older_target_is_refused_before_controller_or_services_start(
    tmp_path, monkeypatch
):
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    _write(
        paths.instance_dir / "config/disposable-test-dataset.json",
        {"contract": "disposable-test-dataset-v1"},
    )
    candidate = tmp_path / "older"
    (candidate / "bootstrap").mkdir(parents=True)
    versions = _read(ROOT / "bootstrap/versions.json")
    versions["administrateme"].pop("supported_acquisition_contracts")
    _write(candidate / "bootstrap/versions.json", versions)
    manager = UpgradeManager(paths, root=ROOT, test_mode=True)
    monkeypatch.setattr(
        manager,
        "_recover_interrupted_transaction",
        lambda *args: pytest.fail("recovery started before compatibility guard"),
    )
    with pytest.raises(UpgradeError, match="bounded dataset acquisition contract"):
        manager.activate(candidate)
    monkeypatch.setattr(
        reviewed_launcher, "_reviewed_commit", lambda value: (value, "a" * 40)
    )
    calls = []

    def git_metadata(_candidate, arguments, **kwargs):
        calls.append(arguments)
        assert arguments[-1].endswith(":bootstrap/versions.json"), (
            "older controller source was loaded"
        )
        return json.dumps(versions).encode()

    monkeypatch.setattr(reviewed_launcher, "_run_git", git_metadata)
    with pytest.raises(
        reviewed_launcher.ReviewedLauncherError,
        match="bounded dataset acquisition contract",
    ):
        reviewed_launcher._execute_supervisor("update", paths.root, candidate, [])
    assert len(calls) == 1
    assert not paths.service_state_path.exists()
    current_commit, older_commit = "b" * 40, "c" * 40
    current = paths.app_dir / "releases" / current_commit
    older = paths.app_dir / "releases" / older_commit
    for release in (current, older):
        (release / "bootstrap").mkdir(parents=True, mode=0o700)
        release.chmod(0o700)
        _write(release / "bootstrap/versions.json", versions)
    _write(
        paths.app_dir / "current-release.json",
        {
            "commit": current_commit,
            "path": str(current),
            "previous": {"commit": older_commit, "path": str(older)},
        },
    )
    for rollback in (manager._rollback_locked, manager._rollback_transactional_locked):
        with pytest.raises(UpgradeError, match="bounded dataset acquisition contract"):
            rollback()
    recovery = UpgradeManager(paths, root=ROOT, test_mode=True)
    monkeypatch.setattr(
        recovery.activation_journal,
        "load",
        lambda: {
            "phase": "services_started",
            "predecessor_commit": older_commit,
            "predecessor_path": str(older),
        },
    )
    with pytest.raises(UpgradeError, match="bounded dataset acquisition contract"):
        recovery._recover_interrupted_transaction()
    assert not paths.service_state_path.exists()
    (paths.instance_dir / "config/disposable-test-dataset.json").unlink()
    # Ordinary datasets keep their existing runtime/tool compatibility rules.
    recovery._require_bounded_target(older)
