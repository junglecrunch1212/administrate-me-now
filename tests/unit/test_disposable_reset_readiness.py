"""Disposable reset proof never grants production recovery or other health gates."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from adminme.deployment_readiness import evaluate_deployment_readiness


def _inputs() -> dict:
    binding = {
        "release_commit": "a" * 40,
        "configuration_fingerprint": "b" * 64,
        "instance_fingerprint": "c" * 64,
        "host_fingerprint": "d" * 64,
    }
    marker = {
        "contract": "disposable-test-dataset-v1",
        "dataset_id": "e" * 32,
        "reset_manifest_sha256": "f" * 64,
    }
    reset = {
        "contract": "disposable-test-reset-v1",
        "dataset_id": marker["dataset_id"],
        "manifest_sha256": marker["reset_manifest_sha256"],
        "release_commit": binding["release_commit"],
        "original_state_retained": True,
        "original_journal_retained_unchanged": True,
        "diagnostic_archive_authenticated": True,
        "fresh_dataset_restore_rehearsed": True,
        "automatic_state_reversal": True,
        "production_rollback_proven": False,
        "fresh_rehearsal_sha256": "1" * 64,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "required": True,
        "host_topology": {"deployment_binding": binding},
        "deployment_evidence": {
            "deployment_binding": binding,
            "disposable_test_reset": reset,
        },
        "member_roles": {},
        "portal_member_ids": set(),
        "portal_identity_count": 0,
        "provider_bindings": (),
        "service_channel_accounts": (),
        "bridge_enrollments": {},
        "openclaw_enabled": False,
        "openclaw_member_ids": set(),
        "openclaw_topology": {},
        "observation_active": True,
        "source_assurance": {},
        "disposable_test_dataset": marker,
    }


def test_valid_reset_proof_still_requires_all_other_health_gates():
    result = evaluate_deployment_readiness(**_inputs())
    assert result["dimensions"]["backup_rollback"]["state"] == "healthy"
    assert result["production_rollback_proven"] is False
    assert result["ready"] is False
    assert "observation_effects" in result["blocking_dimensions"]
    assert "openclaw" in result["blocking_dimensions"]
    assert "topology" in result["blocking_dimensions"]


@pytest.mark.parametrize(
    "field",
    [
        "original_state_retained",
        "original_journal_retained_unchanged",
        "diagnostic_archive_authenticated",
        "fresh_dataset_restore_rehearsed",
        "automatic_state_reversal",
        "dataset_id",
        "manifest_sha256",
        "release_commit",
        "fresh_rehearsal_sha256",
        "tested_at",
        "contract",
    ],
)
def test_missing_or_mismatched_reset_proof_blocks(field):
    values = _inputs()
    values["deployment_evidence"]["disposable_test_reset"].pop(field)
    assert (
        evaluate_deployment_readiness(**values)["dimensions"]["backup_rollback"][
            "state"
        ]
        == "blocked"
    )


def test_reset_cannot_enable_writes_or_claim_production_recovery():
    values = _inputs()
    values["observation_active"] = False
    assert (
        evaluate_deployment_readiness(**values)["dimensions"]["backup_rollback"][
            "state"
        ]
        == "blocked"
    )
    values = _inputs()
    values["deployment_evidence"]["disposable_test_reset"][
        "production_rollback_proven"
    ] = True
    assert (
        evaluate_deployment_readiness(**values)["dimensions"]["backup_rollback"][
            "state"
        ]
        == "blocked"
    )


def test_production_backup_requirements_are_unchanged():
    values = _inputs()
    values.pop("disposable_test_dataset")
    assert (
        evaluate_deployment_readiness(**values)["dimensions"]["backup_rollback"][
            "state"
        ]
        == "blocked"
    )
    backup = {
        "encrypted_backup_authenticated": True,
        "restore_rehearsed": True,
        "prior_release_retained": True,
        "automatic_rollback_eligible": True,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }
    values["deployment_evidence"]["backup_rollback"] = backup
    assert (
        evaluate_deployment_readiness(**values)["dimensions"]["backup_rollback"][
            "state"
        ]
        == "healthy"
    )
    for key in backup:
        missing = copy.deepcopy(values)
        missing["deployment_evidence"]["backup_rollback"].pop(key)
        assert (
            evaluate_deployment_readiness(**missing)["dimensions"]["backup_rollback"][
                "state"
            ]
            == "blocked"
        )
