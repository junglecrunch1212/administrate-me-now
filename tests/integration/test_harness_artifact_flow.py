"""End-to-end local artifact flow with no repository or household run state."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.harness.core import HarnessError, repository_state, validate_run
from scripts.harness.run_artifacts import (
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
    tmp_path: Path,
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

    record_review(run_dir, external, root)

    canonical = json.loads((run_dir / "review-report.json").read_text("utf-8"))
    verification = json.loads((run_dir / "verification-report.json").read_text("utf-8"))
    assert canonical["conclusion"] == "APPROVE"
    assert verification["status"] == "UNDETERMINED"
    assert not any("Independent review" in gap for gap in verification["unresolved"])


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
