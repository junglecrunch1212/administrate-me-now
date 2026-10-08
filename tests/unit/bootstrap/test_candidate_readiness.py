"""Fail-closed coverage for immutable candidate-pending readiness evidence."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from bootstrap import candidate_readiness
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    build_active_attestation_from_pending,
    build_htr10_review,
    build_pending_attestation,
    capture_deployment_snapshot,
    load_pending_attestation_for_activation,
    record_htr10_review,
    record_pending_attestation,
    validate_pending_attestation,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.topology_readiness import load_deployment_evidence


NOW = datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc)


def _private_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(value)
    path.chmod(0o600)


def _git_candidate(root: Path) -> tuple[Path, str]:
    candidate = root / "candidate"
    (candidate / "bootstrap").mkdir(parents=True, mode=0o700)
    (candidate / "bootstrap" / "candidate_readiness.py").write_text(
        "# fixed controller fixture\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(candidate)], check=True)
    subprocess.run(["git", "-C", str(candidate), "add", "--all"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "-c",
            "user.name=Candidate Fixture",
            "-c",
            "user.email=candidate-fixture.invalid",
            "commit",
            "-qm",
            "candidate",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return candidate, commit


def _deployment(root: Path) -> DeploymentPaths:
    paths = DeploymentPaths.from_root(root / "deployment")
    paths.create()
    for directory in (paths.root, paths.app_dir, paths.instance_dir, paths.backup_dir):
        directory.chmod(0o700)
    config: dict[str, bytes] = {
        "deployment.json": json.dumps({"schema_version": 1}).encode(),
        "instance.yaml": yaml.safe_dump(
            {
                "schema_version": 1,
                "tenant_id": "tenant_fixture_pending",
                "observation_mode": True,
            }
        ).encode(),
        "member-mapping.json": json.dumps(
            {
                "members": [
                    {
                        "member_id": "member_fixture_a",
                        "tailscale_identity": "member-a.ts.net",
                    },
                    {
                        "member_id": "member_fixture_b",
                        "tailscale_identity": "member-b.ts.net",
                    },
                ]
            }
        ).encode(),
        "adapters.json": json.dumps({"adapters": []}).encode(),
        "secret-references.json": json.dumps({"references": {}}).encode(),
        "openclaw-activation.json": json.dumps({"configured": True}).encode(),
    }
    for name, payload in config.items():
        _private_write(paths.instance_dir / "config" / name, payload)
    _private_write(
        paths.instance_dir / "openclaw" / "runtime" / "openclaw.json",
        json.dumps({"agents": {}, "bindings": []}).encode(),
    )
    _private_write(
        paths.instance_dir / "bridges" / "member-mapping.json",
        json.dumps({"bridges": []}).encode(),
    )
    predecessor = "a" * 40
    release = paths.app_dir / "releases" / predecessor
    release.mkdir(parents=True, mode=0o700)
    release.chmod(0o700)
    _private_write(
        paths.app_dir / "current-release.json",
        json.dumps({"commit": predecessor, "path": str(release)}).encode(),
    )
    _private_write(
        paths.instance_dir / "config" / "deployment-readiness.json",
        json.dumps({"contract": "deployment-readiness-v1", "sentinel": True}).encode(),
    )
    return paths


def _htr10(paths: DeploymentPaths) -> Path:
    value: dict[str, Any] = {
        "contract": "htr-10-review-v1",
        "schema_version": 1,
        "status": "healthy",
        "observed_at": (NOW - timedelta(hours=1)).isoformat(),
        "provider_effect_window_started_at": (NOW - timedelta(days=2)).isoformat(),
        "event_watermark_sha256": "1" * 64,
        "historical_attribution_sha256": "2" * 64,
        "source_assurance_sha256": "3" * 64,
        "provider_binding_transition_sha256": None,
        "source_event_count": 7,
        "unattributed_event_count": 0,
        "provider_effect_integrity": "healthy",
        "provider_effect_true_count": 0,
        "provider_effect_unknown_count": 0,
        "content_exposed": False,
        "raw_identifiers_exposed": False,
    }
    value["receipt_sha256"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = paths.backup_dir / "htr-10-review.json"
    _private_write(path, json.dumps(value, sort_keys=True).encode())
    return path


def _rehearsal(
    paths: DeploymentPaths,
    *,
    candidate_commit: str,
) -> tuple[Path, Path]:
    artifact = paths.backup_dir / "preliminary.ambackup"
    _private_write(artifact, b"encrypted-backup-fixture")
    snapshot = capture_deployment_snapshot(paths)
    value: dict[str, Any] = {
        "contract": "backup-rehearsal-v1",
        "schema_version": 1,
        "status": "healthy",
        "tested_at": (NOW - timedelta(minutes=30)).isoformat(),
        "candidate": {
            "commit": candidate_commit,
            "clean_checkout_at_start": True,
            "clean_checkout_at_end": True,
            "controller_coordinate_matches": True,
            "execution_immutability": "not_evaluated",
        },
        "predecessor": {
            "commit": snapshot.predecessor_commit,
            "pointer_sha256": snapshot.pointer_sha256,
            "retained": True,
            "path_commit_coordinate_matches": True,
            "immutable_release_valid": "not_evaluated",
            "rollback_compatible": "not_evaluated",
            "automatic_rollback_eligible": "not_evaluated",
            "automatic_rollback_executed": False,
        },
        "deployment_binding": snapshot.public(),
        "artifact": {
            "format": "administrateme-backup-v2",
            "version": 2,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "authenticated": True,
            "exact_manifest_members": True,
        },
        "watermark": {"event_id_sha256": "4" * 64},
        "replay": {},
        "workbooks": {},
        "offline_replay_checks": {
            "status": "healthy",
            "live_checks": "not_evaluated",
            "activation_eligible": False,
            "external_effects": "not_evaluated",
            "provider_components_instantiated": 0,
            "event_sink_mode": "rejecting",
        },
        "isolation": {
            "active_state_replaced": False,
            "active_release_pointer_changed": False,
            "rollback_tree_changed": False,
            "service_manager_constructed": False,
            "plaintext_stage_destroyed": True,
            "staging_destroyed": True,
            "staging_outside_deployment_root": True,
        },
        "source_events_appended": 0,
        "content_emitted_in_receipt": False,
    }
    value["receipt_sha256"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt = paths.backup_dir / "preliminary-rehearsal.json"
    _private_write(receipt, json.dumps(value, sort_keys=True).encode())
    return receipt, artifact


def _arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    DeploymentPaths, Path, str, Path, Path, Path, dict[str, bool], dict[str, str]
]:
    paths = _deployment(tmp_path)
    candidate, commit = _git_candidate(tmp_path)
    monkeypatch.setattr(
        candidate_readiness,
        "__file__",
        str(candidate / "bootstrap" / "candidate_readiness.py"),
    )
    receipt, artifact = _rehearsal(paths, candidate_commit=commit)
    confirmations = {
        "htr_01": True,
        "htr_02": True,
        "htr_03": True,
        "htr_04": True,
        "htr_06": True,
        "htr_07": True,
        "htr_10": True,
    }
    observed = {
        name: (NOW - timedelta(hours=index + 2)).isoformat()
        for index, name in enumerate(
            ("htr_01", "htr_02", "htr_03", "htr_04", "htr_06", "htr_07")
        )
    }
    return (
        paths,
        candidate,
        commit,
        receipt,
        artifact,
        _htr10(paths),
        confirmations,
        observed,
    )


def test_pending_attestation_is_immutable_and_active_readiness_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    active = paths.instance_dir / "config" / "deployment-readiness.json"
    active_before = (active.read_bytes(), active.stat().st_ino)

    result = record_pending_attestation(
        paths,
        candidate=candidate,
        expected_candidate_commit=commit,
        rehearsal_receipt=receipt,
        backup_artifact=artifact,
        htr10_review=htr10,
        confirmations=confirmations,
        observed_at=observed,
        now=NOW,
        attestation_id="5" * 32,
    )

    pending = paths.app_dir / "readiness" / "candidate-pending" / f"{'5' * 32}.json"
    stored = validate_pending_attestation(
        json.loads(pending.read_text("utf-8")), now=NOW
    )
    assert result["activation_authorized"] is False
    assert stored["deployment_binding"]["candidate_commit"] == commit
    assert stored["activation_boundary"]["updater_fresh_backup"] == "not_evaluated"
    assert hashlib.sha256(pending.read_bytes()).hexdigest() == result["artifact_sha256"]
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    assert (active.read_bytes(), active.stat().st_ino) == active_before
    assert load_deployment_evidence(paths)["sentinel"] is True
    with pytest.raises(CandidateReadinessError, match="already exists"):
        record_pending_attestation(
            paths,
            candidate=candidate,
            expected_candidate_commit=commit,
            rehearsal_receipt=receipt,
            backup_artifact=artifact,
            htr10_review=htr10,
            confirmations=confirmations,
            observed_at=observed,
            now=NOW,
            attestation_id="5" * 32,
        )


def test_htr03_member_set_excludes_deferred_household_records(tmp_path: Path) -> None:
    paths = _deployment(tmp_path)
    mapping_path = paths.instance_dir / "config" / "member-mapping.json"
    mapping = json.loads(mapping_path.read_text("utf-8"))
    mapping["members"].append({"member_id": "member_fixture_deferred"})
    _private_write(mapping_path, json.dumps(mapping).encode())

    assert candidate_readiness._member_ids(paths) == [
        "member_fixture_a",
        "member_fixture_b",
    ]


def test_updater_loads_exact_pending_artifact_and_builds_active_v1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    # The active builder reads the clock internally; match the explicit clock
    # used to record and load this fixed synthetic evidence.
    monkeypatch.setattr(candidate_readiness, "datetime", FixtureClock)
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    attestation_id = "8" * 32
    result = record_pending_attestation(
        paths,
        candidate=candidate,
        expected_candidate_commit=commit,
        rehearsal_receipt=receipt,
        backup_artifact=artifact,
        htr10_review=htr10,
        confirmations=confirmations,
        observed_at=observed,
        now=NOW,
        attestation_id=attestation_id,
    )

    pending, pending_sha256, snapshot = load_pending_attestation_for_activation(
        paths,
        attestation_id=attestation_id,
        candidate=candidate,
        candidate_commit=commit,
        now=NOW,
    )
    active = build_active_attestation_from_pending(
        pending,
        pending_sha256=pending_sha256,
        fresh_backup_sha256="9" * 64,
        fresh_rehearsal_receipt_sha256="a" * 64,
        rollback_eligibility_sha256="b" * 64,
        tested_at=(NOW - timedelta(minutes=5)).isoformat(),
    )

    assert pending_sha256 == result["artifact_sha256"]
    assert all(
        pending["deployment_binding"][name] == value
        for name, value in snapshot.public().items()
    )
    assert active["contract"] == "deployment-readiness-v1"
    assert active["deployment_binding"]["release_commit"] == commit
    assert active["backup_rollback"]["automatic_rollback_eligible"] is True
    assert active["activation_evidence"] == {
        "contract": "deployment-activation-evidence-v1",
        "candidate_pending_attestation_id": attestation_id,
        "candidate_pending_attestation_sha256": pending_sha256,
        "fresh_backup_artifact_sha256": "9" * 64,
        "fresh_rehearsal_receipt_sha256": "a" * 64,
        "predecessor_rollback_eligibility_sha256": "b" * 64,
        "crash_recoverable_replacement": True,
        "candidate_live_doctor": "pending_post_switch",
        "post_switch_acceptance": "pending_post_switch",
        "content_exposed": False,
    }


def test_pending_attestation_refuses_receipt_drift_and_expired_human_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    receipt_value = json.loads(receipt.read_text("utf-8"))
    receipt_value["candidate"]["commit"] = "f" * 40
    receipt_value.pop("receipt_sha256")
    receipt_value["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt_value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _private_write(receipt, json.dumps(receipt_value, sort_keys=True).encode())
    with pytest.raises(CandidateReadinessError, match="not eligible"):
        build_pending_attestation(
            paths,
            candidate=candidate,
            expected_candidate_commit=commit,
            rehearsal_receipt=receipt,
            backup_artifact=artifact,
            htr10_review=htr10,
            confirmations=confirmations,
            observed_at=observed,
            now=NOW,
        )

    receipt, artifact = _rehearsal(paths, candidate_commit=commit)
    stale = dict(observed)
    stale["htr_01"] = (NOW - timedelta(days=32)).isoformat()
    with pytest.raises(CandidateReadinessError, match="older than 31 days"):
        build_pending_attestation(
            paths,
            candidate=candidate,
            expected_candidate_commit=commit,
            rehearsal_receipt=receipt,
            backup_artifact=artifact,
            htr10_review=htr10,
            confirmations=confirmations,
            observed_at=stale,
            now=NOW,
        )


def test_pending_attestation_shares_upgrade_operation_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    lock = paths.app_dir / ".upgrade-operation.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(CandidateReadinessError, match="already running"):
            record_pending_attestation(
                paths,
                candidate=candidate,
                expected_candidate_commit=commit,
                rehearsal_receipt=receipt,
                backup_artifact=artifact,
                htr10_review=htr10,
                confirmations=confirmations,
                observed_at=observed,
                now=NOW,
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert not (paths.app_dir / "readiness").exists()


def test_active_loader_rejects_a_pending_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    value, _snapshot = build_pending_attestation(
        paths,
        candidate=candidate,
        expected_candidate_commit=commit,
        rehearsal_receipt=receipt,
        backup_artifact=artifact,
        htr10_review=htr10,
        confirmations=confirmations,
        observed_at=observed,
        now=NOW,
    )
    _private_write(
        paths.instance_dir / "config" / "deployment-readiness.json",
        json.dumps(value).encode(),
    )

    with pytest.raises(RuntimeError, match="contract is invalid"):
        load_deployment_evidence(paths)


def test_htr10_reducer_requires_matching_healthy_zero_effect_metadata(
    tmp_path: Path,
) -> None:
    paths = _deployment(tmp_path)
    window = (NOW - timedelta(days=2)).isoformat()
    _private_write(
        paths.instance_dir / "config" / "deployment-readiness.json",
        json.dumps(
            {
                "contract": "deployment-readiness-v1",
                "provider_effect_window": {"started_at": window},
            }
        ).encode(),
    )
    historical = paths.backup_dir / "historical.json"
    source = paths.backup_dir / "source-assurance.json"
    _private_write(
        historical,
        json.dumps(
            {
                "contract": "historical-attribution-v1",
                "state": "healthy",
                "watermark_event_id": "event-watermark-fixture",
                "source_event_count": 7,
                "attributed_event_count": 7,
                "unattributed_event_count": 0,
                "groups": [],
                "content_exposed": False,
                "raw_identifiers_exposed": False,
                "provider_binding_transition_sha256": None,
            }
        ).encode(),
    )
    zero_counts = {
        "provider_called_true": 0,
        "provider_called_false": 0,
        "provider_called_unknown": 0,
        "suppressed_true": 0,
        "suppressed_false_or_unknown": 0,
        "missing_terminal": 0,
        "duplicate_terminal": 0,
        "approval_correlation_missing": 0,
        "duplicate_request": 0,
        "malformed_request": 0,
        "orphan_terminal": 0,
        "invalid_legacy_reconciliation": 0,
        "duplicate_legacy_reconciliation": 0,
    }
    source_value: dict[str, Any] = {
        "contract": "source-assurance-v1",
        "state": "healthy",
        "watermark_event_id": "event-watermark-fixture",
        "counts": {"missing": 0, "pending": 0, "failed": 0},
        "provider_effects": {
            "state": "healthy",
            "counts": zero_counts,
            "observation_window": {
                "state": "healthy",
                "started_at_configured": True,
                "counts": zero_counts,
                "content_exposed": False,
            },
            "content_exposed": False,
        },
        "content_exposed": False,
        "provider_binding_transition_sha256": None,
    }
    _private_write(source, json.dumps(source_value).encode())

    value = build_htr10_review(
        paths,
        historical_attribution=historical,
        source_assurance=source,
        observed_at=(NOW - timedelta(hours=1)).isoformat(),
        now=NOW,
    )
    result = record_htr10_review(
        paths,
        historical_attribution=historical,
        source_assurance=source,
        observed_at=(NOW - timedelta(hours=1)).isoformat(),
        now=NOW,
        receipt_id="7" * 32,
    )
    receipt = paths.backup_dir / f"htr-10-review-{'7' * 32}.json"
    assert result["provider_effect_integrity"] == "healthy"
    assert value["event_watermark_sha256"] != "event-watermark-fixture"
    assert "event-watermark-fixture" not in receipt.read_text("utf-8")
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600

    source_value["provider_effects"]["observation_window"]["counts"] = {
        **zero_counts,
        "provider_called_true": 1,
    }
    _private_write(source, json.dumps(source_value).encode())
    with pytest.raises(CandidateReadinessError, match="incomplete, unhealthy"):
        build_htr10_review(
            paths,
            historical_attribution=historical,
            source_assurance=source,
            observed_at=(NOW - timedelta(hours=1)).isoformat(),
            now=NOW,
        )


def test_pending_schema_and_private_inputs_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    value, _snapshot = build_pending_attestation(
        paths,
        candidate=candidate,
        expected_candidate_commit=commit,
        rehearsal_receipt=receipt,
        backup_artifact=artifact,
        htr10_review=htr10,
        confirmations=confirmations,
        observed_at=observed,
        now=NOW,
    )
    with pytest.raises(CandidateReadinessError, match="schema is invalid"):
        validate_pending_attestation({**value, "unexpected": True}, now=NOW)

    htr10.chmod(0o640)
    with pytest.raises(CandidateReadinessError, match="unsafe private evidence"):
        build_pending_attestation(
            paths,
            candidate=candidate,
            expected_candidate_commit=commit,
            rehearsal_receipt=receipt,
            backup_artifact=artifact,
            htr10_review=htr10,
            confirmations=confirmations,
            observed_at=observed,
            now=NOW,
        )


def test_pending_directory_symlink_is_rejected_without_outside_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (paths.app_dir / "readiness").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CandidateReadinessError, match="private directory is unsafe"):
        record_pending_attestation(
            paths,
            candidate=candidate,
            expected_candidate_commit=commit,
            rehearsal_receipt=receipt,
            backup_artifact=artifact,
            htr10_review=htr10,
            confirmations=confirmations,
            observed_at=observed,
            now=NOW,
        )

    assert list(outside.iterdir()) == []


def test_post_publication_drift_leaves_only_an_unselected_immutable_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, candidate, commit, receipt, artifact, htr10, confirmations, observed = (
        _arguments(tmp_path, monkeypatch)
    )
    active = paths.instance_dir / "config" / "deployment-readiness.json"
    active_before = (active.read_bytes(), active.stat().st_ino)
    original = candidate_readiness.build_pending_attestation
    calls = 0

    def drift_on_revalidation(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        if calls == 2:
            config = paths.instance_dir / "config" / "adapters.json"
            config.write_text(json.dumps({"adapters": [], "drift": True}), "utf-8")
            config.chmod(0o600)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        candidate_readiness, "build_pending_attestation", drift_on_revalidation
    )
    with pytest.raises(CandidateReadinessError, match="receipt is not eligible"):
        record_pending_attestation(
            paths,
            candidate=candidate,
            expected_candidate_commit=commit,
            rehearsal_receipt=receipt,
            backup_artifact=artifact,
            htr10_review=htr10,
            confirmations=confirmations,
            observed_at=observed,
            now=NOW,
            attestation_id="6" * 32,
        )

    residue = paths.app_dir / "readiness" / "candidate-pending" / f"{'6' * 32}.json"
    assert residue.is_file()
    assert stat.S_IMODE(residue.stat().st_mode) == 0o600
    assert (active.read_bytes(), active.stat().st_ino) == active_before
    assert not (paths.app_dir / "readiness" / "candidate-pending.json").exists()
