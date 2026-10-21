"""Static release-gate invariants; behavioral CI runs only in isolated runners."""

from __future__ import annotations

import copy
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
RELEASE_JOBS = (
    ("administrateme-consistency.yml", "deterministic-consistency"),
    ("macos-bootstrap.yml", "credential-free-rehearsal"),
)
REQUIRED_STAGES = {
    "deterministic-consistency": {
        "Validate architecture, style, and types",
        "Validate Python behavior",
        "Validate release-strict acceptance",
        "Validate bridge package",
        "Validate OpenClaw plugin",
        "Validate candidate diff",
    },
    "credential-free-rehearsal": {
        "Build and verify the exact reviewed OpenClaw source",
        "Validate member browser behavior",
        "Credential-free clean install and recovery rehearsal",
        "Whole-repository release-candidate suite",
    },
}


def _workflow(filename: str) -> dict[str, Any]:
    # BaseLoader preserves GitHub's literal "on" key (YAML 1.2 semantics).
    return yaml.load((WORKFLOWS / filename).read_text(), Loader=yaml.BaseLoader)


def _assert_storage_diagnostics_only(script: str) -> None:
    # An unconditional post-failure step may inspect storage, but cannot execute
    # a test suite. Check complete command tokens: the fixture directory itself
    # legitimately contains "pytest". Reject wrappers, compound commands, and
    # substitutions instead of trying to recognize every possible test runner.
    directory = "${RUNNER_TEMP}/adminme-consistency-pytest"
    allowed = {
        ("df", "-h", "${RUNNER_TEMP}"),
        ("if", "[[", "-d", directory, "]];", "then"),
        ("du", "-sh", directory),
        ("fi",),
    }
    for line in script.splitlines():
        tokens = tuple(shlex.split(line, comments=True))
        if tokens:
            assert tokens in allowed


def _assert_complete_release_gate(job: dict[str, Any], job_id: str) -> None:
    # GitHub considers a skipped job successful for branch protection. Never
    # implement this boundary as job-level "if" or a soft-failing dependency.
    assert "if" not in job
    assert "needs" not in job
    assert "continue-on-error" not in job
    steps = job["steps"]
    refusal = steps[0]
    assert refusal["name"] == "Require complete release validation"
    assert refusal["if"] in {
        "${{ github.event.pull_request.draft }}",
        "${{ github.event_name == 'pull_request' && github.event.pull_request.draft }}",
    }
    assert refusal["run"].strip().endswith("exit 1")
    assert REQUIRED_STAGES[job_id] <= {step.get("name") for step in steps[1:]}
    for step in steps:
        assert "continue-on-error" not in step
    for step in steps[1:]:
        if "if" not in step:
            continue  # Default success() cannot run after the draft refusal.
        if step.get("name") == "Record hosted test storage":
            assert step["if"] == "${{ always() }}"
            _assert_storage_diagnostics_only(step["run"])
            continue
        # These diagnostics/checks still run after ordinary full-suite failure,
        # but never after the draft refusal.
        assert step["if"] in {
            "${{ !cancelled() && !github.event.pull_request.draft }}",
            "${{ always() && !github.event.pull_request.draft }}",
        }


@pytest.mark.parametrize(("filename", "job_id"), RELEASE_JOBS)
def test_every_final_revision_gets_a_complete_non_skippable_gate(
    filename: str, job_id: str
) -> None:
    workflow = _workflow(filename)
    trigger = workflow["on"]["pull_request"]
    assert trigger["branches"] == ["main"]
    assert set(trigger["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    }
    assert "paths" not in trigger
    assert "paths-ignore" not in trigger
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert "github.event.pull_request.head.sha" in workflow["concurrency"]["group"]
    _assert_complete_release_gate(workflow["jobs"][job_id], job_id)


@pytest.mark.parametrize(("filename", "job_id"), RELEASE_JOBS)
@pytest.mark.parametrize(
    "regression", ["skipped", "soft_failure", "missing", "partial"]
)
def test_gate_contract_rejects_false_release_success(
    filename: str, job_id: str, regression: str
) -> None:
    job = copy.deepcopy(_workflow(filename)["jobs"][job_id])
    if regression == "skipped":
        job["if"] = "${{ !github.event.pull_request.draft }}"
    elif regression == "soft_failure":
        job["steps"][0]["continue-on-error"] = "true"
    elif regression == "missing":
        job["steps"] = [
            step
            for step in job["steps"]
            if step.get("name") not in REQUIRED_STAGES[job_id]
        ]
    else:
        job["steps"][-1]["if"] = "${{ false }}"
    with pytest.raises(AssertionError):
        _assert_complete_release_gate(job, job_id)


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m pytest -q",
        "poetry run pytest -q",
        "npm test",
        "bash scripts/acceptance/release-candidate.sh",
        'du -sh "${RUNNER_TEMP}/adminme-consistency-pytest"; pytest -q',
        'df -h "$(pytest -q)"',
    ],
)
def test_unconditional_storage_diagnostics_cannot_execute_tests(command: str) -> None:
    job = _workflow("administrateme-consistency.yml")["jobs"][
        "deterministic-consistency"
    ]
    diagnostics = next(
        step
        for step in job["steps"]
        if step.get("name") == "Record hosted test storage"
    )
    # The real df/du commands, including their "pytest" directory, are allowed.
    _assert_storage_diagnostics_only(diagnostics["run"])
    diagnostics["run"] += f"\n{command}\n"
    with pytest.raises(AssertionError):
        _assert_complete_release_gate(job, "deterministic-consistency")


@pytest.mark.parametrize(("filename", "job_id"), RELEASE_JOBS)
def test_development_selects_composition_without_claiming_release(
    filename: str, job_id: str
) -> None:
    workflow = _workflow(filename)
    development = workflow["jobs"]["bridge-development"]
    assert "github.event.pull_request.draft" in development["if"]
    commands = "\n".join(step.get("run", "") for step in development["steps"])
    assert "tests/acceptance/test_self_service_member_bridge_enrollment.py" in commands
    assert "tests/unit/bootstrap/test_member_bridge_helper_app.py" in commands
    assert "tests/unit/test_bridge_release_identity.py" in commands
    assert "tests/unit/test_ci_release_gates.py" in commands
    assert "scripts/acceptance/release-candidate.sh" not in commands
    assert "scripts/acceptance/clean-install.sh" not in commands
    assert "tests/acceptance --runxfail" not in commands
    if job_id == "credential-free-rehearsal":
        focused = next(
            step
            for step in development["steps"]
            if step.get("name") == "Validate enrollment browser behavior"
        )
        assert focused["env"]["ADMINME_GATE7_BRIDGE_ENROLLMENT_ONLY"] == "1"
        full = workflow["jobs"][job_id]
        assert "ADMINME_GATE7_BRIDGE_ENROLLMENT_ONLY" not in full.get("env", {})
        for step in full["steps"]:
            assert "ADMINME_GATE7_BRIDGE_ENROLLMENT_ONLY" not in step.get("env", {})
            assert "ADMINME_GATE7_BRIDGE_ENROLLMENT_ONLY" not in step.get("run", "")
