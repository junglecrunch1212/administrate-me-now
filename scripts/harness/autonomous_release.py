"""Fail-closed G-002 admission and settlement decisions.

The evaluator is deliberately content-free. It accepts only an exact candidate
identity, declared release-impact booleans, proof statuses, and capability IDs.
It never reads household records, credentials, provider payloads, or installed
release state on its own.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import uuid

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from .core import (
    HarnessError,
    canonical_json,
    load_json,
    g002_queue_root,
    repository_root,
    schema_root,
    utc_now,
    validate_run,
)


MANUAL_EXACT_PATHS = frozenset(
    {
        "AGENTS.md",
        "pyproject.toml",
        "poetry.lock",
        "adminme/events/log.py",
        "adminme/lib/governance.py",
        "adminme/lib/observation.py",
        "adminme/lib/scope.py",
        "adminme/lib/session.py",
        "adminme/openclaw_approvals.py",
        "adminme/products/auth.py",
        "adminme/products/bridge/auth.py",
        "bootstrap/backup_restore.py",
        "bootstrap/cli.py",
        "bootstrap/contracts.py",
        "bootstrap/doctor.py",
        "bootstrap/g002_bounded_repair.py",
        "bootstrap/google_oauth.py",
        "bootstrap/keychain_probe.py",
        "bootstrap/keychain_runtime_migration.py",
        "bootstrap/openclaw_activation.py",
        "bootstrap/secrets.py",
        "bootstrap/services.py",
        "bootstrap/source_assessment_repair.py",
        "bootstrap/update.sh",
        "bootstrap/upgrade.py",
        "bootstrap/versions.json",
        "docs/refactor/ACCEPTANCE-CONTRACT.md",
        "docs/refactor/ADR-0009-CONTROLLED-RELEASE-AUTHORITY.md",
        "docs/refactor/ADR-0011-AUTONOMOUS-OBSERVATION-RELEASE-TRAIN.md",
        "docs/refactor/CONSTITUTIONAL-CROSSWALK.md",
        "docs/refactor/MIGRATION-MAP.md",
        "scripts/acceptance/backup-restore.sh",
        "scripts/acceptance/macos-bootstrap-rehearsal.sh",
        "scripts/acceptance/release-candidate.sh",
        "scripts/acceptance/tenant-isolation.sh",
        "tests/acceptance/test_macos_bootstrap_operations.py",
        "tests/unit/bootstrap/test_g002_bounded_repair.py",
    }
)

MANUAL_PATH_PREFIXES = (
    ".agents/skills/",
    ".github/",
    "adminme/events/",
    "bootstrap/policy/",
    "schemas/harness/",
    "scripts/harness/",
    "tests/unit/harness/",
)

MEMBER_VISIBLE_PREFIXES = (
    "adminme/products/member/",
    "adminme/surface_contracts.py",
    "adminme/runtime/application.py",
    "adminme/daemons/adapter_supervisor.py",
    "bootstrap/doctor.py",
    "tools/console-reference-harness/",
    "tools/gate7_visual_server.py",
)

HIGH_RISK_IMPACTS = (
    "provider_writes_enabled",
    "runtime_or_dependency_migration",
    "provider_enrollment",
    "credentials_keychain_or_permissions",
    "privacy_or_authorization_change",
    "destructive_data_change",
    "observation_mode_change",
    "release_control_change",
)

CLOSED_QUEUE_DECISIONS = frozenset(
    {
        "SUPERSEDED",
        "G001_REQUIRED",
        "ADMISSION_DENIED",
        "COMPLETE",
        "ROLLED_BACK",
        "DEPLOYMENT_INCOMPLETE",
        "REPAIR_LIMIT_REACHED",
    }
)

OPEN_QUEUE_DECISIONS = frozenset({"WAITING_REVIEW", "WAITING_CHECKS"})

MANUAL_FILENAMES = frozenset(
    {
        ".node-version",
        ".nvmrc",
        ".python-version",
        ".tool-versions",
        "AGENTS.md",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }
)

MACOS_REQUIRED_PREFIXES = (
    "bootstrap/",
    "adminme/",
    "packs/adapters/",
    "openclaw/administrateme/",
    "scripts/acceptance/",
    "tests/unit/bootstrap/",
)

MACOS_REQUIRED_EXACT = frozenset(
    {
        ".github/workflows/macos-bootstrap.yml",
        "scripts/verify_invariants.sh",
        "tests/acceptance/test_macos_bootstrap_operations.py",
        "tests/integration/test_adapter_supervisor_gmail.py",
    }
)


def _matches(path: str, exact: frozenset[str], prefixes: tuple[str, ...]) -> bool:
    return path in exact or any(
        path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes
    )


def manual_release_paths(paths: list[str]) -> list[str]:
    """Return candidate paths that cannot use standing autonomous authority."""

    return sorted(
        path
        for path in paths
        if _matches(path, MANUAL_EXACT_PATHS, MANUAL_PATH_PREFIXES)
        or Path(path).name in MANUAL_FILENAMES
    )


def browser_required(paths: list[str], *, declared_visible: bool = False) -> bool:
    """Require browser proof when code can change a member-visible result."""

    return declared_visible or any(
        _matches(path, frozenset(), MEMBER_VISIBLE_PREFIXES) for path in paths
    )


def hosted_macos_required(paths: list[str]) -> bool:
    """Mirror the reviewed hosted macOS applicability boundary."""

    return any(
        path in MACOS_REQUIRED_EXACT
        or any(path.startswith(prefix) for prefix in MACOS_REQUIRED_PREFIXES)
        for path in paths
    )


def load_release_manifest(path: Path, root: Path | None = None) -> dict[str, Any]:
    value = load_json(path)
    schema_path = schema_root(root) / "autonomous-release.schema.json"
    schema = load_json(schema_path)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except (SchemaError, ValidationError) as exc:
        raise HarnessError(
            f"invalid autonomous release manifest: {exc.message}"
        ) from exc
    return value


def load_schema_value(path: Path, schema_name: str, root: Path | None = None) -> Any:
    """Load one auxiliary release artifact through its checked-in schema."""

    value = load_json(path)
    schema_path = schema_root(root) / schema_name
    schema = load_json(schema_path)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except (SchemaError, ValidationError) as exc:
        raise HarnessError(f"invalid {schema_name} artifact: {exc.message}") from exc
    return value


def _validate_schema_value(
    value: Any, schema_name: str, root: Path | None = None
) -> None:
    schema_path = schema_root(root) / schema_name
    schema = load_json(schema_path)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except (SchemaError, ValidationError) as exc:
        raise HarnessError(f"invalid {schema_name} artifact: {exc.message}") from exc


def _write_immutable_json(path: Path, value: Any, description: str) -> None:
    """Create one receipt without an overwrite race."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(canonical_json(value))
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise HarnessError(f"{description} path already exists") from exc
    path.chmod(0o400)


