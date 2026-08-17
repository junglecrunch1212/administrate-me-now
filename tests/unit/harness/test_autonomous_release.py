from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from scripts.harness import autonomous_release
from scripts.harness import core as harness_core
from scripts.harness.autonomous_release import (
    browser_required,
    complete_release_manifest,
    evaluate_and_record_admission,
    evaluate_release,
    hosted_macos_required,
    load_release_manifest,
    manual_release_paths,
    next_g002_queue,
    prepare_bounded_repair_policy,
    prepare_observation_release_policy,
    prepare_release_manifest,
    queue_g002_candidate,
    record_g002_operator_attempt,
    validate_g002_queue,
)
from scripts.harness.core import HarnessError


HEAD = "2" * 40
BASE = "1" * 40
MERGE = "3" * 40


def _workflow(root: Path, name: str) -> dict[str, object]:
    return yaml.load(
        (root / ".github" / "workflows" / name).read_text("utf-8"),
        Loader=yaml.BaseLoader,
    )


def _manifest(*, lane: str = "observation") -> dict[str, object]:
    targets = [] if lane == "observation" else ["adapter:calendar"]
    unhealthy = [] if lane == "observation" else ["adapter:calendar", "pipeline:noise"]
    failed_checks = (
        [] if lane == "observation" else ["adapter_instances", "source_assurance"]
    )
    target_checks = [] if lane == "observation" else ["adapter_instances"]
    runtime = "healthy" if lane == "observation" else "degraded"
    return {
        "schema_version": 1,
        "lane": lane,
        "candidate": {
            "base_sha": BASE,
            "head_sha": HEAD,
            "changed_paths": ["packs/adapters/calendaring/google-calendar/adapter.py"],
        },
        "github": {
            "repository": "example/administrateme",
            "pull_request": 123,
        },
        "harness": {
            "run_directory": "/tmp/administrateme-harness-fixture",
            "run_id": "fixture-autonomous-release",
            "binding_diff_sha256": "a" * 64,
            "hosted_review_result": "hosted-review/result.json",
            "hosted_review_receipt": "hosted-review/receipt.json",
        },
        "impacts": {
            "provider_writes_enabled": False,
            "runtime_or_dependency_migration": False,
            "provider_enrollment": False,
            "credentials_keychain_or_permissions": False,
            "privacy_or_authorization_change": False,
            "destructive_data_change": False,
            "observation_mode_change": False,
            "release_control_change": False,
            "member_visible_behavior": False,
        },
        "proof": {
            "exact_head_review": "APPROVE",
            "deterministic": "PASS",
            "production_journey": "PASS",
            "browser": "NOT_APPLICABLE",
            "breadth": "PASS",
            "release_candidate": "PASS",
            "hosted_consistency": "PASS",
            "hosted_macos": "PASS",
            "base_contained": True,
            "clean_mergeable": True,
            "actionable_threads": 0,
            "review_completed_at": "2026-08-16T12:00:00Z",
            "breadth_started_at": "2026-08-16T12:01:00Z",
            "attested_updater": True,
            "backup_capable": True,
        },
        "baseline": {
            "runtime": runtime,
            "observation_active": True,
            "required_unhealthy": unhealthy,
            "failed_doctor_checks": failed_checks,
            "doctor_available": True,
            "provider_claims": 0,
        },
        "target_degradations": targets,
        "target_doctor_checks": target_checks,
    }


def test_healthy_observation_candidate_is_admitted_without_per_sha_owner_prompt() -> (
    None
):
    assert evaluate_release(_manifest()) == {
        "status": "ADMIT",
        "lane": "observation",
        "reasons": [],
        "target_degradations": [],
    }


