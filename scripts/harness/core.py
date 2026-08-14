"""Repository-state, artifact, and inventory primitives for the harness."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from bootstrap.contracts import (
    ADAPTER_CATEGORIES,
    BUILT_IN_PERSONAS,
    BUILT_IN_PROFILES,
    PACK_KINDS,
    PROJECTION_NAMES,
)

HARNESS_SCHEMA_VERSION = 1
UNKNOWN_MARKERS = frozenset({"UNKNOWN", "TRACE_BREAK"})
ARTIFACT_SCHEMAS = {
    "outcome-contract.json": "outcome-contract.schema.json",
    "evidence-inventory.json": "evidence-inventory.schema.json",
    "preservation-manifest.json": "preservation-manifest.schema.json",
    "verification-report.json": "verification-report.schema.json",
    "review-report.json": "review-report.schema.json",
    "owner-handoff.metadata.json": "owner-handoff.schema.json",
}
GATE7_MIN_ASSERTIONS = 260
GATE7_MIN_SCREENSHOTS = 39
GATE7_REQUIRED_VIEWPORTS = frozenset(
    {"1440x1000", "1280x900", "1024x768", "820x1180", "390x844"}
)
GATE7_REQUIRED_STATES = frozenset(
    {
        "day-brief",
        "carousel",
        "compressed",
        "child",
        "observation",
        "degraded",
        "chat-streamed",
        "quest-private",
        "quest-promoted",
        "quest-expired",
        "quest-archived",
    }
)
REVIEW_SCOPES = frozenset(
    {
        "correctness",
        "privacy",
        "replay",
        "determinism",
        "surface",
        "failure_paths",
        "completeness",
        "release_boundary",
    }
)
OUTCOME_STATES = frozenset(
    {
        "happy",
        "empty",
        "loading",
        "stale",
        "degraded",
        "denied",
        "retry",
        "terminal_failure",
    }
)
MARKDOWN_CONTRACTS = {
    "implementation-plan.md": (
        "# Implementation plan",
        "## Technical choices",
        "## Failure and rollback",
    ),
    "owner-handoff.md": (
        "# Owner handoff",
        "## What now works",
        "## What did not change",
        "## Try this",
        "## Release",
    ),
}


class HarnessError(RuntimeError):
    """A deterministic harness gate failed without changing repository state."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def schema_root(root: Path | None = None) -> Path:
    return (root or repository_root()) / "schemas" / "harness"


