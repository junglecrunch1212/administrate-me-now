"""Failure proof remains private, durable and independent of rollback success."""

from __future__ import annotations

import ast
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bootstrap.activation_transaction import ActivationJournalError
from bootstrap.deployment_failure import (
    FAILURE_CHECKS,
    FAILURE_REASONS,
    failure_evidence,
    failure_summary,
    persist_failure,
    settlement_diagnostic_evidence,
)
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.unit.bootstrap.test_activation_transaction import (
    CANDIDATE_COMMIT,
    PREDECESSOR_COMMIT,
    _journal_value,
    _paths,
)


PRIVATE = "fixture-private-source-and-exception-text"


def test_triggering_readiness_is_detached_and_resanitized_before_persistence():
    consumer_id = "projection:triage"
    diagnostics = {
        "private_body": PRIVATE,
        "ready": False,
        "deployment_readiness": {
            "contract": "deployment-readiness-v1",
            "blocking_dimensions": ["observation_effects", PRIVATE],
        },
        "capabilities": {
            "policy:source-assurance": {
                "details": {
                    "contract": "source-assurance-v1",
                    "counts": {"failed": 1, "pending": 0, "private": PRIVATE},
                    "freshness": {
                        "state": "fresh",
                        "revision_current": True,
                        "refresh_in_progress": False,
                        "refresh_error_type": PRIVATE,
                    },
                },
            },
            consumer_id: {
                "kind": "projection",
                "state": "degraded",
                "details": {
                    "version": 12,
                    "lag_count": 7,
                    "consecutive_failures": 3,
                    "checkpoint_event_id": PRIVATE,
                    "poison_event_id": PRIVATE,
                },
            },
        },
    }
    trigger = settlement_diagnostic_evidence(diagnostics, consumer_id=consumer_id)
    diagnostics["capabilities"]["policy:source-assurance"]["details"]["counts"][
        "failed"
    ] = 0
    assert trigger["source_counts"]["failed"] == 1
    assert trigger["consumer"] == {
        "kind": "projection",
        "state": "degraded",
        "version": 12,
        "lag_count": 7,
        "consecutive_failures": 3,
        "checkpoint_present": True,
        "poison_present": True,
    }
    assert PRIVATE not in json.dumps(trigger)
    # Recheck even an allegedly sanitized record from another process.
    trigger["raw_exception"] = PRIVATE
    trigger["consumer"]["checkpoint_event_id"] = PRIVATE
    trigger["consumer"]["version"] = PRIVATE
    report = _report()
    bounded = report["checks"][2]["evidence"]
    bounded["reason"] = "source_assurance_failed"
    bounded["triggering_readiness"] = trigger
    safe = failure_evidence(report, allowed_consumers=frozenset({consumer_id}))
    assert safe["readiness"]["source_counts"]["failed"] == 0
    assert safe["readiness_origin"] == "retained_doctor"
    actual = safe["bounded"][0]["triggering_readiness"]
    assert actual["origin"] == "runtime_diagnostics"
    assert actual["source_counts"]["failed"] == 1
    assert actual["source_freshness"]["refresh_error_present"] is True
    assert "version" not in actual["consumer"]
    assert PRIVATE not in json.dumps(safe)
    assert "trigger_source_failed=1" in failure_summary(safe)
    assert "readiness_origin=retained_doctor" in failure_summary(safe)


def test_failure_allowlists_cover_reviewed_doctor_and_settlement_codes() -> None:
    root = Path(__file__).resolve().parents[3]
    for relative, constructor, allowed in (
        ("bootstrap/doctor.py", "DoctorCheck", FAILURE_CHECKS),
        ("bootstrap/service_settlement.py", "_SettlementStopped", FAILURE_REASONS),
    ):
        tree = ast.parse((root / relative).read_text("utf-8"))
        codes = {
            call.args[0].value
            for call in ast.walk(tree)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == constructor
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        }
        assert codes <= allowed