def test_bounded_repair_can_improve_one_target_while_other_degradation_remains() -> (
    None
):
    manifest = _manifest(lane="bounded_repair")
    manifest["settlement"] = {
        "active_head": MERGE,
        "merge_commit": MERGE,
        "observation_active": True,
        "required_unhealthy": ["pipeline:noise"],
        "failed_doctor_checks": ["source_assurance"],
        "doctor_available": True,
        "privacy_isolation": "PASS",
        "browser": "NOT_APPLICABLE",
        "backup_verified": True,
        "rollback_ready": True,
        "provider_effects_delta": 0,
    }

    assert evaluate_release(manifest) == {
        "status": "COMPLETE",
        "lane": "bounded_repair",
        "reasons": [],
        "remaining_degradations": ["pipeline:noise"],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "review-after-breadth",
        "provider-write",
        "observation-off",
        "provider-claim",
        "manual-path",
        "missing-target",
        "missing-target-doctor",
        "healthy-lane-degraded",
        "visible-without-browser",
        "hosted-macos-missing",
    ],
)
def test_autonomous_admission_fails_closed(mutation: str) -> None:
    manifest = _manifest(
        lane="bounded_repair" if mutation == "missing-target" else "observation"
    )
    if mutation == "review-after-breadth":
        manifest["proof"]["review_completed_at"] = "2026-08-16T12:02:00Z"
    elif mutation == "provider-write":
        manifest["impacts"]["provider_writes_enabled"] = True
    elif mutation == "observation-off":
        manifest["baseline"]["observation_active"] = False
    elif mutation == "provider-claim":
        manifest["baseline"]["provider_claims"] = 1
    elif mutation == "manual-path":
        manifest["candidate"]["changed_paths"] = ["bootstrap/upgrade.py"]
    elif mutation == "missing-target":
        manifest["target_degradations"] = ["adapter:missing"]
    elif mutation == "missing-target-doctor":
        manifest["lane"] = "bounded_repair"
        manifest["baseline"]["runtime"] = "degraded"
        manifest["baseline"]["required_unhealthy"] = ["adapter:calendar"]
        manifest["baseline"]["failed_doctor_checks"] = ["adapter_instances"]
        manifest["target_degradations"] = ["adapter:calendar"]
        manifest["target_doctor_checks"] = ["doctor:missing"]
    elif mutation == "healthy-lane-degraded":
        manifest["baseline"]["runtime"] = "degraded"
        manifest["baseline"]["required_unhealthy"] = ["adapter:calendar"]
    elif mutation == "visible-without-browser":
        manifest["impacts"]["member_visible_behavior"] = True
    elif mutation == "hosted-macos-missing":
        manifest["proof"]["hosted_macos"] = "NOT_APPLICABLE"
    else:  # pragma: no cover
        raise AssertionError(mutation)

    assert evaluate_release(manifest)["status"] == "DENY"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-head",
        "target-still-broken",
        "new-degradation",
        "provider-effect",
        "privacy-fail",
        "backup-missing",
        "observation-off",
        "browser-fail-nonapplicable",
        "target-doctor-still-broken",
        "new-doctor-failure",
    ],
)
def test_failed_or_ambiguous_settlement_requires_rollback(mutation: str) -> None:
    manifest = _manifest(lane="bounded_repair")
    settlement = {
        "active_head": MERGE,
        "merge_commit": MERGE,
        "observation_active": True,
        "required_unhealthy": ["pipeline:noise"],
        "failed_doctor_checks": ["source_assurance"],
        "doctor_available": True,
        "privacy_isolation": "PASS",
        "browser": "NOT_APPLICABLE",
        "backup_verified": True,
        "rollback_ready": True,
        "provider_effects_delta": 0,
    }
    if mutation == "wrong-head":
        settlement["active_head"] = "4" * 40
    elif mutation == "target-still-broken":
        settlement["required_unhealthy"] = ["adapter:calendar", "pipeline:noise"]
    elif mutation == "new-degradation":
        settlement["required_unhealthy"] = ["pipeline:noise", "projection:new"]
    elif mutation == "provider-effect":
        settlement["provider_effects_delta"] = 1
    elif mutation == "privacy-fail":
        settlement["privacy_isolation"] = "FAIL"
    elif mutation == "backup-missing":
        settlement["backup_verified"] = False
    elif mutation == "observation-off":
        settlement["observation_active"] = False
    elif mutation == "browser-fail-nonapplicable":
        settlement["browser"] = "FAIL"
    elif mutation == "target-doctor-still-broken":
        settlement["failed_doctor_checks"] = [
            "adapter_instances",
            "source_assurance",
        ]
    elif mutation == "new-doctor-failure":
        settlement["failed_doctor_checks"] = [
            "source_assurance",
            "canonical_synthetic_round_trip",
        ]
    else:  # pragma: no cover
        raise AssertionError(mutation)
    manifest["settlement"] = settlement

    assert evaluate_release(manifest)["status"] == "ROLLBACK"


def test_release_controller_and_governance_paths_always_require_manual_g001() -> None:
    assert manual_release_paths(
        [
            "AGENTS.md",
            ".github/workflows/release.yml",
            "scripts/harness/autonomous_release.py",
            "bootstrap/upgrade.py",
            "poetry.lock",
        ]
    ) == [
        ".github/workflows/release.yml",
        "AGENTS.md",
        "bootstrap/upgrade.py",
        "poetry.lock",
        "scripts/harness/autonomous_release.py",
    ]
    assert manual_release_paths(
        [
            "packs/adapters/AGENTS.md",
            "adminme/events/bus.py",
            "bootstrap/doctor.py",
            "tests/unit/harness/test_core.py",
        ]
    ) == [
        "adminme/events/bus.py",
        "bootstrap/doctor.py",
        "packs/adapters/AGENTS.md",
        "tests/unit/harness/test_core.py",
    ]


