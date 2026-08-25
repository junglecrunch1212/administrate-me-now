"""Regression tests for current-state and constitutional consistency."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.harness.core import repository_root
from scripts.harness.current_state import (
    LEGACY_ACTIVE_PATHS,
    active_legacy_paths,
    all_findings,
    cascade_findings,
    check_current_state,
    current_capabilities,
    generate_current_state,
    known_contradictions,
)
from scripts.harness.skill_references import check_skill_references


def test_generated_current_state_is_fresh() -> None:
    check_current_state()


def test_current_state_contains_exact_inventories_and_evidence_boundaries() -> None:
    content = generate_current_state()

    assert "Adult surfaces (10)" in content
    assert "Projections (15)" in content
    assert "Adapter categories (5)" in content
    assert "Pack kinds (6)" in content
    assert "Built-in profiles (5)" in content
    assert "Built-in personas (4)" in content
    assert "CANDIDATE_ONLY" in content
    assert "NOT_CONFIGURED" in content
    assert "Nothing in this index grants merge" in content


def test_resolved_product_and_co016_status_have_no_known_contradiction() -> None:
    assert known_contradictions(repository_root()) == []
    assert (
        "CO-016 Triage queue continuity",
        "RELEASED_IN_OBSERVATION",
        "docs/refactor/CHANGE-ORDER-016-TRIAGE-QUEUE-CONTINUITY.md",
    ) in current_capabilities()


def test_consistency_has_no_unexpected_failure() -> None:
    failures = [
        item for item in all_findings(repository_root()) if item.status == "FAIL"
    ]
    assert failures == []


def test_legacy_executor_state_is_truthful_and_indexed() -> None:
    root = repository_root()
    history = (root / "docs/refactor/LEGACY-EXECUTOR-HISTORY.md").read_text("utf-8")
    active = active_legacy_paths(root)

    drift = [item for item in all_findings(root) if item.status == "DRIFT"]
    assert bool(drift) is bool(active)
    for relative in LEGACY_ACTIVE_PATHS:
        assert f"`{relative}`" in history


def test_generated_skill_references_are_fresh() -> None:
    assert check_skill_references() == []


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)


def test_governing_change_requires_all_four_current_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Harness fixture")
    _git(repo, "config", "user.email", "harness@example.invalid")
    for relative in (
        "docs/refactor/ADR-0001-FIXTURE.md",
        "docs/refactor/CONSTITUTIONAL-CROSSWALK.md",
        "docs/refactor/MIGRATION-MAP.md",
        "docs/refactor/ACCEPTANCE-CONTRACT.md",
        "docs/refactor/HANDOFF.md",
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")

    (repo / "docs/refactor/ADR-0001-FIXTURE.md").write_text(
        "changed\n", encoding="utf-8"
    )
    findings = cascade_findings(repo)
    assert len(findings) == 1
    assert findings[0].status == "FAIL"
    assert "HANDOFF.md" in findings[0].detail

    for relative in (
        "docs/refactor/CONSTITUTIONAL-CROSSWALK.md",
        "docs/refactor/MIGRATION-MAP.md",
        "docs/refactor/ACCEPTANCE-CONTRACT.md",
        "docs/refactor/HANDOFF.md",
    ):
        (repo / relative).write_text("changed\n", encoding="utf-8")
    assert cascade_findings(repo) == []
