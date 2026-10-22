"""Risk-based release gates must prove complete, exact-revision component evidence."""

from __future__ import annotations

import copy
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.release_scope import (
    CONTRACT,
    choose_scope,
    required_workers,
    select_scope,
    verify_gate,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
RELEASE_JOBS = (
    (
        "administrateme-consistency.yml",
        "deterministic-consistency",
        "linux",
        "system-consistency",
    ),
    ("macos-bootstrap.yml", "credential-free-rehearsal", "macos", "system-rehearsal"),
)
FULL_STAGES = {
    "system-consistency": {
        "Validate architecture, style, and types",
        "Validate Python behavior",
        "Validate release-strict acceptance",
        "Validate bridge package",
        "Validate OpenClaw plugin",
        "Validate candidate diff",
        "Validate production triage replay, activation, and rollback",
        "Validate historical review and recovery",
    },
    "system-rehearsal": {
        "Require packaged member installation",
        "Build and verify the exact reviewed OpenClaw source",
        "Validate member browser behavior",
        "Credential-free clean install and recovery rehearsal",
        "Whole-repository release-candidate suite",
        "Validate encrypted metadata migration and production replay",
    },
}
PROSE = "docs/refactor/CHANGE-ORDER-027-SELF-SERVICE-MEMBER-BRIDGE-ENROLLMENT.md"
HELPER = "bootstrap/install-member-bridge.sh"
SHA = "a" * 40


def _workflow(filename: str) -> dict[str, Any]:
    return yaml.load((WORKFLOWS / filename).read_text(), Loader=yaml.BaseLoader)


def _proof(scope: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "scope": scope,
        "draft": False,
        "checkout_sha": SHA,
        "interface_sha256": "b" * 64,
    }


def _needs(proof: dict[str, Any], platform: str) -> dict[str, Any]:
    needs = {
        worker: {"result": "success", "outputs": {"validated_sha": SHA}}
        for worker in required_workers(proof["scope"], platform)
    }
    needs["release-scope"]["outputs"].update(
        scope=proof["scope"], interface_sha256=proof["interface_sha256"]
    )
    return needs


@pytest.mark.parametrize("scope", ["documentation", "bridge-helper", "system-wide"])
@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_scope_gate_requires_exact_corresponding_success(
    scope: str, platform: str
) -> None:
    proof = _proof(scope)
    needs = _needs(proof, platform)
    verify_gate(proof, needs, platform=platform)
    for worker in required_workers(scope, platform):
        for result in ("skipped", "failure", "cancelled", "neutral", "timed_out", None):
            changed = copy.deepcopy(needs)
            changed[worker]["result"] = result
            with pytest.raises(ValueError, match="successfully"):
                verify_gate(proof, changed, platform=platform)
        changed = copy.deepcopy(needs)
        del changed[worker]
        with pytest.raises(ValueError):
            verify_gate(proof, changed, platform=platform)
        changed = copy.deepcopy(needs)
        changed[worker]["outputs"]["validated_sha"] = "c" * 40
        with pytest.raises(ValueError, match="revision"):
            verify_gate(proof, changed, platform=platform)
        del changed[worker]["outputs"]["validated_sha"]
        with pytest.raises(ValueError, match="revision"):
            verify_gate(proof, changed, platform=platform)


@pytest.mark.parametrize("defect", ["draft", "scope", "interface", "contract"])
def test_gate_refuses_development_or_reinterpreted_evidence(defect: str) -> None:
    proof = _proof("bridge-helper")
    needs = _needs(proof, "macos")
    if defect == "draft":
        proof["draft"] = True
    elif defect == "contract":
        proof["contract"] = "unrecognized"
    else:
        key = "interface_sha256" if defect == "interface" else "scope"
        needs["release-scope"]["outputs"][key] = "unrelated"
    with pytest.raises(ValueError):
        verify_gate(proof, needs, platform="macos")


@pytest.mark.parametrize(
    ("changes", "equal", "expected"),
    [
        ([("M", PROSE)], True, "documentation"),
        ([("M", "docs/refactor/ARCHITECTURE.md")], True, "documentation"),
        ([("M", "AGENTS.md")], True, "documentation"),
        ([("D", PROSE)], True, "documentation"),
        ([("M", HELPER)], True, "bridge-helper"),
        ([("A", "bootstrap/member_bridge_component.py")], True, "bridge-helper"),
        (
            [("A", "bootstrap/member_bridge_permission_controller.py")],
            True,
            "bridge-helper",
        ),
        (
            [("M", "bootstrap/member_bridge_permission_controller.py")],
            False,
            "system-wide",
        ),
        ([("M", "scripts/release_scope.py")], True, "bridge-helper"),
        ([("M", HELPER), ("M", PROSE)], True, "bridge-helper"),
        ([("M", HELPER)], False, "system-wide"),
        ([("M", PROSE)], False, "system-wide"),
        ([("D", HELPER)], True, "system-wide"),
        ([("T", HELPER)], True, "system-wide"),
        ([("M", "adminme/bridge_enrollment.py")], True, "system-wide"),
        ([("M", "adminme/products/bridge/routers/ingest.py")], True, "system-wide"),
        ([("M", "adminme/bridge_history.py")], True, "system-wide"),
        ([("M", "adminme/bridge_enrollment.py")], True, "system-wide"),
        ([("M", "bridge/adminme_bridge/enrollment_helper.py")], True, "system-wide"),
        ([("M", "bootstrap/bridge_enrollment.py")], True, "system-wide"),
        ([("M", "bootstrap/upgrade.py")], True, "system-wide"),
        ([("M", "bootstrap/source_identity.py")], True, "system-wide"),
        ([("M", "poetry.lock")], True, "system-wide"),
        ([("M", "bridge/poetry.lock")], True, "system-wide"),
        ([("M", "packs/skills/reply/SKILL.md")], True, "system-wide"),
        ([("M", "tests/unit/test_event_log.py")], True, "system-wide"),
        ([("A", "new-runtime.py")], True, "system-wide"),
        ([("M", "bootstrap/README.md")], True, "system-wide"),
        ([("M", "openclaw/README.md")], True, "system-wide"),
        ([("M", "README.md")], True, "system-wide"),
        ([("M", "bridge/README.md")], True, "system-wide"),
        ([("M", "docs/refactor/contract.json")], True, "system-wide"),
        ([("M", "docs/refactor/loader.py")], True, "system-wide"),
        ([("M", "docs/refactor/evidence/report.md")], True, "system-wide"),
        ([("M", "tests/unreviewed-policy.md")], True, "system-wide"),
        ([("M", ".github/unreviewed-policy.md")], True, "system-wide"),
        ([], True, "system-wide"),
    ],
)
def test_component_selection_is_narrow_and_fail_closed(
    changes: list[tuple[str, str]], equal: bool, expected: str
) -> None:
    assert (
        choose_scope(
            changes,
            interfaces_equal=equal,
            path_modes={path: {"100644"} for _, path in changes},
        )
        == expected
    )


@pytest.mark.parametrize("modes", [{"100755"}, {"100644", "100755"}, {"120000"}, set()])
def test_prose_scope_requires_regular_non_executable_git_modes(modes: set[str]) -> None:
    assert (
        choose_scope([("M", PROSE)], interfaces_equal=True, path_modes={PROSE: modes})
        == "system-wide"
    )


def _git(repository: Path, *arguments: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repository: Path, path: str, text: str) -> str:
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(repository, "add", "--", path)
    _git(repository, "commit", "-m", "Isolated release scope fixture")
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "scope repository"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Fixture author")
    _git(path, "config", "user.email", "fixture@example.invalid")
    _commit(path, "adminme/runtime.py", "RETAINED = True\n")
    _commit(path, HELPER, "#!/bin/bash\nexit 0\n")
    return path, _commit(path, PROSE, "# Synthetic bridge contract\n")


def _event(base: str, head: str, *, draft: bool = False) -> dict[str, Any]:
    return {
        "pull_request": {
            "draft": draft,
            "base": {"ref": "main", "sha": base},
            "head": {"sha": head},
        }
    }


def test_selector_uses_real_git_objects_not_worktree_or_supplied_paths(
    repository: tuple[Path, str],
) -> None:
    path, base = repository
    head = _commit(path, HELPER, "#!/bin/bash\nexit 1\n")
    # An uncommitted runtime edit cannot alter what the immutable candidate is.
    (path / "adminme/runtime.py").write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "bridge-helper"
    assert proof["changes"] == [{"status": "M", "path": HELPER}]
    changed = _commit(path, "adminme/runtime.py", "RETAINED = False\n")
    assert (
        select_scope(path, _event(base, changed), checkout_sha=changed)["scope"]
        == "system-wide"
    )
    with pytest.raises(ValueError, match="checkout differs"):
        select_scope(path, _event(base, head), checkout_sha=head)


def test_selector_binds_test_merge_to_exact_base_and_head(
    repository: tuple[Path, str],
) -> None:
    path, base = repository
    _git(path, "checkout", "-b", "helper")
    head = _commit(path, HELPER, "#!/bin/bash\nexit 1\n")
    _git(path, "checkout", "main")
    _git(path, "merge", "--no-ff", "helper", "-m", "Synthetic test merge")
    merged = _git(path, "rev-parse", "HEAD")
    assert (
        select_scope(path, _event(base, head), checkout_sha=merged)["scope"]
        == "bridge-helper"
    )
    with pytest.raises(ValueError, match="exact event"):
        select_scope(path, _event(head, base), checkout_sha=merged)


def test_selector_uses_prose_modes_from_both_git_endpoints(
    repository: tuple[Path, str],
) -> None:
    path, base = repository
    (path / PROSE).chmod(0o755)
    head = _commit(path, PROSE, "# Executable prose remains a runtime input\n")
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )
    (path / PROSE).unlink()
    _git(path, "add", "--", PROSE)
    _git(path, "commit", "-m", "Remove executable prose")
    deleted = _git(path, "rev-parse", "HEAD")
    assert (
        select_scope(path, _event(head, deleted), checkout_sha=deleted)["scope"]
        == "system-wide"
    )


