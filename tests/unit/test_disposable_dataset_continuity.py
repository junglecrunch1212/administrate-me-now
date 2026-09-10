"""Later ordinary proofs never rehabilitate the abandoned reset predecessor."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from adminme.deployment_readiness import evaluate_deployment_readiness
from bootstrap.disposable_test_reset import (
    OfflineTestReset,
    TestResetError as ResetError,
    _read_admissions,
)
from tests.unit.bootstrap.test_disposable_test_reset import (
    reset_fixture as reset_fixture,
)
from tests.unit.test_disposable_reset_readiness import _inputs


def _ordinary_inputs():
    values = _inputs()
    values["deployment_evidence"].pop("disposable_test_reset")
    values["deployment_evidence"]["backup_rollback"] = {
        "encrypted_backup_authenticated": True,
        "restore_rehearsed": True,
        "prior_release_retained": True,
        "automatic_rollback_eligible": True,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }
    values["deployment_evidence"]["activation_evidence"] = {
        "contract": "deployment-activation-evidence-v1",
        "crash_recoverable_replacement": True,
        "candidate_live_doctor": "pending_post_switch",
        "candidate_pending_attestation_sha256": "1" * 64,
        "fresh_backup_artifact_sha256": "2" * 64,
        "fresh_rehearsal_receipt_sha256": "3" * 64,
        "predecessor_rollback_eligibility_sha256": "4" * 64,
    }
    return values


def test_bounded_dataset_accepts_ordinary_proof_for_its_current_release():
    values = _ordinary_inputs()
    result = evaluate_deployment_readiness(**values)
    backup = result["dimensions"]["backup_rollback"]
    assert backup["state"] == "healthy"
    assert backup["evidence"]["predecessor_scope"] == "accepted_bounded_dataset"
    assert backup["evidence"]["abandoned_original_health_proven"] is False
    assert result["production_rollback_proven"] is False
    assert result["ready"] is False  # Other required dimensions are still absent.
    for field in values["deployment_evidence"]["backup_rollback"]:
        missing = copy.deepcopy(values)
        missing["deployment_evidence"]["backup_rollback"].pop(field)
        assert (
            evaluate_deployment_readiness(**missing)["dimensions"]["backup_rollback"][
                "state"
            ]
            == "blocked"
        )


@pytest.mark.parametrize("invalid_reset_present", [False, True])
def test_old_backup_booleans_cannot_hide_missing_initial_reset_proof(
    invalid_reset_present,
):
    values = _ordinary_inputs()
    values["deployment_evidence"].pop("activation_evidence")
    if invalid_reset_present:
        invalid_reset = _inputs()["deployment_evidence"]["disposable_test_reset"]
        invalid_reset.pop("diagnostic_archive_authenticated")
        values["deployment_evidence"]["disposable_test_reset"] = invalid_reset
    assert (
        evaluate_deployment_readiness(**values)["dimensions"]["backup_rollback"][
            "state"
        ]
        == "blocked"
    )
    for field in _ordinary_inputs()["deployment_evidence"]["activation_evidence"]:
        missing = _ordinary_inputs()
        missing["deployment_evidence"]["activation_evidence"].pop(field)
        assert (
            evaluate_deployment_readiness(**missing)["dimensions"]["backup_rollback"][
                "state"
            ]
            == "blocked"
        )


@pytest.mark.parametrize(
    "field",
    [
        "release_commit",
        "instance_fingerprint",
        "host_fingerprint",
        "configuration_fingerprint",
    ],
)
def test_ordinary_proof_cannot_cross_bounded_activation_binding(field):
    values = copy.deepcopy(_ordinary_inputs())
    values["deployment_evidence"]["deployment_binding"] = dict(
        values["deployment_evidence"]["deployment_binding"]
    )
    values["deployment_evidence"]["deployment_binding"][field] = "other"
    assert (
        evaluate_deployment_readiness(**values)["dimensions"]["backup_rollback"][
            "state"
        ]
        == "blocked"
    )


def test_reset_intent_initializes_required_private_lifetime_receipt(reset_fixture):
    paths, manifest = reset_fixture
    before = paths.instance_dir.stat().st_ino
    operation = OfflineTestReset(paths, manifest)
    record = operation.begin()
    assert record["phase"] == "initialized"
    assert paths.instance_dir.stat().st_ino == before
    admission_path = operation.archive / "lifetime-admissions.json"
    assert manifest["lifetime_admissions"]["path"] == str(admission_path)
    assert admission_path.stat().st_mode & 0o777 == 0o600
    value = _read_admissions(operation.archive, manifest)
    assert value["message_sha256"] == []
    assert value["dataset_id"] == manifest["reset_id"]
    encoded = admission_path.read_bytes()
    wrong_owner = {**value, "owner_sha256": "0" * 64}
    admission_path.write_text(json.dumps(wrong_owner))
    with pytest.raises(ResetError, match="admission receipt is invalid"):
        _read_admissions(operation.archive, manifest)
    admission_path.write_bytes(encoded)
    admission_path.unlink()
    with pytest.raises(ResetError, match="admission receipt is unavailable"):
        _read_admissions(operation.archive, manifest)
    assert not admission_path.exists()
