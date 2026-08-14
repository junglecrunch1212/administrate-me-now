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
from .run_artifacts import (
    create_run,
    finalize_owner_handoff,
    record_browser,
    record_checks,
    record_review,
    refresh_bindings,
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

    finalize = subparsers.add_parser("finalize-owner")
    finalize.add_argument("run_dir", type=Path)
    finalize.add_argument("--what-now-works", required=True)
    finalize.add_argument("--task", required=True)
    finalize.add_argument("--expected-result", required=True)
    finalize.add_argument("--start-path", default="/member/today")
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
                changed_paths=args.changed_path,
                node_bin=args.node_bin,
            )
            record_checks(args.run_dir, receipts)
            print(json.dumps(receipts, indent=2, sort_keys=True))
            if any(receipt["status"] != "PASS" for receipt in receipts):
                return 1
        elif args.command == "browser":
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