def test_non_pr_runs_require_complete_system_validation(
    repository: tuple[Path, str],
) -> None:
    path, head = repository
    proof = select_scope(path, {}, checkout_sha=head)
    assert proof["scope"] == "system-wide"
    assert proof["draft"] is False


def _assert_storage_diagnostics_only(script: str) -> None:
    directory = "${RUNNER_TEMP}/adminme-consistency-pytest"
    # Keep exact existing diagnostic commands; no post-failure behavioral retry.
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


@pytest.mark.parametrize(("filename", "protected", "platform", "system"), RELEASE_JOBS)
def test_stable_protected_job_recomputes_scope_and_requires_real_worker_results(
    filename: str, protected: str, platform: str, system: str
) -> None:
    workflow = _workflow(filename)
    assert workflow["on"]["pull_request"]["branches"] == ["main"]
    assert "paths" not in workflow["on"]["pull_request"]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    jobs = workflow["jobs"]
    gate = jobs[protected]
    assert gate["name"] == protected
    assert gate["if"] == "${{ always() }}"
    expected = {
        "release-scope",
        "documentation-validation",
        "bridge-helper-validation",
        system,
    }
    if platform == "macos":
        expected.add("bridge-installed-runtime")
    assert set(gate["needs"]) == expected
    assert "continue-on-error" not in gate
    commands = "\n".join(step.get("run", "") for step in gate["steps"])
    assert f"scripts.release_scope gate --platform {platform}" in commands
    assert '--event "$GITHUB_EVENT_PATH" --checkout-sha "$GITHUB_SHA"' in commands
    check = gate["steps"][-1]
    assert check["env"] == {"RELEASE_NEEDS_JSON": "${{ toJSON(needs) }}"}
    assert "if" not in check
    for worker_name in expected:
        worker = jobs[worker_name]
        assert "continue-on-error" not in worker
        assert "validated_sha" in worker["outputs"]
        for step in worker["steps"]:
            assert "continue-on-error" not in step
    selector = jobs["release-scope"]
    assert "if" not in selector
    policy = "\n".join(step.get("run", "") for step in selector["steps"])
    assert "tests/unit/test_ci_release_gates.py" in policy
    assert "scripts.release_scope select" in policy
    assert "--github-output" in policy