def test_browser_requirement_maps_surface_and_capability_health_paths() -> None:
    assert browser_required(["adminme/products/member/assets/member.js"])
    assert browser_required(["bootstrap/doctor.py"])
    assert not browser_required(
        ["packs/adapters/calendaring/google-calendar/adapter.py"]
    )
    assert browser_required([], declared_visible=True)


def test_hosted_macos_requirement_matches_runtime_and_docs_only_paths() -> None:
    assert hosted_macos_required(["adminme/runtime/application.py"])
    assert hosted_macos_required(["packs/adapters/messaging/gmail/adapter.py"])
    assert not hosted_macos_required(["docs/refactor/HANDOFF.md"])


def test_hosted_review_is_last_in_secret_job_and_receipts_use_fresh_job() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = _workflow(root, "administrateme-autonomous-review.yml")
    jobs = workflow["jobs"]
    codex = jobs["codex-review"]
    exact = jobs["exact-head-review"]

    assert codex["permissions"] == {"contents": "read"}
    assert codex["steps"][-1]["name"] == "Run isolated read-only Codex review"
    review_with = codex["steps"][-1]["with"]
    assert review_with["output-schema-file"] == (
        "${{ runner.temp }}/hosted-review-result.schema.json"
    )
    assert review_with["prompt-file"] == (
        "${{ runner.temp }}/g002-adversarial-review.md"
    )
    assert review_with["sandbox"] == "read-only"
    assert review_with["safety-strategy"] == "drop-sudo"
    contract = next(
        step
        for step in codex["steps"]
        if step.get("name") == "Materialize effective base-controlled review contract"
    )
    assert 'git show "$BASE_REF:.github/codex/prompts/' in contract["run"]
    assert 'git show "$BASE_REF:schemas/harness/' in contract["run"]
    assert exact["needs"] == "codex-review"
    assert exact["permissions"] == {
        "contents": "read",
        "issues": "write",
    }
    helper = next(
        step
        for step in exact["steps"]
        if step.get("name") == "Materialize effective base-controlled binding helper"
    )
    assert 'git show "$BASE_REF:scripts/harness/hosted_binding.py"' in helper["run"]
    validator = next(
        step
        for step in exact["steps"]
        if step.get("name")
        == "Validate exact-head conclusion and prepare content-free receipt"
    )
    assert "python -I -" in validator["run"]
    assert "REVIEW_OUTPUT_DIR" in validator["env"]


def test_required_g002_checks_bind_exact_head_reusable_suites() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = _workflow(root, "administrateme-autonomous-review.yml")
    jobs = workflow["jobs"]

    assert jobs["g002-deterministic-consistency"]["needs"] == (
        "deterministic-consistency"
    )
    assert (
        "ADMINME_CODEX_REVIEW_ENABLED" in jobs["g002-deterministic-consistency"]["if"]
    )
    assert jobs["g002-credential-free-rehearsal"]["needs"] == [
        "exact-head-review",
        "credential-free-macos-rehearsal",
    ]
    assert (
        "ADMINME_CODEX_REVIEW_ENABLED" in jobs["g002-credential-free-rehearsal"]["if"]
    )
    for name in (
        "administrateme-consistency.yml",
        "macos-bootstrap.yml",
    ):
        called = _workflow(root, name)
        checkout = next(
            step
            for step in next(iter(called["jobs"].values()))["steps"]
            if "uses" in step and str(step["uses"]).startswith("actions/checkout@")
        )
        assert checkout["with"]["ref"] == (
            "${{ github.event.pull_request.head.sha || github.sha }}"
        )
        assert checkout["with"]["fetch-depth"] == "0"
        assert checkout["with"]["persist-credentials"] == "false"