def _run_git(root: Path, *arguments: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no diagnostic"
        raise HarnessError(f"git {' '.join(arguments)} failed: {detail}")
    return process.stdout


def git_lines(root: Path, *arguments: str) -> list[str]:
    return [line for line in _run_git(root, *arguments).splitlines() if line]


def normalize_undetermined(value: Any) -> Any:
    """Replace prohibited confident-looking unknown markers recursively."""

    if isinstance(value, str):
        return "UNDETERMINED" if value in UNKNOWN_MARKERS else value
    if isinstance(value, list):
        return [normalize_undetermined(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_undetermined(item) for key, item in value.items()}
    return value


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            normalize_undetermined(value),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json(value))


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read JSON artifact {path}: {exc}") from exc


def validate_schema_file(path: Path) -> None:
    schema = load_json(path)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise HarnessError(f"invalid JSON Schema {path}: {exc.message}") from exc


def validate_artifact_value(
    artifact_name: str, value: Any, root: Path | None = None
) -> Any:
    """Validate an in-memory artifact before it can replace canonical evidence."""

    schema_name = ARTIFACT_SCHEMAS.get(artifact_name)
    if schema_name is None:
        raise HarnessError(f"no harness schema is registered for {artifact_name}")
    schema_path = schema_root(root) / schema_name
    validate_schema_file(schema_path)
    validator = Draft202012Validator(
        load_json(schema_path), format_checker=FormatChecker()
    )
    artifact = normalize_undetermined(value)
    try:
        validator.validate(artifact)
    except ValidationError as exc:
        coordinate = ".".join(str(item) for item in exc.absolute_path) or "<root>"
        raise HarnessError(
            f"artifact {artifact_name} is invalid at {coordinate}: {exc.message}"
        ) from exc
    if artifact_name == "review-report.json":
        scopes = artifact["scope"]
        if len(scopes) != len(REVIEW_SCOPES) or set(scopes) != REVIEW_SCOPES:
            raise HarnessError(
                "artifact review-report.json is invalid: scope must contain "
                "each required review category exactly once"
            )
    if artifact_name == "outcome-contract.json":
        states = [case["state"] for case in artifact["acceptance_cases"]]
        if len(states) != len(OUTCOME_STATES) or set(states) != OUTCOME_STATES:
            raise HarnessError(
                "artifact outcome-contract.json is invalid: acceptance_cases must "
                "contain each required state exactly once"
            )
    return artifact


def validate_artifact(path: Path, root: Path | None = None) -> None:
    validate_artifact_value(path.name, load_json(path), root)


def validate_markdown_artifact(path: Path) -> None:
    headings = MARKDOWN_CONTRACTS.get(path.name)
    if headings is None:
        raise HarnessError(f"no Markdown contract is registered for {path.name}")
    try:
        content = path.read_text("utf-8")
    except OSError as exc:
        raise HarnessError(f"cannot read Markdown artifact {path}: {exc}") from exc
    missing = [heading for heading in headings if heading not in content]
    if missing:
        raise HarnessError(f"{path.name} is missing required sections: {missing}")
    if any(marker in content for marker in UNKNOWN_MARKERS):
        raise HarnessError(
            f"{path.name} contains UNKNOWN or TRACE_BREAK; use UNDETERMINED"
        )


def render_owner_handoff(owner: dict[str, Any]) -> str:
    """Render the only accepted owner prose from schema-validated metadata."""

    proof = owner["proof"]
    unchanged = "\n".join(
        f"- {statement}" for statement in owner["what_did_not_change"]
    )
    trial = owner["try_this"]
    desktop = trial["desktop_screenshot"] or "UNDETERMINED"
    narrow = trial["narrow_screenshot"] or "UNDETERMINED"
    remaining = owner["remaining_decision"] or "None."
    return f"""# Owner handoff

## What now works

{owner["what_now_works"]}

Proof: deterministic={proof["deterministic"]}; production journey={proof["production_journey"]}; browser={proof["browser"]}; independent review={proof["independent_review"]}.

## What did not change

{unchanged}

## Try this

Start at `{trial["start_path"]}`. {trial["task"]}

Expected: {trial["expected_result"]}

Desktop screenshot: {desktop}

Narrow screenshot: {narrow}

Remaining decision: {remaining}

## Release

{owner["release_status"]}.
"""


def ensure_ephemeral_run_dir(path: Path, root: Path | None = None) -> Path:
    resolved = path.resolve()
    repo = (root or repository_root()).resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(temp)
    except ValueError as exc:
        raise HarnessError(
            f"harness run must be under the system temp directory: {temp}"
        ) from exc
    try:
        resolved.relative_to(repo)
    except ValueError:
        pass
    else:
        raise HarnessError("harness run must not be stored inside the repository")
    if not resolved.name.startswith("administrateme-harness-"):
        raise HarnessError(
            "harness run directory must start with administrateme-harness-"
        )
    return resolved


def new_run_directory(run_id: str | None = None) -> Path:
    identifier = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    return Path(tempfile.mkdtemp(prefix=f"administrateme-harness-{identifier}-"))


def _git_commit_exists(root: Path, revision: str) -> bool:
    process = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return process.returncode == 0


def _github_pull_request_base_sha(root: Path) -> str:
    """Resolve and materialize an exact base for GitHub's shallow PR checkout."""

    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise HarnessError(
            "origin/main is unavailable outside a verified GitHub Actions checkout"
        )
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        raise HarnessError(
            "origin/main is unavailable and the GitHub event is not pull_request"
        )
    if os.environ.get("GITHUB_BASE_REF") != "main":
        raise HarnessError("origin/main is unavailable and GITHUB_BASE_REF is not main")

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise HarnessError("GitHub pull-request checkout has no GITHUB_EVENT_PATH")
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        pull_request = event["pull_request"]
        base_ref = pull_request["base"]["ref"]
        base_sha = pull_request["base"]["sha"]
        head_sha = pull_request["head"]["sha"]
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise HarnessError(
            f"cannot read GitHub pull-request base and head from {event_path}: {exc}"
        ) from exc

    sha_pattern = re.compile(r"[0-9a-f]{40}")
    if (
        base_ref != "main"
        or not isinstance(base_sha, str)
        or not sha_pattern.fullmatch(base_sha)
    ):
        raise HarnessError("GitHub pull-request event has an invalid main base")
    if not isinstance(head_sha, str) or not sha_pattern.fullmatch(head_sha):
        raise HarnessError("GitHub pull-request event has an invalid head SHA")

    checkout_sha = _run_git(root, "rev-parse", "HEAD").strip()
    if os.environ.get("GITHUB_SHA") != checkout_sha:
        raise HarnessError("GITHUB_SHA does not identify the checked-out commit")
    commit_headers = _run_git(root, "cat-file", "-p", "HEAD").split("\n\n", 1)[0]
    parents = [
        line.removeprefix("parent ")
        for line in commit_headers.splitlines()
        if line.startswith("parent ")
    ]
    if parents != [base_sha, head_sha]:
        raise HarnessError(
            "GitHub pull-request event base/head do not match checkout parents"
        )

    if not _git_commit_exists(root, base_sha):
        process = subprocess.run(
            [
                "git",
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "--depth=1",
                "origin",
                base_sha,
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "no diagnostic"
            raise HarnessError(
                f"cannot fetch exact GitHub pull-request base {base_sha}: {detail}"
            )
    if not _git_commit_exists(root, base_sha):
        raise HarnessError(
            f"exact GitHub pull-request base {base_sha} is not a commit object"
        )
    return base_sha


def candidate_base_sha(root: Path) -> str:
    """Return the candidate's verified immutable main base."""

    if _git_commit_exists(root, "refs/remotes/origin/main"):
        return _run_git(root, "merge-base", "HEAD", "origin/main").strip()
    return _github_pull_request_base_sha(root)


def candidate_branch(root: Path) -> str:
    """Return the attached branch or an exact detached-HEAD identity."""

    branch = _run_git(root, "branch", "--show-current").strip()
    if branch:
        return branch
    head_sha = _run_git(root, "rev-parse", "HEAD").strip()
    return f"detached@{head_sha}"


def candidate_diff_paths(root: Path, *, base_sha: str | None = None) -> list[str]:
    """Return every committed path changed between the immutable base and HEAD."""

    base = base_sha or candidate_base_sha(root)
    return sorted(set(git_lines(root, "diff", "--name-only", base, "HEAD")))


def _task_selected_candidate_paths(
    root: Path,
    task_paths: Iterable[str],
    *,
    base_sha: str,
) -> list[str]:
    normalized = sorted({str(Path(path)) for path in task_paths})
    if not normalized:
        return []
    return sorted(
        set(
            git_lines(
                root,
                "diff",
                "--name-only",
                base_sha,
                "HEAD",
                "--",
                *normalized,
            )
        )
    )


def assert_task_scope_covers_candidate_diff(
    root: Path,
    task_paths: Iterable[str],
) -> None:
    """Fail when a declared task manifest omits any committed candidate path."""

    base_sha = candidate_base_sha(root)
    complete = set(candidate_diff_paths(root, base_sha=base_sha))
    selected = set(_task_selected_candidate_paths(root, task_paths, base_sha=base_sha))
    missing = sorted(complete - selected)
    if missing:
        raise HarnessError(
            "task-owned path manifest omits candidate diff paths: " + ", ".join(missing)
        )


def complete_task_scope(root: Path, task_paths: Iterable[str]) -> list[str]:
    """Add exact committed candidate paths to an explicitly supplied task scope."""

    return sorted(
        {str(Path(path)) for path in task_paths} | set(candidate_diff_paths(root))
    )


def run_diff_hygiene(root: Path) -> str:
    """Check both the committed candidate and current task worktree for defects."""

    base_sha = candidate_base_sha(root)
    checks = (
        ("committed candidate diff", [base_sha, "HEAD"]),
        ("working-tree diff", ["HEAD"]),
    )
    for label, revisions in checks:
        process = subprocess.run(
            ["git", "diff", "--check", *revisions],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            detail = process.stdout.strip() or process.stderr.strip() or "no diagnostic"
            raise HarnessError(f"diff hygiene failed for {label}: {detail}")
    return base_sha


def task_diff_bytes(
    root: Path,
    paths: Iterable[str],
    *,
    base_sha: str | None = None,
) -> bytes:
    """Return a deterministic diff over only explicitly task-owned paths."""

    normalized = sorted({str(Path(path)) for path in paths})
    if not normalized:
        return b""
    base = base_sha or _run_git(root, "rev-parse", "HEAD").strip()
    manifest = [b"TASK_PATHS_V2\0"]
    for relative in normalized:
        encoded = relative.encode("utf-8")
        manifest.extend((f"{len(encoded)}:".encode("ascii"), encoded, b"\0"))
    committed = _run_git(
        root, "diff", "--binary", base, "HEAD", "--", *normalized
    ).encode()
    working = _run_git(root, "diff", "--binary", "HEAD", "--", *normalized).encode()
    blocks = [
        *manifest,
        b"\nCOMMITTED_DIFF\0",
        committed,
        b"\nWORKING_TREE_DIFF\0",
        working,
    ]
    untracked = sorted(
        {
            child
            for relative in normalized
            for child in git_lines(
                root, "ls-files", "--others", "--exclude-standard", "--", relative
            )
        }
    )
    for child_relative in untracked:
        child = root / child_relative
        mode = child.lstat().st_mode
        header = (
            f"\nUNTRACKED:{child_relative}\n"
            f"MODE:{stat.S_IFMT(mode):o}:{stat.S_IMODE(mode):o}\n"
        ).encode()
        if stat.S_ISREG(mode):
            payload = child.read_bytes()
        elif stat.S_ISLNK(mode):
            payload = f"SYMLINK:{os.readlink(child)}".encode()
        else:
            raise HarnessError(
                f"unsupported untracked file type in task binding: {child_relative}"
            )
        blocks.extend([header, payload])
    return b"".join(blocks)


def binding(
    root: Path,
    task_paths: Iterable[str],
    *,
    evidence_class: str = "local_candidate",
) -> dict[str, str]:
    base_sha = candidate_base_sha(root)
    digest = hashlib.sha256(
        task_diff_bytes(root, task_paths, base_sha=base_sha)
    ).hexdigest()
    return {
        "base_sha": base_sha,
        "head_sha": _run_git(root, "rev-parse", "HEAD").strip(),
        "diff_sha256": digest,
        "branch": candidate_branch(root),
        "evidence_class": evidence_class,
    }


def _protected_relative_path(relative: str) -> str:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise HarnessError(
            f"protected path must stay within the repository: {relative}"
        )
    normalized = candidate.as_posix().rstrip("/")
    if not normalized or normalized == ".":
        raise HarnessError("protected path must name a repository child")
    return normalized


def _protected_file_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def _protected_entry_digest(path: Path, relative: str) -> tuple[str, int]:
    mode = path.lstat().st_mode
    file_type = _protected_file_type(mode)
    header = (
        f"PROTECTED_ENTRY_V1\0{relative}\0{file_type}\0"
        f"{stat.S_IFMT(mode):o}:{stat.S_IMODE(mode):o}\0"
    ).encode("utf-8")
    if file_type == "regular":
        payload = bytes.fromhex(sha256_file(path))
        count = 1
    elif file_type == "symlink":
        payload = hashlib.sha256(os.fsencode(os.readlink(path))).digest()
        count = 1
    elif file_type == "directory":
        blocks: list[bytes] = []
        count = 1
        with os.scandir(path) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for child in children:
            child_relative = f"{relative}/{child.name}"
            child_digest, child_count = _protected_entry_digest(
                Path(child.path), child_relative
            )
            blocks.extend(
                (
                    child.name.encode("utf-8"),
                    b"\0",
                    bytes.fromhex(child_digest),
                )
            )
            count += child_count
        payload = b"".join(blocks)
    else:
        payload = b""
        count = 1
    return hashlib.sha256(header + payload).hexdigest(), count


def protected_path_binding(root: Path, relative: str) -> dict[str, Any]:
    """Bind protected work by hashes and metadata without recording its content."""

    normalized = _protected_relative_path(relative)
    path = root / normalized
    if not path.exists() and not path.is_symlink():
        return {
            "path": normalized,
            "exists": False,
            "file_type": "absent",
            "mode": None,
            "content_sha256": hashlib.sha256(
                f"PROTECTED_ABSENT_V1\0{normalized}".encode("utf-8")
            ).hexdigest(),
            "symlink_target_sha256": None,
            "entry_count": 0,
        }
    mode = path.lstat().st_mode
    file_type = _protected_file_type(mode)
    digest, count = _protected_entry_digest(path, normalized)
    target_digest = (
        hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
        if file_type == "symlink"
        else None
    )
    return {
        "path": normalized,
        "exists": True,
        "file_type": file_type,
        "mode": f"{stat.S_IMODE(mode):04o}",
        "content_sha256": digest,
        "symlink_target_sha256": target_digest,
        "entry_count": count,
    }


def repository_state(root: Path, protected_paths: Iterable[str]) -> dict[str, Any]:
    normalized = sorted({_protected_relative_path(path) for path in protected_paths})
    return {
        "tracked_changes": git_lines(root, "diff", "--name-only"),
        "untracked_paths": git_lines(
            root, "ls-files", "--others", "--exclude-standard"
        ),
        "protected_paths": normalized,
        "protected_path_bindings": [
            protected_path_binding(root, path) for path in normalized
        ],
    }


def applicable_instructions(root: Path, paths: Iterable[str]) -> list[str]:
    """Find the root-to-leaf AGENTS chain for the requested repository paths."""

    found: set[Path] = set()
    for relative in paths:
        candidate = (root / relative).resolve()
        directory = candidate if candidate.is_dir() else candidate.parent
        while directory == root or root in directory.parents:
            for name in ("AGENTS.override.md", "AGENTS.md"):
                instruction = directory / name
                if instruction.is_file():
                    found.add(instruction)
                    break
            if directory == root:
                break
            directory = directory.parent
    return [path.relative_to(root).as_posix() for path in sorted(found)]


def _folder_names(root: Path, relative: str) -> tuple[str, ...]:
    path = root / relative
    if not path.is_dir():
        return ()
    return tuple(sorted(item.name for item in path.iterdir() if item.is_dir()))


def executable_inventory(root: Path | None = None) -> dict[str, list[str]]:
    base = root or repository_root()
    from adminme.products.member.surfaces import ADULT_SURFACES

    profiles = _folder_names(base, "packs/profiles")
    personas = _folder_names(base, "packs/personas")
    if profiles != tuple(BUILT_IN_PROFILES):
        raise HarnessError(
            f"profile inventory mismatch: metadata={BUILT_IN_PROFILES}, folders={profiles}"
        )
    if personas != tuple(BUILT_IN_PERSONAS):
        raise HarnessError(
            f"persona inventory mismatch: metadata={BUILT_IN_PERSONAS}, folders={personas}"
        )
    return {
        "surfaces": list(ADULT_SURFACES),
        "projections": list(PROJECTION_NAMES),
        "adapter_categories": list(ADAPTER_CATEGORIES),
        "pack_kinds": list(PACK_KINDS),
        "profiles": list(profiles),
        "personas": list(personas),
    }


def validate_run(run_dir: Path, root: Path | None = None) -> None:
    ensure_ephemeral_run_dir(run_dir, root)
    for artifact_name in ARTIFACT_SCHEMAS:
        artifact = run_dir / artifact_name
        if not artifact.is_file():
            raise HarnessError(f"missing run artifact: {artifact_name}")
        validate_artifact(artifact, root)
    for artifact_name in MARKDOWN_CONTRACTS:
        artifact = run_dir / artifact_name
        if not artifact.is_file():
            raise HarnessError(f"missing run artifact: {artifact_name}")
        validate_markdown_artifact(artifact)
    _validate_run_semantics(run_dir, root)


def _validate_run_semantics(run_dir: Path, root: Path | None) -> None:
    """Reject cross-artifact contradictions and receipts bound to another diff."""

    artifacts = {name: load_json(run_dir / name) for name in ARTIFACT_SCHEMAS}
    canonical_binding = artifacts["outcome-contract.json"]["binding"]
    for name, artifact in artifacts.items():
        if artifact["binding"] != canonical_binding:
            raise HarnessError(f"{name} is bound to a different candidate")

    verification = artifacts["verification-report.json"]
    preservation = artifacts["preservation-manifest.json"]
    review = artifacts["review-report.json"]
    owner = artifacts["owner-handoff.metadata.json"]
    if preservation["rollback"]["task_owned_paths"] != verification["changed_files"]:
        raise HarnessError(
            "preservation-manifest.json task-owned paths differ from the bound "
            "verification scope"
        )
    if (run_dir / "owner-handoff.md").read_text("utf-8") != render_owner_handoff(owner):
        raise HarnessError(
            "owner-handoff.md contradicts or is stale against owner handoff metadata"
        )
    if root is not None:
        assert_task_scope_covers_candidate_diff(root, verification["changed_files"])
        current = binding(root, verification["changed_files"])
        if current != canonical_binding:
            raise HarnessError(
                "run binding is stale for the current task-owned diff; "
                "refreshing must invalidate and rerun evidence"
            )
        evidence_inventory = artifacts["evidence-inventory.json"]
        protected = evidence_inventory["repository_state"]["protected_paths"]
        if evidence_inventory["repository_state"] != repository_state(root, protected):
            raise HarnessError(
                "evidence-inventory.json repository state is stale for the current worktree"
            )

    check_statuses = [check["status"] for check in verification["checks"]]
    for check in verification["checks"]:
        _validate_check_receipt(run_dir, canonical_binding, check)
    has_failure = (
        "FAIL" in check_statuses
        or verification["browser"]["status"] == "FAIL"
        or review["conclusion"] == "CHANGES_REQUIRED"
    )
    has_unresolved_proof = (
        bool(verification["unresolved"])
        or any(
            status in {"UNDETERMINED", "NOT_CONFIGURED"} for status in check_statuses
        )
        or verification["browser"]["status"] == "UNDETERMINED"
        or review["conclusion"] != "APPROVE"
        or bool(review["unresolved_evidence"])
        or any(finding["actionable"] for finding in review["findings"])
    )
    browser = verification["browser"]
    if browser["status"] == "PASS":
        _validate_browser_evidence(run_dir, canonical_binding, browser)
    if verification["status"] == "PASS":
        _validate_required_check_specs(verification)
        required_tiers = {"static", "focused", "journey", "breadth"}
        passed_tiers = {
            check["tier"]
            for check in verification["checks"]
            if check["status"] == "PASS"
        }
        missing_tiers = sorted(required_tiers - passed_tiers)
        if has_failure or has_unresolved_proof or missing_tiers:
            raise HarnessError(
                "verification-report.json claims PASS with unresolved, failed, "
                f"or missing proof (missing tiers: {missing_tiers})"
            )
        if browser["status"] == "PASS" and (
            browser["console_errors"]
            or browser["page_errors"]
            or browser["request_failures"]
            or not browser["desktop"]
            or not browser["narrow"]
        ):
            raise HarnessError("browser PASS lacks clean desktop and narrow evidence")
        if owner["proof"] != {
            "deterministic": "PASS",
            "production_journey": "PASS",
            "browser": browser["status"],
            "independent_review": "APPROVE",
        }:
            raise HarnessError("owner handoff proof contradicts verification PASS")
        if owner["release_status"] == "NOT_RELEASED":
            pass
        elif owner["release_status"] == "CANDIDATE_ONLY":
            if canonical_binding["evidence_class"] == "live":
                raise HarnessError("live evidence cannot be labeled CANDIDATE_ONLY")
        elif canonical_binding["evidence_class"] != "live":
            raise HarnessError("only live-bound PASS evidence may claim RELEASED")
    elif verification["status"] == "FAIL" and not has_failure:
        raise HarnessError("verification-report.json claims FAIL without failed proof")
    elif any(status in {"PASS", "APPROVE"} for status in owner["proof"].values()):
        raise HarnessError("owner handoff proof overclaims an incomplete verification")
    if verification["status"] != "PASS" and owner["release_status"] != "NOT_RELEASED":
        raise HarnessError(
            "incomplete verification cannot claim a candidate or release"
        )

    metrics = verification["metrics"]
    if metrics["passed_journeys"] > metrics["applicable_journeys"]:
        raise HarnessError("passed_journeys exceeds applicable_journeys")
    if review["conclusion"] == "APPROVE" and (
        review["unresolved_evidence"]
        or any(finding["actionable"] for finding in review["findings"])
    ):
        raise HarnessError("review APPROVE retains actionable or unresolved evidence")


def _validate_required_check_specs(verification: dict[str, Any]) -> None:
    """Require every reviewed check identity and exact command for a green tier."""

    from .checks import tier_checks

    declared: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for check in verification["checks"]:
        declared.setdefault((check["tier"], check["check_id"]), []).append(check)
    required_tiers = ("static", "focused", "journey", "breadth")
    for tier in required_tiers:
        specs = tier_checks(tier, verification["changed_files"])
        for spec in specs:
            matches = declared.get((spec.tier, spec.check_id), [])
            if len(matches) != 1:
                raise HarnessError(
                    f"required reviewed check is missing or duplicated: "
                    f"{spec.tier}/{spec.check_id}"
                )
            check = matches[0]
            if check["command"] != spec.command:
                raise HarnessError(
                    f"required reviewed check command changed: "
                    f"{spec.tier}/{spec.check_id}"
                )
            if check["status"] != "PASS" or check["exit_code"] != 0:
                raise HarnessError(
                    f"required reviewed check did not pass: {spec.tier}/{spec.check_id}"
                )


def _validate_browser_evidence(
    run_dir: Path,
    canonical_binding: dict[str, Any],
    browser: dict[str, Any],
) -> None:
    """Re-hash and decode required Gate 7 evidence on every run validation."""

    output_value = browser.get("output_directory")
    report_value = browser.get("report_path")
    if not isinstance(output_value, str) or not isinstance(report_value, str):
        raise HarnessError("browser PASS lacks an evidence directory and report path")
    output_dir = Path(output_value).resolve()
    report_path = Path(report_value).resolve()
    try:
        output_dir.relative_to(run_dir.resolve())
        report_path.relative_to(output_dir)
    except ValueError as exc:
        raise HarnessError("browser evidence escapes the current harness run") from exc
    binding_path = output_dir / "harness-binding.json"
    if not binding_path.is_file() or load_json(binding_path) != canonical_binding:
        raise HarnessError("browser evidence manifest is missing or stale")
    if not report_path.is_file() or sha256_file(report_path) != browser.get(
        "report_sha256"
    ):
        raise HarnessError("browser visual report hash mismatch")
    report = load_json(report_path)
    results = report.get("results")
    screenshots = report.get("screenshots")
    if (
        report.get("schema_version") != 1
        or report.get("status") != "PASS"
        or not isinstance(report.get("browser"), str)
        or "; engine " not in report["browser"]
        or not isinstance(results, list)
        or len(results) < GATE7_MIN_ASSERTIONS
        or any(
            not isinstance(item, dict)
            or item.get("status") != "PASS"
            or not isinstance(item.get("name"), str)
            or not item["name"]
            for item in results
        )
        or not isinstance(screenshots, dict)
        or len(screenshots) < GATE7_MIN_SCREENSHOTS
        or not GATE7_REQUIRED_VIEWPORTS.issubset(report.get("viewports", []))
        or not GATE7_REQUIRED_STATES.issubset(report.get("states", []))
    ):
        raise HarnessError(
            "browser PASS report is incomplete or contains failed assertions"
        )
    if browser.get("assertion_count") != len(results):
        raise HarnessError("browser assertion count contradicts the visual report")
    if browser.get("browser_engine") != report["browser"]:
        raise HarnessError("browser engine contradicts the visual report")
    for field in ("console_errors", "page_errors", "request_failures"):
        if report.get(field) != browser.get(field) or report.get(field):
            raise HarnessError(f"browser PASS contains or conceals {field}")

    declared_items = browser["desktop"] + browser["narrow"]
    declared_names = {Path(item["path"]).name for item in declared_items}
    if len(declared_items) != len(declared_names) or declared_names != set(screenshots):
        raise HarnessError("browser screenshot inventory contradicts the visual report")
    from .checks import _png_pixels

    for item in declared_items:
        path = Path(item["path"]).resolve()
        try:
            path.relative_to(output_dir)
        except ValueError as exc:
            raise HarnessError(
                "browser screenshot escapes its evidence directory"
            ) from exc
        if not path.is_file():
            raise HarnessError(f"browser screenshot is missing: {path.name}")
        actual_hash = sha256_file(path)
        report_metadata = screenshots.get(path.name, {})
        if actual_hash != item["sha256"] or actual_hash != report_metadata.get(
            "sha256"
        ):
            raise HarnessError(f"browser screenshot hash mismatch: {path.name}")
        width, height, _channels, _rows = _png_pixels(path)
        if (width, height) != (item["width"], item["height"]):
            raise HarnessError(f"browser screenshot dimensions mismatch: {path.name}")


def _validate_check_receipt(
    run_dir: Path, canonical_binding: dict[str, Any], check: dict[str, Any]
) -> None:
    receipt_value = Path(check["receipt"])
    if receipt_value.is_absolute() or ".." in receipt_value.parts:
        raise HarnessError(f"unsafe check receipt path: {check['receipt']}")
    receipt_path = (run_dir / receipt_value).resolve()
    try:
        receipt_path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise HarnessError(
            f"check receipt escapes run directory: {check['receipt']}"
        ) from exc
    if not receipt_path.is_file():
        raise HarnessError(f"missing check receipt: {check['receipt']}")
    receipt = load_json(receipt_path)
    if receipt.get("binding") != canonical_binding:
        raise HarnessError(f"stale check receipt binding: {check['receipt']}")
    expected_check = {key: value for key, value in check.items() if key != "receipt"}
    if receipt.get("check") != expected_check:
        raise HarnessError(f"check receipt contradicts report: {check['receipt']}")
    if check["status"] == "PASS" and check["exit_code"] != 0:
        raise HarnessError(f"PASS check has nonzero exit: {check['check_id']}")
    if check["status"] == "FAIL" and check["exit_code"] in {0, None}:
        raise HarnessError(f"FAIL check lacks nonzero exit: {check['check_id']}")
    log = receipt.get("log")
    if not isinstance(log, dict):
        raise HarnessError(f"check receipt lacks a hashed log: {check['receipt']}")
    log_value = Path(log.get("path", ""))
    if log_value.is_absolute() or ".." in log_value.parts:
        raise HarnessError(f"unsafe check log path: {log_value}")
    log_path = (run_dir / log_value).resolve()
    try:
        log_path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise HarnessError(f"check log escapes run directory: {log_value}") from exc
    if not log_path.is_file() or sha256_file(log_path) != log.get("sha256"):
        raise HarnessError(f"check log hash mismatch: {log_value}")
    if check["command"].startswith(
        "npm --prefix tools/console-reference-harness run test:gate7"
    ):
        _validate_supported_toolchain(receipt, log_path, context="browser")
    if check["command"].startswith("npm --prefix openclaw/administrateme "):
        _validate_supported_toolchain(receipt, log_path, context="OpenClaw")


def _validate_browser_toolchain(receipt: dict[str, Any], log_path: Path) -> None:
    """Require an immutable supported toolchain attestation for browser proof."""

    _validate_supported_toolchain(receipt, log_path, context="browser")


def _validate_supported_toolchain(
    receipt: dict[str, Any], log_path: Path, *, context: str
) -> None:
    """Require an immutable supported candidate toolchain attestation."""

    toolchain = receipt.get("toolchain")
    required = {
        "node_path",
        "node_version",
        "node_sha256",
        "npm_path",
        "npm_version",
        "npm_sha256",
        "python_path",
        "python_version",
        "python_sha256",
    }
    if not isinstance(toolchain, dict) or set(toolchain) != required:
        raise HarnessError(f"{context} check receipt lacks exact toolchain evidence")
    match = re.fullmatch(r"v24\.(\d+)\.(\d+)", toolchain["node_version"])
    if match is None or int(match.group(1)) < 15:
        raise HarnessError(
            f"{context} check used unsupported Node {toolchain['node_version']}"
        )
    npm_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", toolchain["npm_version"])
    if (
        npm_match is None
        or int(npm_match.group(1)) != 11
        or int(npm_match.group(2)) < 6
    ):
        raise HarnessError(
            f"{context} check used unsupported npm {toolchain['npm_version']}"
        )
    if not re.fullmatch(r"Python 3\.14\.\d+", toolchain["python_version"]):
        raise HarnessError(
            f"{context} check used unsupported Python {toolchain['python_version']}"
        )
    paths: dict[str, Path] = {}
    for name in ("node", "npm", "python"):
        value = Path(toolchain[f"{name}_path"])
        if not value.is_absolute():
            raise HarnessError(f"{context} {name} tool path is not absolute")
        resolved = value.resolve()
        if (
            not resolved.is_file()
            or resolved != value
            or sha256_file(resolved) != toolchain[f"{name}_sha256"]
        ):
            raise HarnessError(
                f"{context} {name} tool evidence changed after execution"
            )
        paths[name] = resolved
    try:
        paths["npm"].relative_to(paths["node"].parent.parent)
    except ValueError as exc:
        raise HarnessError(
            f"{context} npm did not come from the recorded Node runtime"
        ) from exc
    expected_line = f"toolchain={json.dumps(toolchain, sort_keys=True)}"
    if expected_line not in log_path.read_text("utf-8"):
        raise HarnessError(
            f"{context} toolchain is not bound into the hashed check log"
        )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a subprocess environment without inventing credentials or live paths."""

    environment = dict(os.environ)
    if extra:
        environment.update(extra)
    safe_adminme_names = {
        "ADMINME_GATE7_EVIDENCE_DIR",
        "ADMINME_GATE7_PYTHON",
        "ADMINME_GATE7_VISUAL_NOW",
    }
    credential_names = {
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CODEX_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
    }
    for name in tuple(environment):
        if (
            name in credential_names
            or name.startswith("OPENCLAW_")
            or (name.startswith("ADMINME_") and name not in safe_adminme_names)
        ):
            environment.pop(name, None)
    return environment