def _report() -> dict:
    return {
        "status": "failed",
        "private_body": PRIVATE,
        "checks": [
            {
                "name": "source_assurance_terminal_dispositions",
                "ok": False,
                "detail": PRIVATE,
                "evidence": {
                    "contract": "source-assurance-v1",
                    "raw_source": PRIVATE,
                    "counts": {"pending": 4, "failed": 0, "source_id": PRIVATE},
                    "freshness": {
                        "state": "stale",
                        "revision_current": False,
                        "refresh_in_progress": True,
                        "refresh_error_type": PRIVATE,
                    },
                },
            },
            {
                "name": "deployment_topology_and_recovery_readiness",
                "ok": False,
                "evidence": {
                    "contract": "deployment-readiness-v1",
                    "blocking_dimensions": ["observation_effects", PRIVATE],
                },
            },
            {
                "name": "bounded_service_settlement",
                "ok": False,
                "evidence": {
                    "contract": "bounded-service-settlement-v1",
                    "reason": "consumer_stalled",
                    "consumer_id": "projection:triage",
                    "phase": "readiness",
                    "source_id": PRIVATE,
                },
            },
            {"name": PRIVATE, "ok": False, "evidence": PRIVATE},
        ],
    }


def test_failure_evidence_retains_only_allowlisted_diagnostics() -> None:
    safe = failure_evidence(
        _report(), allowed_consumers=frozenset({"projection:triage"})
    )
    assert PRIVATE not in json.dumps(safe)
    assert PRIVATE not in failure_summary(safe)
    assert safe["bounded"] == [
        {
            "reason": "consumer_stalled",
            "consumer_id": "projection:triage",
            "phase": "readiness",
        }
    ]
    assert safe["readiness"] == {
        "deployment_evidence_present": True,
        "source_evidence_present": True,
        "blocking_dimensions": ["observation_effects", "unrecognized_dimension"],
        "source_counts": {"pending": 4, "failed": 0},
        "source_freshness": {
            "refresh_in_progress": True,
            "revision_current": False,
            "state": "stale",
            "refresh_error_present": True,
        },
    }
    missing = failure_evidence(
        {
            "checks": [
                {
                    "name": "deployment_topology_and_recovery_readiness",
                    "ok": False,
                    "evidence": {},
                },
                {
                    "name": "source_assurance_terminal_dispositions",
                    "ok": False,
                    "evidence": {},
                },
            ]
        },
        allowed_consumers=frozenset(),
    )
    assert missing["readiness"] == {
        "deployment_evidence_present": False,
        "source_evidence_present": False,
    }
    unknown = _report()
    unknown["checks"][2]["evidence"].update(
        reason=PRIVATE, consumer_id=PRIVATE, phase=PRIVATE
    )
    safe_unknown = failure_evidence(unknown, allowed_consumers=frozenset())
    assert PRIVATE not in json.dumps(safe_unknown)
    assert safe_unknown["bounded"] == [
        {
            "reason": "unrecognized_reason",
            "consumer_id": "unrecognized_consumer",
            "phase": "unrecognized_phase",
        }
    ]


