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
    from .autonomous_release import browser_required

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
                "start_path": "/",
                "steps": ["UNDETERMINED"],
                "expected_result": "UNDETERMINED",
            },
            "visible_non_goals": [
                "No release or live household change is authorized by a local run."
            ],
            "acceptance_cases": [
                {
                    "case_id": f"outcome-{state.replace('_', '-')}",
                    "state": state,
                    "applicable": False,
                    "when": "UNDETERMINED",
                    "then": "UNDETERMINED",
                    "not_applicable_reason": "UNDETERMINED",
                }
                for state in (
                    "happy",
                    "empty",
                    "loading",
                    "stale",
                    "degraded",
                    "denied",
                    "retry",
                    "terminal_failure",
                )
            ],
            "privacy": {
                "touches_private_data": False,
                "changes_authorization": False,
                "notes": "UNDETERMINED",
            },
            "release_impacts": {
                "provider_writes_enabled": "UNDETERMINED",
                "runtime_or_dependency_migration": "UNDETERMINED",
                "provider_enrollment": "UNDETERMINED",
                "credentials_keychain_or_permissions": "UNDETERMINED",
                "privacy_or_authorization_change": "UNDETERMINED",
                "destructive_data_change": "UNDETERMINED",
                "observation_mode_change": "UNDETERMINED",
                "release_control_change": "UNDETERMINED",
                "member_visible_behavior": "UNDETERMINED",
            },
            "external_or_irreversible": {
                "required": False,
                "side_effect_class": "REPOSITORY_WRITE",
                "notes": "UNDETERMINED",
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
                    "path": "docs/refactor/CURRENT-STATE.md",
                    "status": "accepted",
                    "evidence_class": "local_candidate",
                },
            ],
            "repository_state": repository_state(root, protected_paths),
            "inventories": inventories,
            "evidence": [
                {
                    "claim": "UNDETERMINED",
                    "source": "UNDETERMINED",
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
            "touched_surfaces": [],
            "preserved_behaviors": [
                {
                    "behavior_id": "task-preservation",
                    "when": "UNDETERMINED",
                    "then": "UNDETERMINED",
                    "evidence": ["UNDETERMINED"],
                },
            ],
            "no_change_boundaries": ["UNDETERMINED"],
            "state_boundaries": [
                {
                    "state": "happy",
                    "expected_behavior": "UNDETERMINED",
                    "evidence": "UNDETERMINED",
                },
            ],
            "characterization_checks": ["UNDETERMINED"],
            "migration_required": False,
            "rollback": {
                "task_owned_paths": task_paths,
                "method": "Reverse only task-owned lines with a reviewed patch, then rerun the classified characterization and production-composition checks.",
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
        f"""# Implementation plan

## Technical choices

Requested outcome: {requested_outcome}

UNDETERMINED until `adminme-outcome` and `adminme-change` replace this scaffold with the bounded technical choice and exact failure paths.

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
                "status": (
                    "UNDETERMINED" if browser_required(task_paths) else "NOT_APPLICABLE"
                ),
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
                "first_pass_focused": "UNDETERMINED",
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
            "start_path": "/",
            "task": requested_outcome,
            "expected_result": "UNDETERMINED",
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

    from .autonomous_release import browser_required

    verification_path = run_dir / "verification-report.json"
    verification: dict[str, Any] = json.loads(verification_path.read_text("utf-8"))
    outcome = load_json(run_dir / "outcome-contract.json")
    verification.update(
        {
            "status": "UNDETERMINED",
            "finished_at": utc_now(),
            "checks": [],
            "browser": {
                "status": (
                    "UNDETERMINED"
                    if browser_required(
                        verification["changed_files"],
                        declared_visible=(
                            outcome["release_impacts"]["member_visible_behavior"]
                            == "YES"
                        ),
                    )
                    else "NOT_APPLICABLE"
                ),
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


def record_browser_not_applicable(
    run_dir: Path, root: Path | None = None
) -> dict[str, Any]:
    """Record the classifier's N/A result without executing a browser suite."""

    ensure_ephemeral_run_dir(run_dir, root)
    if root is not None:
        assert_current_run_state(run_dir, root)
    path = run_dir / "verification-report.json"
    data: dict[str, Any] = load_json(path)
    outcome = load_json(run_dir / "outcome-contract.json")
    from .autonomous_release import browser_required

    if browser_required(
        data["changed_files"],
        declared_visible=(
            outcome["release_impacts"]["member_visible_behavior"] == "YES"
        ),
    ):
        raise HarnessError("browser proof is required for this candidate")
    data["checks"] = [
        check for check in data["checks"] if check["check_id"] != "gate7-browser"
    ]
    data["browser"] = {
        "status": "NOT_APPLICABLE",
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
    }
    write_json(path, data)
    _reconcile_verification(run_dir)
    return data["browser"]


def _reconcile_verification(run_dir: Path) -> None:
    """Derive the current conclusion from current-bound proof, never stale prose."""

    from .checks import tier_checks
    from .autonomous_release import browser_required

    path = run_dir / "verification-report.json"
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    outcome = load_json(run_dir / "outcome-contract.json")
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
    needs_browser = browser_required(
        data["changed_files"],
        declared_visible=(
            outcome["release_impacts"]["member_visible_behavior"] == "YES"
        ),
    )
    if not needs_browser and browser_status == "UNDETERMINED":
        browser_status = "NOT_APPLICABLE"
        data["browser"]["status"] = browser_status
    if needs_browser and browser_status != "PASS":
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
    from .checks import tier_checks

    required_journeys = {
        check.check_id for check in tier_checks("journey", data["changed_files"])
    }
    passed_journey_ids = {
        check["check_id"]
        for check in checks
        if check["tier"] == "journey" and check["status"] == "PASS"
    }
    has_production_journey = required_journeys <= passed_journey_ids
    data["metrics"]["passed_journeys"] = (
        data["metrics"]["applicable_journeys"]
        if has_production_journey and browser_status in {"PASS", "NOT_APPLICABLE"}
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


def record_hosted_review(
    run_dir: Path,
    result_path: Path,
    receipt_path: Path,
    root: Path,
) -> dict[str, str]:
    """Import an immutable hosted review only at the exact pre-breadth checkpoint."""

    from .autonomous_release import load_schema_value

    checkpoint = review_checkpoint(run_dir, root)
    if checkpoint["status"] != "READY":
        raise HarnessError(
            "hosted review import requires the ready pre-breadth checkpoint"
        )
    verification = load_json(run_dir / "verification-report.json")
    if any(check["tier"] in {"breadth", "release"} for check in verification["checks"]):
        raise HarnessError("hosted review cannot be imported after breadth began")
    result = load_schema_value(result_path, "hosted-review-result.schema.json", root)
    receipt = load_schema_value(receipt_path, "hosted-review-receipt.schema.json", root)
    bound = checkpoint["binding"]
    actionable = sorted(
        finding["finding_id"] for finding in result["findings"] if finding["actionable"]
    )
    if (
        result["head_sha"] != bound["head_sha"]
        or result["base_sha"] != bound["base_sha"]
        or receipt["head_sha"] != bound["head_sha"]
        or receipt["base_sha"] != bound["base_sha"]
        or receipt["diff_sha256"] != bound["diff_sha256"]
        or sorted(receipt["actionable_finding_ids"]) != actionable
        or receipt["finding_count"] != len(result["findings"])
        or receipt["unresolved_evidence_count"] != len(result["unresolved_evidence"])
        or receipt["conclusion"] != result["conclusion"]
    ):
        raise HarnessError("hosted review result and receipt do not match this binding")
    attempt_name = f"{receipt['workflow_run_id']}-{receipt['workflow_run_attempt']}"
    attempt_dir = run_dir / "hosted-review" / "attempts" / attempt_name
    if attempt_dir.exists():
        raise HarnessError("hosted review attempt is already retained and immutable")
    attempt_dir.mkdir(parents=True)
    canonical_result = attempt_dir / "result.json"
    canonical_receipt = attempt_dir / "receipt.json"
    candidate_path = attempt_dir / "review-report.import.json"
    try:
        write_json(canonical_result, result)
        write_json(canonical_receipt, receipt)
        candidate = {
            "schema_version": 1,
            "run_id": checkpoint["run_id"],
            "binding": bound,
            "reviewer": f"hosted-codex:{receipt['workflow_run']}",
            "read_only": True,
            "scope": result["scope"],
            "findings": result["findings"],
            "unresolved_evidence": result["unresolved_evidence"],
            "conclusion": result["conclusion"],
        }
        write_json(candidate_path, candidate)
        record_review(run_dir, candidate_path, root)
    except Exception:
        raise
    finally:
        candidate_path.unlink(missing_ok=True)
    return {
        "result": canonical_result.relative_to(run_dir).as_posix(),
        "receipt": canonical_receipt.relative_to(run_dir).as_posix(),
    }


def finalize_owner_handoff(
    run_dir: Path,
    *,
    what_now_works: str,
    task: str,
    expected_result: str,
    start_path: str = "/",
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
    from .checks import tier_checks

    required_journeys = {
        check.check_id
        for check in tier_checks("journey", verification["changed_files"])
    }
    passed_journey_ids = {
        check["check_id"]
        for check in verification["checks"]
        if check["tier"] == "journey" and check["status"] == "PASS"
    }
    production_journey_passed = required_journeys <= passed_journey_ids
    actionable_findings = [
        finding["finding_id"] for finding in review["findings"] if finding["actionable"]
    ]
    from .autonomous_release import browser_required

    needs_browser = browser_required(
        verification["changed_files"],
        declared_visible=(
            outcome["release_impacts"]["member_visible_behavior"] == "YES"
        ),
    )
    expected_browser = "PASS" if needs_browser else verification["browser"]["status"]
    if (
        verification["status"] != "PASS"
        or verification["browser"]["status"] != expected_browser
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
    desktop = browser["desktop"][0]["path"] if needs_browser else None
    narrow = browser["narrow"][0]["path"] if needs_browser else None
    owner = {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": outcome["run_id"],
        "binding": outcome["binding"],
        "requested_outcome": outcome["requested_outcome"],
        "what_now_works": what_now_works,
        "what_did_not_change": list(
            dict.fromkeys(
                [
                    *outcome["visible_non_goals"],
                    "No release, deployment, provider write, or live household action was performed.",
                ]
            )
        ),
        "proof": {
            "deterministic": "PASS",
            "production_journey": "PASS",
            "browser": expected_browser,
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


def review_checkpoint(run_dir: Path, root: Path) -> dict[str, Any]:
    """Prove the cheap, exact-bound defect-discovery checkpoint is ready.

    Independent review belongs here, before breadth and release-candidate work.
    This function does not manufacture review evidence or mark the run green.
    """

    from .autonomous_release import browser_required
    from .checks import tier_checks

    validate_run(run_dir, root)
    verification = load_json(run_dir / "verification-report.json")
    outcome = load_json(run_dir / "outcome-contract.json")
    declared = {
        (check["tier"], check["check_id"]): check for check in verification["checks"]
    }
    gaps: list[str] = []

    def contains_undetermined(value: Any) -> bool:
        if isinstance(value, str):
            return "UNDETERMINED" in value
        if isinstance(value, list):
            return any(contains_undetermined(item) for item in value)
        if isinstance(value, dict):
            return any(contains_undetermined(item) for item in value.values())
        return False

    for artifact_name in (
        "outcome-contract.json",
        "evidence-inventory.json",
        "preservation-manifest.json",
    ):
        if contains_undetermined(load_json(run_dir / artifact_name)):
            gaps.append(f"contract/{artifact_name}")
    if "UNDETERMINED" in (run_dir / "implementation-plan.md").read_text("utf-8"):
        gaps.append("contract/implementation-plan.md")
    for tier in ("static", "focused", "journey"):
        for spec in tier_checks(tier, verification["changed_files"]):
            check = declared.get((spec.tier, spec.check_id))
            if (
                check is None
                or check["command"] != spec.command
                or check["status"] != "PASS"
                or check["exit_code"] != 0
            ):
                gaps.append(f"{spec.tier}/{spec.check_id}")
    if (
        browser_required(
            verification["changed_files"],
            declared_visible=(
                outcome["release_impacts"]["member_visible_behavior"] == "YES"
            ),
        )
        and verification["browser"]["status"] != "PASS"
    ):
        gaps.append("journey/gate7-browser")
    if any(check["status"] == "FAIL" for check in verification["checks"]):
        gaps.append("failed-current-check")
    return {
        "status": "READY" if not gaps else "NOT_READY",
        "run_id": outcome["run_id"],
        "binding": outcome["binding"],
        "changed_files": verification["changed_files"],
        "missing_or_failed": sorted(set(gaps)),
        "next": (
            "Run an independent exhaustive adversarial review now; do not start breadth."
            if not gaps
            else "Complete the exact static, focused, production-journey, and applicable browser checkpoint."
        ),
    }