def _ensure_private_queue_directory(path: Path, parent: Path) -> Path:
    """Create or validate one owner-only queue directory without following links."""

    if path.is_symlink():
        raise HarnessError("G-002 queue directory must not be a symlink")
    if path.exists():
        if not path.is_dir():
            raise HarnessError("G-002 queue directory is not a directory")
    else:
        path.mkdir(mode=0o700)
    if path.resolve().parent != parent.resolve():
        raise HarnessError("G-002 queue directory escaped its enrolled parent")
    path.chmod(0o700)
    return path


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise HarnessError("release evidence timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise HarnessError("release evidence timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HarnessError("release evidence timestamp lacks a UTC offset")
    return parsed


def evaluate_release(value: dict[str, Any]) -> dict[str, Any]:
    """Derive ADMIT, COMPLETE, DENY, or ROLLBACK from declared exact proof."""

    lane = value["lane"]
    candidate = value["candidate"]
    impacts = value["impacts"]
    proof = value["proof"]
    baseline = value["baseline"]
    targets = set(value["target_degradations"])
    target_doctor_checks = set(value["target_doctor_checks"])
    baseline_unhealthy = set(baseline["required_unhealthy"])
    baseline_failed_checks = set(baseline["failed_doctor_checks"])
    reasons: list[str] = []

    forbidden = manual_release_paths(candidate["changed_paths"])
    if forbidden:
        reasons.append(
            "candidate changes manual-authority paths: " + ", ".join(forbidden)
        )
    risky = sorted(name for name in HIGH_RISK_IMPACTS if impacts[name])
    if risky:
        reasons.append("candidate declares high-risk impacts: " + ", ".join(risky))
    if not baseline["observation_active"]:
        reasons.append("observation mode is not active at baseline")
    if not baseline["doctor_available"]:
        reasons.append("baseline Doctor evidence is unavailable")
    if baseline["provider_claims"] != 0:
        reasons.append("baseline contains live provider claims")
    if _timestamp(proof["review_completed_at"]) > _timestamp(
        proof["breadth_started_at"]
    ):
        reasons.append("independent adversarial review began after breadth work")
    if (
        browser_required(
            candidate["changed_paths"],
            declared_visible=impacts["member_visible_behavior"],
        )
        and proof["browser"] != "PASS"
    ):
        reasons.append("member-visible candidate lacks browser proof")
    needs_macos = hosted_macos_required(candidate["changed_paths"])
    if needs_macos and proof["hosted_macos"] != "PASS":
        reasons.append("candidate lacks applicable hosted macOS proof")

    if lane == "observation":
        if targets:
            reasons.append("ordinary observation release declares repair targets")
        if target_doctor_checks:
            reasons.append("ordinary observation release declares target Doctor checks")
        if baseline["runtime"] != "healthy" or baseline_unhealthy:
            reasons.append("ordinary observation release requires a healthy baseline")
        if baseline_failed_checks:
            reasons.append("ordinary observation release has failed Doctor checks")
    else:
        if not targets:
            reasons.append("bounded repair declares no target degradation")
        missing_targets = sorted(targets - baseline_unhealthy)
        if missing_targets:
            reasons.append(
                "repair targets are absent from the baseline: "
                + ", ".join(missing_targets)
            )
        if baseline["runtime"] != "degraded":
            reasons.append("bounded repair requires a degraded baseline")
        if not target_doctor_checks:
            reasons.append("bounded repair declares no target Doctor check")
        missing_target_checks = sorted(target_doctor_checks - baseline_failed_checks)
        if missing_target_checks:
            reasons.append(
                "repair target Doctor checks are absent from the baseline: "
                + ", ".join(missing_target_checks)
            )

    if reasons:
        return {"status": "DENY", "lane": lane, "reasons": reasons}

    settlement = value.get("settlement")
    if settlement is None:
        return {
            "status": "ADMIT",
            "lane": lane,
            "reasons": [],
            "target_degradations": sorted(targets),
        }

    post_unhealthy = set(settlement["required_unhealthy"])
    post_failed_checks = set(settlement["failed_doctor_checks"])
    if settlement["active_head"] != settlement["merge_commit"]:
        reasons.append("active selector does not name the admitted merge commit")
    if not settlement["observation_active"]:
        reasons.append("observation mode was not retained")
    if not settlement["doctor_available"]:
        reasons.append("post-activation Doctor evidence is unavailable")
    if settlement["privacy_isolation"] != "PASS":
        reasons.append("privacy/isolation proof did not pass")
    needs_browser = browser_required(
        candidate["changed_paths"],
        declared_visible=impacts["member_visible_behavior"],
    )
    if needs_browser and settlement["browser"] != "PASS":
        reasons.append("required live browser acceptance did not pass")
    if not needs_browser and settlement["browser"] not in {
        "PASS",
        "NOT_APPLICABLE",
    }:
        reasons.append("non-applicable live browser evidence reports a failure")
    if not settlement["backup_verified"] or not settlement["rollback_ready"]:
        reasons.append("backup or rollback evidence is incomplete")
    if settlement["provider_effects_delta"] != 0:
        reasons.append("activation produced a provider effect")

    permitted_post = baseline_unhealthy - targets
    unresolved_targets = sorted(targets & post_unhealthy)
    new_degradations = sorted(post_unhealthy - permitted_post)
    permitted_post_checks = baseline_failed_checks - target_doctor_checks
    unresolved_target_checks = sorted(target_doctor_checks & post_failed_checks)
    new_failed_checks = sorted(post_failed_checks - permitted_post_checks)
    if unresolved_targets:
        reasons.append(
            "target degradations remain after settlement: "
            + ", ".join(unresolved_targets)
        )
    if new_degradations:
        reasons.append(
            "activation introduced or retained disallowed degradations: "
            + ", ".join(new_degradations)
        )
    if unresolved_target_checks:
        reasons.append(
            "target Doctor checks remain after settlement: "
            + ", ".join(unresolved_target_checks)
        )
    if new_failed_checks:
        reasons.append(
            "activation introduced or retained disallowed Doctor failures: "
            + ", ".join(new_failed_checks)
        )

    return {
        "status": "ROLLBACK" if reasons else "COMPLETE",
        "lane": lane,
        "reasons": reasons,
        "remaining_degradations": sorted(post_unhealthy),
    }


def _run_child(run_dir: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise HarnessError(f"release evidence must stay inside its run: {relative}")
    resolved = (run_dir / value).resolve()
    try:
        resolved.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise HarnessError(f"release evidence escapes its run: {relative}") from exc
    return resolved


def _receipt_start(run_dir: Path, check: dict[str, Any]) -> datetime:
    receipt = load_json(_run_child(run_dir, check["receipt"]))
    log = receipt.get("log")
    if not isinstance(log, dict) or not isinstance(log.get("path"), str):
        raise HarnessError(f"check receipt lacks timing log: {check['check_id']}")
    log_path = _run_child(run_dir, log["path"])
    first = log_path.read_text("utf-8").splitlines()[0]
    if not first.startswith("started_at="):
        raise HarnessError(f"check log lacks start time: {check['check_id']}")
    return _timestamp(first.removeprefix("started_at="))


def _gh_json(arguments: list[str]) -> Any:
    process = subprocess.run(
        ["gh", "api", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no diagnostic"
        raise HarnessError(f"cannot validate GitHub release evidence: {detail}")
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("GitHub release evidence is not valid JSON") from exc


def _validate_github_evidence(value: dict[str, Any]) -> None:
    github = value["github"]
    candidate = value["candidate"]
    repository = github["repository"]
    number = github["pull_request"]
    pull = _gh_json([f"repos/{repository}/pulls/{number}"])
    if (
        pull.get("head", {}).get("sha") != candidate["head_sha"]
        or pull.get("base", {}).get("ref") != "main"
        or pull.get("draft") is not False
        or pull.get("mergeable") is not True
        or pull.get("mergeable_state") != "clean"
    ):
        raise HarnessError("GitHub PR is not exact-head, ready, clean, and mergeable")

    main = _gh_json([f"repos/{repository}/commits/main"])
    main_sha = main.get("sha")
    if not isinstance(main_sha, str):
        raise HarnessError("GitHub current main SHA is unavailable")
    comparison = _gh_json(
        [f"repos/{repository}/compare/{main_sha}...{candidate['head_sha']}"]
    )
    if comparison.get("status") not in {"ahead", "identical"}:
        raise HarnessError("candidate does not contain current GitHub main")

    checks = _gh_json(
        [
            "--method",
            "GET",
            f"repos/{repository}/commits/{candidate['head_sha']}/check-runs",
            "-F",
            "per_page=100",
        ]
    )
    latest: dict[str, dict[str, Any]] = {}
    for check in checks.get("check_runs", []):
        name = check.get("name")
        if isinstance(name, str) and check.get("status") == "completed":
            if name not in latest or int(check.get("id", 0)) > int(
                latest[name].get("id", 0)
            ):
                latest[name] = check
    required = {"exact-head-review", "g002-deterministic-consistency"}
    if hosted_macos_required(candidate["changed_paths"]):
        required.add("g002-credential-free-rehearsal")
    failed = sorted(
        name for name in required if latest.get(name, {}).get("conclusion") != "success"
    )
    if failed:
        raise HarnessError(
            "required exact-head GitHub checks are not successful: " + ", ".join(failed)
        )

    owner, name = repository.split("/", 1)
    query = """
      query($owner:String!, $name:String!, $number:Int!) {
        repository(owner:$owner, name:$name) {
          pullRequest(number:$number) {
            reviewThreads(first:100) { nodes { isResolved } pageInfo { hasNextPage } }
          }
        }
      }
    """
    try:
        threads = _gh_json(
            [
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={number}",
            ]
        )["data"]["repository"]["pullRequest"]["reviewThreads"]
    except (KeyError, TypeError) as exc:
        raise HarnessError("GitHub review-thread evidence is unavailable") from exc
    if threads["pageInfo"]["hasNextPage"]:
        raise HarnessError("more than 100 review threads is UNDETERMINED")
    unresolved = sum(not node["isResolved"] for node in threads["nodes"])
    if unresolved or value["proof"]["actionable_threads"] != unresolved:
        raise HarnessError("actionable GitHub review threads remain")


def _validate_bound_harness(value: dict[str, Any], root: Path) -> None:
    """Reject a release decision that is not derived from exact Harness proof."""

    declared = value["harness"]
    run_dir = Path(declared["run_directory"]).expanduser().resolve()
    validate_run(run_dir, root)
    outcome = load_json(run_dir / "outcome-contract.json")
    verification = load_json(run_dir / "verification-report.json")
    review = load_json(run_dir / "review-report.json")
    bound = outcome["binding"]
    candidate = value["candidate"]
    mismatches: list[str] = []
    if outcome["run_id"] != declared["run_id"]:
        mismatches.append("run ID")
    if bound["diff_sha256"] != declared["binding_diff_sha256"]:
        mismatches.append("diff binding")
    if candidate["base_sha"] != bound["base_sha"]:
        mismatches.append("candidate base")
    if candidate["head_sha"] != bound["head_sha"]:
        mismatches.append("candidate head")
    if sorted(candidate["changed_paths"]) != sorted(verification["changed_files"]):
        mismatches.append("complete changed-path manifest")
    bound_impacts = {
        name: declared == "YES" for name, declared in outcome["release_impacts"].items()
    }
    if value["impacts"] != bound_impacts:
        mismatches.append("outcome-bound release impacts")
    if mismatches:
        raise HarnessError(
            "autonomous release contradicts its Harness run: " + ", ".join(mismatches)
        )
    if verification["status"] != "PASS" or review["conclusion"] != "APPROVE":
        raise HarnessError(
            "autonomous release requires a validated passing Harness run"
        )

    from .checks import tier_checks

    declared_checks = {
        (check["tier"], check["check_id"]): check for check in verification["checks"]
    }
    for tier in ("static", "focused", "journey", "breadth", "release"):
        for spec in tier_checks(tier, verification["changed_files"]):
            check = declared_checks.get((tier, spec.check_id))
            if (
                check is None
                or check["command"] != spec.command
                or check["status"] != "PASS"
                or check["exit_code"] != 0
            ):
                raise HarnessError(
                    f"autonomous release lacks exact passing {tier}/{spec.check_id}"
                )

    result_path = _run_child(run_dir, declared["hosted_review_result"])
    receipt_path = _run_child(run_dir, declared["hosted_review_receipt"])
    result = load_schema_value(result_path, "hosted-review-result.schema.json", root)
    receipt = load_schema_value(receipt_path, "hosted-review-receipt.schema.json", root)
    actionable = sorted(
        finding["finding_id"] for finding in result["findings"] if finding["actionable"]
    )
    if (
        receipt["head_sha"] != bound["head_sha"]
        or receipt["base_sha"] != bound["base_sha"]
        or receipt["diff_sha256"] != bound["diff_sha256"]
        or result["head_sha"] != bound["head_sha"]
        or result["base_sha"] != bound["base_sha"]
        or receipt["conclusion"] != "APPROVE"
        or result["conclusion"] != "APPROVE"
        or receipt["actionable_finding_ids"] != actionable
        or actionable
        or receipt["finding_count"] != len(result["findings"])
        or receipt["unresolved_evidence_count"] != len(result["unresolved_evidence"])
        or result["unresolved_evidence"]
    ):
        raise HarnessError("hosted review result or receipt is not exact and approving")

    breadth = [check for check in verification["checks"] if check["tier"] == "breadth"]
    if not breadth:
        raise HarnessError("autonomous release has no breadth start evidence")
    actual_review_completed = _timestamp(receipt["review_completed_at"])
    actual_breadth_started = min(_receipt_start(run_dir, check) for check in breadth)
    proof = value["proof"]
    if (
        _timestamp(proof["review_completed_at"]) != actual_review_completed
        or _timestamp(proof["breadth_started_at"]) != actual_breadth_started
        or actual_review_completed > actual_breadth_started
    ):
        raise HarnessError("hosted review did not precede exact breadth execution")


def evaluate_manifest(path: Path, root: Path | None = None) -> dict[str, Any]:
    repository = root or repository_root()
    value = load_release_manifest(path, repository)
    _validate_bound_harness(value, repository)
    _validate_github_evidence(value)
    return evaluate_release(value)


def queue_g002_candidate(
    run_dir: Path,
    *,
    repository: str,
    pull_request: int,
    lane: str,
    target_degradations: list[str],
    target_doctor_checks: list[str],
    output_path: Path,
    root: Path | None = None,
) -> dict[str, Any]:
    """Write one immutable exact-head handoff for the local release operator."""

    from .run_artifacts import review_checkpoint

    repository_root_path = root or repository_root()
    checkpoint = review_checkpoint(run_dir, repository_root_path)
    if checkpoint["status"] != "READY":
        raise HarnessError("G-002 candidate is not review-ready")
    outcome = load_json(run_dir / "outcome-contract.json")
    verification = load_json(run_dir / "verification-report.json")
    impacts = {
        name: declared == "YES" for name, declared in outcome["release_impacts"].items()
    }
    risky = sorted(name for name in HIGH_RISK_IMPACTS if impacts[name])
    manual = manual_release_paths(verification["changed_files"])
    if risky or manual:
        detail = [
            *(f"impact:{name}" for name in risky),
            *(f"path:{path}" for path in manual),
        ]
        raise HarnessError(
            "candidate requires manual G-001 authority: " + ", ".join(detail)
        )
    if lane not in {"observation", "bounded_repair"}:
        raise HarnessError("unknown G-002 release lane")
    if lane == "observation" and (target_degradations or target_doctor_checks):
        raise HarnessError("ordinary observation queue entry cannot declare targets")
    if lane == "bounded_repair" and (
        not target_degradations or not target_doctor_checks
    ):
        raise HarnessError("bounded-repair queue entry requires exact targets")
    if len(set(target_degradations)) != len(target_degradations) or len(
        set(target_doctor_checks)
    ) != len(target_doctor_checks):
        raise HarnessError("G-002 queue target IDs must be unique")

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root_path,
        text=True,
        timeout=15,
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository_root_path,
        text=True,
        timeout=15,
    )
    if status:
        raise HarnessError("G-002 queue requires a clean committed candidate checkout")
    if checkpoint["binding"]["head_sha"] != head:
        raise HarnessError(
            "G-002 queue run is not bound to the committed candidate head"
        )

    pull = _gh_json([f"repos/{repository}/pulls/{pull_request}"])
    if (
        pull.get("head", {}).get("sha") != head
        or pull.get("base", {}).get("ref") != "main"
        or pull.get("state") != "open"
    ):
        raise HarnessError("G-002 queue PR is not open at the exact candidate head")
    enrolled_root = g002_queue_root(repository_root_path)
    assert enrolled_root is not None
    entries_root = _ensure_private_queue_directory(
        enrolled_root / "entries", enrolled_root
    )
    runs_root = _ensure_private_queue_directory(enrolled_root / "runs", enrolled_root)
    resolved_output = output_path.resolve()
    if resolved_output.parent != entries_root.resolve():
        raise HarnessError("G-002 queue entry must be written under its enrolled root")
    persisted_run = runs_root / (
        f"{run_dir.name}-{checkpoint['binding']['diff_sha256'][:16]}"
    )
    if persisted_run.exists():
        raise HarnessError("persisted G-002 Harness run already exists")
    if any(child.is_symlink() for child in run_dir.rglob("*")):
        raise HarnessError("G-002 Harness persistence refuses symlinks")
    try:
        shutil.copytree(run_dir, persisted_run)
        for child in persisted_run.rglob("*"):
            child.chmod(0o700 if child.is_dir() else 0o600)
        persisted_run.chmod(0o700)
        validate_run(persisted_run, repository_root_path)
    except Exception:
        if persisted_run.exists():
            shutil.rmtree(persisted_run)
        raise

    entry = {
        "schema_version": 1,
        "status": "QUEUED",
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": checkpoint["binding"]["base_sha"],
        "head_sha": head,
        "changed_paths": sorted(verification["changed_files"]),
        "run_directory": str(persisted_run.resolve()),
        "run_id": checkpoint["run_id"],
        "binding_diff_sha256": checkpoint["binding"]["diff_sha256"],
        "lane": lane,
        "target_degradations": sorted(target_degradations),
        "target_doctor_checks": sorted(target_doctor_checks),
        "impacts": impacts,
        "queued_at": utc_now(),
    }
    try:
        _validate_schema_value(
            entry, "g002-release-queue.schema.json", repository_root_path
        )
        _write_immutable_json(resolved_output, entry, "G-002 release queue")
    except Exception:
        shutil.rmtree(persisted_run)
        raise
    return entry


def validate_g002_queue(queue_path: Path, root: Path | None = None) -> dict[str, Any]:
    """Revalidate an exact queue handoff against its checkout, run, and PR."""

    repository_root_path = root or repository_root()
    enrolled_root = g002_queue_root(repository_root_path)
    assert enrolled_root is not None
    entries_root = enrolled_root / "entries"
    if (
        entries_root.is_symlink()
        or not entries_root.is_dir()
        or queue_path.is_symlink()
        or queue_path.resolve().parent != entries_root.resolve()
        or queue_path.stat().st_mode & 0o777 != 0o400
    ):
        raise HarnessError("G-002 queue entry is not an immutable enrolled handoff")
    queue = load_schema_value(
        queue_path, "g002-release-queue.schema.json", repository_root_path
    )
    run_dir = Path(queue["run_directory"]).resolve()
    runs_root = enrolled_root / "runs"
    if (
        runs_root.is_symlink()
        or not runs_root.is_dir()
        or Path(queue["run_directory"]).is_symlink()
        or run_dir.parent != runs_root.resolve()
    ):
        raise HarnessError("G-002 queue run is not its enrolled durable snapshot")
    validate_run(run_dir, repository_root_path)
    outcome = load_json(run_dir / "outcome-contract.json")
    verification = load_json(run_dir / "verification-report.json")
    bound = outcome["binding"]
    bound_impacts = {
        name: declared == "YES" for name, declared in outcome["release_impacts"].items()
    }
    mismatches: list[str] = []
    if queue["run_id"] != outcome["run_id"]:
        mismatches.append("run ID")
    if queue["base_sha"] != bound["base_sha"]:
        mismatches.append("base SHA")
    if queue["head_sha"] != bound["head_sha"]:
        mismatches.append("head SHA")
    if queue["binding_diff_sha256"] != bound["diff_sha256"]:
        mismatches.append("diff binding")
    if queue["changed_paths"] != sorted(verification["changed_files"]):
        mismatches.append("complete changed paths")
    if queue["impacts"] != bound_impacts:
        mismatches.append("outcome-bound impacts")
    if mismatches:
        raise HarnessError(
            "G-002 queue contradicts its Harness run: " + ", ".join(mismatches)
        )

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root_path,
        text=True,
        timeout=15,
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository_root_path,
        text=True,
        timeout=15,
    )
    if head != queue["head_sha"] or status:
        raise HarnessError(
            "G-002 queue checkout is not clean at its exact committed head"
        )
    pull = _gh_json([f"repos/{queue['repository']}/pulls/{queue['pull_request']}"])
    if (
        pull.get("head", {}).get("sha") != queue["head_sha"]
        or pull.get("base", {}).get("ref") != "main"
        or pull.get("state") != "open"
    ):
        raise HarnessError("G-002 queue PR no longer matches its exact handoff")
    return queue


def record_g002_operator_attempt(
    queue_path: Path,
    *,
    decision: str,
    merge_commit: str | None,
    deployed_sha: str | None,
    settlement_manifest_path: Path | None,
    rollback_result: str,
    remaining_degradations: list[str],
    remaining_doctor_checks: list[str],
    reason_codes: list[str],
    elapsed_seconds: float,
    root: Path | None = None,
) -> dict[str, Any]:
    """Append one content-free immutable scheduled-run result for a queue entry."""

    repository_root_path = root or repository_root()
    enrolled_root = g002_queue_root(repository_root_path)
    assert enrolled_root is not None
    entries_root = enrolled_root / "entries"
    if entries_root.is_symlink() or not entries_root.is_dir():
        raise HarnessError("G-002 queue entries directory is invalid")
    resolved_queue = queue_path.resolve()
    if (
        resolved_queue.parent != entries_root.resolve()
        or queue_path.is_symlink()
        or queue_path.stat().st_mode & 0o777 != 0o400
    ):
        raise HarnessError("G-002 attempt does not reference an enrolled queue entry")
    queue = load_schema_value(
        resolved_queue, "g002-release-queue.schema.json", repository_root_path
    )
    if decision not in CLOSED_QUEUE_DECISIONS | OPEN_QUEUE_DECISIONS:
        raise HarnessError("unknown G-002 operator decision")
    queue_closed = decision in CLOSED_QUEUE_DECISIONS
    if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise HarnessError("G-002 attempt elapsed time is invalid")
    queue_digest = hashlib.sha256(resolved_queue.read_bytes()).hexdigest()
    attempts_root = _ensure_private_queue_directory(
        enrolled_root / "attempts", enrolled_root
    )
    attempt_dir = _ensure_private_queue_directory(
        attempts_root / queue_digest, attempts_root
    )
    previous_attempts = _load_g002_attempt_history(
        attempt_dir,
        queue=queue,
        queue_digest=queue_digest,
        enrolled_root=enrolled_root,
        repository_root_path=repository_root_path,
    )
    if any(previous["queue_closed"] for previous in previous_attempts):
        raise HarnessError("G-002 queue is already closed")

    settlement_output: Path | None = None
    settlement_digest: str | None = None
    settled: dict[str, Any] | None = None
    if decision == "COMPLETE":
        if (
            merge_commit is None
            or deployed_sha != merge_commit
            or settlement_manifest_path is None
            or settlement_manifest_path.is_symlink()
            or rollback_result != "NOT_APPLICABLE"
            or remaining_degradations
            or remaining_doctor_checks
            or reason_codes
        ):
            raise HarnessError("COMPLETE G-002 attempt contradicts release settlement")
        settled = load_release_manifest(settlement_manifest_path, repository_root_path)
        result = evaluate_release(settled)
        if (
            result["status"] != "COMPLETE"
            or settled["github"]["repository"] != queue["repository"]
            or settled["github"]["pull_request"] != queue["pull_request"]
            or settled["candidate"]["base_sha"] != queue["base_sha"]
            or settled["candidate"]["head_sha"] != queue["head_sha"]
            or settled["harness"]["run_id"] != queue["run_id"]
            or settled["harness"]["binding_diff_sha256"] != queue["binding_diff_sha256"]
            or settled.get("settlement", {}).get("merge_commit") != merge_commit
        ):
            raise HarnessError("COMPLETE attempt lacks its exact settlement manifest")
        _validate_bound_harness(settled, repository_root_path)
        _validate_exact_github_merge(settled, merge_commit)
    elif not reason_codes:
        raise HarnessError("non-complete G-002 attempt requires a reason code")
    elif settlement_manifest_path is not None:
        raise HarnessError("non-complete G-002 attempt received a settlement manifest")
    if decision == "ROLLED_BACK" and rollback_result != "PASS":
        raise HarnessError("ROLLED_BACK attempt requires passing rollback evidence")
    if deployed_sha is not None and merge_commit != deployed_sha:
        raise HarnessError("G-002 deployed SHA must be the exact merge commit")

    identifier = f"{time.time_ns()}-{uuid.uuid4().hex}"
    if settled is not None:
        settlements_root = _ensure_private_queue_directory(
            enrolled_root / "settlements", enrolled_root
        )
        settlement_dir = _ensure_private_queue_directory(
            settlements_root / queue_digest, settlements_root
        )
        settlement_output = settlement_dir / f"{identifier}.json"
        _write_immutable_json(
            settlement_output, settled, "G-002 exact settlement manifest"
        )
        settlement_digest = hashlib.sha256(settlement_output.read_bytes()).hexdigest()

    attempt = {
        "schema_version": 1,
        "queue_sha256": queue_digest,
        "repository": queue["repository"],
        "pull_request": queue["pull_request"],
        "base_sha": queue["base_sha"],
        "head_sha": queue["head_sha"],
        "run_id": queue["run_id"],
        "binding_diff_sha256": queue["binding_diff_sha256"],
        "decision": decision,
        "queue_closed": queue_closed,
        "merge_commit": merge_commit,
        "deployed_sha": deployed_sha,
        "settlement_manifest_path": (
            str(settlement_output.resolve()) if settlement_output is not None else None
        ),
        "settlement_manifest_sha256": settlement_digest,
        "rollback_result": rollback_result,
        "remaining_degradations": sorted(set(remaining_degradations)),
        "remaining_doctor_checks": sorted(set(remaining_doctor_checks)),
        "reason_codes": sorted(set(reason_codes)),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "recorded_at": utc_now(),
    }
    output = attempt_dir / f"{identifier}.json"
    try:
        _validate_schema_value(
            attempt, "g002-operator-attempt.schema.json", repository_root_path
        )
        _validate_g002_attempt(
            attempt,
            queue=queue,
            queue_digest=queue_digest,
            enrolled_root=enrolled_root,
            repository_root_path=repository_root_path,
        )
        _write_immutable_json(output, attempt, "G-002 operator attempt")
    except Exception:
        if settlement_output is not None:
            settlement_output.unlink(missing_ok=True)
        raise
    return {**attempt, "path": str(output)}


def _validate_g002_attempt(
    attempt: dict[str, Any],
    *,
    queue: dict[str, Any],
    queue_digest: str,
    enrolled_root: Path,
    repository_root_path: Path,
) -> None:
    identity_mismatch = (
        attempt["queue_sha256"] != queue_digest
        or attempt["repository"] != queue["repository"]
        or attempt["pull_request"] != queue["pull_request"]
        or attempt["base_sha"] != queue["base_sha"]
        or attempt["head_sha"] != queue["head_sha"]
        or attempt["run_id"] != queue["run_id"]
        or attempt["binding_diff_sha256"] != queue["binding_diff_sha256"]
    )
    if identity_mismatch:
        raise HarnessError("G-002 operator attempt contradicts its queue")
    decision = attempt["decision"]
    if attempt["queue_closed"] != (decision in CLOSED_QUEUE_DECISIONS):
        raise HarnessError("G-002 operator attempt has a false queue state")
    elapsed = attempt["elapsed_seconds"]
    if not math.isfinite(elapsed) or elapsed < 0:
        raise HarnessError("G-002 attempt elapsed time is invalid")
    if attempt["deployed_sha"] is not None and (
        attempt["merge_commit"] != attempt["deployed_sha"]
    ):
        raise HarnessError("G-002 deployed SHA must be the exact merge commit")

    if decision != "COMPLETE":
        if (
            attempt["settlement_manifest_path"] is not None
            or attempt["settlement_manifest_sha256"] is not None
            or not attempt["reason_codes"]
        ):
            raise HarnessError("non-complete G-002 attempt has invalid evidence")
        if decision == "ROLLED_BACK" and attempt["rollback_result"] != "PASS":
            raise HarnessError("ROLLED_BACK attempt requires passing rollback evidence")
        return

    settlement_value = attempt["settlement_manifest_path"]
    if not isinstance(settlement_value, str):
        raise HarnessError("COMPLETE G-002 attempt lacks retained settlement evidence")
    settlement_path = Path(settlement_value)
    expected_parent = (enrolled_root / "settlements" / queue_digest).resolve()
    if (
        not settlement_path.is_absolute()
        or settlement_path.is_symlink()
        or not settlement_path.is_file()
        or settlement_path.stat().st_mode & 0o777 != 0o400
        or settlement_path.resolve().parent != expected_parent
        or hashlib.sha256(settlement_path.read_bytes()).hexdigest()
        != attempt["settlement_manifest_sha256"]
    ):
        raise HarnessError("COMPLETE G-002 settlement evidence is invalid")
    settled = load_release_manifest(settlement_path, repository_root_path)
    if (
        attempt["merge_commit"] is None
        or attempt["deployed_sha"] != attempt["merge_commit"]
        or attempt["rollback_result"] != "NOT_APPLICABLE"
        or attempt["remaining_degradations"]
        or attempt["remaining_doctor_checks"]
        or attempt["reason_codes"]
        or evaluate_release(settled)["status"] != "COMPLETE"
        or settled["github"]["repository"] != queue["repository"]
        or settled["github"]["pull_request"] != queue["pull_request"]
        or settled["candidate"]["base_sha"] != queue["base_sha"]
        or settled["candidate"]["head_sha"] != queue["head_sha"]
        or settled["harness"]["run_id"] != queue["run_id"]
        or settled["harness"]["binding_diff_sha256"] != queue["binding_diff_sha256"]
        or settled.get("settlement", {}).get("merge_commit") != attempt["merge_commit"]
    ):
        raise HarnessError("COMPLETE attempt lacks its exact settlement manifest")


def _load_g002_attempt_history(
    attempt_dir: Path,
    *,
    queue: dict[str, Any],
    queue_digest: str,
    enrolled_root: Path,
    repository_root_path: Path,
) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for previous_path in sorted(attempt_dir.iterdir()):
        if (
            previous_path.is_symlink()
            or not previous_path.is_file()
            or previous_path.suffix != ".json"
            or previous_path.stat().st_mode & 0o777 != 0o400
        ):
            raise HarnessError("G-002 attempt history has non-canonical contents")
        previous = load_schema_value(
            previous_path,
            "g002-operator-attempt.schema.json",
            repository_root_path,
        )
        _validate_g002_attempt(
            previous,
            queue=queue,
            queue_digest=queue_digest,
            enrolled_root=enrolled_root,
            repository_root_path=repository_root_path,
        )
        attempts.append(previous)
    return attempts


def next_g002_queue(root: Path | None = None) -> dict[str, Any]:
    """Select the oldest valid queue entry that has no immutable closing attempt."""

    repository_root_path = root or repository_root()
    enrolled_root = g002_queue_root(repository_root_path)
    assert enrolled_root is not None
    entries_root = enrolled_root / "entries"
    if entries_root.is_symlink():
        raise HarnessError("G-002 queue directory must not be a symlink")
    if not entries_root.exists():
        return {"status": "NO_WORK"}
    if not entries_root.is_dir() or entries_root.resolve().parent != enrolled_root:
        raise HarnessError("G-002 queue directory is not an enrolled directory")
    entry_paths = sorted(entries_root.iterdir())
    if any(
        path.is_symlink()
        or not path.is_file()
        or path.suffix != ".json"
        or path.stat().st_mode & 0o777 != 0o400
        for path in entry_paths
    ):
        raise HarnessError("G-002 queue directory has non-canonical contents")
    candidates: list[tuple[datetime, Path, dict[str, Any], str]] = []
    for queue_path in entry_paths:
        queue = load_schema_value(
            queue_path, "g002-release-queue.schema.json", repository_root_path
        )
        digest = hashlib.sha256(queue_path.read_bytes()).hexdigest()
        attempt_dir = enrolled_root / "attempts" / digest
        closing = 0
        if attempt_dir.exists():
            if not attempt_dir.is_dir() or attempt_dir.is_symlink():
                raise HarnessError("G-002 attempt history is not a directory")
            for attempt in _load_g002_attempt_history(
                attempt_dir,
                queue=queue,
                queue_digest=digest,
                enrolled_root=enrolled_root,
                repository_root_path=repository_root_path,
            ):
                closing += int(attempt["queue_closed"])
        if closing > 1:
            raise HarnessError("G-002 queue has multiple closing attempts")
        if not closing:
            candidates.append(
                (_timestamp(queue["queued_at"]), queue_path, queue, digest)
            )
    if not candidates:
        return {"status": "NO_WORK"}
    _, queue_path, queue, digest = min(candidates, key=lambda item: item[0])
    return {
        "status": "QUEUED",
        "path": str(queue_path),
        "queue_sha256": digest,
        "queue": queue,
    }


def _content_free_live_baseline(
    doctor: dict[str, Any], diagnostics: dict[str, Any]
) -> dict[str, Any]:
    checks = doctor.get("checks")
    capabilities = diagnostics.get("capabilities")
    claims = doctor.get("live_provider_claims")
    if (
        not isinstance(checks, list)
        or not isinstance(capabilities, dict)
        or not isinstance(claims, list)
    ):
        raise HarnessError("live Doctor or capability evidence is unavailable")
    failed_checks: list[str] = []
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise HarnessError("live Doctor check evidence is malformed")
        if check.get("ok") is not True:
            failed_checks.append(check["name"])
    unhealthy: list[str] = []
    for capability_id, capability in capabilities.items():
        if not isinstance(capability_id, str) or not isinstance(capability, dict):
            raise HarnessError("live capability evidence is malformed")
        if capability.get("state") in {"degraded", "blocked"}:
            unhealthy.append(capability_id)
    if doctor.get("mode") != "observation":
        raise HarnessError("live Doctor does not attest observation mode")
    if diagnostics.get("observation_active") is not True:
        raise HarnessError("live runtime observation mode is not active")
    backup = next(
        (
            check
            for check in checks
            if check.get("name") == "disk_space_and_backup_target"
        ),
        None,
    )
    if not isinstance(backup, dict) or backup.get("ok") is not True:
        raise HarnessError("verified backup capability is unavailable")
    runtime = (
        "healthy"
        if doctor.get("status") == "healthy" and not unhealthy and not failed_checks
        else "degraded"
    )
    return {
        "runtime": runtime,
        "observation_active": True,
        "required_unhealthy": sorted(set(unhealthy)),
        "failed_doctor_checks": sorted(set(failed_checks)),
        "doctor_available": True,
        "provider_claims": len(claims),
    }


def _fresh_live_baseline(doctor_path: Path, diagnostics_path: Path) -> dict[str, Any]:
    """Require one recent, coherent, content-free admission observation."""

    doctor = load_json(doctor_path)
    diagnostics = load_json(diagnostics_path)
    doctor_at = _timestamp(doctor.get("observed_at"))
    diagnostics_at = _timestamp(diagnostics.get("observed_at"))
    now = _timestamp(utc_now())
    newest = max(doctor_at, diagnostics_at)
    oldest = min(doctor_at, diagnostics_at)
    if newest - oldest > timedelta(seconds=60):
        raise HarnessError("live admission observations are not one coherent snapshot")
    if now - oldest > timedelta(minutes=5) or newest - now > timedelta(seconds=30):
        raise HarnessError("live admission evidence is stale or future-dated")
    return _content_free_live_baseline(doctor, diagnostics)


def _latest_hosted_review(
    run_dir: Path, root: Path
) -> tuple[Path, Path, dict[str, Any]]:
    attempts = run_dir / "hosted-review" / "attempts"
    candidates: list[tuple[datetime, Path, Path, dict[str, Any]]] = []
    if attempts.is_dir():
        for attempt in attempts.iterdir():
            result_path = attempt / "result.json"
            receipt_path = attempt / "receipt.json"
            if not result_path.is_file() or not receipt_path.is_file():
                continue
            result = load_schema_value(
                result_path, "hosted-review-result.schema.json", root
            )
            receipt = load_schema_value(
                receipt_path, "hosted-review-receipt.schema.json", root
            )
            if result["conclusion"] == "APPROVE" and receipt["conclusion"] == "APPROVE":
                candidates.append(
                    (
                        _timestamp(receipt["review_completed_at"]),
                        result_path,
                        receipt_path,
                        receipt,
                    )
                )
    if not candidates:
        raise HarnessError("no imported approving hosted review is available")
    _, result_path, receipt_path, receipt = max(candidates, key=lambda item: item[0])
    return result_path, receipt_path, receipt


def prepare_release_manifest(
    queue_path: Path,
    *,
    doctor_path: Path,
    diagnostics_path: Path,
    output_path: Path,
    root: Path | None = None,
) -> dict[str, Any]:
    """Derive the admission manifest from exact queue, Harness, and live facts."""

    repository_root_path = root or repository_root()
    queue = validate_g002_queue(queue_path, repository_root_path)
    run_dir = Path(queue["run_directory"]).resolve()
    validate_run(run_dir, repository_root_path)
    outcome = load_json(run_dir / "outcome-contract.json")
    verification = load_json(run_dir / "verification-report.json")
    review = load_json(run_dir / "review-report.json")
    bound = outcome["binding"]
    bound_impacts = {
        name: declared == "YES" for name, declared in outcome["release_impacts"].items()
    }
    if (
        queue["run_id"] != outcome["run_id"]
        or queue["base_sha"] != bound["base_sha"]
        or queue["head_sha"] != bound["head_sha"]
        or queue["binding_diff_sha256"] != bound["diff_sha256"]
        or queue["changed_paths"] != sorted(verification["changed_files"])
        or queue["impacts"] != bound_impacts
    ):
        raise HarnessError("G-002 queue no longer matches its exact Harness run")
    if review["conclusion"] != "APPROVE" or review["unresolved_evidence"]:
        raise HarnessError("G-002 queue has no canonical approving review")

    declared = {
        (check["tier"], check["check_id"]): check for check in verification["checks"]
    }
    for tier in ("static", "focused", "journey", "breadth", "release"):
        from .checks import tier_checks

        for spec in tier_checks(tier, verification["changed_files"]):
            check = declared.get((tier, spec.check_id))
            if (
                check is None
                or check["command"] != spec.command
                or check["status"] != "PASS"
                or check["exit_code"] != 0
            ):
                raise HarnessError(
                    f"G-002 manifest lacks passing {tier}/{spec.check_id}"
                )

    result_path, receipt_path, receipt = _latest_hosted_review(
        run_dir, repository_root_path
    )
    breadth = [check for check in verification["checks"] if check["tier"] == "breadth"]
    if not breadth:
        raise HarnessError("G-002 manifest lacks breadth timing evidence")
    baseline = _fresh_live_baseline(doctor_path, diagnostics_path)
    entry = {
        "schema_version": 1,
        "lane": queue["lane"],
        "candidate": {
            "base_sha": queue["base_sha"],
            "head_sha": queue["head_sha"],
            "changed_paths": queue["changed_paths"],
        },
        "github": {
            "repository": queue["repository"],
            "pull_request": queue["pull_request"],
        },
        "harness": {
            "run_directory": str(run_dir),
            "run_id": queue["run_id"],
            "binding_diff_sha256": queue["binding_diff_sha256"],
            "hosted_review_result": result_path.relative_to(run_dir).as_posix(),
            "hosted_review_receipt": receipt_path.relative_to(run_dir).as_posix(),
        },
        "impacts": queue["impacts"],
        "proof": {
            "exact_head_review": "APPROVE",
            "deterministic": "PASS",
            "production_journey": "PASS",
            "browser": verification["browser"]["status"],
            "breadth": "PASS",
            "release_candidate": "PASS",
            "hosted_consistency": "PASS",
            "hosted_macos": (
                "PASS"
                if hosted_macos_required(queue["changed_paths"])
                else "NOT_APPLICABLE"
            ),
            "base_contained": True,
            "clean_mergeable": True,
            "actionable_threads": 0,
            "review_completed_at": receipt["review_completed_at"],
            "breadth_started_at": min(
                _receipt_start(run_dir, check) for check in breadth
            ).isoformat(),
            "attested_updater": True,
            "backup_capable": True,
        },
        "baseline": baseline,
        "target_degradations": queue["target_degradations"],
        "target_doctor_checks": queue["target_doctor_checks"],
    }
    _validate_schema_value(
        entry, "autonomous-release.schema.json", repository_root_path
    )
    _write_immutable_json(output_path, entry, "G-002 release manifest")
    return entry


def evaluate_and_record_admission(
    manifest_path: Path,
    receipt_path: Path,
    root: Path | None = None,
) -> dict[str, Any]:
    """Evaluate once and emit an immutable content-free receipt only for ADMIT."""

    repository = root or repository_root()
    result = evaluate_manifest(manifest_path, repository)
    if result["status"] != "ADMIT":
        return result
    manifest = load_release_manifest(manifest_path, repository)
    receipt = {
        "schema_version": 1,
        "decision": "ADMIT",
        "lane": manifest["lane"],
        "repository": manifest["github"]["repository"],
        "pull_request": manifest["github"]["pull_request"],
        "base_sha": manifest["candidate"]["base_sha"],
        "head_sha": manifest["candidate"]["head_sha"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "run_id": manifest["harness"]["run_id"],
        "binding_diff_sha256": manifest["harness"]["binding_diff_sha256"],
        "target_degradations": sorted(manifest["target_degradations"]),
        "target_doctor_checks": sorted(manifest["target_doctor_checks"]),
        "admitted_at": utc_now(),
    }
    _validate_schema_value(receipt, "g002-admission-receipt.schema.json", repository)
    _write_immutable_json(receipt_path, receipt, "G-002 admission receipt")
    return {**result, "receipt": str(receipt_path)}


def prepare_bounded_repair_policy(
    manifest_path: Path,
    receipt_path: Path,
    *,
    merge_commit: str,
    output_path: Path,
    root: Path | None = None,
) -> dict[str, Any]:
    """Bind an admitted repair to GitHub's exact two-parent merge commit."""

    repository_root_path = root or repository_root()
    manifest = load_release_manifest(manifest_path, repository_root_path)
    receipt = load_schema_value(
        receipt_path, "g002-admission-receipt.schema.json", repository_root_path
    )
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if (
        manifest["lane"] != "bounded_repair"
        or receipt["decision"] != "ADMIT"
        or receipt["lane"] != manifest["lane"]
        or receipt["repository"] != manifest["github"]["repository"]
        or receipt["pull_request"] != manifest["github"]["pull_request"]
        or receipt["base_sha"] != manifest["candidate"]["base_sha"]
        or receipt["head_sha"] != manifest["candidate"]["head_sha"]
        or receipt["manifest_sha256"] != manifest_digest
        or receipt["run_id"] != manifest["harness"]["run_id"]
        or receipt["binding_diff_sha256"] != manifest["harness"]["binding_diff_sha256"]
        or receipt["target_degradations"] != sorted(manifest["target_degradations"])
        or receipt["target_doctor_checks"] != sorted(manifest["target_doctor_checks"])
    ):
        raise HarnessError("G-002 admission receipt contradicts its repair manifest")
    if len(merge_commit) != 40 or any(
        character not in "0123456789abcdef" for character in merge_commit
    ):
        raise HarnessError("G-002 merge commit is not a 40-character SHA")

    number = manifest["github"]["pull_request"]
    parents = _validate_exact_github_merge(manifest, merge_commit)

    policy = {
        "contract": "g002-bounded-repair-activation-v1",
        "pull_request": number,
        "merge_commit": merge_commit,
        "merge_base_parent": parents[0],
        "reviewed_head": manifest["candidate"]["head_sha"],
        "admission_manifest_sha256": manifest_digest,
        "observation_mode_required": True,
        "provider_claims_required": 0,
        "baseline_required_unhealthy": sorted(
            manifest["baseline"]["required_unhealthy"]
        ),
        "target_degradations": sorted(manifest["target_degradations"]),
        "baseline_failed_doctor_checks": sorted(
            manifest["baseline"]["failed_doctor_checks"]
        ),
        "target_doctor_checks": sorted(manifest["target_doctor_checks"]),
    }
    _validate_schema_value(
        policy, "g002-bounded-repair-policy.schema.json", repository_root_path
    )
    from bootstrap.g002_bounded_repair import G002BoundedRepairPolicy

    try:
        G002BoundedRepairPolicy.model_validate(policy)
    except Exception as exc:
        raise HarnessError("generated G-002 bounded-repair policy is invalid") from exc
    _write_immutable_json(output_path, policy, "G-002 bounded-repair policy")
    return policy


def prepare_observation_release_policy(
    manifest_path: Path,
    receipt_path: Path,
    *,
    merge_commit: str,
    output_path: Path,
    root: Path | None = None,
) -> dict[str, Any]:
    """Bind an admitted healthy observation release to its exact merge."""

    repository_root_path = root or repository_root()
    manifest = load_release_manifest(manifest_path, repository_root_path)
    receipt = load_schema_value(
        receipt_path, "g002-admission-receipt.schema.json", repository_root_path
    )
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if (
        manifest["lane"] != "observation"
        or manifest["target_degradations"]
        or manifest["target_doctor_checks"]
        or manifest["baseline"]["required_unhealthy"]
        or manifest["baseline"]["failed_doctor_checks"]
        or receipt["decision"] != "ADMIT"
        or receipt["lane"] != manifest["lane"]
        or receipt["repository"] != manifest["github"]["repository"]
        or receipt["pull_request"] != manifest["github"]["pull_request"]
        or receipt["base_sha"] != manifest["candidate"]["base_sha"]
        or receipt["head_sha"] != manifest["candidate"]["head_sha"]
        or receipt["manifest_sha256"] != manifest_digest
        or receipt["run_id"] != manifest["harness"]["run_id"]
        or receipt["binding_diff_sha256"] != manifest["harness"]["binding_diff_sha256"]
        or receipt["target_degradations"]
        or receipt["target_doctor_checks"]
    ):
        raise HarnessError(
            "G-002 admission receipt contradicts its observation manifest"
        )
    if len(merge_commit) != 40 or any(
        character not in "0123456789abcdef" for character in merge_commit
    ):
        raise HarnessError("G-002 merge commit is not a 40-character SHA")
    parents = _validate_exact_github_merge(manifest, merge_commit)
    policy = {
        "contract": "g002-observation-release-activation-v1",
        "pull_request": manifest["github"]["pull_request"],
        "merge_commit": merge_commit,
        "merge_base_parent": parents[0],
        "reviewed_head": manifest["candidate"]["head_sha"],
        "admission_manifest_sha256": manifest_digest,
        "observation_mode_required": True,
        "provider_claims_required": 0,
        "baseline_required_unhealthy": [],
        "baseline_failed_doctor_checks": [],
    }
    _validate_schema_value(
        policy,
        "g002-observation-release-policy.schema.json",
        repository_root_path,
    )
    from bootstrap.g002_bounded_repair import G002ObservationReleasePolicy

    try:
        G002ObservationReleasePolicy.model_validate(policy)
    except Exception as exc:
        raise HarnessError("generated G-002 observation policy is invalid") from exc
    _write_immutable_json(output_path, policy, "G-002 observation policy")
    return policy


def _validate_exact_github_merge(
    manifest: dict[str, Any], merge_commit: str
) -> list[str]:
    """Prove one admitted head entered current main through a merge commit."""

    repository = manifest["github"]["repository"]
    number = manifest["github"]["pull_request"]
    pull = _gh_json([f"repos/{repository}/pulls/{number}"])
    if (
        pull.get("merged") is not True
        or pull.get("merge_commit_sha") != merge_commit
        or pull.get("head", {}).get("sha") != manifest["candidate"]["head_sha"]
    ):
        raise HarnessError("G-002 repair PR is not merged at the admitted head")
    commit = _gh_json([f"repos/{repository}/commits/{merge_commit}"])
    parents = [parent.get("sha") for parent in commit.get("parents", [])]
    expected_parents = [
        manifest["candidate"]["base_sha"],
        manifest["candidate"]["head_sha"],
    ]
    if parents != expected_parents:
        raise HarnessError("G-002 repair is not the admitted two-parent merge")
    main = _gh_json([f"repos/{repository}/commits/main"])
    main_sha = main.get("sha")
    if not isinstance(main_sha, str):
        raise HarnessError("GitHub current main SHA is unavailable")
    containment = _gh_json([f"repos/{repository}/compare/{merge_commit}...{main_sha}"])
    if containment.get("status") not in {"ahead", "identical"}:
        raise HarnessError("current GitHub main does not contain the G-002 merge")
    return parents


def complete_release_manifest(
    manifest_path: Path,
    admission_receipt_path: Path,
    *,
    merge_commit: str,
    updater_result_path: Path,
    doctor_path: Path,
    diagnostics_path: Path,
    active_pointer_path: Path,
    live_privacy_receipt_path: Path,
    output_path: Path,
    live_browser_receipt_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Derive exact post-activation settlement and return its decision."""

    repository_root_path = root or repository_root()
    manifest = load_release_manifest(manifest_path, repository_root_path)
    if "settlement" in manifest:
        raise HarnessError("G-002 admission manifest already contains settlement")
    _validate_bound_harness(manifest, repository_root_path)
    receipt = load_schema_value(
        admission_receipt_path,
        "g002-admission-receipt.schema.json",
        repository_root_path,
    )
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if (
        receipt["decision"] != "ADMIT"
        or receipt["manifest_sha256"] != digest
        or receipt["repository"] != manifest["github"]["repository"]
        or receipt["pull_request"] != manifest["github"]["pull_request"]
        or receipt["base_sha"] != manifest["candidate"]["base_sha"]
        or receipt["head_sha"] != manifest["candidate"]["head_sha"]
        or receipt["run_id"] != manifest["harness"]["run_id"]
        or receipt["binding_diff_sha256"] != manifest["harness"]["binding_diff_sha256"]
        or receipt["lane"] != manifest["lane"]
        or receipt["target_degradations"] != sorted(manifest["target_degradations"])
        or receipt["target_doctor_checks"] != sorted(manifest["target_doctor_checks"])
    ):
        raise HarnessError("G-002 settlement receipt contradicts its admission")
    _validate_exact_github_merge(manifest, merge_commit)

    updater = load_json(updater_result_path)
    pointer = load_json(active_pointer_path)
    if (
        updater.get("status") != "upgraded"
        or updater.get("commit") != merge_commit
        or updater.get("upgrade_controller_attested") is not True
        or updater.get("rollback_available") is not True
        or pointer.get("commit") != merge_commit
        or not isinstance(pointer.get("previous"), dict)
    ):
        raise HarnessError("guarded updater or active selector evidence is incomplete")
    activation_completed_at = _timestamp(updater.get("completed_at"))
    backup_value = updater.get("backup")
    backup_verification = updater.get("backup_verification")
    if (
        not isinstance(backup_value, str)
        or not Path(backup_value).is_file()
        or not isinstance(backup_verification, dict)
        or backup_verification.get("status") != "verified"
        or backup_verification.get("manifest_sha256")
        != updater.get("backup_manifest_sha256")
        or backup_verification.get("projections_rebuilt") != 15
        or backup_verification.get("workbooks_rebuilt") is not True
    ):
        raise HarnessError("guarded updater backup evidence is unavailable")

    post_doctor = load_json(doctor_path)
    post_diagnostics = load_json(diagnostics_path)
    if (
        _timestamp(post_doctor.get("observed_at")) < activation_completed_at
        or _timestamp(post_diagnostics.get("observed_at")) < activation_completed_at
    ):
        raise HarnessError("post-activation Doctor or diagnostics evidence is stale")
    post = _content_free_live_baseline(post_doctor, post_diagnostics)
    live_privacy = load_schema_value(
        live_privacy_receipt_path,
        "g002-live-privacy-receipt.schema.json",
        repository_root_path,
    )
    if live_privacy["merge_commit"] != merge_commit:
        raise HarnessError("live privacy proof belongs to another release")
    if _timestamp(live_privacy["observed_at"]) < activation_completed_at:
        raise HarnessError("live privacy proof predates activation")
    needs_browser = browser_required(
        manifest["candidate"]["changed_paths"],
        declared_visible=manifest["impacts"]["member_visible_behavior"],
    )
    if needs_browser:
        if live_browser_receipt_path is None:
            raise HarnessError(
                "member-visible G-002 settlement lacks live browser proof"
            )
        live_browser = load_schema_value(
            live_browser_receipt_path,
            "g002-live-browser-receipt.schema.json",
            repository_root_path,
        )
        if live_browser["merge_commit"] != merge_commit:
            raise HarnessError("live browser proof belongs to another release")
        if _timestamp(live_browser["observed_at"]) < activation_completed_at:
            raise HarnessError("live browser proof predates activation")
        browser = "PASS"
    else:
        if live_browser_receipt_path is not None:
            raise HarnessError(
                "non-visible settlement received unexpected browser proof"
            )
        browser = "NOT_APPLICABLE"

    if manifest["lane"] == "bounded_repair":
        updater_repair = updater.get("g002_bounded_repair")
        if (
            not isinstance(updater_repair, dict)
            or updater_repair.get("baseline") is None
            or updater_repair.get("settlement") is None
        ):
            raise HarnessError("bounded-repair updater evidence is unavailable")
        if updater.get("g002_observation_release") is not None:
            raise HarnessError("bounded repair used ordinary observation authority")
    else:
        updater_observation = updater.get("g002_observation_release")
        if (
            updater.get("g002_bounded_repair") is not None
            or not isinstance(updater_observation, dict)
            or updater_observation.get("baseline") is None
        ):
            raise HarnessError(
                "ordinary observation-release updater evidence is unavailable"
            )

    settlement = {
        "active_head": merge_commit,
        "merge_commit": merge_commit,
        "observation_active": post["observation_active"],
        "required_unhealthy": post["required_unhealthy"],
        "failed_doctor_checks": post["failed_doctor_checks"],
        "doctor_available": post["doctor_available"],
        "privacy_isolation": live_privacy["status"],
        "browser": browser,
        "backup_verified": True,
        "rollback_ready": True,
        "provider_effects_delta": (
            post["provider_claims"] - manifest["baseline"]["provider_claims"]
        ),
    }
    completed = {**manifest, "settlement": settlement}
    _validate_schema_value(
        completed, "autonomous-release.schema.json", repository_root_path
    )
    decision = evaluate_release(completed)
    _write_immutable_json(output_path, completed, "G-002 settlement manifest")
    return {**decision, "manifest": str(output_path)}


__all__ = [
    "browser_required",
    "evaluate_manifest",
    "evaluate_and_record_admission",
    "evaluate_release",
    "hosted_macos_required",
    "load_release_manifest",
    "load_schema_value",
    "manual_release_paths",
    "next_g002_queue",
    "prepare_bounded_repair_policy",
    "prepare_observation_release_policy",
    "prepare_release_manifest",
    "complete_release_manifest",
    "queue_g002_candidate",
    "record_g002_operator_attempt",
    "validate_g002_queue",
]
