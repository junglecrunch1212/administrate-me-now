"""Create and refresh schema-valid, run-scoped harness artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import (
    HARNESS_SCHEMA_VERSION,
    HarnessError,
    assert_current_run_state,
    assert_task_scope_covers_tracked_changes,
    binding,
    complete_task_scope,
    ensure_ephemeral_run_dir,
    executable_inventory,
    load_json,
    new_run_directory,
    repository_state,
    render_owner_handoff,
    utc_now,
    validate_artifact_value,
    validate_run,
    write_json,
)


def _run_id(run_dir: Path) -> str:
    value = run_dir.name.removeprefix("administrateme-harness-")
    return value[:80].lower()


def _write_markdown(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _write_owner_handoff(run_dir: Path, owner: dict[str, Any]) -> None:
    _write_markdown(run_dir / "owner-handoff.md", render_owner_handoff(owner))


def create_run(
    root: Path,
    *,
    requested_outcome: str,
    task_paths: list[str],
    protected_paths: list[str],
    run_dir: Path | None = None,
) -> Path:
    task_paths = complete_task_scope(root, task_paths)
    assert_task_scope_covers_tracked_changes(root, task_paths, protected_paths)
    directory = run_dir or new_run_directory()
    ensure_ephemeral_run_dir(directory, root)
    directory.mkdir(parents=True, exist_ok=True)
    run_id = _run_id(directory)
    current_binding = binding(root, task_paths)
    inventories = executable_inventory(root)
    common = {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": run_id,
        "binding": current_binding,
    }
    write_json(
        directory / "outcome-contract.json",
        {
            **common,
            "requested_outcome": requested_outcome,
            "primary_journey": {
                "actor_role": "principal",
                "start_path": "/member/today",
                "steps": [
                    "Open the authenticated member portal.",
                    "Move through the existing navigation destinations.",
                    "Confirm labels, order, authorization, and mode are unchanged.",
                ],
                "expected_result": "The same surfaces render with no visible navigation or mode change.",
            },
            "visible_non_goals": [
                "No wording, route, click, payload, or information-density change.",
                "No release or live household change.",
            ],
            "acceptance_cases": [
                {
                    "case_id": "navigation-happy",
                    "state": "happy",
                    "applicable": True,
                    "when": "An authorized adult opens the portal.",
                    "then": "All ten existing destinations appear in their existing order and labels.",
                },
                {
                    "case_id": "navigation-empty",
                    "state": "empty",
                    "applicable": False,
                    "when": "Navigation has no destinations.",
                    "then": "No empty navigation state is introduced by this extraction.",
                    "not_applicable_reason": "Every supported role retains at least one deterministic destination.",
                },
                {
                    "case_id": "navigation-loading",
                    "state": "loading",
                    "applicable": False,
                    "when": "The navigation payload is being loaded.",
                    "then": "No new loading state is introduced by this server-side extraction.",
                    "not_applicable_reason": "The extracted functions are synchronous and retain the existing page bootstrap behavior.",
                },
                {
                    "case_id": "navigation-stale",
                    "state": "stale",
                    "applicable": False,
                    "when": "A stored navigation snapshot becomes stale.",
                    "then": "No stale snapshot behavior is introduced.",
                    "not_applicable_reason": "Navigation is derived from the current authenticated session rather than a stored navigation projection.",
                },
                {
                    "case_id": "navigation-denied",
                    "state": "denied",
                    "applicable": True,
                    "when": "A child, device, or ambient identity requests an unauthorized surface directly.",
                    "then": "The server returns the existing non-enumerating 404 response.",
                },
                {
                    "case_id": "navigation-degraded",
                    "state": "degraded",
                    "applicable": True,
                    "when": "Domain projections are unavailable after route authorization.",
                    "then": "The surface retains the existing unavailable-domain failure behavior.",
                },
                {
                    "case_id": "navigation-retry",
                    "state": "retry",
                    "applicable": False,
                    "when": "A read-only navigation request is retried.",
                    "then": "No mutation or idempotency receipt is involved.",
                    "not_applicable_reason": "The pilot changes no command or write path.",
                },
                {
                    "case_id": "navigation-terminal-failure",
                    "state": "terminal_failure",
                    "applicable": False,
                    "when": "A navigation operation reaches a terminal write outcome.",
                    "then": "No terminal write result is produced.",
                    "not_applicable_reason": "The extraction changes only deterministic read and authorization helpers; its existing unavailable-domain read failure is covered by the degraded case.",
                },
            ],
            "privacy": {
                "touches_private_data": False,
                "changes_authorization": False,
                "notes": "Authorization behavior is characterized and preserved, not redesigned.",
            },
            "external_or_irreversible": {
                "required": False,
                "side_effect_class": "REPOSITORY_WRITE",
                "notes": "Local repository implementation only; no external, release, or live action.",
            },
            "owner_questions": [],
        },
    )
    write_json(
        directory / "evidence-inventory.json",
        {
            **common,
            "instructions": ["AGENTS.md"],
            "governing_sources": [
                {
                    "path": "docs/refactor/ADR-0000-CONSTITUTIONAL-RECONCILIATION.md",
                    "status": "accepted",
                    "evidence_class": "local_candidate",
                },
                {
                    "path": "docs/refactor/UX-BEHAVIOR-CONTRACT.md",
                    "status": "accepted",
                    "evidence_class": "local_candidate",
                },
                {
                    "path": "docs/refactor/CHANGE-ORDER-018-SOURCE-TIME-AND-CONTEXT-CORRECTION.md",
                    "status": "candidate",
                    "evidence_class": "local_candidate",
                },
            ],
            "repository_state": repository_state(root, protected_paths),
            "inventories": inventories,
            "evidence": [
                {
                    "claim": "The immutable candidate merge base and current candidate each pass the reviewed navigation production-composition journey.",
                    "source": "scripts/harness/baseline_navigation.py and bound check receipts",
                    "evidence_class": "local_candidate",
                }
            ],
        },
    )
    write_json(
        directory / "preservation-manifest.json",
        {
            **common,
            "classification": "PRESERVE",
            "touched_surfaces": inventories["surfaces"],
            "preserved_behaviors": [
                {
                    "behavior_id": "navigation-role-map",
                    "when": "Adult, child, device, ambient, or view-as navigation is requested.",
                    "then": "The existing role-derived routes, labels, order, and hrefs are returned.",
                    "evidence": [
                        "tests/unit/harness/test_member_navigation_characterization.py",
                        "tests/integration/test_harness_member_navigation.py",
                    ],
                },
                {
                    "behavior_id": "experience-precedence",
                    "when": "Experience mode and semantics are composed.",
                    "then": "Child-role, view-member profile, preference override, quiet-hours, and interruption-budget precedence remain exact.",
                    "evidence": [
                        "tests/unit/harness/test_member_navigation_characterization.py"
                    ],
                },
                {
                    "behavior_id": "observation-payload",
                    "when": "A surface is returned in observation mode.",
                    "then": "The existing active, writes_execute, explanation, and evidence link fields remain exact.",
                    "evidence": ["tests/integration/test_harness_member_navigation.py"],
                },
            ],
            "no_change_boundaries": [
                "Event schemas, encrypted log, bus, projections, and pipelines.",
                "Provider commands, approvals, observation semantics, and terminal audit.",
                "Routes, aliases, payloads, visible wording, navigation order, and click count.",
                "Privacy, authMember/viewMember, release state, and OpenClaw boundaries.",
            ],
            "state_boundaries": [
                {
                    "state": "happy",
                    "expected_behavior": "Authorized routes render through production composition.",
                    "evidence": "tests/integration/test_harness_member_navigation.py",
                },
                {
                    "state": "denied",
                    "expected_behavior": "Unauthorized direct routes return the existing non-enumerating 404.",
                    "evidence": "tests/unit/harness/test_member_navigation_characterization.py",
                },
                {
                    "state": "degraded",
                    "expected_behavior": "Unavailable domain views remain a 503 after authorization.",
                    "evidence": "adminme/products/member/surfaces.py",
                },
            ],
            "characterization_checks": [
                "tests/unit/harness/test_member_navigation_characterization.py",
                "tests/integration/test_harness_member_navigation.py",
                "tests/acceptance/test_gate7_household_experience.py::test_ten_surfaces_three_modes_and_independent_route_denial",
            ],
            "migration_required": False,
            "rollback": {
                "task_owned_paths": task_paths,
                "method": "Reverse only task-owned lines with a reviewed patch, then rerun characterization and production-composition checks.",
                "forbidden_operations": [
                    "git reset --hard",
                    "git checkout --",
                    "git clean",
                    "git stash",
                    "broad restore",
                    "history rewrite",
                ],
            },
        },
    )
    _write_markdown(
        directory / "implementation-plan.md",
        """# Implementation plan

