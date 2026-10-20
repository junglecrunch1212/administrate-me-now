"""Exact repair authority cannot become an ordinary healthy readiness claim."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from bootstrap import candidate_readiness, htr10_export, submission_repair
from bootstrap.contracts import PROJECTION_NAMES
from bootstrap.doctor import _required_reactive_pipeline_ids
from bootstrap.secrets import secret_store
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    build_active_attestation_from_pending,
    build_htr10_review,
    build_pending_attestation,
    load_pending_attestation_for_activation,
    record_pending_attestation,
)
from bootstrap.submission_repair import (
    SubmissionRepairError,
    load_submission_repair_manifest,
    repair_binding,
)
from tests.unit.bootstrap.test_candidate_readiness import (
    NOW,
    _arguments,
    _private_write,
    _retained_window_review,
)
from tests.acceptance.test_forwarded_submission_extraction import _settings
from tests.acceptance.test_forwarded_submission_recovery import (
    failed_forward,
    gateway_boundary,
    repair_manifest,
    source_audit,
)
from tests.acceptance.test_service_owned_cos_sources_direct_intake import (
    _fixture_config,
)
from tests.acceptance.test_triage_vertical_slice import MEMBER


REPO_ROOT = Path(__file__).resolve().parents[3]


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(candidate_readiness, "datetime", FixtureClock)
    paths, candidate, commit, rehearsal, artifact, _old, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    # The real source validator still requires a clean checkout matching its
    # protected-main coordinate; only this disposable Git repository is used.
    subprocess.run(
        ["git", "-C", str(candidate), "update-ref", "refs/remotes/origin/main", commit],
        check=True,
    )
    review, windows = _retained_window_review(paths, closed=False)
    manifest_value = {
        "contract": "forwarded-submission-repair-v1",
        "predecessor_commit": "a" * 40,
        "candidate_commit": commit,
        "operator_principal_id": "operator:fixture-maintenance",
        "owner_member_id": "member_fixture_a",
        "owner_authorization_sha256": "b" * 64,
        "minimum_source_facts": 7,
        "targets": [
            {
                "source_event_id": "fixture-forwarded-source",
                "failed_assessment_event_id": "fixture-failed-assessment",
                "member_id": "member_fixture_a",
                "expected_failure_reason": "language_seam_OpenClawTimeout",
            }
        ],
    }
    manifest_path = paths.backup_dir / "submission-repair.json"
    _private_write(manifest_path, json.dumps(manifest_value).encode())
    manifest = load_submission_repair_manifest(
        manifest_path, candidate=candidate, active_commit="a" * 40
    )
    binding = repair_binding(manifest)
    historical_path = paths.backup_dir / "historical.json"
    historical = json.loads(historical_path.read_bytes())
    historical["submission_repair"] = binding
    _private_write(historical_path, json.dumps(historical).encode())
    source_path = paths.backup_dir / "source-assurance.json"
    source = json.loads(source_path.read_bytes())
    source["state"] = "degraded"
    source["counts"].update(source_facts=7, obligations=7, accounted=7, failed=1)
    source["failed"] = [
        {
            "event_id": "fixture-forwarded-source",
            "event_type": "triage.intent.submitted",
            "reason": "member_text_requires_human_interpretation",
        }
    ]
    source["provider_effects"]["zero_effect_state"] = "healthy"
    source["submission_repair"] = binding
    # Match the producer's nested proof shape, including its content-free
    # global audit. The ordinary source audit separately retains all windows.
    source["submission_repair_audit"] = {
        "status": "repair_pending",
        "binding": binding,
        "watermark_event_id": source["watermark_event_id"],
        "counts": copy.deepcopy(source["counts"]),
        "provider_effects": {
            key: copy.deepcopy(source["provider_effects"][key])
            for key in ("state", "zero_effect_state", "counts", "content_exposed")
        },
        "content_exposed": False,
    }
    _private_write(source_path, json.dumps(source).encode())
    return {
        "paths": paths,
        "candidate": candidate,
        "commit": commit,
        "manifest": manifest_path,
        "manifest_value": manifest_value,
        "binding": binding,
        "historical": historical_path,
        "source": source_path,
        "review": review,
        "windows": windows,
        "pending_args": {
            "candidate": candidate,
            "expected_candidate_commit": commit,
            "rehearsal_receipt": rehearsal,
            "backup_artifact": artifact,
            "htr10_review": review,
            "confirmations": confirmations,
            "observed_at": observed,
            "now": NOW,
        },
    }


def _review(fixture: dict[str, Any], *, explicit: bool = True) -> dict[str, Any]:
    return build_htr10_review(
        fixture["paths"],
        historical_attribution=fixture["historical"],
        source_assurance=fixture["source"],
        observed_at=(NOW - timedelta(hours=1)).isoformat(),
        submission_repair_manifest=fixture["manifest"] if explicit else None,
        now=NOW,
    )


def _record_pending(fixture: dict[str, Any]) -> dict[str, Any]:
    _private_write(fixture["review"], json.dumps(_review(fixture)).encode())
    return record_pending_attestation(
        fixture["paths"],
        **fixture["pending_args"],
        submission_repair_manifest=fixture["manifest"],
    )


def test_repair_review_retains_degraded_counts_and_requires_explicit_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    original = fixture["source"].read_bytes()
    review = _review(fixture)
    assert review["status"] == "repair_pending"
    assert review["submission_repair"] == fixture["binding"]
    assert review["source_assurance_sha256"] == hashlib.sha256(original).hexdigest()
    assert review["provider_effect_windows"] == fixture["windows"]
    assert fixture["source"].read_bytes() == original
    assert json.loads(original)["state"] == "degraded"
    assert json.loads(original)["counts"]["failed"] == 1
    assert "fixture-forwarded-source" not in json.dumps(review)
    assert "member_fixture_a" not in json.dumps(review)
    with pytest.raises(CandidateReadinessError, match="explicit private manifest"):
        _review(fixture, explicit=False)
    _private_write(fixture["review"], json.dumps(review).encode())
    with pytest.raises(CandidateReadinessError, match="explicit candidate authority"):
        build_pending_attestation(fixture["paths"], **fixture["pending_args"])


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_manifest",
        "wrong_candidate",
        "extra_failed_source",
        "replaced_failed_source",
        "dropped_source_facts",
        "nonzero_effect",
        "unknown_effect",
        "wrong_proof_binding",
        "wrong_proof_watermark",
        "wrong_proof_counts",
        "wrong_proof_effects",
        "proof_content_exposed",
    ],
)
def test_repair_review_refuses_any_expansion_of_its_exact_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    source = json.loads(fixture["source"].read_bytes())
    if defect in {"wrong_manifest", "wrong_candidate"}:
        manifest = fixture["manifest_value"]
        manifest[
            "owner_authorization_sha256"
            if defect == "wrong_manifest"
            else "candidate_commit"
        ] = "c" * (64 if defect == "wrong_manifest" else 40)
        _private_write(fixture["manifest"], json.dumps(manifest).encode())
    elif defect == "extra_failed_source":
        source["counts"]["failed"] = 2
        source["failed"].append(
            {
                **source["failed"][0],
                "event_id": "fixture-unrelated-failed-source",
            }
        )
    elif defect == "replaced_failed_source":
        source["failed"][0]["event_id"] = "fixture-unrelated-failed-source"
    elif defect == "dropped_source_facts":
        source["counts"]["source_facts"] = 6
    elif defect in {"nonzero_effect", "unknown_effect"}:
        source["provider_effects"]["counts"][
            "provider_called_true"
            if defect == "nonzero_effect"
            else "provider_called_unknown"
        ] = 1
    elif defect == "wrong_proof_binding":
        source["submission_repair_audit"]["binding"]["targets_sha256"] = "d" * 64
    elif defect == "wrong_proof_watermark":
        source["submission_repair_audit"]["watermark_event_id"] = "different-watermark"
    elif defect == "wrong_proof_counts":
        source["submission_repair_audit"]["counts"]["failed"] = 0
    elif defect == "wrong_proof_effects":
        source["submission_repair_audit"]["provider_effects"]["counts"][
            "provider_called_true"
        ] = 1
    else:
        source["submission_repair_audit"]["content_exposed"] = True
    _private_write(fixture["source"], json.dumps(source).encode())
    with pytest.raises((CandidateReadinessError, SubmissionRepairError)):
        _review(fixture)


def test_repair_pending_promotion_preserves_all_windows_and_revalidates_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    result = _record_pending(fixture)
    load_args = {
        "attestation_id": result["attestation_id"],
        "candidate": fixture["candidate"],
        "candidate_commit": fixture["commit"],
        "now": NOW,
    }
    with pytest.raises(CandidateReadinessError, match="exact explicit manifest"):
        load_pending_attestation_for_activation(fixture["paths"], **load_args)
    pending, digest, _snapshot = load_pending_attestation_for_activation(
        fixture["paths"],
        **load_args,
        submission_repair_manifest=fixture["manifest"],
    )
    assert pending["deployment_binding"]["submission_repair"] == fixture["binding"]
    assert pending["pre_switch_state"]["provider_effect_windows"] == fixture["windows"]
    active = build_active_attestation_from_pending(
        pending,
        pending_sha256=digest,
        fresh_backup_sha256="1" * 64,
        fresh_rehearsal_receipt_sha256="2" * 64,
        rollback_eligibility_sha256="3" * 64,
        tested_at=NOW.isoformat(),
    )
    assert {key: active[key] for key in fixture["windows"]} == fixture["windows"]
    assert (
        active["activation_evidence"]["candidate_live_doctor"] == "pending_post_switch"
    )
    assert active["activation_evidence"]["submission_repair"] == fixture["binding"]
    original = fixture["manifest"].read_bytes()
    changed = json.loads(original)
    changed["owner_authorization_sha256"] = "e" * 64
    _private_write(fixture["manifest"], json.dumps(changed).encode())
    with pytest.raises(CandidateReadinessError, match="exact explicit manifest"):
        load_pending_attestation_for_activation(
            fixture["paths"],
            **load_args,
            submission_repair_manifest=fixture["manifest"],
        )
    _private_write(fixture["manifest"], original)
    fixture["manifest"].chmod(0o644)
    with pytest.raises(SubmissionRepairError, match="owner-only regular file"):
        load_pending_attestation_for_activation(
            fixture["paths"],
            **load_args,
            submission_repair_manifest=fixture["manifest"],
        )


@pytest.mark.asyncio
async def test_encrypted_failed_forward_exports_exact_degraded_repair_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    fixture = _fixture(tmp_path, monkeypatch)
    paths = fixture["paths"]
    instance = tmp_path / "forward-runtime"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client, gateway_boundary(client):
        runtime = client.app.state.runtime
        source, failed = failed_forward(client, "export-proof")
        assurance = client.portal.call(source_audit, runtime)
        assert assurance["state"] == "degraded"
        assert assurance["counts"]["failed"] == 1
        assert assurance["counts"]["missing"] == assurance["counts"]["pending"] == 0
    # Copy only the closed encrypted fixture database. No real account or
    # credentials are available; the complete message→cue→failed receipt chain
    # above came through the production application and real skill wrapper.
    (paths.instance_dir / "events").mkdir(mode=0o700)
    shutil.copy2(
        settings.instance.event_log_path, paths.instance_dir / "events/events.db"
    )
    reference = secret_store(paths, test_mode=True).put(
        "event-log", settings.encryption_key
    )
    config = paths.instance_dir / "config"
    adapter_config = copy.deepcopy(settings.adapter_config)
    adapter_config["adapters"] = [
        row for row in _fixture_config()["adapters"] if row["id"] == "cos-gmail-read"
    ]
    for name, value in (
        ("adapters.json", adapter_config),
        (
            "member-mapping.json",
            {"members": [{"member_id": MEMBER, "role": "principal"}]},
        ),
        ("secret-references.json", {"references": {"event_log": reference}}),
    ):
        _private_write(config / name, json.dumps(value).encode())
    manifest_value = repair_manifest(source, failed).model_dump(mode="json")
    manifest_value.update(
        candidate_commit=fixture["commit"],
        minimum_source_facts=assurance["counts"]["source_facts"],
    )
    _private_write(fixture["manifest"], json.dumps(manifest_value).encode())

    # Only the OS diagnostic transport and reviewed checkout coordinate are
    # fixtures. The exporter re-audits encrypted truth, all provider bindings,
    # source identities, repair targets, and observation intervals itself.
    original_identity = submission_repair.reviewed_candidate_commit
    monkeypatch.setattr(
        submission_repair,
        "reviewed_candidate_commit",
        lambda path: (
            fixture["commit"] if path == REPO_ROOT else original_identity(path)
        ),
    )
    assurance["freshness"] = {
        "state": "fresh",
        "revision_current": True,
        "settled_revision": True,
        "pending_recheck_required": False,
        "refresh_in_progress": False,
        "refresh_error_type": None,
    }
    capabilities = {}
    for prefix, kind, names in (
        ("projection:", "projection", PROJECTION_NAMES),
        ("", "reactive_pipeline", _required_reactive_pipeline_ids(REPO_ROOT)),
    ):
        for name in names:
            capabilities[f"{prefix}{name}"] = {
                "kind": kind,
                "state": "healthy",
                "details": {
                    "version": 1,
                    "lag_count": 0,
                    "checkpoint_event_id": assurance["watermark_event_id"],
                    "poison_event_id": None,
                    "consecutive_failures": 0,
                },
            }
    capabilities["policy:source-assurance"] = {
        "state": "degraded",
        "details": assurance,
    }
    diagnostics = {
        "ready": False,
        "service_ready": True,
        "safe_reads": True,
        "event_ingest_ready": True,
        "observation_active": True,
        "write_admission": "blocked",
        "startup_error": None,
        "capabilities": capabilities,
        "deployment_readiness": {
            "contract": "deployment-readiness-v1",
            "blocking_dimensions": ["observation_effects"],
            "dimensions": {
                **{
                    key: {"state": "healthy", "required": True}
                    for key in submission_repair.REPAIR_READINESS_DIMENSIONS
                },
                "observation_effects": {"state": "blocked", "required": True},
            },
        },
    }
    monkeypatch.setattr(
        htr10_export.ServiceManager, "runtime_diagnostics", lambda self: diagnostics
    )
    historical_path = paths.backup_dir / "encrypted-historical.json"
    source_path = paths.backup_dir / "encrypted-source-assurance.json"
    await htr10_export.export_htr10_audits(
        paths,
        repository_root=REPO_ROOT,
        historical_output=historical_path,
        source_output=source_path,
        ownership_reviewed=True,
        submission_repair_manifest=fixture["manifest"],
        test_mode=True,
    )
    historical = json.loads(historical_path.read_bytes())
    exported = json.loads(source_path.read_bytes())
    assert historical["state"] == "healthy"
    assert any(
        row["binding_state"] == "internal_forwarded_member_source_reconciled"
        for row in historical["groups"]
    )
    assert exported["state"] == "degraded"
    assert exported["counts"]["failed"] == 1
    assert exported["provider_effects"]["counts"]["provider_called_true"] == 0
    assert exported["provider_effect_windows"] == fixture["windows"]
    fixture.update(historical=historical_path, source=source_path)
    review = _review(fixture)
    assert review["status"] == "repair_pending"
    assert review["provider_effect_windows"] == fixture["windows"]
    assert "Call the school office" not in source_path.read_text()
    assert "cos-service@example.invalid" not in historical_path.read_text()