def test_release_manifest_schema_requires_bound_harness_evidence(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    path = tmp_path / "release.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert load_release_manifest(path)["harness"]["run_id"] == (
        "fixture-autonomous-release"
    )

    del manifest["harness"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(HarnessError, match="invalid autonomous release manifest"):
        load_release_manifest(path)


def test_admission_receipt_is_content_free_exact_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(lane="bounded_repair")
    manifest_path = tmp_path / "release.json"
    receipt_path = tmp_path / "admission.json"
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    monkeypatch.setattr(
        autonomous_release,
        "evaluate_manifest",
        lambda *_args, **_kwargs: {
            "status": "ADMIT",
            "lane": "bounded_repair",
            "reasons": [],
            "target_degradations": ["adapter:calendar"],
        },
    )

    result = evaluate_and_record_admission(manifest_path, receipt_path)
    receipt = json.loads(receipt_path.read_text("utf-8"))
    assert result["status"] == "ADMIT"
    assert receipt["head_sha"] == HEAD
    assert receipt["target_doctor_checks"] == ["adapter_instances"]
    assert "baseline" not in receipt
    with pytest.raises(HarnessError, match="already exists"):
        evaluate_and_record_admission(manifest_path, receipt_path)


def test_prepare_bounded_repair_binds_admission_to_exact_github_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(lane="bounded_repair")
    manifest_path = tmp_path / "release.json"
    receipt_path = tmp_path / "admission.json"
    output_path = tmp_path / "policy.json"
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    monkeypatch.setattr(
        autonomous_release,
        "evaluate_manifest",
        lambda *_args, **_kwargs: {
            "status": "ADMIT",
            "lane": "bounded_repair",
            "reasons": [],
            "target_degradations": ["adapter:calendar"],
        },
    )
    evaluate_and_record_admission(manifest_path, receipt_path)
    merge = "3" * 40
    parents = [BASE, HEAD]

    def fake_gh(arguments: list[str]) -> object:
        endpoint = arguments[0]
        if endpoint.endswith("/pulls/123"):
            return {
                "merged": True,
                "merge_commit_sha": merge,
                "head": {"sha": HEAD},
            }
        if endpoint.endswith(f"/commits/{merge}"):
            return {"parents": [{"sha": parent} for parent in parents]}
        if endpoint.endswith("/commits/main"):
            return {"sha": merge}
        if "/compare/" in endpoint:
            return {"status": "identical"}
        raise AssertionError(arguments)

    monkeypatch.setattr(autonomous_release, "_gh_json", fake_gh)
    policy = prepare_bounded_repair_policy(
        manifest_path,
        receipt_path,
        merge_commit=merge,
        output_path=output_path,
    )
    assert policy["merge_base_parent"] == BASE
    assert policy["reviewed_head"] == HEAD
    assert policy["baseline_required_unhealthy"] == [
        "adapter:calendar",
        "pipeline:noise",
    ]

    parents[0] = "4" * 40
    with pytest.raises(HarnessError, match="not the admitted two-parent merge"):
        prepare_bounded_repair_policy(
            manifest_path,
            receipt_path,
            merge_commit=merge,
            output_path=tmp_path / "wrong-base-policy.json",
        )
    parents[0] = BASE

    changed = json.loads(manifest_path.read_text("utf-8"))
    changed["target_degradations"] = ["pipeline:noise"]
    manifest_path.write_text(json.dumps(changed), "utf-8")
    with pytest.raises(HarnessError, match="receipt contradicts"):
        prepare_bounded_repair_policy(
            manifest_path,
            receipt_path,
            merge_commit=merge,
            output_path=tmp_path / "changed-policy.json",
        )


def test_prepare_observation_policy_binds_healthy_admission_to_exact_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()
    manifest_path = tmp_path / "release.json"
    receipt_path = tmp_path / "admission.json"
    output_path = tmp_path / "observation-policy.json"
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    monkeypatch.setattr(
        autonomous_release,
        "evaluate_manifest",
        lambda *_args, **_kwargs: {
            "status": "ADMIT",
            "lane": "observation",
            "reasons": [],
            "target_degradations": [],
        },
    )
    evaluate_and_record_admission(manifest_path, receipt_path)
    monkeypatch.setattr(
        autonomous_release,
        "_validate_exact_github_merge",
        lambda *_args: [BASE, HEAD],
    )

    policy = prepare_observation_release_policy(
        manifest_path,
        receipt_path,
        merge_commit=MERGE,
        output_path=output_path,
    )
    assert policy["merge_commit"] == MERGE
    assert policy["baseline_required_unhealthy"] == []
    assert policy["baseline_failed_doctor_checks"] == []


def test_queue_handoff_is_exact_content_free_immutable_and_g001_denying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "administrateme-harness-queue"
    run_dir.mkdir()
    queue_root = tmp_path / "g002-queue"
    queue_root.mkdir(mode=0o700)
    monkeypatch.setenv("ADMINME_G002_QUEUE_ROOT", str(queue_root))
    impacts = {
        "provider_writes_enabled": "NO",
        "runtime_or_dependency_migration": "NO",
        "provider_enrollment": "NO",
        "credentials_keychain_or_permissions": "NO",
        "privacy_or_authorization_change": "NO",
        "destructive_data_change": "NO",
        "observation_mode_change": "NO",
        "release_control_change": "NO",
        "member_visible_behavior": "NO",
    }
    (run_dir / "outcome-contract.json").write_text(
        json.dumps(
            {
                "run_id": "fixture-g002-queue",
                "binding": {
                    "base_sha": BASE,
                    "head_sha": HEAD,
                    "diff_sha256": "a" * 64,
                },
                "release_impacts": impacts,
            }
        ),
        "utf-8",
    )
    (run_dir / "verification-report.json").write_text(
        json.dumps(
            {"changed_files": ["packs/adapters/calendaring/google-calendar/adapter.py"]}
        ),
        "utf-8",
    )
    monkeypatch.setattr(
        "scripts.harness.run_artifacts.review_checkpoint",
        lambda *_args: {
            "status": "READY",
            "run_id": "fixture-g002-queue",
            "binding": {
                "base_sha": BASE,
                "head_sha": HEAD,
                "diff_sha256": "a" * 64,
            },
        },
    )

    def fake_git(arguments: list[str], **_kwargs: object) -> str:
        if arguments[1:3] == ["rev-parse", "HEAD"]:
            return HEAD + "\n"
        if arguments[1:3] == ["status", "--porcelain=v1"]:
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(autonomous_release.subprocess, "check_output", fake_git)
    monkeypatch.setattr(
        autonomous_release,
        "_gh_json",
        lambda _arguments: {
            "head": {"sha": HEAD},
            "base": {"ref": "main"},
            "state": "open",
        },
    )
    monkeypatch.setattr(autonomous_release, "validate_run", lambda *_args: None)
    output = queue_root / "entries" / f"123-{HEAD}.json"
    queued = queue_g002_candidate(
        run_dir,
        repository="example/administrateme",
        pull_request=123,
        lane="observation",
        target_degradations=[],
        target_doctor_checks=[],
        output_path=output,
        root=Path(__file__).resolve().parents[3],
    )
    assert queued["head_sha"] == HEAD
    assert queued["impacts"]["release_control_change"] is False
    assert "baseline" not in queued
    assert Path(queued["run_directory"]).is_dir()
    assert Path(queued["run_directory"]).parent == queue_root / "runs"
    assert output.stat().st_mode & 0o777 == 0o400
    assert (
        validate_g002_queue(output, root=Path(__file__).resolve().parents[3])[
            "head_sha"
        ]
        == HEAD
    )
    assert next_g002_queue(Path(__file__).resolve().parents[3])["path"] == str(
        output.resolve()
    )
    waiting = record_g002_operator_attempt(
        output,
        decision="WAITING_REVIEW",
        merge_commit=None,
        deployed_sha=None,
        settlement_manifest_path=None,
        rollback_result="NOT_APPLICABLE",
        remaining_degradations=[],
        remaining_doctor_checks=[],
        reason_codes=["HOSTED_REVIEW_PENDING"],
        elapsed_seconds=2.5,
        root=Path(__file__).resolve().parents[3],
    )
    assert waiting["queue_closed"] is False
    assert next_g002_queue(Path(__file__).resolve().parents[3])["status"] == "QUEUED"
    with pytest.raises(HarnessError, match="contradicts release settlement"):
        record_g002_operator_attempt(
            output,
            decision="COMPLETE",
            merge_commit=MERGE,
            deployed_sha=MERGE,
            settlement_manifest_path=None,
            rollback_result="NOT_APPLICABLE",
            remaining_degradations=[],
            remaining_doctor_checks=[],
            reason_codes=[],
            elapsed_seconds=3,
            root=Path(__file__).resolve().parents[3],
        )
    second_output = queue_root / "entries" / f"124-{HEAD}.json"
    second_run_dir = tmp_path / "administrateme-harness-g002-queue-second"
    shutil.copytree(run_dir, second_run_dir)
    queue_g002_candidate(
        second_run_dir,
        repository="example/administrateme",
        pull_request=124,
        lane="observation",
        target_degradations=[],
        target_doctor_checks=[],
        output_path=second_output,
        root=Path(__file__).resolve().parents[3],
    )
    closed = record_g002_operator_attempt(
        output,
        decision="SUPERSEDED",
        merge_commit=None,
        deployed_sha=None,
        settlement_manifest_path=None,
        rollback_result="NOT_APPLICABLE",
        remaining_degradations=[],
        remaining_doctor_checks=[],
        reason_codes=["HEAD_REPLACED"],
        elapsed_seconds=3,
        root=Path(__file__).resolve().parents[3],
    )
    assert closed["queue_closed"] is True
    assert next_g002_queue(Path(__file__).resolve().parents[3])["path"] == str(
        second_output.resolve()
    )

    settlement = _manifest()
    settlement["github"]["pull_request"] = 124
    settlement["harness"]["run_id"] = "fixture-g002-queue"
    settlement["settlement"] = {
        "active_head": MERGE,
        "merge_commit": MERGE,
        "observation_active": True,
        "required_unhealthy": [],
        "failed_doctor_checks": [],
        "doctor_available": True,
        "privacy_isolation": "PASS",
        "browser": "NOT_APPLICABLE",
        "backup_verified": True,
        "rollback_ready": True,
        "provider_effects_delta": 0,
    }
    settlement_input = tmp_path / "settlement.json"
    settlement_input.write_text(json.dumps(settlement), "utf-8")
    monkeypatch.setattr(
        autonomous_release, "_validate_bound_harness", lambda *_args: None
    )
    monkeypatch.setattr(
        autonomous_release,
        "_validate_exact_github_merge",
        lambda *_args: [BASE, HEAD],
    )
    complete = record_g002_operator_attempt(
        second_output,
        decision="COMPLETE",
        merge_commit=MERGE,
        deployed_sha=MERGE,
        settlement_manifest_path=settlement_input,
        rollback_result="NOT_APPLICABLE",
        remaining_degradations=[],
        remaining_doctor_checks=[],
        reason_codes=[],
        elapsed_seconds=4,
        root=Path(__file__).resolve().parents[3],
    )
    retained_settlement = Path(complete["settlement_manifest_path"])
    assert retained_settlement.parent.parent == queue_root / "settlements"
    assert retained_settlement.stat().st_mode & 0o777 == 0o400
    assert next_g002_queue(Path(__file__).resolve().parents[3]) == {"status": "NO_WORK"}
    retained_settlement.chmod(0o600)
    retained_settlement.write_text("{}", "utf-8")
    with pytest.raises(HarnessError, match="settlement evidence is invalid"):
        next_g002_queue(Path(__file__).resolve().parents[3])
    with pytest.raises(HarnessError, match="already exists"):
        queue_g002_candidate(
            run_dir,
            repository="example/administrateme",
            pull_request=123,
            lane="observation",
            target_degradations=[],
            target_doctor_checks=[],
            output_path=output,
            root=Path(__file__).resolve().parents[3],
        )

    impacts["release_control_change"] = "YES"
    (run_dir / "outcome-contract.json").write_text(
        json.dumps({"release_impacts": impacts}), "utf-8"
    )
    with pytest.raises(HarnessError, match="manual G-001"):
        queue_g002_candidate(
            run_dir,
            repository="example/administrateme",
            pull_request=124,
            lane="observation",
            target_degradations=[],
            target_doctor_checks=[],
            output_path=queue_root / "entries" / "queue-124.json",
            root=Path(__file__).resolve().parents[3],
        )


def test_release_manifest_is_derived_from_queue_harness_and_live_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.harness.checks import CheckSpec

    root = Path(__file__).resolve().parents[3]
    run_dir = tmp_path / "administrateme-harness-manifest"
    run_dir.mkdir()
    impacts = {name: False for name in _manifest()["impacts"]}
    declarations = {name: "NO" for name in impacts}
    binding = {
        "base_sha": BASE,
        "head_sha": HEAD,
        "diff_sha256": "a" * 64,
    }
    changed = ["packs/adapters/calendaring/google-calendar/adapter.py"]
    (run_dir / "outcome-contract.json").write_text(
        json.dumps(
            {
                "run_id": "fixture-g002-manifest",
                "binding": binding,
                "release_impacts": declarations,
            }
        ),
        "utf-8",
    )
    checks = []
    for tier in ("static", "focused", "journey", "breadth", "release"):
        receipt = run_dir / f"{tier}-receipt.json"
        log = run_dir / f"{tier}.log"
        log.write_text("started_at=2026-08-16T12:01:00Z\n", "utf-8")
        receipt.write_text(json.dumps({"log": {"path": log.name}}), "utf-8")
        checks.append(
            {
                "tier": tier,
                "check_id": f"fixture-{tier}",
                "command": "true",
                "status": "PASS",
                "exit_code": 0,
                "receipt": receipt.name,
            }
        )
    (run_dir / "verification-report.json").write_text(
        json.dumps(
            {
                "changed_files": changed,
                "checks": checks,
                "browser": {"status": "NOT_APPLICABLE"},
            }
        ),
        "utf-8",
    )
    (run_dir / "review-report.json").write_text(
        json.dumps({"conclusion": "APPROVE", "unresolved_evidence": []}),
        "utf-8",
    )
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "QUEUED",
                "repository": "example/administrateme",
                "pull_request": 123,
                "base_sha": BASE,
                "head_sha": HEAD,
                "changed_paths": changed,
                "run_directory": str(run_dir),
                "run_id": "fixture-g002-manifest",
                "binding_diff_sha256": "a" * 64,
                "lane": "bounded_repair",
                "target_degradations": ["adapter:calendar"],
                "target_doctor_checks": ["adapter_instances"],
                "impacts": impacts,
                "queued_at": "2026-08-16T12:00:00Z",
            }
        ),
        "utf-8",
    )
    doctor_path = tmp_path / "doctor.json"
    doctor_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "mode": "observation",
                "observed_at": "2026-08-16T12:00:30Z",
                "live_provider_claims": [],
                "checks": [
                    {"name": "disk_space_and_backup_target", "ok": True},
                    {"name": "adapter_instances", "ok": False},
                ],
            }
        ),
        "utf-8",
    )
    diagnostics_path = tmp_path / "diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(
            {
                "observation_active": True,
                "observed_at": "2026-08-16T12:00:30Z",
                "capabilities": {
                    "adapter:calendar": {"state": "degraded"},
                },
            }
        ),
        "utf-8",
    )
    hosted_dir = run_dir / "hosted-review" / "attempts" / "fixture"
    hosted_dir.mkdir(parents=True)
    hosted_result = hosted_dir / "result.json"
    hosted_receipt = hosted_dir / "receipt.json"
    hosted_result.write_text("{}", "utf-8")
    hosted_receipt.write_text("{}", "utf-8")
    monkeypatch.setattr(
        autonomous_release,
        "validate_g002_queue",
        lambda path, _root: json.loads(path.read_text("utf-8")),
    )
    monkeypatch.setattr(autonomous_release, "validate_run", lambda *_args: None)
    monkeypatch.setattr(
        "scripts.harness.checks.tier_checks",
        lambda tier, _paths: [CheckSpec(f"fixture-{tier}", tier, "true")],
    )
    monkeypatch.setattr(
        autonomous_release,
        "_latest_hosted_review",
        lambda *_args: (
            hosted_result,
            hosted_receipt,
            {"review_completed_at": "2026-08-16T12:00:30Z"},
        ),
    )
    monkeypatch.setattr(
        autonomous_release, "hosted_macos_required", lambda _paths: True
    )
    monkeypatch.setattr(autonomous_release, "utc_now", lambda: "2026-08-16T12:01:00Z")
    output = tmp_path / "release-manifest.json"
    result = prepare_release_manifest(
        queue_path,
        doctor_path=doctor_path,
        diagnostics_path=diagnostics_path,
        output_path=output,
        root=root,
    )
    assert result["baseline"] == {
        "runtime": "degraded",
        "observation_active": True,
        "required_unhealthy": ["adapter:calendar"],
        "failed_doctor_checks": ["adapter_instances"],
        "doctor_available": True,
        "provider_claims": 0,
    }
    assert (
        result["proof"]["review_completed_at"] < result["proof"]["breadth_started_at"]
    )
    assert result["harness"]["hosted_review_result"].startswith(
        "hosted-review/attempts/"
    )


