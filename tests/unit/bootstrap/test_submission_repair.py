"""Only the exact, private forwarded-source failure can enter maintenance."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bootstrap import cos_channel_activation as operation
from bootstrap.activation_transaction import ActivationJournalError, validate_journal
from bootstrap.contracts import PROJECTION_NAMES, atomic_write
from bootstrap.doctor import _required_reactive_pipeline_ids
from bootstrap.htr10_export import _INTEGRITY_ZERO_COUNTS
from bootstrap.submission_repair import (
    REPAIR_DOCTOR_CHECKS,
    REPAIR_READINESS_DIMENSIONS,
    SubmissionRepairError,
    SubmissionRepairManifest,
    load_submission_repair_manifest,
    repair_binding,
    valid_repair_binding,
    validate_submission_repair_audit,
    validate_submission_repair_diagnostics,
    validate_submission_repair_progress,
)
from tests.unit.bootstrap.test_activation_transaction import _journal_value, _paths
from tests.unit.bootstrap.test_cos_channel_activation import _live_controller

ROOT = Path(__file__).resolve().parents[3]


def manifest() -> SubmissionRepairManifest:
    return SubmissionRepairManifest.model_validate(
        {
            "contract": "forwarded-submission-repair-v1",
            "predecessor_commit": "a" * 40,
            "candidate_commit": "b" * 40,
            "operator_principal_id": "operator:fixture-repair",
            "owner_member_id": "member-fixture-forward-a",
            "owner_authorization_sha256": "e" * 64,
            "minimum_source_facts": 1,
            "targets": [
                {
                    "source_event_id": "fixture-source",
                    "failed_assessment_event_id": "fixture-feature",
                    "member_id": "member-fixture-forward-a",
                    "expected_failure_reason": "language_seam_OpenClawTimeout",
                }
            ],
        }
    )


def assurance() -> dict:
    return {
        "contract": "source-assurance-v1",
        "state": "degraded",
        "content_exposed": False,
        "watermark_event_id": "fixture-watermark",
        "counts": {
            "source_facts": 1,
            "obligations": 1,
            "accounted": 1,
            "pending": 0,
            "missing": 0,
            "failed": 1,
        },
        "failed": [
            {
                "event_id": "fixture-source",
                "event_type": "triage.intent.submitted",
                "reason": "member_text_requires_human_interpretation",
            }
        ],
        "provider_effects": {
            "state": "healthy",
            "zero_effect_state": "healthy",
            "counts": {
                **dict.fromkeys(_INTEGRITY_ZERO_COUNTS, 0),
                "provider_called_true": 0,
            },
        },
        "freshness": {
            "state": "fresh",
            "revision_current": True,
            "settled_revision": True,
            "pending_recheck_required": False,
            "refresh_in_progress": False,
            "refresh_error_type": None,
        },
    }


def diagnostics(*, observation: bool = True) -> dict:
    consumers = {f"projection:{name}": "projection" for name in PROJECTION_NAMES}
    consumers.update(
        {name: "reactive_pipeline" for name in _required_reactive_pipeline_ids(ROOT)}
    )
    return {
        "ready": False,
        "service_ready": True,
        "safe_reads": True,
        "event_ingest_ready": True,
        "observation_active": observation,
        "startup_error": None,
        "write_admission": "blocked",
        "capabilities": {
            **{
                name: {
                    "kind": kind,
                    "state": "healthy",
                    "details": {
                        "lag_count": 0,
                        "checkpoint_event_id": "fixture-watermark",
                        "version": "1",
                        "consecutive_failures": 0,
                        "poison_event_id": None,
                    },
                }
                for name, kind in consumers.items()
            },
            "policy:source-assurance": {"state": "degraded", "details": assurance()},
        },
        "deployment_readiness": {
            "contract": "deployment-readiness-v1",
            "state": "blocked",
            "ready": False,
            "blocking_dimensions": ["observation_effects"],
            "dimensions": {
                **{
                    name: {"state": "healthy", "required": True}
                    for name in REPAIR_READINESS_DIMENSIONS
                },
                "observation_effects": {
                    "state": "blocked",
                    "required": True,
                    "evidence": {"provider_called_true": 0},
                },
                "bridges": {"state": "not_applicable", "required": False},
                "identity": {"state": "healthy", "required": True},
            },
        },
    }


def doctor() -> dict:
    failed = {
        "source_assurance_terminal_dispositions",
        "deployment_topology_and_recovery_readiness",
    }
    return {
        "status": "degraded",
        "checks": [
            {"name": name, "ok": name not in failed}
            for name in sorted(
                REPAIR_DOCTOR_CHECKS | {"canonical_synthetic_round_trip"}
            )
        ],
    }


def test_private_manifest_binding_is_content_free_and_exact(tmp_path, monkeypatch):
    path = tmp_path / "repair.json"
    atomic_write(path, manifest().model_dump_json().encode())
    monkeypatch.setattr(
        "bootstrap.submission_repair.reviewed_candidate_commit", lambda _path: "b" * 40
    )
    monkeypatch.setattr(
        "bootstrap.submission_repair._run_git",
        lambda *_a: type("Git", (), {"stdout": b"b" * 40})(),
    )
    loaded = load_submission_repair_manifest(
        path, candidate=tmp_path, active_commit="a" * 40
    )
    binding = repair_binding(loaded)
    assert valid_repair_binding(binding)
    assert "fixture-source" not in json.dumps(binding)
    assert "member-fixture" not in json.dumps(binding)
    with pytest.raises(SubmissionRepairError, match="predecessor"):
        load_submission_repair_manifest(path, active_commit="c" * 40)
    path.chmod(0o644)
    with pytest.raises(SubmissionRepairError, match="owner-only"):
        load_submission_repair_manifest(path)
    path.chmod(0o600)
    path.write_text('{"contract":"wrong",' + manifest().model_dump_json()[1:])
    with pytest.raises(SubmissionRepairError, match="duplicate"):
        load_submission_repair_manifest(path)


@pytest.mark.parametrize(
    "defect",
    [
        "extra_source",
        "missing_source",
        "source_floor",
        "pending",
        "effect",
        "unknown",
        "boolean_count",
        "orphan",
        "new_dimension",
        "required_dimension",
        "missing_consumer",
        "missing_pipeline",
        "lag",
        "unknown_worker",
        "stale",
        "doctor",
    ],
)
def test_exact_baseline_refuses_any_unrelated_or_unsettled_gap(defect):
    value = diagnostics()
    source = value["capabilities"]["policy:source-assurance"]["details"]
    full = doctor()
    if defect == "extra_source":
        source["failed"].append({**source["failed"][0], "event_id": "unrelated"})
    elif defect == "missing_source":
        source["failed"] = []
    elif defect == "source_floor":
        source["counts"]["source_facts"] = 0
    elif defect == "pending":
        source["counts"]["pending"] = 1
    elif defect in {"effect", "unknown", "boolean_count", "orphan"}:
        key = {
            "effect": "provider_called_true",
            "unknown": "provider_called_unknown",
            "boolean_count": "provider_called_true",
            "orphan": "orphan_terminal",
        }[defect]
        source["provider_effects"]["counts"][key] = (
            False if defect == "boolean_count" else 1
        )
    elif defect == "new_dimension":
        value["deployment_readiness"]["blocking_dimensions"].append("openclaw")
    elif defect == "required_dimension":
        value["deployment_readiness"]["dimensions"]["bridges"]["required"] = True
    elif defect == "missing_consumer":
        value["capabilities"].pop("projection:context_fabric")
    elif defect == "missing_pipeline":
        value["capabilities"].pop("pipeline:pipeline:noise_filtering")
    elif defect == "lag":
        value["capabilities"]["projection:context_fabric"]["details"]["lag_count"] = 1
    elif defect == "unknown_worker":
        value["capabilities"]["projection:context_fabric"]["state"] = "failed"
    elif defect == "stale":
        source["freshness"]["revision_current"] = False
    else:
        full["checks"].append({"name": "privacy", "ok": False})
    with pytest.raises(SubmissionRepairError):
        validate_submission_repair_diagnostics(
            manifest(), value, observation_active=True, doctor=full
        )


def test_exact_baseline_is_contained_degraded_never_healthy():
    original = diagnostics()
    before = copy.deepcopy(original)
    assert (
        validate_submission_repair_diagnostics(
            manifest(), original, observation_active=True, doctor=doctor()
        )["status"]
        == "contained_degraded"
    )
    validate_submission_repair_audit(manifest(), assurance())
    assert original == before


def test_repair_progress_requires_actual_runtime_pipeline_capability_ids():
    value = diagnostics()
    pipeline_id = "pipeline:pipeline:noise_filtering"
    assert pipeline_id in value["capabilities"]
    assert f"pipeline:{pipeline_id}" not in value["capabilities"]
    validate_submission_repair_progress(value)
    value["capabilities"].pop(pipeline_id)
    with pytest.raises(SubmissionRepairError, match="consumer inventory"):
        validate_submission_repair_progress(value)


def test_failed_summary_retains_deterministic_preface_without_expanding_failures():
    value = assurance()
    value["failed"][0]["reason"] = "member_kind_or_text_rule_matched"
    validate_submission_repair_audit(manifest(), value)
    value["failed"][0]["reason"] = "unrecognized_failure"
    with pytest.raises(SubmissionRepairError, match="failure summary"):
        validate_submission_repair_audit(manifest(), value)


@pytest.mark.parametrize("fail_backup", [False, True])
def test_containment_journals_exact_exception_and_keeps_hold_on_failure(
    tmp_path, monkeypatch, fail_backup
):
    controller, _activate, pause = _live_controller(tmp_path, monkeypatch)
    declaration = manifest().model_copy(
        update={"operator_principal_id": pause.operator_principal_id}
    )
    path = tmp_path / "repair.json"
    atomic_write(path, declaration.model_dump_json().encode())
    monkeypatch.setattr(
        "bootstrap.submission_repair.reviewed_candidate_commit", lambda _path: "b" * 40
    )
    original = controller.services.runtime_diagnostics
    monkeypatch.setattr(
        controller.services,
        "runtime_diagnostics",
        lambda: diagnostics(observation=original()["observation_active"]),
    )
    calls = []
    monkeypatch.setattr(
        operation,
        "offline_submission_repair",
        lambda *_a, **kw: calls.append(kw) or {"status": "repair_pending"},
    )
    monkeypatch.setattr(operation, "run_release_doctor", lambda *_a, **_kw: doctor())
    if fail_backup:
        monkeypatch.setattr(
            controller.backups,
            "rehearse",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                RuntimeError("synthetic rehearsal failure")
            ),
        )
        with pytest.raises(RuntimeError, match="rehearsal"):
            controller.enter_observation(pause, submission_repair_manifest=path)
        journal = json.loads(controller.journal_path.read_bytes())
        assert journal["submission_repair"] == repair_binding(declaration)
        assert journal["phase"] == "quiesced"
    else:
        result = controller.enter_observation(pause, submission_repair_manifest=path)
        assert result["status"] == "contained_degraded"
        assert not controller.journal_path.exists()
        history = list((controller.directory / "history").glob("*.json"))
        operation_history = [
            json.loads(p.read_bytes())
            for p in history
            if json.loads(p.read_bytes()).get("operation") == "enter_observation"
        ]
        assert operation_history[0][
            "submission_repair_manifest"
        ] == declaration.model_dump(mode="json")
        assert all(call.get("append_requests", False) is False for call in calls)


def test_activation_journal_requires_exact_manifest_binding(tmp_path):
    paths = _paths(tmp_path)
    value = _journal_value(paths)
    declaration = manifest().model_copy(
        update={
            "candidate_commit": str(value["candidate_commit"]),
            "predecessor_commit": str(value["predecessor_commit"]),
        }
    )
    value["submission_repair_manifest"] = declaration.model_dump(mode="json")
    value["submission_repair"] = repair_binding(declaration)
    journal = (
        __import__("bootstrap.activation_transaction", fromlist=["ActivationJournal"])
        .ActivationJournal(paths)
        .create(value)
    )
    assert validate_journal(journal)["submission_repair"] == repair_binding(declaration)
    changed = copy.deepcopy(journal)
    changed["submission_repair"]["targets_sha256"] = "f" * 64
    with pytest.raises(ActivationJournalError, match="binding"):
        validate_journal(changed)


def test_missing_readiness_and_doctor_inventory_is_not_a_repair_baseline():
    value = diagnostics()
    value["deployment_readiness"]["dimensions"].pop("provider_bindings")
    with pytest.raises(SubmissionRepairError, match="inventory"):
        validate_submission_repair_diagnostics(
            manifest(), value, observation_active=True
        )
    with pytest.raises(SubmissionRepairError, match="inventory"):
        validate_submission_repair_diagnostics(
            manifest(),
            diagnostics(),
            observation_active=True,
            doctor={
                "checks": [
                    {"name": "source_assurance_terminal_dispositions", "ok": False}
                ]
            },
        )


def test_settlement_reserves_full_doctor_and_both_diagnostics(monkeypatch):
    from bootstrap import submission_repair

    monkeypatch.setattr(submission_repair, "deployment_clock", lambda: 10.0)
    with pytest.raises(SubmissionRepairError, match="deadline"):
        submission_repair.wait_for_submission_repair(
            manifest(),
            diagnostics=lambda: pytest.fail("insufficient budget reached diagnostics"),
            doctor=lambda: pytest.fail("insufficient budget reached Doctor"),
            deadline=189.0,
        )


def test_journal_rejects_orphan_recovery_request_ids(tmp_path):
    from bootstrap.activation_transaction import ActivationJournal

    paths = _paths(tmp_path)
    value = _journal_value(paths)
    value["submission_repair_request_event_ids"] = ["fixture-command"]
    with pytest.raises(ActivationJournalError, match="manifest"):
        ActivationJournal(paths).create(value)


@pytest.mark.parametrize("defect", ["dead", "worker", "poison", "dimension", "refresh"])
def test_pending_repair_rejects_new_structural_failure(defect):
    from bootstrap.submission_repair import validate_submission_repair_progress

    value = diagnostics()
    validate_submission_repair_progress(value)
    if defect == "dead":
        value["service_ready"] = False
    elif defect == "worker":
        value["capabilities"]["projection:context_fabric"]["state"] = "failed"
    elif defect == "poison":
        value["capabilities"]["projection:context_fabric"]["details"][
            "poison_event_id"
        ] = "fixture-poison"
    elif defect == "dimension":
        value["deployment_readiness"]["blocking_dimensions"].append("backup_rollback")
    else:
        value["capabilities"]["policy:source-assurance"]["details"]["freshness"][
            "refresh_error_type"
        ] = "RuntimeError"
    with pytest.raises(SubmissionRepairError):
        validate_submission_repair_progress(value)


def test_pending_repair_budget_tracks_actual_pack_and_bounded_concurrency():
    from bootstrap.submission_repair import submission_repair_wait_budget

    assert submission_repair_wait_budget(manifest(), ROOT) == 182
    batch = manifest().model_copy(
        update={
            "targets": [
                manifest()
                .targets[0]
                .model_copy(
                    update={
                        "source_event_id": f"source-{index}",
                        "failed_assessment_event_id": f"failed-{index}",
                    }
                )
                for index in range(8)
            ]
        }
    )
    assert submission_repair_wait_budget(batch, ROOT) == 548