@pytest.mark.parametrize("audit_unavailable", [False, True])
def test_failed_proof_is_recorded_before_recovery_without_changing_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_unavailable: bool
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    journal = manager.activation_journal.create(_journal_value(paths))
    original_journal = manager.activation_journal.path.read_bytes()
    candidate = paths.app_dir / "releases" / CANDIDATE_COMMIT
    monkeypatch.setattr(
        "bootstrap.upgrade.wait_for_settled_doctor", lambda **_kwargs: _report()
    )
    if audit_unavailable:

        def fail_audit(*_args):
            raise OSError(PRIVATE)

        monkeypatch.setattr("bootstrap.upgrade.persist_failure", fail_audit)

    result = manager._post_switch_doctor(candidate)
    assert result == _report()
    assert manager.activation_journal.path.read_bytes() == original_journal
    artifact = (
        manager.activation_journal.directory
        / "failures"
        / f"{journal['transaction_id']}-candidate_doctor.json"
    )
    original_audit = None
    if audit_unavailable:
        assert not artifact.exists()
        assert "audit unavailable" in manager._settlement_failure_summary(result)
    else:
        original_audit = artifact.read_bytes()
        document = json.loads(original_audit)
        assert document["transaction_id"] == journal["transaction_id"]
        assert document["operation"] == "activate"
        assert document["selected_commit"] == CANDIDATE_COMMIT
        assert document["stage"] == "candidate_doctor"
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
        assert stat.S_IMODE(artifact.parent.stat().st_mode) == 0o700
        assert PRIVATE not in original_audit.decode()
        manager._record_settlement_failure(
            candidate, {"status": "failed", "checks": []}
        )
        assert artifact.read_bytes() == original_audit
        assert manager._failure_audit_available is True

    recovery_proofs = []

    def recovered_doctor(release):
        assert release.name == PREDECESSOR_COMMIT
        assert manager.activation_journal.load()["phase"] == "recovering"
        if original_audit is not None:
            assert artifact.read_bytes() == original_audit
        recovery_proofs.append(release)
        return {"status": "healthy", "checks": []}

    monkeypatch.setattr(manager, "_post_switch_doctor", recovered_doctor)
    manager.services = SimpleNamespace(
        stop=lambda *_args, **_kwargs: None,
        start=lambda: SimpleNamespace(
            status="healthy", runtime="running", openclaw="running"
        ),
    )
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda _release: None)
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents", lambda *_args, **_kwargs: None
    )
    recovered = manager._recover_interrupted_transaction()
    assert recovered["status"] == "recovered"
    assert len(recovery_proofs) == 1
    assert not manager.activation_journal.path.exists()
    if original_audit is not None:
        assert artifact.read_bytes() == original_audit


@pytest.mark.parametrize("wrapped", [False, True])
def test_doctor_exception_records_safe_category_without_raw_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrapped: bool
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    journal = manager.activation_journal.create(_journal_value(paths))

    def timeout(**_kwargs):
        if wrapped:
            raise UpgradeError(PRIVATE) from subprocess.TimeoutExpired(PRIVATE, 120)
        raise TimeoutError(PRIVATE)

    monkeypatch.setattr("bootstrap.upgrade.wait_for_settled_doctor", timeout)
    with pytest.raises(UpgradeError, match="exception=timeout") as failure:
        manager._post_switch_doctor(paths.app_dir / "releases" / CANDIDATE_COMMIT)
    assert PRIVATE not in str(failure.value)
    artifact = (
        manager.activation_journal.directory
        / "failures"
        / f"{journal['transaction_id']}-candidate_doctor.json"
    )
    assert PRIVATE not in artifact.read_text("utf-8")
    assert (
        json.loads(artifact.read_text("utf-8"))["failure"]["exception_category"]
        == "timeout"
    )


