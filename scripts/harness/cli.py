"""Command-line interface for deterministic AdministrateMe harness operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checks import (
    checks_as_json,
    compare_browser_evidence,
    run_browser,
    run_tier,
    tier_checks,
)
from .core import (
    HarnessError,
    applicable_instructions,
    executable_inventory,
    repository_root,
    run_diff_hygiene,
    validate_artifact,
    validate_run,
)
from .current_state import (
    all_findings,
    check_current_state,
    write_current_state,
)
from .autonomous_release import (
    complete_release_manifest,
    evaluate_and_record_admission,
    evaluate_manifest,
    next_g002_queue,
    prepare_bounded_repair_policy,
    prepare_observation_release_policy,
    prepare_release_manifest,
    queue_g002_candidate,
    record_g002_operator_attempt,
    validate_g002_queue,
)
from .run_artifacts import (
    create_run,
    finalize_owner_handoff,
    record_browser,
    record_browser_not_applicable,
    record_checks,
    record_hosted_review,
    record_review,
    refresh_bindings,
    review_checkpoint,
    sync_owner_handoff,
)
from .skill_references import check_skill_references, update_skill_references


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inventory")

    instructions = subparsers.add_parser("instructions")
    instructions.add_argument("paths", nargs="+")

    validate = subparsers.add_parser("validate-artifact")
    validate.add_argument("path", type=Path)

    validate_all = subparsers.add_parser("validate-run")
    validate_all.add_argument("run_dir", type=Path)

    create = subparsers.add_parser("create-run")
    create.add_argument("--requested-outcome", required=True)
    create.add_argument("--task-path", action="append", default=[])
    create.add_argument("--protected-path", action="append", default=[])

    refresh = subparsers.add_parser("refresh-bindings")
    refresh.add_argument("run_dir", type=Path)
    refresh.add_argument("--task-path", action="append", default=[])

    current = subparsers.add_parser("current-state")
    current.add_argument("--check", action="store_true")

    consistency = subparsers.add_parser("consistency")
    consistency.add_argument("--base-ref")
    consistency.add_argument("--check-generated", action="store_true")

    subparsers.add_parser("diff-hygiene")

    autonomous_release = subparsers.add_parser("autonomous-release")
    autonomous_release.add_argument("manifest", type=Path)
    autonomous_release.add_argument("--receipt", type=Path)

    repair_policy = subparsers.add_parser("prepare-bounded-repair")
    repair_policy.add_argument("manifest", type=Path)
    repair_policy.add_argument("admission_receipt", type=Path)
    repair_policy.add_argument("--merge-commit", required=True)
    repair_policy.add_argument("--output", required=True, type=Path)

    observation_policy = subparsers.add_parser("prepare-observation-release")
    observation_policy.add_argument("manifest", type=Path)
    observation_policy.add_argument("admission_receipt", type=Path)
    observation_policy.add_argument("--merge-commit", required=True)
    observation_policy.add_argument("--output", required=True, type=Path)

    queue = subparsers.add_parser("queue-g002")
    queue.add_argument("run_dir", type=Path)
    queue.add_argument("--repository", required=True)
    queue.add_argument("--pull-request", required=True, type=int)
    queue.add_argument(
        "--lane", required=True, choices=("observation", "bounded_repair")
    )
    queue.add_argument("--target-degradation", action="append", default=[])
    queue.add_argument("--target-doctor-check", action="append", default=[])
    queue.add_argument("--output", required=True, type=Path)

    validate_queue = subparsers.add_parser("validate-g002-queue")
    validate_queue.add_argument("queue_entry", type=Path)

    subparsers.add_parser("next-g002-queue")

    operator_attempt = subparsers.add_parser("record-g002-attempt")
    operator_attempt.add_argument("queue_entry", type=Path)
    operator_attempt.add_argument(
        "--decision",
        required=True,
        choices=(
            "WAITING_REVIEW",
            "WAITING_CHECKS",
            "SUPERSEDED",
            "G001_REQUIRED",
            "ADMISSION_DENIED",
            "COMPLETE",
            "ROLLED_BACK",
            "DEPLOYMENT_INCOMPLETE",
            "REPAIR_LIMIT_REACHED",
        ),
    )
    operator_attempt.add_argument("--merge-commit")
    operator_attempt.add_argument("--deployed-sha")
    operator_attempt.add_argument("--settlement-manifest", type=Path)
    operator_attempt.add_argument(
        "--rollback-result",
        choices=("NOT_APPLICABLE", "PASS", "FAIL", "UNDETERMINED"),
        default="NOT_APPLICABLE",
    )
    operator_attempt.add_argument(
        "--remaining-degradation", action="append", default=[]
    )
    operator_attempt.add_argument(
        "--remaining-doctor-check", action="append", default=[]
    )
    operator_attempt.add_argument("--reason-code", action="append", default=[])
    operator_attempt.add_argument("--elapsed-seconds", type=float, required=True)

    release_manifest = subparsers.add_parser("prepare-release-manifest")
    release_manifest.add_argument("queue_entry", type=Path)
    release_manifest.add_argument("--doctor", required=True, type=Path)
    release_manifest.add_argument("--diagnostics", required=True, type=Path)
    release_manifest.add_argument("--output", required=True, type=Path)

    settlement = subparsers.add_parser("complete-release")
    settlement.add_argument("manifest", type=Path)
    settlement.add_argument("admission_receipt", type=Path)
    settlement.add_argument("--merge-commit", required=True)
    settlement.add_argument("--updater-result", required=True, type=Path)
    settlement.add_argument("--doctor", required=True, type=Path)
    settlement.add_argument("--diagnostics", required=True, type=Path)
    settlement.add_argument("--active-pointer", required=True, type=Path)
    settlement.add_argument("--live-privacy-receipt", required=True, type=Path)
    settlement.add_argument("--live-browser-receipt", type=Path)
    settlement.add_argument("--output", required=True, type=Path)

    skill_refs = subparsers.add_parser("skill-references")
    skill_refs.add_argument("--check", action="store_true")

    select = subparsers.add_parser("select-checks")
    select.add_argument(
        "tier", choices=("static", "focused", "journey", "breadth", "release")
    )
    select.add_argument("paths", nargs="*")

    run = subparsers.add_parser("run-tier")
    run.add_argument(
        "tier", choices=("static", "focused", "journey", "breadth", "release")
    )
    run.add_argument("run_dir", type=Path)
    run.add_argument("--changed-path", action="append", default=[])
    run.add_argument("--node-bin")

    browser = subparsers.add_parser("browser")
    browser.add_argument("run_dir", type=Path)
    browser.add_argument("--node-bin")
    browser.add_argument("--label", default="current")

    compare_browser = subparsers.add_parser("compare-browser")
    compare_browser.add_argument("run_dir", type=Path)
    compare_browser.add_argument("--before-label", default="current")
    compare_browser.add_argument("--after-label", default="after")
    sync_owner = subparsers.add_parser("sync-owner-handoff")
    sync_owner.add_argument("run_dir", type=Path)

    review = subparsers.add_parser("record-review")
    review.add_argument("run_dir", type=Path)
    review.add_argument("review_path", type=Path)

    hosted_review = subparsers.add_parser("record-hosted-review")
    hosted_review.add_argument("run_dir", type=Path)
    hosted_review.add_argument("result_path", type=Path)
    hosted_review.add_argument("receipt_path", type=Path)

    review_ready = subparsers.add_parser("review-ready")
    review_ready.add_argument("run_dir", type=Path)

    finalize = subparsers.add_parser("finalize-owner")
    finalize.add_argument("run_dir", type=Path)
    finalize.add_argument("--what-now-works", required=True)
    finalize.add_argument("--task", required=True)
    finalize.add_argument("--expected-result", required=True)
    finalize.add_argument("--start-path", default="/")
    finalize.add_argument("--remaining-decision")
    finalize.add_argument(
        "--release-status",
        choices=("NOT_RELEASED", "CANDIDATE_ONLY"),
        default="CANDIDATE_ONLY",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = repository_root()
    try:
        if args.command == "inventory":
            print(json.dumps(executable_inventory(root), indent=2, sort_keys=True))
        elif args.command == "instructions":
            print(json.dumps(applicable_instructions(root, args.paths), indent=2))
        elif args.command == "validate-artifact":
            validate_artifact(args.path, root)
            print(f"PASS {args.path}")
        elif args.command == "validate-run":
            validate_run(args.run_dir, root)
            print(f"PASS {args.run_dir}")
        elif args.command == "create-run":
            directory = create_run(
                root,
                requested_outcome=args.requested_outcome,
                task_paths=args.task_path,
                protected_paths=args.protected_path,
            )
            print(directory)
        elif args.command == "refresh-bindings":
            refresh_bindings(root, args.run_dir, args.task_path)
            validate_run(args.run_dir, root)
            print(f"PASS {args.run_dir}")
        elif args.command == "current-state":
            if args.check:
                check_current_state(root)
                print("PASS docs/refactor/CURRENT-STATE.md")
            else:
                print(write_current_state(root))
        elif args.command == "consistency":
            if args.check_generated:
                check_current_state(root)
            findings = all_findings(root, args.base_ref)
            print(
                json.dumps(
                    [finding.__dict__ for finding in findings],
                    indent=2,
                    sort_keys=True,
                )
            )
            if any(finding.status == "FAIL" for finding in findings):
                return 1
        elif args.command == "diff-hygiene":
            base_sha = run_diff_hygiene(root)
            print(f"PASS committed={base_sha}..HEAD working=HEAD")
        elif args.command == "autonomous-release":
            result = (
                evaluate_and_record_admission(args.manifest, args.receipt, root)
                if args.receipt is not None
                else evaluate_manifest(args.manifest, root)
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            if result["status"] in {"DENY", "ROLLBACK"}:
                return 1
        elif args.command == "prepare-bounded-repair":
            result = prepare_bounded_repair_policy(
                args.manifest,
                args.admission_receipt,
                merge_commit=args.merge_commit,
                output_path=args.output,
                root=root,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "prepare-observation-release":
            result = prepare_observation_release_policy(
                args.manifest,
                args.admission_receipt,
                merge_commit=args.merge_commit,
                output_path=args.output,
                root=root,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "queue-g002":
            result = queue_g002_candidate(
                args.run_dir,
                repository=args.repository,
                pull_request=args.pull_request,
                lane=args.lane,
                target_degradations=args.target_degradation,
                target_doctor_checks=args.target_doctor_check,
                output_path=args.output,
                root=root,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "validate-g002-queue":
            validate_g002_queue(args.queue_entry, root)
            print(f"PASS {args.queue_entry}")
        elif args.command == "next-g002-queue":
            print(json.dumps(next_g002_queue(root), indent=2, sort_keys=True))
        elif args.command == "record-g002-attempt":
            result = record_g002_operator_attempt(
                args.queue_entry,
                decision=args.decision,
                merge_commit=args.merge_commit,
                deployed_sha=args.deployed_sha,
                settlement_manifest_path=args.settlement_manifest,
                rollback_result=args.rollback_result,
                remaining_degradations=args.remaining_degradation,
                remaining_doctor_checks=args.remaining_doctor_check,
                reason_codes=args.reason_code,
                elapsed_seconds=args.elapsed_seconds,
                root=root,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "prepare-release-manifest":
            result = prepare_release_manifest(
                args.queue_entry,
                doctor_path=args.doctor,
                diagnostics_path=args.diagnostics,
                output_path=args.output,
                root=root,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "complete-release":
            result = complete_release_manifest(
                args.manifest,
                args.admission_receipt,
                merge_commit=args.merge_commit,
                updater_result_path=args.updater_result,
                doctor_path=args.doctor,
                diagnostics_path=args.diagnostics,
                active_pointer_path=args.active_pointer,
                live_privacy_receipt_path=args.live_privacy_receipt,
                live_browser_receipt_path=args.live_browser_receipt,
                output_path=args.output,
                root=root,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            if result["status"] != "COMPLETE":
                return 1
        elif args.command == "skill-references":
            if args.check:
                stale = check_skill_references(root)
                if stale:
                    raise HarnessError(f"stale generated skill references: {stale}")
                print("PASS skill references")
            else:
                print("\n".join(str(path) for path in update_skill_references(root)))
        elif args.command == "select-checks":
            print(checks_as_json(tier_checks(args.tier, args.paths)))
        elif args.command == "run-tier":
            receipts = run_tier(
                root,
                args.run_dir,
                args.tier,
                changed_paths=args.changed_path or None,
                node_bin=args.node_bin,
            )
            record_checks(args.run_dir, receipts)
            print(json.dumps(receipts, indent=2, sort_keys=True))
            if any(receipt["status"] != "PASS" for receipt in receipts):
                return 1
        elif args.command == "browser":
            from .autonomous_release import browser_required

            verification = json.loads(
                (args.run_dir / "verification-report.json").read_text("utf-8")
            )
            outcome = json.loads(
                (args.run_dir / "outcome-contract.json").read_text("utf-8")
            )
            needs_browser = browser_required(
                verification["changed_files"],
                declared_visible=(
                    outcome["release_impacts"]["member_visible_behavior"] == "YES"
                ),
            )
            if not needs_browser:
                browser = record_browser_not_applicable(args.run_dir, root)
                print(json.dumps({"browser": browser}, indent=2, sort_keys=True))
            else:
                result = run_browser(
                    root,
                    args.run_dir,
                    node_bin=args.node_bin,
                    label=args.label,
                )
                record_browser(args.run_dir, result)
                print(json.dumps(result, indent=2, sort_keys=True))
                if result["browser"]["status"] != "PASS":
                    return 1
        elif args.command == "compare-browser":
            receipt = compare_browser_evidence(
                root,
                args.run_dir,
                before_label=args.before_label,
                after_label=args.after_label,
            )
            record_checks(args.run_dir, [receipt])
            print(json.dumps(receipt, indent=2, sort_keys=True))
            if receipt["status"] != "PASS":
                return 1
        elif args.command == "sync-owner-handoff":
            sync_owner_handoff(args.run_dir)
            validate_run(args.run_dir, root)
            print(f"PASS {args.run_dir / 'owner-handoff.md'}")
        elif args.command == "record-review":
            record_review(args.run_dir, args.review_path, root)
            print(f"PASS {args.run_dir / 'review-report.json'}")
        elif args.command == "record-hosted-review":
            retained = record_hosted_review(
                args.run_dir, args.result_path, args.receipt_path, root
            )
            print(json.dumps(retained, indent=2, sort_keys=True))
        elif args.command == "review-ready":
            checkpoint = review_checkpoint(args.run_dir, root)
            print(json.dumps(checkpoint, indent=2, sort_keys=True))
            if checkpoint["status"] != "READY":
                return 1
        elif args.command == "finalize-owner":
            finalize_owner_handoff(
                args.run_dir,
                what_now_works=args.what_now_works,
                task=args.task,
                expected_result=args.expected_result,
                start_path=args.start_path,
                remaining_decision=args.remaining_decision,
                release_status=args.release_status,
                root=root,
            )
            print(f"PASS {args.run_dir / 'owner-handoff.md'}")
    except HarnessError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
