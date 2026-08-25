"""End-to-end local artifact flow with no repository or household run state."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.harness.core import (
    HarnessError,
    _validate_run_semantics,
    binding,
    candidate_diff_paths,
    executable_inventory,
    load_json,
    repository_state,
    validate_run,
    write_json,
)
from scripts.harness import run_artifacts
from scripts.harness.run_artifacts import (
    assert_final_gate_unlocked,
    create_run,
    finalize_owner_handoff,
    record_browser,
    record_checks,
    record_review,
    refresh_bindings,
    sync_owner_handoff,
)


def test_run_artifacts_create_validate_and_refresh_in_system_temp(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-artifact-flow"

    result = create_run(
        root,
        requested_outcome="Preserve member navigation while extracting its module seam.",
        task_paths=["scripts/harness", "tests/unit/harness"],
        protected_paths=[".codex/", "docs/map/"],
        run_dir=run_dir,
    )

    assert result == run_dir
    validate_run(run_dir, root)
    refresh_bindings(root, run_dir, ["scripts/harness", "tests/unit/harness"])
    validate_run(run_dir, root)

    evidence = json.loads((run_dir / "evidence-inventory.json").read_text("utf-8"))
    outcome = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))
    assert {case["state"] for case in outcome["acceptance_cases"]} == {
        "happy",
        "empty",
        "loading",
        "stale",
        "degraded",
        "denied",
        "retry",
        "terminal_failure",
    }
    assert evidence["repository_state"] == repository_state(
        root, [".codex/", "docs/map/"]
    )
    assert not str(run_dir.resolve()).startswith(str(root.resolve()))


def test_preservation_task_paths_must_match_bound_verification_scope(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-divergent-task-paths"
    create_run(
        root,
        requested_outcome="Reject a narrowed preservation scope.",
        task_paths=["scripts/harness", "AGENTS.md"],
        protected_paths=[],
        run_dir=run_dir,
    )
    manifest_path = run_dir / "preservation-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["rollback"]["task_owned_paths"].remove("AGENTS.md")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(HarnessError, match="task-owned paths differ"):
        validate_run(run_dir, root)


def test_validation_rejects_self_consistent_manifest_missing_candidate_path(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-incomplete-candidate-scope"
    complete = candidate_diff_paths(root)
    assert len(complete) > 1
    create_run(
        root,
        requested_outcome="Reject a self-consistent but incomplete candidate manifest.",
        task_paths=complete,
        protected_paths=[],
        run_dir=run_dir,
    )
    narrowed = complete[1:]
    narrowed_binding = binding(root, narrowed)
    for name in (
        "outcome-contract.json",
        "evidence-inventory.json",
        "preservation-manifest.json",
        "verification-report.json",
        "review-report.json",
        "owner-handoff.metadata.json",
    ):
        path = run_dir / name
        artifact = load_json(path)
        artifact["binding"] = narrowed_binding
        if name == "verification-report.json":
            artifact["changed_files"] = narrowed
        elif name == "preservation-manifest.json":
            artifact["rollback"]["task_owned_paths"] = narrowed
        write_json(path, artifact)
    sync_owner_handoff(run_dir)

    with pytest.raises(HarnessError, match="omits candidate diff paths"):
        validate_run(run_dir, root)


def test_validation_invalidates_in_place_protected_file_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness fixture"],
        cwd=root,
        check=True,
    )
    (root / "candidate.txt").write_text("candidate\n", "utf-8")
    subprocess.run(["git", "add", "candidate.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", head],
        cwd=root,
        check=True,
    )
    protected = root / "protected.txt"
    protected.write_text("private fixture one\n", "utf-8")
    run_dir = tmp_path / "administrateme-harness-protected-state"
    monkeypatch.setattr(
        run_artifacts,
        "executable_inventory",
        lambda _root: executable_inventory(),
    )
    monkeypatch.setattr(run_artifacts, "validate_run", lambda *_args: None)
    create_run(
        root,
        requested_outcome="Invalidate protected content changes.",
        task_paths=[],
        protected_paths=["protected.txt"],
        run_dir=run_dir,
    )
    _validate_run_semantics(run_dir, root)

    protected.write_text("private fixture two\n", "utf-8")

    with pytest.raises(HarnessError, match="repository state is stale"):
        _validate_run_semantics(run_dir, root)

    verification_path = run_dir / "verification-report.json"
    verification = load_json(verification_path)
    verification["checks"] = [{"stale": "proof"}]
    write_json(verification_path, verification)
    review_path = run_dir / "review-report.json"
    review = load_json(review_path)
    review["unresolved_evidence"] = []
    review["conclusion"] = "APPROVE"
    write_json(review_path, review)
    owner_path = run_dir / "owner-handoff.metadata.json"
    owner = load_json(owner_path)
    owner["proof"]["deterministic"] = "PASS"
    write_json(owner_path, owner)
    sync_owner_handoff(run_dir)

    refresh_bindings(root, run_dir, [])

    verification = load_json(verification_path)
    review = load_json(review_path)
    owner = load_json(owner_path)
    assert verification["checks"] == []
    assert verification["browser"]["status"] == "UNDETERMINED"
    assert review["conclusion"] == "UNDETERMINED"
    assert owner["proof"]["deterministic"] == "UNDETERMINED"
    _validate_run_semantics(run_dir, root)


def test_semantically_false_green_run_is_rejected(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-false-green"
    create_run(
        root,
        requested_outcome="Reject contradictory proof.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    report_path = run_dir / "verification-report.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["status"] = "PASS"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(HarnessError, match="required reviewed check"):
        validate_run(run_dir, root)


def test_owner_prose_cannot_claim_a_release_against_metadata(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-owner-prose"
    create_run(
        root,
        requested_outcome="Bind owner prose to proof.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    handoff = run_dir / "owner-handoff.md"
    handoff.write_text(handoff.read_text("utf-8") + "\nRELEASED.\n", "utf-8")

    with pytest.raises(HarnessError, match="contradicts or is stale"):
        validate_run(run_dir, root)


def test_owner_metadata_cannot_overclaim_incomplete_proof(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-owner-metadata"
    create_run(
        root,
        requested_outcome="Reject metadata-driven false green prose.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    owner_path = run_dir / "owner-handoff.metadata.json"
    owner = json.loads(owner_path.read_text("utf-8"))
    owner["proof"] = {
        "deterministic": "PASS",
        "production_journey": "PASS",
        "browser": "PASS",
        "independent_review": "APPROVE",
    }
    owner_path.write_text(json.dumps(owner), "utf-8")
    sync_owner_handoff(run_dir)

    with pytest.raises(HarnessError, match="overclaims"):
        validate_run(run_dir, root)


def test_owner_metadata_cannot_claim_release_for_incomplete_local_run(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-owner-release"
    create_run(
        root,
        requested_outcome="Keep local evidence candidate-only.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    owner_path = run_dir / "owner-handoff.metadata.json"
    owner = json.loads(owner_path.read_text("utf-8"))
    owner["release_status"] = "RELEASED"
    owner_path.write_text(json.dumps(owner), "utf-8")
    sync_owner_handoff(run_dir)

    with pytest.raises(HarnessError, match="cannot claim"):
        validate_run(run_dir, root)


def test_record_review_rejects_another_binding_without_overwriting(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-review-import"
    create_run(
        root,
        requested_outcome="Import only exact-bound independent review evidence.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    canonical_path = run_dir / "review-report.json"
    before = canonical_path.read_bytes()
    review = json.loads(canonical_path.read_text("utf-8"))
    review["binding"]["diff_sha256"] = "f" * 64
    review["reviewer"] = "independent-reviewer"
    external = tmp_path / "review.json"
    external.write_text(json.dumps(review), "utf-8")

    with pytest.raises(HarnessError, match="different candidate"):
        record_review(run_dir, external, root)

    assert canonical_path.read_bytes() == before


def test_record_review_accepts_exact_binding_and_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-review-reconcile"
    create_run(
        root,
        requested_outcome="Record exact-bound independent review evidence.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    review = json.loads((run_dir / "review-report.json").read_text("utf-8"))
    review.update(
        {
            "reviewer": "independent-reviewer",
            "unresolved_evidence": [],
            "conclusion": "APPROVE",
        }
    )
    external = tmp_path / "approved-review.json"
    external.write_text(json.dumps(review), "utf-8")

    monkeypatch.setattr(run_artifacts, "assert_review_ready", lambda *_: None)
    record_review(run_dir, external, root)

    canonical = json.loads((run_dir / "review-report.json").read_text("utf-8"))
    verification = json.loads((run_dir / "verification-report.json").read_text("utf-8"))
    assert canonical["conclusion"] == "APPROVE"
    assert verification["status"] == "UNDETERMINED"
    assert not any("Independent review" in gap for gap in verification["unresolved"])


def test_record_review_refuses_before_early_gates(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-review-too-early"
    create_run(
        root,
        requested_outcome="Require early proof before independent review.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    review = load_json(run_dir / "review-report.json")
    review.update(
        {
            "reviewer": "independent-reviewer",
            "unresolved_evidence": [],
            "conclusion": "APPROVE",
        }
    )
    external = tmp_path / "premature-review.json"
    write_json(external, review)

    with pytest.raises(HarnessError, match="independent review is premature"):
        record_review(run_dir, external, root)


def test_expensive_final_gates_are_locked_before_review(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-final-gates-locked"
    create_run(
        root,
        requested_outcome="Lock expensive final gates before review.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )

    with pytest.raises(HarnessError, match="final gates remain locked"):
        assert_final_gate_unlocked(run_dir, root)


def test_record_review_rejects_stale_repository_state_without_overwriting(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-review-stale-state"
    create_run(
        root,
        requested_outcome="Reject review import after repository state changes.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    canonical_path = run_dir / "review-report.json"
    verification_path = run_dir / "verification-report.json"
    review = load_json(canonical_path)
    review.update(
        {
            "reviewer": "independent-reviewer",
            "unresolved_evidence": [],
            "conclusion": "APPROVE",
        }
    )
    external = tmp_path / "stale-state-review.json"
    write_json(external, review)
    evidence_path = run_dir / "evidence-inventory.json"
    evidence = load_json(evidence_path)
    evidence["repository_state"]["tracked_changes"] = ["stale-state.txt"]
    write_json(evidence_path, evidence)
    previous_review = canonical_path.read_bytes()
    previous_verification = verification_path.read_bytes()

    with pytest.raises(HarnessError, match="repository state is stale"):
        record_review(run_dir, external, root)

    assert canonical_path.read_bytes() == previous_review
    assert verification_path.read_bytes() == previous_verification


def test_record_review_restores_artifacts_after_post_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-review-post-write-failure"
    create_run(
        root,
        requested_outcome="Restore canonical review proof after failed reconciliation.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    canonical_path = run_dir / "review-report.json"
    verification_path = run_dir / "verification-report.json"
    review = load_json(canonical_path)
    review.update(
        {
            "reviewer": "independent-reviewer",
            "unresolved_evidence": [],
            "conclusion": "APPROVE",
        }
    )
    external = tmp_path / "post-write-review.json"
    write_json(external, review)
    previous_review = canonical_path.read_bytes()
    previous_verification = verification_path.read_bytes()

    def fail_after_write(reconciled_run_dir: Path) -> None:
        assert (
            load_json(reconciled_run_dir / "review-report.json")["conclusion"]
            == "APPROVE"
        )
        write_json(
            reconciled_run_dir / "verification-report.json",
            {"mutated": "during reconciliation"},
        )
        raise HarnessError("forced post-write reconciliation failure")

    monkeypatch.setattr(run_artifacts, "_reconcile_verification", fail_after_write)
    monkeypatch.setattr(run_artifacts, "assert_review_ready", lambda *_: None)

    with pytest.raises(HarnessError, match="forced post-write"):
        record_review(run_dir, external, root)

    assert canonical_path.read_bytes() == previous_review
    assert verification_path.read_bytes() == previous_verification


def test_finalize_owner_refuses_incomplete_evidence(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-owner-finalize"
    create_run(
        root,
        requested_outcome="Generate owner prose only from complete proof.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )

    with pytest.raises(HarnessError, match="requires current-bound passing"):
        finalize_owner_handoff(
            run_dir,
            what_now_works="Candidate cleanup is fully proved.",
            task="Open Today and move through the existing destinations.",
            expected_result="Existing member behavior remains visible.",
            root=root,
        )


def test_finalize_owner_rejects_stale_state_without_overwriting(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-owner-stale-state"
    create_run(
        root,
        requested_outcome="Refuse owner proof after repository state changes.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    evidence_path = run_dir / "evidence-inventory.json"
    evidence = load_json(evidence_path)
    evidence["repository_state"]["tracked_changes"] = ["stale-state.txt"]
    write_json(evidence_path, evidence)
    owner_path = run_dir / "owner-handoff.metadata.json"
    handoff_path = run_dir / "owner-handoff.md"
    verification_path = run_dir / "verification-report.json"
    previous_owner = owner_path.read_bytes()
    previous_handoff = handoff_path.read_bytes()
    previous_verification = verification_path.read_bytes()

    with pytest.raises(HarnessError, match="repository state is stale"):
        finalize_owner_handoff(
            run_dir,
            what_now_works="This must not be written.",
            task="Open Today.",
            expected_result="No stale proof is promoted.",
            root=root,
        )

    assert owner_path.read_bytes() == previous_owner
    assert handoff_path.read_bytes() == previous_handoff
    assert verification_path.read_bytes() == previous_verification


def test_finalize_owner_restores_artifacts_after_post_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-owner-post-write-failure"
    create_run(
        root,
        requested_outcome="Restore canonical owner proof after failed validation.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    owner_path = run_dir / "owner-handoff.metadata.json"
    handoff_path = run_dir / "owner-handoff.md"
    verification_path = run_dir / "verification-report.json"
    review_path = run_dir / "review-report.json"
    verification = load_json(verification_path)
    verification["status"] = "PASS"
    verification["browser"]["status"] = "PASS"
    verification["browser"]["desktop"] = [{"path": "desktop.png", "sha256": "a" * 64}]
    verification["browser"]["narrow"] = [{"path": "narrow.png", "sha256": "b" * 64}]
    verification["checks"] = [
        {"tier": tier, "check_id": check_id, "status": "PASS"}
        for tier, check_id in (
            ("static", "static-fixture"),
            ("focused", "focused-fixture"),
            ("journey", "navigation-production-composition"),
            ("breadth", "breadth-fixture"),
        )
    ]
    write_json(verification_path, verification)
    review = load_json(review_path)
    review.update(
        {
            "reviewer": "independent-reviewer",
            "unresolved_evidence": [],
            "conclusion": "APPROVE",
        }
    )
    write_json(review_path, review)
    previous_owner = owner_path.read_bytes()
    previous_handoff = handoff_path.read_bytes()
    previous_verification = verification_path.read_bytes()
    previous_review = review_path.read_bytes()

    def fail_after_write(validated_run_dir: Path, _root: Path | None = None) -> None:
        assert (
            load_json(validated_run_dir / "owner-handoff.metadata.json")[
                "release_status"
            ]
            == "CANDIDATE_ONLY"
        )
        assert "Candidate cleanup is proved." in (
            validated_run_dir / "owner-handoff.md"
        ).read_text("utf-8")
        raise HarnessError("forced post-write owner validation failure")

    monkeypatch.setattr(run_artifacts, "validate_run", fail_after_write)

    with pytest.raises(HarnessError, match="forced post-write"):
        finalize_owner_handoff(
            run_dir,
            what_now_works="Candidate cleanup is proved.",
            task="Open Today.",
            expected_result="Existing member behavior remains visible.",
            root=root,
        )

    assert owner_path.read_bytes() == previous_owner
    assert handoff_path.read_bytes() == previous_handoff
    assert verification_path.read_bytes() == previous_verification
    assert review_path.read_bytes() == previous_review


def test_fabricated_green_checks_without_bound_receipts_are_rejected(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-fabricated-green"
    create_run(
        root,
        requested_outcome="Require real bound receipts.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    report_path = run_dir / "verification-report.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["checks"] = [
        {
            "check_id": f"fabricated-{tier}",
            "tier": tier,
            "command": "not executed",
            "status": "PASS",
            "exit_code": 0,
            "duration_seconds": 0,
            "evidence_class": "local_candidate",
            "receipt": f"missing-{tier}.json",
        }
        for tier in ("static", "focused", "journey", "breadth")
    ]
    report["status"] = "PASS"
    report["unresolved"] = []
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(HarnessError, match="missing check receipt"):
        validate_run(run_dir, root)


def test_self_consistent_arbitrary_commands_cannot_replace_reviewed_checks(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-arbitrary-command"
    create_run(
        root,
        requested_outcome="Require the reviewed check matrix.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    binding_value = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))[
        "binding"
    ]
    receipts: list[dict[str, object]] = []
    for tier in ("static", "focused", "journey", "breadth"):
        check = {
            "check_id": (
                "navigation-production-composition"
                if tier == "journey"
                else f"forged-{tier}"
            ),
            "tier": tier,
            "command": "true",
            "status": "PASS",
            "exit_code": 0,
            "duration_seconds": 0,
            "evidence_class": "fixture",
        }
        log = run_dir / f"forged-{tier}.log"
        log.write_text("true returned zero\n", "utf-8")
        receipt = run_dir / f"forged-{tier}.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "binding": binding_value,
                    "check": check,
                    "log": {
                        "path": log.name,
                        "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                    },
                }
            ),
            "utf-8",
        )
        receipts.append({**check, "receipt": receipt.name})
    report_path = run_dir / "verification-report.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["checks"] = receipts
    report["status"] = "PASS"
    report["unresolved"] = []
    report_path.write_text(json.dumps(report), "utf-8")

    with pytest.raises(HarnessError, match="required reviewed check"):
        validate_run(run_dir, root)


def test_fabricated_green_receipt_without_hashed_log_is_rejected(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-unlogged-green"
    create_run(
        root,
        requested_outcome="Require a hashed execution log.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    binding_value = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))[
        "binding"
    ]
    check = {
        "check_id": "fabricated-pass",
        "tier": "static",
        "command": "not executed",
        "status": "PASS",
        "exit_code": 0,
        "duration_seconds": 0,
        "evidence_class": "fixture",
    }
    receipt_path = run_dir / "fabricated-pass.json"
    receipt_path.write_text(
        json.dumps({"schema_version": 1, "binding": binding_value, "check": check}),
        "utf-8",
    )
    report_path = run_dir / "verification-report.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["checks"] = [{**check, "receipt": receipt_path.name}]
    report_path.write_text(json.dumps(report), "utf-8")

    with pytest.raises(HarnessError, match="lacks a hashed log"):
        validate_run(run_dir, root)


def test_browser_receipt_without_toolchain_attestation_is_rejected(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-browser-toolchain"
    create_run(
        root,
        requested_outcome="Bind browser proof to the supported toolchain.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    binding_value = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))[
        "binding"
    ]
    check = {
        "check_id": "gate7-browser-forged",
        "tier": "journey",
        "command": "npm --prefix tools/console-reference-harness run test:gate7",
        "status": "PASS",
        "exit_code": 0,
        "duration_seconds": 0,
        "evidence_class": "fixture",
    }
    log = run_dir / "browser.log"
    log.write_text("command ran without a toolchain attestation\n", "utf-8")
    receipt_path = run_dir / "browser.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "binding": binding_value,
                "check": check,
                "log": {
                    "path": log.name,
                    "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                },
            }
        ),
        "utf-8",
    )
    report_path = run_dir / "verification-report.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["checks"] = [{**check, "receipt": receipt_path.name}]
    report_path.write_text(json.dumps(report), "utf-8")

    with pytest.raises(HarnessError, match="toolchain evidence"):
        validate_run(run_dir, root)


def test_openclaw_receipt_without_toolchain_attestation_is_rejected(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-openclaw-toolchain"
    create_run(
        root,
        requested_outcome="Bind OpenClaw proof to the supported toolchain.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    binding_value = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))[
        "binding"
    ]
    check = {
        "check_id": "openclaw-forged",
        "tier": "breadth",
        "command": "npm --prefix openclaw/administrateme test",
        "status": "PASS",
        "exit_code": 0,
        "duration_seconds": 0,
        "evidence_class": "fixture",
    }
    log = run_dir / "openclaw.log"
    log.write_text("command ran without a toolchain attestation\n", "utf-8")
    receipt_path = run_dir / "openclaw.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "binding": binding_value,
                "check": check,
                "log": {
                    "path": log.name,
                    "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                },
            }
        ),
        "utf-8",
    )
    report_path = run_dir / "verification-report.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["checks"] = [{**check, "receipt": receipt_path.name}]
    report_path.write_text(json.dumps(report), "utf-8")

    with pytest.raises(HarnessError, match="toolchain evidence"):
        validate_run(run_dir, root)


def test_changed_binding_invalidates_every_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-binding-change"
    create_run(
        root,
        requested_outcome="Never rebind stale evidence.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    outcome = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))
    changed_binding = dict(outcome["binding"])
    changed_binding["diff_sha256"] = "f" * 64
    monkeypatch.setattr(
        "scripts.harness.run_artifacts.binding",
        lambda *_args, **_kwargs: changed_binding,
    )

    refresh_bindings(root, run_dir, ["scripts/harness"])

    verification = json.loads((run_dir / "verification-report.json").read_text("utf-8"))
    review = json.loads((run_dir / "review-report.json").read_text("utf-8"))
    owner = json.loads((run_dir / "owner-handoff.metadata.json").read_text("utf-8"))
    assert verification["checks"] == []
    assert verification["browser"]["status"] == "UNDETERMINED"
    assert review["conclusion"] == "UNDETERMINED"
    assert owner["proof"]["deterministic"] == "UNDETERMINED"


def test_rerun_receipts_replace_binding_invalidation_with_current_gaps(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-reconciled"
    create_run(
        root,
        requested_outcome="Reconcile current receipts.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    binding_value = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))[
        "binding"
    ]
    check = {
        "check_id": "current-static",
        "tier": "static",
        "command": "fixture",
        "status": "PASS",
        "exit_code": 0,
        "duration_seconds": 0,
        "evidence_class": "fixture",
    }
    receipt = run_dir / "check-receipts/current-static.json"
    receipt.parent.mkdir()
    log = run_dir / "current-static.log"
    log.write_text("current static proof\n", "utf-8")
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "binding": binding_value,
                "check": check,
                "log": {
                    "path": log.name,
                    "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                },
            }
        ),
        "utf-8",
    )

    record_checks(run_dir, [{**check, "receipt": "check-receipts/current-static.json"}])

    report = json.loads((run_dir / "verification-report.json").read_text("utf-8"))
    assert not any("binding changed" in item for item in report["unresolved"])
    assert report["unresolved"][0] == (
        "Required check tiers not yet passing: breadth, focused, journey."
    )


def test_current_browser_and_production_journey_reconcile_journey_metric(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "administrateme-harness-journey-metric"
    create_run(
        root,
        requested_outcome="Reconcile the journey metric.",
        task_paths=["scripts/harness"],
        protected_paths=[],
        run_dir=run_dir,
    )
    binding_value = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))[
        "binding"
    ]

    def receipt(check_id: str) -> dict[str, object]:
        check: dict[str, object] = {
            "check_id": check_id,
            "tier": "journey",
            "command": "fixture",
            "status": "PASS",
            "exit_code": 0,
            "duration_seconds": 0,
            "evidence_class": "fixture",
        }
        log = run_dir / f"{check_id}.log"
        log.write_text(f"{check_id} passed\n", "utf-8")
        artifact = run_dir / f"{check_id}.json"
        artifact.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "binding": binding_value,
                    "check": check,
                    "log": {
                        "path": log.name,
                        "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                    },
                }
            ),
            "utf-8",
        )
        return {**check, "receipt": artifact.name}

    journey = receipt("navigation-production-composition")
    record_checks(run_dir, [journey])
    record_browser(
        run_dir,
        {
            "receipt": receipt("gate7-browser-fixture"),
            "browser": {
                "status": "PASS",
                "output_directory": str(run_dir / "browser"),
                "desktop": [],
                "narrow": [],
                "console_errors": [],
                "page_errors": [],
                "request_failures": [],
            },
        },
    )

    report = json.loads((run_dir / "verification-report.json").read_text("utf-8"))
    assert report["metrics"]["passed_journeys"] == 1