@pytest.mark.parametrize(("filename", "protected", "platform", "system"), RELEASE_JOBS)
def test_system_scope_retains_all_complete_release_stages(
    filename: str, protected: str, platform: str, system: str
) -> None:
    job = _workflow(filename)["jobs"][system]
    assert "needs.release-scope.outputs.scope == 'system-wide'" in job["if"]
    assert "!github.event.pull_request.draft" in job["if"]
    assert FULL_STAGES[system] <= {step.get("name") for step in job["steps"]}
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    if platform == "linux":
        assert "tests/acceptance --runxfail" in commands
        assert "poetry -C bridge run pytest" in commands
        assert "npm test --prefix openclaw/administrateme" in commands
        storage = next(
            step
            for step in job["steps"]
            if step.get("name") == "Record hosted test storage"
        )
        assert storage["if"] == "${{ always() }}"
        _assert_storage_diagnostics_only(storage["run"])
    else:
        for script in (
            "clean-install.sh",
            "macos-bootstrap-rehearsal.sh",
            "backup-restore.sh",
            "release-candidate.sh",
        ):
            assert script in commands
    assert job["steps"][-1]["id"] == "validated"


@pytest.mark.parametrize(("filename", "protected", "platform", "system"), RELEASE_JOBS)
def test_helper_scope_requires_native_integrity_recovery_and_retained_compatibility(
    filename: str, protected: str, platform: str, system: str
) -> None:
    job = _workflow(filename)["jobs"]["bridge-helper-validation"]
    assert job["if"] == "${{ needs.release-scope.outputs.scope == 'bridge-helper' }}"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for path in (
        "test_member_bridge_helper_app.py",
        "test_member_bridge_helper_diagnostics.py",
        "test_member_bridge_permission_controller.py",
        "test_member_bridge_permission_recovery.py",
        "test_member_bridge_progress_synchronization.py",
        "test_cos_member_bridge_acquisition.py",
        "test_member_bridge_historical_attribution.py",
        "test_member_bridge_component.py",
        "test_self_service_member_bridge_enrollment.py",
        "test_bridge_readiness_transition.py",
        "test_ci_release_gates.py",
    ):
        assert path in commands
    assert "--runxfail" in commands
    assert "('skipped', 'failures', 'errors')" in commands
    assert "suites) > 0" in commands
    assert "scripts/verify_invariants.sh" in commands
    if platform == "macos":
        assert "test_builds_reviewed_document_helper" not in commands
        assert "test_sealed_component_reuses_exact_old_runtime" not in commands
    else:
        assert "-k 'not test_builds_reviewed_document_helper" in commands
        assert (
            "and not test_sealed_component_reuses_exact_old_runtime_and_preserves_rollback"
            in commands
        )
        assert (
            "and not test_sealed_mixed_release_keeps_exact_paired_archive_and_requires_central_negotiation"
            in commands
        )
    assert job["steps"][-1]["id"] == "validated"


def test_packaged_installation_requires_all_real_platform_cases() -> None:
    job = _workflow("macos-bootstrap.yml")["jobs"]["bridge-installed-runtime"]
    assert job["if"] == "${{ needs.release-scope.outputs.scope != 'documentation' }}"
    assert job["runs-on"] == "macos-26"
    install = next(
        step
        for step in job["steps"]
        if step.get("name")
        == "Validate packaged member installation and partial recovery"
    )
    assert "if" not in install
    assert install["env"]["ADMINME_RUN_BRIDGE_INSTALLER_RUNTIME"] == "1"
    for key in (
        "ADMINME_BRIDGE_INSTALLER_PYTHON",
        "ADMINME_BRIDGE_INSTALLER_HOMEBREW_PYTHON",
        "ADMINME_BRIDGE_INSTALLER_OLDER_DEVELOPER",
        "ADMINME_BRIDGE_INSTALLER_NEWER_SDK",
    ):
        assert key in install["env"]
    assert "suites) == 3" in install["run"]
    assert '("skipped", "failures", "errors")' in install["run"]
    assert job["steps"][-1]["id"] == "validated"
