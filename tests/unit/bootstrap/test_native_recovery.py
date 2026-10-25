"""Stopped-generation admission and crash-safe linked recovery, no native lab."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.deployment_readiness import native_forward_recovery_ready
from bootstrap import native_recovery as recovery
from bootstrap import openclaw_migration as migration
from bootstrap.activation_transaction import (
    ActivationJournal,
    ActivationJournalError,
    sha256_json,
)
from bootstrap.contracts import atomic_write
from bootstrap.upgrade import UpgradeManager, UpgradeError
from tests.unit.bootstrap.test_activation_transaction import (
    _paths,
    _journal_value,
    _commit_activation,
)

CORRECTED = "c" * 40


def fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Path, dict[str, Any]]:
    paths = _paths(tmp_path)
    journal = _journal_value(paths)
    native = paths.instance_dir / "openclaw"
    (native / "runtime").mkdir(mode=0o700, parents=True)
    (native / "workspace").mkdir(mode=0o700)
    atomic_write(
        native / "runtime/openclaw.json",
        json.dumps(
            {
                "agents": {
                    "entries": {"main": {"workspace": str(native / "workspace")}},
                    "ownership": "explicit",
                }
            }
        ).encode(),
    )
    atomic_write(native / "runtime/sessions.json", b'{"session":"retained"}')
    atomic_write(native / "runtime/jobs.json", b'{"timer":"retained"}')
    atomic_write(paths.instance_dir / "events.db", b"retained encrypted domain history")
    atomic_write(
        paths.instance_dir / "config/runtime.yaml",
        b"observation_mode_override:\n  active: true\n",
    )
    atomic_write(paths.instance_dir / "config/observation.json", b'{"active":true}')
    capsule = migration.capture(paths, journal)
    journal["openclaw_migration"] = {
        **capsule,
        "state": "contained",
        "candidate_admitted": True,
    }
    journal["phase"] = "native_forward_repair_required"
    parent = ActivationJournal(paths).create(journal)
    candidate = tmp_path / "corrected"
    (candidate / "bootstrap").mkdir(parents=True)
    (candidate / "bootstrap/versions.json").write_bytes(
        (Path(parent["target_path"]) / "bootstrap/versions.json").read_bytes()
    )
    monkeypatch.setattr(recovery, "reviewed_candidate_commit", lambda _path: CORRECTED)
    monkeypatch.setattr(
        recovery, "verify_release_source_identity", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        recovery,
        "capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(public=lambda: {"synthetic": "bound"}),
    )
    return paths, candidate, parent


def save_plan(paths: Any, candidate: Path) -> tuple[Path, dict[str, Any]]:
    value = recovery.prepare(paths, candidate, test_mode=True)
    path = paths.app_dir / "native-recovery-plan.json"
    atomic_write(path, json.dumps(value).encode())
    return path, value


def successor(
    parent: dict[str, Any], plan: dict[str, Any], paths: Any
) -> dict[str, Any]:
    value = {
        key: copy.deepcopy(child)
        for key, child in parent.items()
        if key
        not in {
            "contract",
            "schema_version",
            "created_at_ns",
            "updated_at_ns",
            "uid",
            "content_exposed",
            "openclaw_migration",
        }
    }
    value.update(
        transaction_id="e" * 32,
        phase="created",
        candidate_commit=CORRECTED,
        target_commit=CORRECTED,
        candidate_path=str(paths.app_dir / "releases" / CORRECTED),
        target_path=str(paths.app_dir / "releases" / CORRECTED),
        native_recovery=recovery.binding(plan),
    )
    return value


def test_corrected_plan_binds_retained_generations_and_distinct_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    plan_path, plan = save_plan(paths, candidate)
    assert recovery.load(paths, candidate, plan_path, test_mode=True) == plan
    assert plan["prior_target_health_proven"] is False
    assert plan["automatic_rollback_eligible"] is False
    assert ActivationJournal(paths).load() == parent
    monkeypatch.setattr(
        recovery, "reviewed_candidate_commit", lambda _path: parent["target_commit"]
    )
    with pytest.raises(recovery.NativeRecoveryError, match="distinct"):
        recovery.load(paths, candidate, plan_path, test_mode=True)


@pytest.mark.parametrize(
    "relative",
    [
        "events.db",
        "config/adapters.json",
        "config/settings-control.json",
        "openclaw/runtime/sessions.json",
        "openclaw/runtime/jobs.json",
    ],
)
def test_foreign_domain_grant_control_or_native_write_invalidates_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    plan_path, _ = save_plan(paths, candidate)
    atomic_write(paths.instance_dir / relative, b"changed generation")
    with pytest.raises(recovery.NativeRecoveryError, match="changed"):
        recovery.load(paths, candidate, plan_path, test_mode=True)
    assert ActivationJournal(paths).load() == parent


def test_unknown_native_child_or_removed_containment_is_not_recovery_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    atomic_write(paths.instance_dir / "config/observation.json", b'{"active":false}')
    with pytest.raises(recovery.NativeRecoveryError, match="containment"):
        recovery.prepare(paths, candidate, test_mode=True)
    atomic_write(paths.instance_dir / "config/observation.json", b'{"active":true}')
    receipt = {
        **parent["openclaw_migration"],
        "doctor": {
            "settled": False,
            "result_path": "/tmp/openclaw/openclaw-update-doctor-1-00000000-0000-4000-8000-000000000001.json",
            "nonce": "00000000-0000-4000-8000-000000000002",
            "pid": 123,
            "returncode": None,
            "classification": None,
        },
    }
    ActivationJournal(paths).transition(
        parent, "native_forward_repair_required", openclaw_migration=receipt
    )
    with pytest.raises(recovery.NativeRecoveryError, match="unsettled"):
        recovery.prepare(paths, candidate, test_mode=True)


def test_atomic_successor_admission_retains_exact_failed_journal_and_revalidates_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    plan_path, plan = save_plan(paths, candidate)
    owner = ActivationJournal(paths)
    child = owner.create(successor(parent, plan, paths), contained_parent=parent)
    retained = json.loads(
        (owner.history / f"{parent['transaction_id']}.json").read_text()
    )
    assert retained == parent
    assert retained["phase"] == "native_forward_repair_required"
    assert recovery.lineage(paths, child) == [child, parent]
    assert recovery.load(paths, candidate, plan_path, test_mode=True) == plan
    owner.transition(child, "selector_mutation_authorized")
    with pytest.raises(recovery.NativeRecoveryError, match="mutation boundary"):
        recovery.load(paths, candidate, plan_path, test_mode=True)


def test_crash_after_parent_archive_keeps_old_owner_and_identical_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bootstrap.activation_transaction as module

    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    _, plan = save_plan(paths, candidate)
    owner = ActivationJournal(paths)
    write = module._write_private_at

    def interrupted(fd: int, name: str, payload: bytes, *, replace: bool) -> None:
        if name == "active.json" and replace:
            raise RuntimeError("synthetic process interruption")
        write(fd, name, payload, replace=replace)

    monkeypatch.setattr(module, "_write_private_at", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        owner.create(successor(parent, plan, paths), contained_parent=parent)
    assert owner.load() == parent
    assert (
        json.loads((owner.history / f"{parent['transaction_id']}.json").read_text())
        == parent
    )
    monkeypatch.setattr(module, "_write_private_at", write)
    child = owner.create(successor(parent, plan, paths), contained_parent=parent)
    assert child["native_recovery"]["parent_journal_sha256"] == sha256_json(parent)


def test_conflicting_history_or_link_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    _, plan = save_plan(paths, candidate)
    owner = ActivationJournal(paths)
    atomic_write(owner.history / f"{parent['transaction_id']}.json", b'{"forged":true}')
    with pytest.raises(ActivationJournalError, match="conflicts"):
        owner.create(successor(parent, plan, paths), contained_parent=parent)
    assert owner.load() == parent


def test_failed_correction_before_native_capture_stays_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    _, plan = save_plan(paths, candidate)
    owner = ActivationJournal(paths)
    child = owner.create(successor(parent, plan, paths), contained_parent=parent)
    manager = UpgradeManager(paths, root=candidate, test_mode=True)
    monkeypatch.setattr(manager, "_required_intake_policies", lambda: frozenset())
    with pytest.raises(UpgradeError, match="linked state/history"):
        manager._recover_interrupted_transaction(candidate)  # noqa: SLF001
    current = owner.load()
    assert current["phase"] == "native_forward_repair_required"
    assert current["native_recovery"] == child["native_recovery"]
    assert recovery.lineage(paths, current)[-1] == parent


def test_only_committed_corrected_artifact_links_original_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, parent = fixture(tmp_path, monkeypatch)
    _, plan = save_plan(paths, candidate)
    owner = ActivationJournal(paths)
    child = owner.create(successor(parent, plan, paths), contained_parent=parent)
    with pytest.raises(ActivationJournalError, match="unavailable"):
        owner.native_successor(
            target_commit=CORRECTED,
            original_target=parent["target_commit"],
            original_predecessor=parent["predecessor_commit"],
        )
    completed = _commit_activation(owner, child)
    owner.finish(completed)
    assert owner.native_successor(
        target_commit=CORRECTED,
        original_target=parent["target_commit"],
        original_predecessor=parent["predecessor_commit"],
    ) == (completed, parent)
    with pytest.raises(ActivationJournalError, match="unavailable"):
        owner.native_successor(
            target_commit=CORRECTED,
            original_target="f" * 40,
            original_predecessor=parent["predecessor_commit"],
        )


def test_forward_readiness_does_not_claim_rollback_or_live_success() -> None:
    from datetime import datetime, timezone

    evidence = {
        "deployment_binding": {"release_commit": CORRECTED},
        "backup_rollback": {
            "automatic_rollback_eligible": True,
            "encrypted_backup_authenticated": True,
            "restore_rehearsed": True,
            "prior_release_retained": True,
            "tested_at": datetime.now(timezone.utc).isoformat(),
        },
        "activation_evidence": {
            "contract": "deployment-activation-evidence-v1",
            "crash_recoverable_replacement": True,
            "candidate_live_doctor": "pending_post_switch",
            **{
                key: "a" * 64
                for key in (
                    "candidate_pending_attestation_sha256",
                    "fresh_backup_artifact_sha256",
                    "fresh_rehearsal_receipt_sha256",
                    "predecessor_rollback_eligibility_sha256",
                )
            },
        },
    }
    recovery.mark_forward_readiness(
        evidence,
        journal={
            "target_commit": CORRECTED,
            "transaction_id": "b" * 32,
            "pending_attestation_sha256": "a" * 64,
        },
        migration_origin="c" * 32,
    )
    assert native_forward_recovery_ready(evidence)
    assert evidence["backup_rollback"]["automatic_rollback_eligible"] is False
    assert (
        evidence["activation_evidence"]["candidate_live_doctor"]
        == "pending_post_switch"
    )
    for mutation in (
        "release_commit",
        "prior_target_health_proven",
        "pending_attestation_sha256",
    ):
        wrong = copy.deepcopy(evidence)
        wrong["native_forward_recovery"][mutation] = True
        assert not native_forward_recovery_ready(wrong)
    wrong = copy.deepcopy(evidence)
    wrong["backup_rollback"]["automatic_rollback_eligible"] = True
    assert not native_forward_recovery_ready(wrong)


def test_pending_readiness_requires_same_explicit_recovery_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap import candidate_readiness
    from tests.unit.bootstrap.test_candidate_readiness import _arguments, NOW

    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    plan = {
        "parent_transaction_id": "a" * 32,
        "parent_journal_sha256": "b" * 64,
        "candidate_commit": commit,
        "failed_target_commit": "c" * 40,
    }
    plan_path = paths.backup_dir / "native-plan.json"
    monkeypatch.setattr(recovery, "load", lambda *_a, **_k: plan)
    pending, _snapshot = candidate_readiness.build_pending_attestation(
        paths,
        candidate=candidate,
        expected_candidate_commit=commit,
        rehearsal_receipt=receipt,
        backup_artifact=artifact,
        htr10_review=htr10,
        confirmations=confirmations,
        observed_at=observed,
        now=NOW,
        native_recovery_plan=plan_path,
    )
    assert pending["native_recovery"] == recovery.binding(plan)
    directory = paths.app_dir / "readiness/candidate-pending"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(
        directory / f"{pending['attestation_id']}.json", json.dumps(pending).encode()
    )
    with pytest.raises(
        candidate_readiness.CandidateReadinessError, match="exact current plan"
    ):
        candidate_readiness.load_pending_attestation_for_activation(
            paths,
            attestation_id=pending["attestation_id"],
            candidate=candidate,
            candidate_commit=commit,
            now=NOW,
        )
    actual, _digest, _binding = (
        candidate_readiness.load_pending_attestation_for_activation(
            paths,
            attestation_id=pending["attestation_id"],
            candidate=candidate,
            candidate_commit=commit,
            now=NOW,
            native_recovery_plan=plan_path,
        )
    )
    assert actual == pending
    changed = {**plan, "parent_journal_sha256": "d" * 64}
    monkeypatch.setattr(recovery, "load", lambda *_a, **_k: changed)
    with pytest.raises(
        candidate_readiness.CandidateReadinessError, match="exact current plan"
    ):
        candidate_readiness.load_pending_attestation_for_activation(
            paths,
            attestation_id=pending["attestation_id"],
            candidate=candidate,
            candidate_commit=commit,
            now=NOW,
            native_recovery_plan=plan_path,
        )