@pytest.mark.parametrize("unsafe_directory", ["symlink", "permissions"])
def test_unsafe_failure_directory_cannot_write_or_change_active_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe_directory: str
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    manager.activation_journal.create(_journal_value(paths))
    journal_bytes = manager.activation_journal.path.read_bytes()
    failures = manager.activation_journal.directory / "failures"
    outside = tmp_path / "outside-failure-audit"
    outside.mkdir(mode=0o700)
    sentinel = outside / "untouched"
    sentinel.write_bytes(b"fixture outside content")
    if unsafe_directory == "symlink":
        failures.symlink_to(outside, target_is_directory=True)
    else:
        failures.mkdir(mode=0o700)
        failures.chmod(0o755)
    monkeypatch.setattr(
        "bootstrap.upgrade.wait_for_settled_doctor", lambda **_kwargs: _report()
    )

    result = manager._post_switch_doctor(paths.app_dir / "releases" / CANDIDATE_COMMIT)

    assert result == _report()
    assert manager._failure_audit_available is False
    assert "failure audit unavailable" in manager._settlement_failure_summary(result)
    assert manager.activation_journal.path.read_bytes() == journal_bytes
    assert sorted(path.name for path in outside.iterdir()) == ["untouched"]
    assert sentinel.read_bytes() == b"fixture outside content"
    if unsafe_directory == "symlink":
        assert failures.is_symlink()
    else:
        assert list(failures.iterdir()) == []
        assert stat.S_IMODE(failures.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    "invalid_existing", ["transaction", "stage", "symlink", "permissions"]
)
def test_existing_failure_identity_and_file_safety_cannot_be_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_existing: str
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    journal = manager.activation_journal.create(_journal_value(paths))
    journal_bytes = manager.activation_journal.path.read_bytes()
    candidate = paths.app_dir / "releases" / CANDIDATE_COMMIT
    manager._record_settlement_failure(candidate, _report())
    assert manager._failure_audit_available is True
    artifact = (
        manager.activation_journal.directory
        / "failures"
        / f"{journal['transaction_id']}-candidate_doctor.json"
    )
    document = json.loads(artifact.read_bytes())
    if invalid_existing in {"transaction", "stage"}:
        invalid_document = dict(document)
        if invalid_existing == "transaction":
            invalid_document["transaction_id"] = "9" * 32
        else:
            invalid_document["stage"] = "recovery_doctor"
        artifact.write_text(json.dumps(invalid_document), encoding="utf-8")
    elif invalid_existing == "symlink":
        outside = tmp_path / "outside-audit.json"
        outside.write_bytes(artifact.read_bytes())
        outside.chmod(0o600)
        artifact.unlink()
        artifact.symlink_to(outside)
    else:
        artifact.chmod(0o644)
    prior_bytes = artifact.read_bytes()
    prior_inode = artifact.lstat().st_ino

    with pytest.raises(ActivationJournalError):
        persist_failure(manager.activation_journal, document)

    assert artifact.read_bytes() == prior_bytes
    assert artifact.lstat().st_ino == prior_inode
    assert manager.activation_journal.path.read_bytes() == journal_bytes
    assert sorted(path.name for path in artifact.parent.iterdir()) == [artifact.name]
    if invalid_existing == "symlink":
        assert artifact.is_symlink()
        assert outside.read_bytes() == prior_bytes
    elif invalid_existing == "permissions":
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o644


def test_candidate_and_recovery_each_preserve_their_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    for name in (
        "ADMINME_UPDATE_SUPERVISED",
        "ADMINME_CONTROLLER_STARTED_MONOTONIC",
        "ADMINME_CONTROLLER_DEADLINE_MONOTONIC",
    ):
        monkeypatch.delenv(name, raising=False)
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    journal = manager.activation_journal.create(_journal_value(paths))
    candidate = paths.app_dir / "releases" / CANDIDATE_COMMIT
    predecessor = paths.app_dir / "releases" / PREDECESSOR_COMMIT
    report = _report()
    monkeypatch.setattr(
        "bootstrap.upgrade.wait_for_settled_doctor", lambda **_kwargs: report
    )
    manager._post_switch_doctor(candidate)
    failures = manager.activation_journal.directory / "failures"
    candidate_artifact = failures / f"{journal['transaction_id']}-candidate_doctor.json"
    candidate_bytes = candidate_artifact.read_bytes()
    manager._begin_recovery_settlement()
    report = _report()
    report["checks"][2]["evidence"]["reason"] = "deadline_exhausted"
    manager._post_switch_doctor(predecessor)
    recovery_artifact = failures / f"{journal['transaction_id']}-recovery_doctor.json"
    recovery_bytes = recovery_artifact.read_bytes()
    recovery_document = json.loads(recovery_bytes)
    assert recovery_document["transaction_id"] == journal["transaction_id"]
    assert recovery_document["selected_commit"] == PREDECESSOR_COMMIT
    assert recovery_document["stage"] == "recovery_doctor"
    assert recovery_document["failure"]["bounded"][0]["reason"] == "deadline_exhausted"
    report = _report()
    report["checks"][2]["evidence"]["reason"] = "source_assurance_failed"
    manager._post_switch_doctor(predecessor)

    assert manager._failure_audit_available is True
    assert candidate_artifact.read_bytes() == candidate_bytes
    assert recovery_artifact.read_bytes() == recovery_bytes
    assert sorted(path.name for path in failures.iterdir()) == sorted(
        [candidate_artifact.name, recovery_artifact.name]
    )
    for artifact in (candidate_artifact, recovery_artifact):
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
        assert PRIVATE not in artifact.read_text("utf-8")