def test_complete_release_derives_exact_merge_monotonic_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[3]
    manifest = _manifest(lane="bounded_repair")
    manifest_path = tmp_path / "admission-manifest.json"
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    receipt_path = tmp_path / "admission-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "decision": "ADMIT",
                "lane": "bounded_repair",
                "repository": "example/administrateme",
                "pull_request": 123,
                "base_sha": BASE,
                "head_sha": HEAD,
                "manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
                "run_id": "fixture-autonomous-release",
                "binding_diff_sha256": "a" * 64,
                "target_degradations": ["adapter:calendar"],
                "target_doctor_checks": ["adapter_instances"],
                "admitted_at": "2026-08-16T12:02:00Z",
            }
        ),
        "utf-8",
    )
    backup = tmp_path / "verified.ambackup"
    backup.write_text("encrypted", "utf-8")
    updater_path = tmp_path / "updater.json"
    updater_path.write_text(
        json.dumps(
            {
                "status": "upgraded",
                "commit": MERGE,
                "completed_at": "2026-08-16T12:05:00Z",
                "backup": str(backup),
                "backup_manifest_sha256": "b" * 64,
                "backup_verification": {
                    "status": "verified",
                    "manifest_sha256": "b" * 64,
                    "file_count": 1,
                    "projections_rebuilt": 15,
                    "workbooks_rebuilt": True,
                },
                "upgrade_controller_attested": True,
                "rollback_available": True,
                "g002_bounded_repair": {
                    "baseline": {"required_unhealthy": ["adapter:calendar"]},
                    "settlement": {"required_unhealthy": ["pipeline:noise"]},
                },
            }
        ),
        "utf-8",
    )
    pointer_path = tmp_path / "current-release.json"
    pointer_path.write_text(
        json.dumps({"commit": MERGE, "previous": {"commit": BASE}}), "utf-8"
    )
    doctor_path = tmp_path / "doctor.json"
    doctor_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "mode": "observation",
                "observed_at": "2026-08-16T12:07:00Z",
                "live_provider_claims": [],
                "checks": [
                    {"name": "disk_space_and_backup_target", "ok": True},
                    {"name": "source_assurance", "ok": False},
                ],
            }
        ),
        "utf-8",
    )
    diagnostics_path = tmp_path / "diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(
            {
                "observation_active": True,
                "observed_at": "2026-08-16T12:08:00Z",
                "capabilities": {"pipeline:noise": {"state": "degraded"}},
            }
        ),
        "utf-8",
    )
    privacy_path = tmp_path / "live-privacy.json"
    privacy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "merge_commit": MERGE,
                "status": "PASS",
                "authenticated_self_scope": "PASS",
                "cross_member_denial": "PASS",
                "non_enumerating_denial": "PASS",
                "session_scope": "PASS",
                "replay_isolation": "PASS",
                "observation_active": True,
                "provider_effects_delta": 0,
                "observed_at": "2026-08-16T12:10:00Z",
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(
        autonomous_release, "_validate_bound_harness", lambda *_args: None
    )
    monkeypatch.setattr(
        autonomous_release, "_validate_exact_github_merge", lambda *_args: [BASE, HEAD]
    )
    output = tmp_path / "settlement.json"
    result = complete_release_manifest(
        manifest_path,
        receipt_path,
        merge_commit=MERGE,
        updater_result_path=updater_path,
        doctor_path=doctor_path,
        diagnostics_path=diagnostics_path,
        active_pointer_path=pointer_path,
        live_privacy_receipt_path=privacy_path,
        output_path=output,
        root=root,
    )
    assert result["status"] == "COMPLETE"
    settled = json.loads(output.read_text("utf-8"))
    assert settled["settlement"]["active_head"] == MERGE
    assert settled["settlement"]["required_unhealthy"] == ["pipeline:noise"]

    stale_doctor = json.loads(doctor_path.read_text("utf-8"))
    stale_doctor["observed_at"] = "2026-08-16T12:04:00Z"
    doctor_path.write_text(json.dumps(stale_doctor), "utf-8")
    with pytest.raises(HarnessError, match="evidence is stale"):
        complete_release_manifest(
            manifest_path,
            receipt_path,
            merge_commit=MERGE,
            updater_result_path=updater_path,
            doctor_path=doctor_path,
            diagnostics_path=diagnostics_path,
            active_pointer_path=pointer_path,
            live_privacy_receipt_path=privacy_path,
            output_path=tmp_path / "stale-settlement.json",
            root=root,
        )