## Technical choices

Build a thin deterministic harness from versioned schemas, repository-local skills, existing production tests, and a generated current-state index. Extract only the member navigation seam behind compatibility imports.

## Failure and rollback

Stop on any unexplained behavior difference. Preserve logs in this temporary run and reverse only the task-owned patch after checking for concurrent overlap. Never reset, clean, stash, rewrite, release, or touch household state.
""",
    )
    write_json(
        directory / "verification-report.json",
        {
            **common,
            "status": "UNDETERMINED",
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "changed_files": task_paths,
            "checks": [],
            "browser": {
                "status": "UNDETERMINED",
                "output_directory": None,
                "report_path": None,
                "report_sha256": None,
                "assertion_count": 0,
                "browser_engine": None,
                "desktop": [],
                "narrow": [],
                "console_errors": [],
                "page_errors": [],
                "request_failures": [],
            },
            "metrics": {
                "backend_questions": 0,
                "product_checkpoints": 0,
                "technical_owner_minutes": 0,
                "applicable_journeys": 1,
                "passed_journeys": 0,
                "false_green_count": 0,
                "first_pass_focused": "PASS",
                "autonomous_recovery_rate": "UNDETERMINED",
            },
            "unresolved": [
                "Final breadth, browser, and independent review evidence is not yet available."
            ],
        },
    )
    write_json(
        directory / "review-report.json",
        {
            **common,
            "reviewer": "not-yet-run",
            "read_only": True,
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
            "unresolved_evidence": ["Independent exact-diff review is not yet run."],
            "conclusion": "UNDETERMINED",
        },
    )
    owner_metadata = {
        **common,
        "requested_outcome": requested_outcome,
        "what_now_works": "UNDETERMINED",
        "what_did_not_change": [
            "No release or live household action is authorized by this run."
        ],
        "proof": {
            "deterministic": "UNDETERMINED",
            "production_journey": "UNDETERMINED",
            "browser": "UNDETERMINED",
            "independent_review": "UNDETERMINED",
        },
        "try_this": {
            "start_path": "/member/today",
            "task": "Open the member portal and use the existing navigation.",
            "expected_result": "The same destinations, labels, order, and experience mode appear.",
            "desktop_screenshot": None,
            "narrow_screenshot": None,
        },
        "remaining_decision": None,
        "release_status": "NOT_RELEASED",
    }
    write_json(directory / "owner-handoff.metadata.json", owner_metadata)
    _write_owner_handoff(directory, owner_metadata)
    validate_run(directory, root)
    return directory


def refresh_bindings(root: Path, run_dir: Path, task_paths: list[str]) -> None:
    ensure_ephemeral_run_dir(run_dir, root)
    task_paths = complete_task_scope(root, task_paths)
    evidence_path = run_dir / "evidence-inventory.json"
    evidence = json.loads(evidence_path.read_text("utf-8"))
    previous_state = evidence["repository_state"]
    protected_paths = previous_state["protected_paths"]
    assert_task_scope_covers_tracked_changes(root, task_paths, protected_paths)
    current = binding(root, task_paths)
    previous = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))[
        "binding"
    ]
    current_state = repository_state(root, protected_paths)
    changed = previous != current or previous_state != current_state
    for name in (
        "outcome-contract.json",
        "evidence-inventory.json",
        "preservation-manifest.json",
        "verification-report.json",
        "review-report.json",
        "owner-handoff.metadata.json",
    ):
        path = run_dir / name
        data: dict[str, Any] = json.loads(path.read_text("utf-8"))
        data["binding"] = current
        if name == "verification-report.json":
            data["changed_files"] = task_paths
        elif name == "evidence-inventory.json":
            data["repository_state"] = current_state
        elif name == "preservation-manifest.json":
            data["rollback"]["task_owned_paths"] = task_paths
        write_json(path, data)
    if changed:
        _invalidate_stale_evidence(run_dir)


def _invalidate_stale_evidence(run_dir: Path) -> None:
    """Clear conclusions when the candidate changes; logs remain historical only."""

    verification_path = run_dir / "verification-report.json"
    verification: dict[str, Any] = json.loads(verification_path.read_text("utf-8"))
    verification.update(
        {
            "status": "UNDETERMINED",
            "finished_at": utc_now(),
            "checks": [],
            "browser": {
                "status": "UNDETERMINED",
                "output_directory": None,
                "report_path": None,
                "report_sha256": None,
                "assertion_count": 0,
                "browser_engine": None,
                "desktop": [],
                "narrow": [],
                "console_errors": [],
                "page_errors": [],
                "request_failures": [],
            },
            "unresolved": [
                "The candidate binding or repository state changed; every check, browser journey, and independent review must be rerun."
            ],
        }
    )
    verification["metrics"]["passed_journeys"] = 0
    verification["metrics"]["autonomous_recovery_rate"] = "UNDETERMINED"
    write_json(verification_path, verification)

    review_path = run_dir / "review-report.json"
    review: dict[str, Any] = json.loads(review_path.read_text("utf-8"))
    review.update(
        {
            "reviewer": "not-yet-run-for-current-binding",
            "findings": [],
            "unresolved_evidence": [
                "Independent exact-diff review is not yet run for the current binding and repository state."
            ],
            "conclusion": "UNDETERMINED",
        }
    )
    write_json(review_path, review)

    owner_path = run_dir / "owner-handoff.metadata.json"
    owner: dict[str, Any] = json.loads(owner_path.read_text("utf-8"))
    owner["what_now_works"] = "UNDETERMINED"
    owner["proof"] = {
        "deterministic": "UNDETERMINED",
        "production_journey": "UNDETERMINED",
        "browser": "UNDETERMINED",
        "independent_review": "UNDETERMINED",
    }
    owner["try_this"]["desktop_screenshot"] = None
    owner["try_this"]["narrow_screenshot"] = None
    owner["release_status"] = "NOT_RELEASED"
    write_json(owner_path, owner)
    _write_owner_handoff(run_dir, owner)


def record_checks(run_dir: Path, receipts: list[dict[str, Any]]) -> None:
    path = run_dir / "verification-report.json"
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    existing = {(check["tier"], check["check_id"]): check for check in data["checks"]}
    existing.update({(check["tier"], check["check_id"]): check for check in receipts})
    data["checks"] = list(existing.values())
    data["finished_at"] = utc_now()
    write_json(path, data)
    _reconcile_verification(run_dir)


def record_browser(run_dir: Path, result: dict[str, Any]) -> None:
    record_checks(run_dir, [result["receipt"]])
    path = run_dir / "verification-report.json"
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    data["browser"] = result["browser"]
    write_json(path, data)
    _reconcile_verification(run_dir)


def _reconcile_verification(run_dir: Path) -> None:
    """Derive the current conclusion from current-bound proof, never stale prose."""

    from .checks import tier_checks

    path = run_dir / "verification-report.json"
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    review: dict[str, Any] = json.loads(
        (run_dir / "review-report.json").read_text("utf-8")
    )
    checks = data["checks"]
    passed_tiers = {check["tier"] for check in checks if check["status"] == "PASS"}
    failed_checks = [check["check_id"] for check in checks if check["status"] == "FAIL"]
    unresolved: list[str] = []
    if failed_checks:
        unresolved.append(f"Failed checks: {', '.join(sorted(failed_checks))}.")
    missing_tiers = sorted({"static", "focused", "journey", "breadth"} - passed_tiers)
    if missing_tiers:
        unresolved.append(
            f"Required check tiers not yet passing: {', '.join(missing_tiers)}."
        )
    declared: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for check in checks:
        declared.setdefault((check["tier"], check["check_id"]), []).append(check)
    invalid_required: list[str] = []
    for tier in ("static", "focused", "journey", "breadth"):
        for spec in tier_checks(tier, data["changed_files"]):
            matches = declared.get((spec.tier, spec.check_id), [])
            if (
                len(matches) != 1
                or matches[0]["command"] != spec.command
                or matches[0]["status"] != "PASS"
                or matches[0]["exit_code"] != 0
            ):
                invalid_required.append(f"{spec.tier}/{spec.check_id}")
    if invalid_required:
        unresolved.append(
            "Required reviewed checks not yet passing exactly: "
            + ", ".join(invalid_required)
            + "."
        )
    browser_status = data["browser"]["status"]
    if browser_status != "PASS":
        unresolved.append(f"Browser proof is {browser_status}.")
    if review["conclusion"] != "APPROVE":
        unresolved.append(
            f"Independent review is {review['conclusion']} for the current binding."
        )
    if review["unresolved_evidence"]:
        unresolved.extend(review["unresolved_evidence"])
    actionable = [
        finding["finding_id"] for finding in review["findings"] if finding["actionable"]
    ]
    if actionable:
        unresolved.append(
            f"Actionable review findings remain: {', '.join(sorted(actionable))}."
        )
    has_failure = (
        bool(failed_checks)
        or browser_status == "FAIL"
        or review["conclusion"] == "CHANGES_REQUIRED"
    )
    data["unresolved"] = list(dict.fromkeys(unresolved))
    data["status"] = (
        "FAIL" if has_failure else "PASS" if not data["unresolved"] else "UNDETERMINED"
    )
    focused = [check["status"] for check in checks if check["tier"] == "focused"]
    data["metrics"]["first_pass_focused"] = (
        "FAIL" if "FAIL" in focused else "PASS" if "PASS" in focused else "UNDETERMINED"
    )
    has_production_journey = any(
        check["tier"] == "journey"
        and check["check_id"] == "navigation-production-composition"
        and check["status"] == "PASS"
        for check in checks
    )
    data["metrics"]["passed_journeys"] = (
        data["metrics"]["applicable_journeys"]
        if has_production_journey and browser_status == "PASS"
        else 0
    )
    data["finished_at"] = utc_now()
    write_json(path, data)


def record_review(run_dir: Path, review_path: Path, root: Path | None = None) -> None:
    """Import an independently produced review only when it matches this run."""

    ensure_ephemeral_run_dir(run_dir, root)
    if root is not None:
        assert_current_run_state(run_dir, root)
    candidate = validate_artifact_value(
        "review-report.json",
        load_json(review_path),
        root,
    )
    outcome = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))
    if candidate["run_id"] != outcome["run_id"]:
        raise HarnessError("independent review belongs to a different harness run")
    if candidate["binding"] != outcome["binding"]:
        raise HarnessError("independent review is bound to a different candidate")
    canonical_review = run_dir / "review-report.json"
    verification_path = run_dir / "verification-report.json"
    previous_review = canonical_review.read_bytes()
    previous_verification = verification_path.read_bytes()
    try:
        write_json(canonical_review, candidate)
        _reconcile_verification(run_dir)
        if root is not None:
            assert_current_run_state(run_dir, root)
    except Exception:
        canonical_review.write_bytes(previous_review)
        verification_path.write_bytes(previous_verification)
        raise


def finalize_owner_handoff(
    run_dir: Path,
    *,
    what_now_works: str,
    task: str,
    expected_result: str,
    start_path: str = "/member/today",
    remaining_decision: str | None = None,
    release_status: str = "CANDIDATE_ONLY",
    root: Path | None = None,
) -> None:
    """Derive the owner handoff from current-bound PASS evidence."""

    ensure_ephemeral_run_dir(run_dir, root)
    if root is not None:
        assert_current_run_state(run_dir, root)
    if release_status not in {"NOT_RELEASED", "CANDIDATE_ONLY"}:
        raise HarnessError("a local harness run cannot finalize as RELEASED")
    verification = json.loads((run_dir / "verification-report.json").read_text("utf-8"))
    review = json.loads((run_dir / "review-report.json").read_text("utf-8"))
    outcome = json.loads((run_dir / "outcome-contract.json").read_text("utf-8"))
    required_tiers = {"static", "focused", "journey", "breadth"}
    passed_tiers = {
        check["tier"] for check in verification["checks"] if check["status"] == "PASS"
    }
    production_journey_passed = any(
        check["check_id"] == "navigation-production-composition"
        and check["tier"] == "journey"
        and check["status"] == "PASS"
        for check in verification["checks"]
    )
    actionable_findings = [
        finding["finding_id"] for finding in review["findings"] if finding["actionable"]
    ]
    if (
        verification["status"] != "PASS"
        or verification["browser"]["status"] != "PASS"
        or required_tiers - passed_tiers
        or not production_journey_passed
        or review["conclusion"] != "APPROVE"
        or review["unresolved_evidence"]
        or actionable_findings
    ):
        raise HarnessError(
            "owner handoff requires current-bound passing deterministic, "
            "production-journey, browser, and independent-review proof"
        )
    if (
        release_status == "CANDIDATE_ONLY"
        and outcome["binding"]["evidence_class"] == "live"
    ):
        raise HarnessError("live evidence cannot be finalized as CANDIDATE_ONLY")

    browser = verification["browser"]
    desktop = browser["desktop"][0]["path"]
    narrow = browser["narrow"][0]["path"]
    owner = {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": outcome["run_id"],
        "binding": outcome["binding"],
        "requested_outcome": outcome["requested_outcome"],
        "what_now_works": what_now_works,
        "what_did_not_change": [
            "The ten member destinations retain their existing labels, order, routes, and role visibility.",
            "Authorization, experience-mode precedence, observation behavior, and domain failure responses retain their characterized behavior.",
            "No release, deployment, provider write, or live household action was performed.",
        ],
        "proof": {
            "deterministic": "PASS",
            "production_journey": "PASS",
            "browser": "PASS",
            "independent_review": "APPROVE",
        },
        "try_this": {
            "start_path": start_path,
            "task": task,
            "expected_result": expected_result,
            "desktop_screenshot": desktop,
            "narrow_screenshot": narrow,
        },
        "remaining_decision": remaining_decision,
        "release_status": release_status,
    }
    owner = validate_artifact_value("owner-handoff.metadata.json", owner, root)
    owner_path = run_dir / "owner-handoff.metadata.json"
    owner_markdown_path = run_dir / "owner-handoff.md"
    previous_owner = owner_path.read_bytes()
    previous_markdown = owner_markdown_path.read_bytes()
    try:
        write_json(owner_path, owner)
        _write_owner_handoff(run_dir, owner)
        validate_run(run_dir, root)
    except Exception:
        owner_path.write_bytes(previous_owner)
        owner_markdown_path.write_bytes(previous_markdown)
        raise


def sync_owner_handoff(run_dir: Path) -> None:
    """Rewrite owner prose from the current metadata after an intentional update."""

    owner = json.loads((run_dir / "owner-handoff.metadata.json").read_text("utf-8"))
    _write_owner_handoff(run_dir, owner)