def test_github_evidence_is_exact_head_current_main_and_required_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()

    def fake_gh(arguments: list[str]) -> object:
        endpoint = arguments[2] if arguments[0] == "--method" else arguments[0]
        if endpoint.endswith("/pulls/123"):
            return {
                "head": {"sha": HEAD},
                "base": {"ref": "main"},
                "draft": False,
                "mergeable": True,
                "mergeable_state": "clean",
            }
        if endpoint.endswith("/commits/main"):
            return {"sha": BASE}
        if "/compare/" in endpoint:
            return {"status": "ahead"}
        if "/check-runs" in endpoint:
            return {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "exact-head-review",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 2,
                        "name": "g002-deterministic-consistency",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 3,
                        "name": "g002-credential-free-rehearsal",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ]
            }
        if endpoint == "graphql":
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            }
        raise AssertionError(arguments)

    monkeypatch.setattr(autonomous_release, "_gh_json", fake_gh)
    autonomous_release._validate_github_evidence(manifest)

    manifest["candidate"]["head_sha"] = "3" * 40
    with pytest.raises(HarnessError, match="not exact-head"):
        autonomous_release._validate_github_evidence(manifest)


def test_every_imported_hosted_review_attempt_is_validated(tmp_path: Path) -> None:
    attempt = tmp_path / "hosted-review" / "attempts" / "123-1"
    attempt.mkdir(parents=True)
    result = {
        "head_sha": HEAD,
        "base_sha": BASE,
        "review_completed_at": "2026-08-16T12:00:00Z",
        "conclusion": "APPROVE",
        "scope": [
            "correctness",
            "privacy",
            "replay",
            "determinism",
            "surface",
            "failure_paths",
            "completeness",
            "release_boundary",
        ],
        "findings": [],
        "unresolved_evidence": [],
    }
    receipt = {
        "schema_version": 1,
        "repository": "example/administrateme",
        "pull_request": 123,
        "workflow_run_id": 123,
        "workflow_run_attempt": 1,
        "head_sha": HEAD,
        "base_sha": BASE,
        "diff_sha256": "a" * 64,
        "review_started_at": "2026-08-16T11:59:00Z",
        "review_completed_at": "2026-08-16T12:00:00Z",
        "conclusion": "APPROVE",
        "actionable_finding_ids": [],
        "finding_count": 0,
        "unresolved_evidence_count": 0,
        "workflow_run": "https://example.invalid/actions/runs/123",
    }
    (attempt / "result.json").write_text(json.dumps(result), "utf-8")
    (attempt / "receipt.json").write_text(json.dumps(receipt), "utf-8")

    harness_core._validate_retained_hosted_reviews(tmp_path, None)

    receipt["finding_count"] = 1
    (attempt / "receipt.json").write_text(json.dumps(receipt), "utf-8")
    with pytest.raises(HarnessError, match="contradicts its receipt"):
        harness_core._validate_retained_hosted_reviews(tmp_path, None)
