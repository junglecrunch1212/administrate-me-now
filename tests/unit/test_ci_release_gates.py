"""Risk-based release gates must prove complete, exact-revision component evidence."""

from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from bootstrap.source_identity import SourceIdentityError
from scripts.release_scope import (
    CONTRACT,
    COS_SERVICE_WORK_APPLICATION,
    COS_SERVICE_WORK_CHECK_SCRIPT,
    COS_SERVICE_WORK_NATIVE_INSTALL,
    COS_SERVICE_WORK_COMPONENT_PATHS,
    COS_SERVICE_WORK_REQUIRED_PATHS,
    _service_work_application,
    UPDATER_COMPONENT_PATHS,
    UPDATER_TEST_PATHS,
    UPDATER_REQUIRED_PATHS,
    UPDATER_CHECK_SCRIPT,
    UPDATER_OPTIONAL_MODULE_TESTS,
    COS_REMINDER_PATHS,
    COS_REMINDER_REQUIRED_PATHS,
    BRIDGE_RUNTIME_PATHS,
    _bridge_runtime_dependency_fingerprint,
    COS_RECOVERY_COMPONENT_PATHS,
    COS_RECOVERY_CONTROLLER,
    COS_RECOVERY_HISTORY,
    COS_RECOVERY_TEST_PATHS,
    ENROLLMENT_INGEST_PATH,
    GOOGLE_MODULE,
    GOOGLE_SHARED_PATHS,
    COS_GOOGLE_WORKSPACE_COMPONENT_PATHS,
    COS_GOOGLE_WORKSPACE_REQUIRED_PATHS,
    _cos_google_workspace_dependency_fingerprint,
    _cos_apple_reminders_dependency_fingerprint,
    COS_APPLE_REMINDERS_COMPONENT_PATHS,
    COS_APPLE_REMINDERS_REQUIRED_PATHS,
    google_dependency_fingerprint,
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
    needs: dict[str, Any] = {
        worker: {
            "result": "success",
            "outputs": {"validated_sha": proof["checkout_sha"]},
        }
        for worker in required_workers(proof["scope"], platform)
    }
    needs["release-scope"]["outputs"].update(
        scope=proof["scope"], interface_sha256=proof["interface_sha256"]
    )
    return needs


@pytest.mark.parametrize(
    "scope",
    [
        "documentation",
        "bridge-helper",
        "bridge-enrollment",
        "bridge-runtime",
        "bridge-setup",
        "google-connections",
        "cos-google-workspace",
        "cos-apple-reminders",
        "cos-recovery-history",
        "cos-reminders",
        "updater-delivery",
        "cos-service-work",
        "system-wide",
    ],
)
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
@pytest.mark.parametrize(
    "scope",
    [
        "bridge-helper",
        "bridge-enrollment",
        "cos-recovery-history",
        "cos-reminders",
        "updater-delivery",
        "cos-service-work",
        "cos-google-workspace",
        "cos-apple-reminders",
    ],
)
def test_gate_refuses_development_or_reinterpreted_evidence(
    defect: str, scope: str
) -> None:
    proof = _proof(scope)
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
        ([("A", "bootstrap/member_bridge_launch.py")], True, "bridge-helper"),
        ([("M", "bootstrap/member_bridge_launch.py")], False, "system-wide"),
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


@pytest.mark.parametrize(
    ("changes", "equal", "modes", "expected"),
    [
        ([("M", ENROLLMENT_INGEST_PATH)], True, {"100644"}, "bridge-enrollment"),
        (
            [("M", ENROLLMENT_INGEST_PATH), ("M", HELPER), ("A", PROSE)],
            True,
            {"100644"},
            "bridge-enrollment",
        ),
        ([("M", ENROLLMENT_INGEST_PATH)], False, {"100644"}, "system-wide"),
        ([("A", ENROLLMENT_INGEST_PATH)], True, {"100644"}, "system-wide"),
        ([("D", ENROLLMENT_INGEST_PATH)], True, {"100644"}, "system-wide"),
        ([("T", ENROLLMENT_INGEST_PATH)], True, {"100644"}, "system-wide"),
        ([("M", ENROLLMENT_INGEST_PATH)], True, {"100755"}, "system-wide"),
        ([("M", ENROLLMENT_INGEST_PATH)], True, {"100644", "100755"}, "system-wide"),
        ([("M", ENROLLMENT_INGEST_PATH)], True, set(), "system-wide"),
        (
            [("M", ENROLLMENT_INGEST_PATH), ("M", "new-runtime.py")],
            True,
            {"100644"},
            "system-wide",
        ),
        (
            [("M", ENROLLMENT_INGEST_PATH), ("M", "tests/unreviewed.py")],
            True,
            {"100644"},
            "system-wide",
        ),
        (
            [("M", ENROLLMENT_INGEST_PATH), ("D", HELPER)],
            True,
            {"100644"},
            "system-wide",
        ),
        ([("M", HELPER)], True, {"100644"}, "system-wide"),
    ],
)
def test_enrollment_scope_has_an_explicit_narrow_route_contract(
    changes: list[tuple[str, str]], equal: bool, modes: set[str], expected: str
) -> None:
    path_modes = {
        path: {"100755" if path == HELPER else "100644"} for _, path in changes
    }
    path_modes[ENROLLMENT_INGEST_PATH] = modes
    assert (
        choose_scope(
            changes,
            interfaces_equal=False,
            enrollment_interfaces_equal=equal,
            path_modes=path_modes,
        )
        == expected
    )


def test_enrollment_scope_cannot_be_inferred_from_full_interface_flag() -> None:
    assert (
        choose_scope(
            [("M", ENROLLMENT_INGEST_PATH)],
            interfaces_equal=True,
            path_modes={ENROLLMENT_INGEST_PATH: {"100644"}},
        )
        == "system-wide"
    )


def test_enrollment_gate_requires_native_package_worker_on_macos() -> None:
    assert required_workers("bridge-enrollment", "linux") == (
        "release-scope",
        "bridge-development",
    )
    assert required_workers("bridge-enrollment", "macos") == (
        "release-scope",
        "bridge-development",
        "bridge-installed-runtime",
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
    helper = path / HELPER
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("#!/bin/bash\nexit 0\n")
    helper.chmod(0o755)
    _commit(path, HELPER, "#!/bin/bash\nexit 0\n")
    _commit(path, ENROLLMENT_INGEST_PATH, "RETAINED_INGEST = True\n")
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


def test_enrollment_selector_uses_immutable_route_and_outside_interfaces(
    repository: tuple[Path, str],
) -> None:
    path, base = repository
    _commit(path, HELPER, "#!/bin/bash\nexit 1\n")
    head = _commit(path, ENROLLMENT_INGEST_PATH, "RETAINED_INGEST = False\n")
    (path / "adminme/runtime.py").write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "bridge-enrollment"
    assert len(proof["enrollment_interface_sha256"]) == 64
    assert {item["path"] for item in proof["changes"]} == {
        HELPER,
        ENROLLMENT_INGEST_PATH,
    }
    verify_gate(proof, _needs(proof, "macos"), platform="macos")
    draft = select_scope(path, _event(base, head, draft=True), checkout_sha=head)
    assert draft["scope"] == "bridge-enrollment"
    with pytest.raises(ValueError, match="development-only"):
        verify_gate(draft, _needs(draft, "macos"), platform="macos")


@pytest.mark.parametrize(
    "other",
    [
        "adminme/bridge_enrollment.py",
        "adminme/bridge_history.py",
        "adminme/events/log.py",
        "adminme/events/schemas/bridge.py",
        "adminme/products/bridge/auth.py",
        "adminme/runtime/application.py",
        "adminme/locks.py",
        "bootstrap/bridge_enrollment.py",
        "bridge/adminme_bridge/enrollment_helper.py",
        "pyproject.toml",
        "poetry.lock",
        "bridge/poetry.lock",
        "new-runtime.py",
    ],
)
def test_enrollment_selector_rejects_shared_dependencies_and_unknown_paths(
    repository: tuple[Path, str],
    other: str,
) -> None:
    path, _base = repository
    base = _commit(path, other, "RETAINED = True\n")
    _commit(path, ENROLLMENT_INGEST_PATH, "RETAINED_INGEST = False\n")
    head = _commit(path, other, "RETAINED = False\n")
    event = _event(base, head)
    event.update(
        enrollment_interfaces_equal=True, changed_paths=[ENROLLMENT_INGEST_PATH]
    )
    assert select_scope(path, event, checkout_sha=head)["scope"] == "system-wide"


@pytest.mark.parametrize("change", ["add", "delete", "executable"])
def test_enrollment_selector_requires_existing_regular_route_at_both_endpoints(
    repository: tuple[Path, str],
    change: str,
) -> None:
    path, base = repository
    route = path / ENROLLMENT_INGEST_PATH
    if change in {"add", "delete"}:
        _git(path, "rm", "--", ENROLLMENT_INGEST_PATH)
        _git(path, "commit", "-m", "Remove synthetic route")
        head = _git(path, "rev-parse", "HEAD")
        if change == "add":
            base = head
            head = _commit(path, ENROLLMENT_INGEST_PATH, "NEW_ROUTE = True\n")
    else:
        route.chmod(0o755)
        head = _commit(path, ENROLLMENT_INGEST_PATH, "CHANGED_ROUTE = True\n")
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "drift",
    [
        "none",
        "blob",
        "mode",
        "unknown",
        "unknown-test",
        "unknown-ci",
        "missing-route",
        "executable-route",
    ],
)
def test_enrollment_selector_checks_exact_test_merge_runtime_objects(
    repository: tuple[Path, str],
    drift: str,
) -> None:
    path, base = repository
    _git(path, "checkout", "-b", "enrollment")
    head = _commit(path, ENROLLMENT_INGEST_PATH, "RETAINED_INGEST = False\n")
    if drift == "blob":
        (path / "adminme/runtime.py").write_text("MERGE_ONLY_RUNTIME_DRIFT = True\n")
    elif drift == "mode":
        (path / "adminme/runtime.py").chmod(0o755)
    elif drift == "unknown":
        (path / "merge-only-runtime.py").write_text("UNREVIEWED = True\n")
    elif drift in {"unknown-test", "unknown-ci"}:
        unexpected = path / (
            "tests/unreviewed.py"
            if drift == "unknown-test"
            else ".github/unreviewed.yml"
        )
        unexpected.parent.mkdir(parents=True, exist_ok=True)
        unexpected.write_text("UNREVIEWED = True\n")
    elif drift == "missing-route":
        (path / ENROLLMENT_INGEST_PATH).unlink()
    elif drift == "executable-route":
        (path / ENROLLMENT_INGEST_PATH).chmod(0o755)
    _git(path, "add", "-A")
    tree = _git(path, "write-tree")
    # Correct parents do not imply an unchanged merge tree. A conflict result
    # must not smuggle shared runtime bytes into this component scope.
    merge = _git(
        path,
        "commit-tree",
        tree,
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Synthetic exact-parent merge",
    )
    _git(path, "checkout", "--detach", merge)
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == ("bridge-enrollment" if drift == "none" else "system-wide")
    assert proof["checkout_sha"] == merge
    if drift == "none":
        needs = _needs(proof, "macos")
        verify_gate(proof, needs, platform="macos")
        needs["bridge-development"]["outputs"]["validated_sha"] = head
        with pytest.raises(ValueError, match="revision"):
            verify_gate(proof, needs, platform="macos")


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
        "bridge-development",
        "google-connections-validation",
        "cos-recovery-history-validation",
        "cos-reminder-validation",
        "updater-delivery-validation",
        "cos-service-work-validation",
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
        "test_member_bridge_launch.py",
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


@pytest.mark.parametrize("filename", [row[0] for row in RELEASE_JOBS])
def test_bootstrap_type_checks_include_the_imported_bridge_sources(
    filename: str,
) -> None:
    checks = []
    for job in _workflow(filename)["jobs"].values():
        for step in job["steps"]:
            for line in step.get("run", "").splitlines():
                if "poetry run mypy" not in line:
                    continue
                tokens = shlex.split(line, comments=True)
                if "bootstrap" in tokens:
                    checks.append(tokens)
    assert checks
    assert all("bridge/adminme_bridge" in tokens for tokens in checks)
    if filename == "macos-bootstrap.yml":
        steps = _workflow(filename)["jobs"]["bridge-development"]["steps"]
        names = [step.get("name") for step in steps]
        assert names.index("Validate helper bootstrap type boundary") < names.index(
            "Validate native helper and enrollment composition"
        )


@pytest.mark.parametrize("filename", [row[0] for row in RELEASE_JOBS])
def test_bridge_development_includes_strict_readiness_composition(
    filename: str,
) -> None:
    steps = _workflow(filename)["jobs"]["bridge-development"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    for boundary in (
        "tests/integration/test_runtime_application.py",
        "tests/acceptance/test_cos_self_replies.py",
        "tests/unit/bootstrap/test_cos_channel_activation.py",
        "tests/unit/bootstrap/test_openclaw_plugin_reconciliation.py",
        "tests/unit/test_source_assurance.py",
        "tests/unit/test_historical_attribution.py",
    ):
        assert boundary in commands
    assert "--runxfail" in commands


def test_packaged_installation_requires_all_real_platform_cases() -> None:
    job = _workflow("macos-bootstrap.yml")["jobs"]["bridge-installed-runtime"]
    assert (
        job["if"]
        == '${{ contains(fromJSON(\'["bridge-helper","bridge-enrollment","bridge-runtime","bridge-setup","system-wide"]\'), needs.release-scope.outputs.scope) }}'
    )
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


def test_packaged_gate_requires_real_sealed_launchd_and_rejects_skips() -> None:
    job = _workflow("macos-bootstrap.yml")["jobs"]["bridge-installed-runtime"]
    step = next(
        step
        for step in job["steps"]
        if step.get("name")
        == "Validate sealed launch lifecycle against real macOS launchd"
    )
    assert "if" not in step
    assert step["env"]["ADMINME_RUN_BRIDGE_LAUNCH_RUNTIME"] == "1"
    commands = step["run"]
    assert "test_member_bridge_launch_runtime.py" in commands
    assert "== 2" in commands
    assert '("skipped", "failures", "errors")' in commands
    assert job["steps"][-1]["id"] == "validated"


@pytest.mark.parametrize(("filename", "protected", "platform", "system"), RELEASE_JOBS)
def test_enrollment_worker_requires_complete_affected_contract_and_final_binding(
    filename: str,
    protected: str,
    platform: str,
    system: str,
) -> None:
    job = _workflow(filename)["jobs"]["bridge-development"]
    assert (
        job["if"]
        == "${{ needs.release-scope.outputs.scope == 'bridge-enrollment' || needs.release-scope.outputs.scope == 'bridge-runtime' || needs.release-scope.outputs.scope == 'bridge-setup' || (github.event.pull_request.draft && needs.release-scope.outputs.scope == 'system-wide') }}"
    )
    assert job["outputs"] == {"validated_sha": "${{ steps.validated.outputs.sha }}"}
    assert job["steps"][-1]["id"] == "validated"
    assert "if" not in job["steps"][-1]
    assert 'test "$actual_sha" = "$GITHUB_SHA"' in job["steps"][-1]["run"]
    assert all("continue-on-error" not in step for step in job["steps"])
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for boundary in (
        "test_self_service_member_bridge_enrollment.py",
        "tests/unit/adapters/calendaring",
        "tests/acceptance/test_apple_calendar_native_date_readback.py",
        "test_member_bridge_permission_recovery.py",
        "test_member_bridge_progress_synchronization.py",
        "test_member_bridge_knowledge_vertical_slice.py",
        "test_cos_member_bridge_acquisition.py",
        "test_member_bridge_historical_attribution.py",
        "test_bridge_readiness_transition.py",
        "test_apple_messages_bridge_ingestion.py",
        "tests/unit/products/bridge",
        "tests/integration/test_bridge_ingest_roundtrip.py",
        "tests/integration/test_bridge_state_mirror_onramp.py",
        "tests/unit/test_bridge_authorization.py",
        "tests/unit/test_bridge_release_identity.py",
        "tests/unit/bootstrap/test_bridge_enrollment.py",
        "tests/unit/bootstrap/test_member_bridge_launch.py",
        "tests/unit/bootstrap/test_member_bridge_component.py",
        "tests/unit/bootstrap/test_source_identity.py",
    ):
        assert boundary in commands
    assert "--runxfail" in commands
    assert "poetry -C bridge run pytest" in commands
    assert '--junitxml="${RUNNER_TEMP}/bridge-enrollment.xml"' in commands
    assert '--junitxml="${RUNNER_TEMP}/bridge-package.xml"' in commands
    assert "scripts/verify_invariants.sh" in commands
    checker = next(
        step
        for step in job["steps"]
        if step.get("name") == "Require complete bridge enrollment evidence"
    )
    assert "if" not in checker
    check = checker["run"]
    for required in (
        '"${RUNNER_TEMP}/bridge-enrollment.xml"',
        '"${RUNNER_TEMP}/bridge-package.xml"',
        "k in s.attrib and s.attrib[k].isdigit()",
        'tests > 0 and len(list(root.iter("testcase"))) == tests',
        "int(s.attrib[k]) == 0",
        "not any(list(root.iter(k))",
        '("skipped", "failure", "error")',
    ):
        assert required in check
    native = (
        "test_builds_reviewed_document_helper_without_secret_or_remote_execution",
        "test_sealed_component_reuses_exact_old_runtime_and_preserves_rollback",
        "test_sealed_mixed_release_keeps_exact_paired_archive_and_requires_central_negotiation",
    )
    if platform == "linux":
        for case in native:
            assert "not " + case in commands
        assert commands.count("-k ") == 1
    else:
        assert "-k " not in commands
        browser = next(
            step
            for step in job["steps"]
            if step.get("name") == "Validate enrollment browser behavior"
        )
        assert "if" not in browser
        assert browser["env"]["ADMINME_GATE7_BRIDGE_ENROLLMENT_ONLY"] == "1"
        assert "test:gate7" in browser["run"]


@pytest.mark.parametrize("filename", [row[0] for row in RELEASE_JOBS])
@pytest.mark.parametrize("report", ["enrollment", "package"])
@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "missing",
        "missing-attribute",
        "noninteger",
        "empty",
        "zero",
        "skipped-count",
        "skipped-element",
        "failure-count",
        "failure-element",
        "error-element",
        "wrong-count",
    ],
)
def test_actual_enrollment_junit_gate_rejects_incomplete_evidence(
    filename: str,
    report: str,
    defect: str,
    tmp_path: Path,
) -> None:
    steps = _workflow(filename)["jobs"]["bridge-development"]["steps"]
    checker = next(
        step
        for step in steps
        if step.get("name") == "Require complete bridge enrollment evidence"
    )
    # Execute the workflow's actual parser in isolated CI, so a future workflow
    # edit cannot make a malformed or partially skipped report pass unnoticed.
    script = checker["run"].split("<<'PY_CHECK'\n", 1)[1].rsplit("\nPY_CHECK", 1)[0]
    valid = '<testsuites><testsuite tests="1" skipped="0" failures="0" errors="0"><testcase name="fixture"/></testsuite></testsuites>'
    paths = {name: tmp_path / f"{name}.xml" for name in ("enrollment", "package")}
    for path in paths.values():
        path.write_text(valid)
    invalid = valid
    if defect == "missing":
        paths[report].unlink()
    else:
        if defect == "missing-attribute":
            invalid = valid.replace(' errors="0"', "")
        elif defect == "noninteger":
            invalid = valid.replace('failures="0"', 'failures="unknown"')
        elif defect == "empty":
            invalid = "<testsuites/>"
        elif defect == "zero":
            invalid = '<testsuites><testsuite tests="0" skipped="0" failures="0" errors="0"/></testsuites>'
        elif defect == "skipped-count":
            invalid = valid.replace('skipped="0"', 'skipped="1"')
        elif defect == "failure-count":
            invalid = valid.replace('failures="0"', 'failures="1"')
        elif defect.endswith("-element"):
            element = defect.removesuffix("-element")
            invalid = valid.replace(
                '<testcase name="fixture"/>',
                f'<testcase name="fixture"><{element}/></testcase>',
            )
        elif defect == "wrong-count":
            invalid = valid.replace('tests="1"', 'tests="2"')
        paths[report].write_text(invalid)
    result = subprocess.run(
        [sys.executable, "-c", script, str(paths["enrollment"]), str(paths["package"])],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert (result.returncode == 0) is (defect == "none")


@pytest.mark.parametrize(
    "path",
    [
        HELPER,
        "bootstrap/member_bridge_launch.py",
        "tests/unit/test_ci_release_gates.py",
        ".github/workflows/macos-bootstrap.yml",
    ],
)
@pytest.mark.parametrize("bad_modes", [{"100644", "100755"}, {"120000"}, set()])
def test_enrollment_component_modes_cannot_change_or_be_unknown(
    path: str, bad_modes: set[str]
) -> None:
    assert (
        choose_scope(
            [("M", ENROLLMENT_INGEST_PATH), ("M", path)],
            interfaces_equal=False,
            enrollment_interfaces_equal=True,
            path_modes={ENROLLMENT_INGEST_PATH: {"100644"}, path: bad_modes},
        )
        == "system-wide"
    )


@pytest.mark.parametrize(
    "path,mode",
    [
        (HELPER, "100644"),
        ("bootstrap/member_bridge_launch.py", "100755"),
        ("tests/unit/test_ci_release_gates.py", "100755"),
        (".github/workflows/macos-bootstrap.yml", "100755"),
    ],
)
def test_enrollment_component_requires_expected_regular_file_mode(
    path: str, mode: str
) -> None:
    assert (
        choose_scope(
            [("M", ENROLLMENT_INGEST_PATH), ("M", path)],
            interfaces_equal=False,
            enrollment_interfaces_equal=True,
            path_modes={ENROLLMENT_INGEST_PATH: {"100644"}, path: {mode}},
        )
        == "system-wide"
    )


@pytest.mark.parametrize("shared_path", sorted(GOOGLE_SHARED_PATHS))
def test_google_scope_accepts_only_exact_wiring_and_unchanged_dependencies(
    repository: tuple[Path, str], shared_path: str
) -> None:
    path, _initial = repository
    contract = json.loads((ROOT / "scripts/google_connection_wiring.json").read_text())
    pair = contract["paths"][shared_path][0]
    baseline = pair["before"] + "\n# Unrelated retained behavior\n"
    base = _commit(path, shared_path, baseline)
    _commit(path, GOOGLE_MODULE, "# Google connection implementation\n")
    head = _commit(path, shared_path, baseline.replace(pair["before"], pair["after"]))
    assert google_dependency_fingerprint(path, base) == google_dependency_fingerprint(
        path, head
    )
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "google-connections"
    )
    # A shared filename is not a blanket exemption: even an adjacent edit fails closed.
    changed = _commit(
        path,
        shared_path,
        baseline.replace(pair["before"], pair["after"])
        + "# Unreviewed runtime change\n",
    )
    assert (
        select_scope(path, _event(base, changed), checkout_sha=changed)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "changed",
    [
        "poetry.lock",
        "bootstrap/upgrade.py",
        "adminme/bridge_enrollment.py",
        "bridge/adminme_bridge/daemon.py",
    ],
)
def test_google_scope_never_exempts_dependency_or_bridge_changes(
    repository: tuple[Path, str], changed: str
) -> None:
    path, base = repository
    _commit(path, GOOGLE_MODULE, "# Google component\n")
    head = _commit(path, changed, "# Changed dependency\n")
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "status,modes", [("D", {"100644"}), ("T", {"100644", "120000"}), ("M", {"100755"})]
)
def test_google_scope_rejects_removed_or_executable_inputs(
    status: str, modes: set[str]
) -> None:
    assert (
        choose_scope(
            [(status, GOOGLE_MODULE)],
            interfaces_equal=False,
            path_modes={GOOGLE_MODULE: modes},
            google_interfaces_equal=True,
        )
        == "system-wide"
    )


@pytest.mark.parametrize("filename", [row[0] for row in RELEASE_JOBS])
def test_google_release_requires_production_packaged_privacy_recovery_and_types(
    filename: str,
) -> None:
    job = _workflow(filename)["jobs"]["google-connections-validation"]
    assert (
        job["if"]
        == "${{ needs.release-scope.outputs.scope == 'google-connections' || needs.release-scope.outputs.scope == 'cos-google-workspace' || needs.release-scope.outputs.scope == 'cos-apple-reminders' }}"
    )
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for required in (
        "test_member_google_connections.py",
        "test_cos_google_workspace.py",
        "test_cos_apple_reminders.py",
        "test_cos_reminders_helper.py",
        "test_cos_reminders_helper_app.py",
        "test_cos_apple_reminders_context.py",
        "test_cos_service_planning_context.py",
        "test_cos_service_working_context.py",
        "test_cos_service_private_work.py",
        "test_lists_projection_runner.py",
        "test_capability_catalog.py",
        "test_adapter_schemas.py",
        "test_lists_schemas.py",
        "packs/adapters/reminders/apple-eventkit/tests",
        "test_google_oauth.py",
        "test_credentials.py",
        "test_provider_bindings.py",
        "test_google_calendar_pack_internal.py",
        "test_google_tasks_pack_internal.py",
        "test_service_owned_google_sources.py",
        "test_google_calendar.py",
        "test_google_tasks.py",
        "test_cos_channel_activation.py",
        "test_governance.py",
        "test_b03_google_tasks_create_production.py",
        "test_cos_self_replies.py",
        "test_cos_response_execution.py",
        "--runxfail",
        "('skipped', 'failures', 'errors')",
        "suites) > 0",
        "poetry run mypy",
        "poetry run pyright",
        "scripts/verify_invariants.sh",
    ):
        assert required in commands
    assert job["steps"][-1]["id"] == "validated"
    assert "bridge-installed-runtime" not in required_workers(
        "google-connections", "macos"
    )


def _recovery_candidate(repository: Path) -> tuple[str, str]:
    _commit(repository, COS_RECOVERY_CONTROLLER, "RETAINED_CONTROLLER = True\n")
    base = _commit(
        repository, "bootstrap/member_bridge_component.py", "RETAINED_CONTRACT = True\n"
    )
    _commit(repository, COS_RECOVERY_CONTROLLER, "RECOVERY_CONTROLLER = True\n")
    return base, _commit(repository, COS_RECOVERY_HISTORY, "PRESERVE_HISTORY = True\n")


def test_recovery_scope_binds_new_module_and_closed_edit_set_to_immutable_sources(
    repository: tuple[Path, str],
) -> None:
    path, _ = repository
    base, _ = _recovery_candidate(path)
    for allowed in (
        "bootstrap/member_bridge_component.py",
        *sorted(COS_RECOVERY_TEST_PATHS),
        "scripts/release_scope.py",
        PROSE,
    ):
        head = _commit(path, allowed, "# Reviewed recovery contract\n")
    (path / "adminme/runtime.py").write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "cos-recovery-history"
    assert len(proof["cos_recovery_dependency_sha256"]) == 64
    assert {item["path"] for item in proof["changes"]} >= COS_RECOVERY_COMPONENT_PATHS
    for platform in ("linux", "macos"):
        assert required_workers(proof["scope"], platform) == (
            "release-scope",
            "cos-recovery-history-validation",
        )
        verify_gate(proof, _needs(proof, platform), platform=platform)


@pytest.mark.parametrize(
    "other",
    [
        "adminme/runtime/application.py",
        "adminme/events/log.py",
        "adminme/events/schemas/cos_channels.py",
        "bootstrap/candidate_readiness.py",
        "bootstrap/backup_restore.py",
        "bootstrap/upgrade.py",
        "bootstrap/source_identity.py",
        "adminme/locks.py",
        "poetry.lock",
        "new-runtime.py",
        "tests/unreviewed.py",
    ],
)
def test_recovery_scope_never_exempts_shared_or_unknown_dependencies(
    repository: tuple[Path, str], other: str
) -> None:
    path, _ = repository
    _commit(path, other, "RETAINED = True\n")
    base, _ = _recovery_candidate(path)
    head = _commit(path, other, "CHANGED = True\n")
    event = _event(base, head)
    event.update(
        cos_recovery_interfaces_equal=True, changed_paths=[COS_RECOVERY_HISTORY]
    )
    assert select_scope(path, event, checkout_sha=head)["scope"] == "system-wide"


@pytest.mark.parametrize("path", sorted(COS_RECOVERY_COMPONENT_PATHS))
@pytest.mark.parametrize("defect", ["removed", "executable"])
def test_recovery_component_requires_all_regular_sources(
    repository: tuple[Path, str], path: str, defect: str
) -> None:
    repository_path, _ = repository
    base, _ = _recovery_candidate(repository_path)
    if defect == "removed":
        _git(repository_path, "rm", "--", path)
    else:
        (repository_path / path).chmod(0o755)
        _git(repository_path, "add", "--", path)
    _git(repository_path, "commit", "-m", "Unsafe recovery fixture")
    head = _git(repository_path, "rev-parse", "HEAD")
    assert (
        select_scope(repository_path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "drift", ["none", "runtime", "mode", "unknown-test", "missing-history"]
)
def test_recovery_scope_checks_exact_test_merge_tree(
    repository: tuple[Path, str], drift: str
) -> None:
    path, _ = repository
    base, head = _recovery_candidate(path)
    if drift == "runtime":
        (path / "adminme/runtime.py").write_text("MERGE_ONLY_CHANGE = True\n")
    elif drift == "mode":
        (path / "adminme/runtime.py").chmod(0o755)
    elif drift == "unknown-test":
        target = path / "tests/merge_only.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("UNREVIEWED = True\n")
    elif drift == "missing-history":
        (path / COS_RECOVERY_HISTORY).unlink()
    _git(path, "add", "-A")
    merge = _git(
        path,
        "commit-tree",
        _git(path, "write-tree"),
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Synthetic exact-parent recovery merge",
    )
    _git(path, "checkout", "--detach", merge)
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == (
        "cos-recovery-history" if drift == "none" else "system-wide"
    )


@pytest.mark.parametrize("filename", [row[0] for row in RELEASE_JOBS])
def test_recovery_worker_requires_affected_persistence_and_compatibility_contracts(
    filename: str,
) -> None:
    job = _workflow(filename)["jobs"]["cos-recovery-history-validation"]
    assert job["if"] == (
        "${{ needs.release-scope.outputs.scope == 'cos-recovery-history' }}"
    )
    assert job["steps"][-1]["id"] == "validated"
    assert "if" not in job["steps"][-1]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for required in (
        "test_cos_recovery_history.py",
        "test_cos_recovery_history_operation.py",
        "test_cos_channel_activation.py",
        "test_cos_live_maintenance.py",
        "test_candidate_readiness.py",
        "test_htr10_export.py",
        "test_member_bridge_component.py",
        "test_streaming_backup_round_trip_and_wrong_key_cleanup",
        "test_interrupted_activation_restores_exact_predecessor_bytes",
        "test_transactional_activation_orders_fresh_rehearsal_before_selectors",
        "test_production_upgrade_controller_must_be_the_reviewed_candidate",
        "test_ci_release_gates.py",
        "--runxfail",
        "poetry run mypy",
        "poetry run pyright",
        "scripts/verify_invariants.sh",
    ):
        assert required in commands
    assert '--junitxml="$RUNNER_TEMP/cos-recovery-history.xml"' in commands
    assert "test_member_google_connections.py" not in commands
    assert "test:gate7" not in commands
    assert "scripts/acceptance/release-candidate.sh" not in commands
    assert "ADMINME_RUN_BRIDGE_INSTALLER_RUNTIME" not in commands
    assert commands.count("-k ") == 1
    assert "not test_sealed_component_reuses_exact_old_runtime" in commands
    assert "and not test_sealed_mixed_release_keeps_exact_paired_archive" in commands


@pytest.mark.parametrize("filename", [row[0] for row in RELEASE_JOBS])
@pytest.mark.parametrize(
    "defect",
    ["none", "missing", "empty", "missing-count", "skipped", "failure", "wrong-count"],
)
def test_actual_recovery_junit_gate_rejects_missing_or_partial_evidence(
    filename: str, defect: str, tmp_path: Path
) -> None:
    steps = _workflow(filename)["jobs"]["cos-recovery-history-validation"]["steps"]
    checker = next(
        step
        for step in steps
        if step.get("name") == "Require complete recovery history evidence"
    )
    assert "if" not in checker
    script = checker["run"].split("<<'PY_CHECK'\n", 1)[1].rsplit("\nPY_CHECK", 1)[0]
    report = tmp_path / "recovery.xml"
    payload = '<testsuites><testsuite tests="1" skipped="0" failures="0" errors="0"><testcase name="fixture"/></testsuite></testsuites>'
    if defect == "empty":
        payload = "<testsuites/>"
    elif defect == "missing-count":
        payload = payload.replace(' errors="0"', "")
    elif defect in {"skipped", "failure"}:
        payload = payload.replace(
            '<testcase name="fixture"/>',
            f'<testcase name="fixture"><{defect}/></testcase>',
        )
    elif defect == "wrong-count":
        payload = payload.replace('tests="1"', 'tests="2"')
    if defect != "missing":
        report.write_text(payload)
    result = subprocess.run(
        [sys.executable, "-c", script, str(report)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) is (defect == "none")


@pytest.mark.parametrize(
    "runtime_path",
    [
        "bootstrap/member_bridge_runtime_repair.py",
        "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py",
        "packs/adapters/reminders/apple-eventkit/adapter.py",
        "packs/adapters/calendaring/apple-calendar/adapter.py",
    ],
)
def test_bridge_runtime_scope_is_closed_to_reviewed_repair_and_dependencies(
    runtime_path,
):
    changes = [
        ("M", runtime_path),
        ("A", "tests/unit/adapters/reminders/test_eventkit_selected_sources.py"),
        ("A", "tests/unit/adapters/calendaring/test_apple_calendar_native_dates.py"),
        ("A", "tests/acceptance/test_apple_calendar_native_date_readback.py"),
        (
            "M",
            "tests/integration/test_adapter_apple_eventkit_dual_deployment_integration.py",
        ),
    ]
    modes = {path: {"100644"} for _, path in changes}
    assert (
        choose_scope(
            changes,
            interfaces_equal=False,
            path_modes=modes,
            bridge_runtime_interfaces_equal=True,
        )
        == "bridge-runtime"
    )
    assert (
        choose_scope(changes, interfaces_equal=False, path_modes=modes) == "system-wide"
    )
    for extra in (
        "adminme/events/log.py",
        "bridge/poetry.lock",
        "adminme/bridge_authorization.py",
        "adminme/events/schemas/lists.py",
        "bridge/adminme_bridge/adapters/sinks.py",
        "tests/unreviewed.py",
        "bootstrap/arbitrary.py",
        "adminme/projections/calendars/queries.py",
        "packs/adapters/calendaring/google-calendar/adapter.py",
    ):
        assert (
            choose_scope(
                changes + [("M", extra)],
                interfaces_equal=False,
                path_modes={**modes, extra: {"100644"}},
                bridge_runtime_interfaces_equal=True,
            )
            == "system-wide"
        )
    assert (
        choose_scope(
            changes,
            interfaces_equal=False,
            path_modes={**modes, changes[0][1]: {"120000"}},
            bridge_runtime_interfaces_equal=True,
        )
        == "system-wide"
    )
    assert (
        choose_scope(
            [("D", runtime_path), *changes[1:]],
            interfaces_equal=False,
            path_modes=modes,
            bridge_runtime_interfaces_equal=True,
        )
        == "system-wide"
    )
    assert (
        choose_scope(
            changes[1:],
            interfaces_equal=False,
            path_modes=modes,
            bridge_runtime_interfaces_equal=True,
        )
        == "system-wide"
    )


def test_bridge_runtime_fingerprint_binds_every_unreviewed_byte_and_mode():
    from bootstrap.source_identity import GitTreeEntry

    def entry(path, blob="a" * 40, mode="100644"):
        return GitTreeEntry(path=path, mode=mode, object_id=blob)

    base = (entry("adminme/events/log.py"),)
    changed = (entry("adminme/events/log.py", "b" * 40),)
    assert _bridge_runtime_dependency_fingerprint(
        base
    ) != _bridge_runtime_dependency_fingerprint(changed)
    for path in BRIDGE_RUNTIME_PATHS:
        mode = "100755" if path.endswith(".sh") else "100644"
        assert _bridge_runtime_dependency_fingerprint(
            base
        ) == _bridge_runtime_dependency_fingerprint((*base, entry(path, mode=mode)))
        assert _bridge_runtime_dependency_fingerprint(
            base
        ) != _bridge_runtime_dependency_fingerprint((*base, entry(path, mode="120000")))


def _reminder_candidate(repository: Path) -> tuple[str, str]:
    base = _git(repository, "rev-parse", "HEAD")
    for path in sorted(COS_REMINDER_REQUIRED_PATHS):
        head = _commit(repository, path, "# Synthetic reminder boundary\n")
    return base, head


def test_reminder_scope_checks_closed_sources_and_normal_protected_workers(
    repository: tuple[Path, str],
) -> None:
    path, _ = repository
    base, head = _reminder_candidate(path)
    for allowed in sorted(COS_REMINDER_PATHS - COS_REMINDER_REQUIRED_PATHS):
        head = _commit(path, allowed, "# Reviewed reminder boundary\n")
        if allowed == "scripts/acceptance/openclaw-source-install.sh":
            (path / allowed).chmod(0o755)
            _git(path, "add", allowed)
            _git(path, "commit", "-m", "Retain executable script mode")
            head = _git(path, "rev-parse", "HEAD")
    # Mutable checkout claims cannot narrow the immutable candidate.
    (path / "adminme/runtime.py").write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "cos-reminders"
    assert len(proof["cos_reminder_dependency_sha256"]) == 64
    for platform in ("linux", "macos"):
        assert required_workers("cos-reminders", platform) == (
            "release-scope",
            "cos-reminder-validation",
        )
        verify_gate(proof, _needs(proof, platform), platform=platform)


@pytest.mark.parametrize("broader_change", [False, True])
def test_reminder_work_link_uses_focused_scope_without_widening_storage(
    repository: tuple[Path, str], broader_change: bool
) -> None:
    path, _ = repository
    _reminder_candidate(path)
    base = _commit(path, "adminme/cos_reminder_work.py", "# Existing work link\n")
    head = _commit(path, "adminme/cos_reminder_work.py", "# Reviewed work link\n")
    if broader_change:
        head = _commit(path, "adminme/events/log.py", "# Broader persistence change\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == ("system-wide" if broader_change else "cos-reminders")
    assert (
        "tests/acceptance/test_cos_service_private_work.py"
        in (ROOT / "scripts/acceptance/cos-reminder-release.sh").read_text()
    )


@pytest.mark.parametrize("extra", ["none", "dependency", "unknown", "mode", "deleted"])
def test_attribution_only_repair_uses_existing_worker_without_relaxing_boundary(
    repository: tuple[Path, str], extra: str
) -> None:
    path, _ = repository
    _reminder_candidate(path)
    source = "adminme/historical_attribution.py"
    regression = "tests/unit/test_historical_attribution.py"
    _commit(path, "poetry.lock", "# Retained dependencies\n")
    _commit(path, source, "# Retained attribution behavior\n")
    base = _commit(path, regression, "# Existing attribution proof\n")
    _commit(path, source, "# Reviewed manual-member attribution repair\n")
    head = _commit(path, regression, "# Real authenticated-emitter regression\n")
    if extra == "dependency":
        head = _commit(path, "poetry.lock", "# Changed dependencies\n")
    elif extra == "unknown":
        head = _commit(path, "tests/unreviewed.py", "# Unreviewed test boundary\n")
    elif extra in {"mode", "deleted"}:
        if extra == "mode":
            (path / source).chmod(0o755)
        else:
            (path / source).unlink()
        _git(path, "add", "--all")
        _git(path, "commit", "-m", "Unsupported attribution source change")
        head = _git(path, "rev-parse", "HEAD")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == ("cos-reminders" if extra == "none" else "system-wide")
    if extra == "none":
        assert {change["path"] for change in proof["changes"]} == {source, regression}
        for platform in ("linux", "macos"):
            assert required_workers(proof["scope"], platform) == (
                "release-scope",
                "cos-reminder-validation",
            )
            verify_gate(proof, _needs(proof, platform), platform=platform)
        script = (ROOT / "scripts/acceptance/cos-reminder-release.sh").read_text()
        # Execute the entire file, including newly added production-emitter proof.
        assert "  tests/unit/test_historical_attribution.py \\" in script.splitlines()
        assert "  tests/unit/test_source_assurance.py \\" in script.splitlines()


@pytest.mark.parametrize(
    "other",
    [
        "adminme/events/log.py",
        "adminme/session.py",
        "bootstrap/upgrade.py",
        "bootstrap/backup_restore.py",
        "bootstrap/versions.json",
        "poetry.lock",
        "openclaw/administrateme/package-lock.json",
        "bridge/adminme_bridge/main.py",
        "tests/unreviewed.py",
        "unreviewed-runtime.py",
    ],
)
def test_reminder_scope_requires_broad_checks_for_unrelated_dependencies(
    repository: tuple[Path, str],
    other: str,
) -> None:
    path, _ = repository
    _commit(path, other, "RETAINED = True\n")
    base, _ = _reminder_candidate(path)
    head = _commit(path, other, "CHANGED = True\n")
    event = _event(base, head)
    event.update(cos_reminder_interfaces_equal=True, changed_paths=[])
    assert select_scope(path, event, checkout_sha=head)["scope"] == "system-wide"


@pytest.mark.parametrize(
    "drift", ["none", "runtime", "mode", "missing-test", "unknown-test"]
)
def test_reminder_scope_checks_the_exact_merge_tree(
    repository: tuple[Path, str],
    drift: str,
) -> None:
    path, _ = repository
    base, head = _reminder_candidate(path)
    if drift == "runtime":
        (path / "adminme/runtime.py").write_text("MERGE_ONLY = True\n")
    elif drift == "mode":
        (path / "adminme/runtime.py").chmod(0o755)
    elif drift == "missing-test":
        (path / "tests/acceptance/test_cos_self_reminders.py").unlink()
    elif drift == "unknown-test":
        (path / "tests/unreviewed.py").write_text("UNREVIEWED = True\n")
    _git(path, "add", "-A")
    merge = _git(
        path,
        "commit-tree",
        _git(path, "write-tree"),
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Synthetic exact-parent reminder merge",
    )
    _git(path, "checkout", "--detach", merge)
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == ("cos-reminders" if drift == "none" else "system-wide")


@pytest.mark.parametrize(("filename", "protected", "platform", "system"), RELEASE_JOBS)
def test_reminder_release_worker_requires_the_real_affected_checks(
    filename: str,
    protected: str,
    platform: str,
    system: str,
) -> None:
    job = _workflow(filename)["jobs"]["cos-reminder-validation"]
    assert job["if"] == "${{ needs.release-scope.outputs.scope == 'cos-reminders' }}"
    assert job["steps"][-1]["id"] == "validated"
    assert "if" not in job["steps"][-1]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert f"bash scripts/acceptance/cos-reminder-release.sh {platform}" in commands
    script = (ROOT / "scripts/acceptance/cos-reminder-release.sh").read_text()
    for required in (
        "set -euo pipefail",
        '"${GITHUB_ACTIONS:-}" == true',
        "--runxfail",
        "test_cos_self_reminders.py",
        "test_cos_self_replies.py",
        "test_cos_response_execution.py",
        "test_cos_response_allowlist_settings.py",
        "test_cos_identity_delegation_kernel.py",
        "test_member_cos_approval.py",
        "test_cos_response_assurance.py",
        "test_source_assurance.py",
        "test_historical_attribution.py",
        "test_schema_registry.py",
        "test_cos_channel_activation.py",
        "test_ci_release_gates.py",
        "npm run check",
        "npm test",
        "poetry run mypy",
        "poetry run pyright",
        "scripts/verify_invariants.sh",
        "openclaw-source-install.sh --self-reminder",
    ):
        assert required in script
    native = (ROOT / "scripts/acceptance/openclaw-source-install.sh").read_text()
    assert '"${1:-}" == --self-reminder' in native
    assert "check-self-reminder-gateway.mjs" in native
    assert "check-cos-approval-gateway.mjs" in native
    assert "check-cos-imessage-text.mjs" in native


def _updater_candidate(path: Path) -> tuple[str, str]:
    for required in sorted(UPDATER_REQUIRED_PATHS - {UPDATER_CHECK_SCRIPT}):
        _commit(path, required, "# Retained updater contract\n")
    base = _git(path, "rev-parse", "HEAD")
    head = _commit(path, UPDATER_CHECK_SCRIPT, "# Reviewed updater checks\n")
    return base, head


def test_updater_scope_binds_closed_paths_and_optional_feature_proof(
    repository: tuple[Path, str],
) -> None:
    path, _ = repository
    base, head = _updater_candidate(path)
    # The scope can ship independently before any optional feature exists.
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "updater-delivery"
    )
    for module, test in UPDATER_OPTIONAL_MODULE_TESTS.items():
        _commit(path, module, "# Reviewed optional delivery feature\n")
        head = _commit(path, test, "# Required feature proof\n")
    for allowed in sorted(UPDATER_COMPONENT_PATHS | UPDATER_TEST_PATHS):
        head = _commit(path, allowed, "# Reviewed updater behavior\n")
    (path / "adminme/runtime.py").write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "updater-delivery"
    assert len(proof["updater_dependency_sha256"]) == 64
    for platform in ("linux", "macos"):
        assert required_workers("updater-delivery", platform) == (
            "release-scope",
            "updater-delivery-validation",
        )
        verify_gate(proof, _needs(proof, platform), platform=platform)


@pytest.mark.parametrize(
    "other",
    [
        "bootstrap/versions.json",
        "bootstrap/services.py",
        "bootstrap/recovery_readiness.py",
        "bootstrap/openclaw_activation.py",
        "bootstrap/cos_imessage.py",
        "openclaw/administrateme/openclaw.plugin.json",
        "bootstrap/update.sh",
        "adminme/events/log.py",
        "adminme/settings_control.py",
        "poetry.lock",
        "openclaw/administrateme/package-lock.json",
        "bootstrap/openclaw/programs/requested_workflows.md",
        "bridge/adminme_bridge/main.py",
        "tests/unreviewed.py",
        "unknown.py",
    ],
)
def test_updater_scope_rejects_changed_dependencies_or_caller_claims(
    repository: tuple[Path, str],
    other: str,
) -> None:
    path, _ = repository
    _commit(path, other, "RETAINED = True\n")
    base, _ = _updater_candidate(path)
    head = _commit(path, other, "CHANGED = True\n")
    event = _event(base, head)
    event.update(updater_interfaces_equal=True, changed_paths=[])
    assert select_scope(path, event, checkout_sha=head)["scope"] == "system-wide"


@pytest.mark.parametrize("module", sorted(UPDATER_OPTIONAL_MODULE_TESTS))
def test_updater_feature_requires_its_test_before_focused_release(
    repository: tuple[Path, str],
    module: str,
) -> None:
    path, _ = repository
    base, _ = _updater_candidate(path)
    head = _commit(path, module, "# Missing feature proof\n")
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize("status", ["D", "T", "U"])
@pytest.mark.parametrize(
    "path", sorted(UPDATER_COMPONENT_PATHS | {UPDATER_CHECK_SCRIPT})
)
def test_updater_scope_refuses_removed_or_retyped_inputs(
    status: str, path: str
) -> None:
    assert (
        choose_scope(
            [(status, path)],
            interfaces_equal=False,
            updater_interfaces_equal=True,
            path_modes={path: {"100644"}},
        )
        == "system-wide"
    )


@pytest.mark.parametrize("modes", [{"100755"}, {"120000"}, {"100644", "100755"}, set()])
def test_updater_scope_requires_regular_non_executable_inputs(modes: set[str]) -> None:
    assert (
        choose_scope(
            [("M", "bootstrap/upgrade.py")],
            interfaces_equal=False,
            updater_interfaces_equal=True,
            path_modes={"bootstrap/upgrade.py": modes},
        )
        == "system-wide"
    )


@pytest.mark.parametrize(
    "drift",
    ["none", "runtime", "mode", "missing-test", "unknown-test", "missing-module-proof"],
)
def test_updater_scope_checks_exact_test_merge_tree(
    repository: tuple[Path, str],
    drift: str,
) -> None:
    path, _ = repository
    base, head = _updater_candidate(path)
    if drift == "runtime":
        (path / "adminme/runtime.py").write_text("MERGE_ONLY = True\n")
    elif drift == "mode":
        (path / "adminme/runtime.py").chmod(0o755)
    elif drift == "missing-test":
        (path / "tests/acceptance/test_upgrade_deadline_rollback.py").unlink()
    elif drift == "unknown-test":
        (path / "tests/unreviewed.py").write_text("UNREVIEWED = True\n")
    elif drift == "missing-module-proof":
        (path / "bootstrap/prepared_runtime.py").write_text("UNPROVED = True\n")
    _git(path, "add", "-A")
    merge = _git(
        path,
        "commit-tree",
        _git(path, "write-tree"),
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Synthetic exact-parent updater merge",
    )
    _git(path, "checkout", "--detach", merge)
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == ("updater-delivery" if drift == "none" else "system-wide")


@pytest.mark.parametrize(("filename", "protected", "platform", "system"), RELEASE_JOBS)
def test_updater_worker_requires_recovery_and_supported_process_checks(
    filename: str,
    protected: str,
    platform: str,
    system: str,
) -> None:
    job = _workflow(filename)["jobs"]["updater-delivery-validation"]
    assert job["if"] == "${{ needs.release-scope.outputs.scope == 'updater-delivery' }}"
    assert job["steps"][-1]["id"] == "validated"
    assert "if" not in job["steps"][-1]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert f"bash {UPDATER_CHECK_SCRIPT} {platform}" in commands
    script = (ROOT / UPDATER_CHECK_SCRIPT).read_text()
    for required in (
        "set -euo pipefail",
        '"${GITHUB_ACTIONS:-}" == true',
        "--runxfail",
        "test_source_identity.py",
        "test_activation_transaction.py",
        "test_cos_channel_activation.py",
        "test_backup_rehearsal.py",
        "test_candidate_readiness.py",
        "test_recovery_readiness.py",
        "test_phase5_operations.py",
        "test_openclaw_plugin_reconciliation.py",
        "test_cos_guard_release.py",
        "test_upgrade_deadline_rollback.py",
        "test_upgrade_readiness_blackout.py",
        "test_recovery_readiness_baseline.py",
        "test_cos_live_maintenance.py",
        "test_macos_bootstrap_operations.py::test_credential_free_bootstrap_doctor_backup_restore_round_trip",
        "test_ci_release_gates.py",
        "prepared_runtime workspace_instructions",
        'test -f "tests/unit/bootstrap/test_$module.py"',
        "poetry run mypy bootstrap bridge/adminme_bridge scripts/release_scope.py",
        "poetry run pyright bootstrap scripts/release_scope.py",
        "scripts/verify_invariants.sh",
        "poetry run ruff check",
        "poetry run ruff format --check",
        "poetry check --lock",
    ):
        assert required in script
    for unrelated in (
        "openclaw-source-install.sh",
        "release-candidate.sh",
        "ADMINME_RUN_BRIDGE_INSTALLER_RUNTIME",
        "-k ",
    ):
        assert unrelated not in script
    assert '"${tests[@]}"' in script


@pytest.mark.parametrize(
    "defect",
    ["none", "missing", "empty", "missing-count", "skipped", "failure", "wrong-count"],
)
@pytest.mark.parametrize(
    "check_script", [UPDATER_CHECK_SCRIPT, COS_SERVICE_WORK_CHECK_SCRIPT]
)
def test_actual_component_junit_gate_rejects_missing_or_partial_evidence(
    check_script: str,
    defect: str,
    tmp_path: Path,
) -> None:
    script = (
        (ROOT / check_script)
        .read_text()
        .split("<<'PY_CHECK'", 1)[1]
        .split("\n", 1)[1]
        .split("\nPY_CHECK", 1)[0]
    )
    report = tmp_path / "updater.xml"
    payload = '<testsuites><testsuite tests="1" skipped="0" failures="0" errors="0"><testcase name="fixture"/></testsuite></testsuites>'
    if defect == "empty":
        payload = "<testsuites/>"
    elif defect == "missing-count":
        payload = payload.replace(' errors="0"', "")
    elif defect in {"skipped", "failure"}:
        payload = payload.replace(
            '<testcase name="fixture"/>',
            f'<testcase name="fixture"><{defect}/></testcase>',
        )
    elif defect == "wrong-count":
        payload = payload.replace('tests="1"', 'tests="2"')
    if defect != "missing":
        report.write_text(payload)
    result = subprocess.run(
        [sys.executable, "-c", script, str(report)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) is (defect == "none")


_SERVICE_IMPORT = "    from adminme.products.openclaw.service_work import router as service_work_router\n"
_SERVICE_INCLUDE = "    app.include_router(service_work_router)\n"
_SERVICE_RUNTIME = "def create_application():\n    app = factory()\n    return app\n"
_SERVICE_WIRED_RUNTIME = _SERVICE_RUNTIME.replace(
    "    return app\n", _SERVICE_IMPORT + _SERVICE_INCLUDE + "    return app\n"
)


def _service_work_candidate(path: Path) -> tuple[str, str]:
    base = _commit(path, COS_SERVICE_WORK_APPLICATION, _SERVICE_RUNTIME)
    for relative in COS_SERVICE_WORK_REQUIRED_PATHS:
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _SERVICE_WIRED_RUNTIME
            if relative == COS_SERVICE_WORK_APPLICATION
            else "# Reviewed service-work component\n"
        )
        if relative == COS_SERVICE_WORK_NATIVE_INSTALL:
            target.chmod(0o755)
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "Service-owned work with exact runtime wiring")
    return base, _git(path, "rev-parse", "HEAD")


def test_service_work_scope_binds_exact_runtime_wiring_and_component_proof(
    repository: tuple[Path, str],
) -> None:
    path, _ = repository
    base, head = _service_work_candidate(path)
    # Working files and caller claims cannot alter immutable scope evidence.
    (path / COS_SERVICE_WORK_APPLICATION).write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "cos-service-work"
    assert len(proof["cos_service_work_dependency_sha256"]) == 64
    for platform in ("linux", "macos"):
        assert required_workers("cos-service-work", platform) == (
            "release-scope",
            "cos-service-work-validation",
        )
        verify_gate(proof, _needs(proof, platform), platform=platform)


@pytest.mark.parametrize(
    "source",
    [
        _SERVICE_RUNTIME,
        _SERVICE_WIRED_RUNTIME.replace(_SERVICE_INCLUDE, ""),
        _SERVICE_WIRED_RUNTIME.replace(_SERVICE_IMPORT, ""),
        _SERVICE_WIRED_RUNTIME.replace(_SERVICE_INCLUDE, _SERVICE_INCLUDE * 2),
        _SERVICE_WIRED_RUNTIME.replace(
            _SERVICE_IMPORT + _SERVICE_INCLUDE, _SERVICE_INCLUDE + _SERVICE_IMPORT
        ),
        _SERVICE_WIRED_RUNTIME.replace("service_work_router)", "unrelated_router)"),
        _SERVICE_WIRED_RUNTIME.replace(
            "    app = factory()", "    app = unsafe_factory()"
        ),
        _SERVICE_WIRED_RUNTIME + "UNRELATED = True\n",
        _SERVICE_WIRED_RUNTIME.replace(
            "def create_application():", "def other_application():"
        ),
        "unparseable python [",
    ],
)
def test_service_work_scope_rejects_missing_or_broader_runtime_edits(
    repository: tuple[Path, str], source: str
) -> None:
    path, _ = repository
    base, _head = _service_work_candidate(path)
    head = _commit(path, COS_SERVICE_WORK_APPLICATION, source)
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "relative",
    [
        "adminme/events/schemas/openclaw.py",
        "adminme/products/openclaw/service_context.py",
        "adminme/projections/tasks/handlers.py",
        "adminme/projections/tasks/schema.sql",
        "adminme/projections/calendars/handlers.py",
        "adminme/projections/lists/handlers.py",
        "tests/acceptance/test_cos_service_planning_context.py",
    ],
)
def test_service_work_source_receipt_changes_keep_focused_proof(
    repository: tuple[Path, str],
    relative: str,
) -> None:
    path, _ = repository
    _base, base = _service_work_candidate(path)
    if relative.startswith("tests/"):
        # A test path belongs to the focused proof alongside its component edit;
        # the existing selector intentionally requires a component change.
        _commit(
            path,
            "adminme/products/openclaw/service_context.py",
            "# Reviewed current-source change\n",
        )
    head = _commit(path, relative, "# Reviewed receipt or current-source change\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "cos-service-work"


def test_service_work_runtime_normalization_is_limited_to_direct_router_statements() -> (
    None
):
    baseline = _service_work_application(_SERVICE_RUNTIME)
    wired = _service_work_application(_SERVICE_WIRED_RUNTIME)
    assert baseline is not None and wired is not None
    assert baseline == (wired[0], False)
    assert wired[1] is True
    nested = _SERVICE_WIRED_RUNTIME.replace(
        _SERVICE_IMPORT + _SERVICE_INCLUDE,
        "    if enabled:\n" + "    " + _SERVICE_IMPORT + "    " + _SERVICE_INCLUDE,
    )
    assert _service_work_application(nested) != wired
    # Invalid syntax and duplicate entry points fail closed, not a broad exemption.
    assert _service_work_application("def [") is None
    assert _service_work_application(_SERVICE_WIRED_RUNTIME * 2) is None


@pytest.mark.parametrize(
    "other",
    [
        "adminme/events/log.py",
        "adminme/events/schemas/messaging_unreviewed.py",
        "adminme/projections/calendars/queries.py",
        "adminme/projections/lists/queries.py",
        "adminme/lib/session.py",
        "adminme/runtime/unreviewed.py",
        "adminme/products/openclaw/routers.py",
        "bootstrap/upgrade.py",
        "bootstrap/backup_restore.py",
        "bootstrap/versions.json",
        "poetry.lock",
        "openclaw/administrateme/package-lock.json",
        "openclaw/administrateme/src/transport.ts",
        "tests/unreviewed.py",
    ],
)
def test_service_work_scope_freezes_other_contracts_and_dependencies(
    repository: tuple[Path, str], other: str
) -> None:
    path, _ = repository
    _commit(path, other, "RETAINED = True\n")
    base, _head = _service_work_candidate(path)
    head = _commit(path, other, "CHANGED = True\n")
    event = _event(base, head)
    event.update(cos_service_work_interfaces_equal=True, changed_paths=[])
    assert select_scope(path, event, checkout_sha=head)["scope"] == "system-wide"


@pytest.mark.parametrize("status", ["D", "T", "U"])
@pytest.mark.parametrize("path", sorted(COS_SERVICE_WORK_COMPONENT_PATHS))
def test_service_work_scope_rejects_deleted_or_retyped_sources(
    status: str, path: str
) -> None:
    assert (
        choose_scope(
            [(status, path)],
            interfaces_equal=False,
            cos_service_work_interfaces_equal=True,
            path_modes={path: {"100644"}},
        )
        == "system-wide"
    )


@pytest.mark.parametrize(
    "drift",
    ["none", "runtime", "unknown", "missing-proof", "mode", "native-mode", "symlink"],
)
def test_service_work_scope_checks_actual_merge_tree(
    repository: tuple[Path, str], drift: str
) -> None:
    path, _ = repository
    base, head = _service_work_candidate(path)
    target = path / "tests/acceptance/test_cos_service_private_work.py"
    if drift == "runtime":
        (path / COS_SERVICE_WORK_APPLICATION).write_text(
            _SERVICE_WIRED_RUNTIME + "SIDE_EFFECT = True\n"
        )
    elif drift == "unknown":
        (path / "tests/unreviewed.py").write_text("UNREVIEWED = True\n")
    elif drift == "missing-proof":
        target.unlink()
    elif drift == "mode":
        target.chmod(0o755)
    elif drift == "native-mode":
        (path / COS_SERVICE_WORK_NATIVE_INSTALL).chmod(0o644)
    elif drift == "symlink":
        target.unlink()
        target.symlink_to("../unit/test_service_work_principal.py")
    _git(path, "add", "-A")
    merge = _git(
        path,
        "commit-tree",
        _git(path, "write-tree"),
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Exact-parent service work merge",
    )
    _git(path, "checkout", "--detach", merge)
    if drift == "symlink":
        with pytest.raises(SourceIdentityError, match="unsupported object or mode"):
            select_scope(path, _event(base, head), checkout_sha=merge)
        return
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == ("cos-service-work" if drift == "none" else "system-wide")


@pytest.mark.parametrize(("filename", "protected", "platform", "system"), RELEASE_JOBS)
def test_service_work_worker_requires_affected_identity_replay_and_recovery_checks(
    filename: str, protected: str, platform: str, system: str
) -> None:
    job = _workflow(filename)["jobs"]["cos-service-work-validation"]
    assert job["if"] == "${{ needs.release-scope.outputs.scope == 'cos-service-work' }}"
    assert job["steps"][-1]["id"] == "validated"
    assert "if" not in job["steps"][-1]
    assert job["steps"][-1]["run"].count('"$GITHUB_SHA"') == 1
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert f"bash {COS_SERVICE_WORK_CHECK_SCRIPT} {platform}" in commands
    script = (ROOT / COS_SERVICE_WORK_CHECK_SCRIPT).read_text()
    for required in (
        '"${GITHUB_ACTIONS:-}" == true',
        "--runxfail",
        "--junitxml=",
        "PY_CHECK",
        "test_cos_service_private_work.py",
        "test_cos_service_working_context.py",
        "test_cos_service_planning_context.py",
        "test_projection_calendars.py",
        "tests/unit/projections/lists/test_handlers.py",
        "tests/unit/projections/lists/test_queries.py",
        "test_lists_projection_runner.py",
        "test_service_owned_google_sources.py",
        "test_service_work_principal.py",
        "test_service_identities.py",
        "test_operating_agreements.py",
        "test_source_policy_v2.py",
        "test_scope.py",
        "test_session.py",
        "test_event_validation.py",
        "test_event_schemas.py",
        "test_schema_registry.py",
        "test_projection_tasks.py",
        "test_projection_rebuild.py::",
        "test_projection_scope_canary.py",
        "test_cos_identity_delegation_kernel.py",
        "test_cos_work_coordination_operating_agreements.py",
        "test_openclaw_native_seam.py",
        "test_member_chat_subscription.py",
        "test_member_cos_approval.py",
        "test_cos_self_replies.py",
        "test_cos_self_reminders.py",
        "test_cos_service_topology.py",
        "test_cos_member_topology.py",
        "test_cos_channel_activation.py",
        "test_openclaw_plugin_reconciliation.py",
        "test_cos_guard_release.py",
        "test_ci_release_gates.py",
        "npm run check",
        "npm run build",
        "npm test",
        "poetry run mypy",
        "poetry run pyright",
        "scripts/verify_invariants.sh",
        "scripts/generate_event_lifecycle_catalog.py --check",
        "openclaw-source-install.sh --service-work",
    ):
        assert required in script
    for unrelated in (
        "release-candidate.sh",
        "ADMINME_RUN_BRIDGE_INSTALLER_RUNTIME",
        "-k ",
    ):
        assert unrelated not in script
    assert 'if [[ "$1" == macos ]]; then' in script
    native = (ROOT / COS_SERVICE_WORK_NATIVE_INSTALL).read_text()
    assert '"${1:-}" == --service-work' in native
    assert "check-service-work-gateway.mjs" in native
    assert "install-openclaw-source.sh" in native
    assert "reviewed_source_status" in native


@pytest.mark.parametrize("failure", ["none", "python", "plugin", "static", "dirty"])
def test_service_work_waits_for_every_group_and_rejects_any_failure(
    tmp_path: Path, failure: str
) -> None:
    dispatch = (
        (ROOT / COS_SERVICE_WORK_CHECK_SCRIPT)
        .read_text()
        .split(
            "# Independent groups finish even when another fails; every result gates release.\n",
            1,
        )[1]
    )
    script = tmp_path / "groups.sh"
    script.write_text(
        """set -euo pipefail
record() {
  printf '%s completed\\n' "$1"
  touch "$RUNNER_TEMP/$1.completed"
  [[ "$FAILED_GROUP" != "$1" ]] || return 17
}
python_checks() { record python; }
plugin_checks() { record plugin; }
static_checks() { record static; }
git() {
  for name in python plugin static; do
    test -f "$RUNNER_TEMP/$name.completed" || return 79
  done
  touch "$RUNNER_TEMP/clean-checked"
  if [[ "$1" == status && "$FAILED_GROUP" == dirty ]]; then
    echo ' M generated-file'
  fi
}
"""
        + dispatch
    )
    result = subprocess.run(
        ["bash", str(script), "linux"],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "FAILED_GROUP": failure},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (0 if failure == "none" else 1), result.stderr
    assert (tmp_path / "clean-checked").is_file()
    for group in ("python", "plugin", "static"):
        status = 17 if group == failure else 0
        assert f"{group} checks (exit {status})" in result.stdout
        assert (tmp_path / f"cos-service-work-{group}.log").read_text() == (
            f"{group} completed\n"
        )


_SETUP_IMPORT = "from adminme.products.member.bridge_setup import router as member_bridge_setup_router\n"
_SETUP_INCLUDE = "    app.include_router(member_bridge_setup_router)\n"
_TAILSCALE_IMPORT = "from adminme.tailscale_local import tailscale_executable\n"
_SETUP_BASE_SHARED = {
    "adminme/runtime/application.py": "def create_application(settings):\n    app = make_app(settings)\n    return app\n",
    "adminme/products/member/assets/member.js": "const frozen = 1;\nfunction bridgeSettings(data) { return data; }\nfunction privacySettings(data) { return data; }\n",
    "bootstrap/bridge_enrollment.py": 'def _tailscale_identity():\n    return subprocess.run(["tailscale", "status", "--json"], check=False)\n',
    "bridge/adminme_bridge/main.py": 'def _local_device_identity(config, path):\n    return subprocess.run(["tailscale", "status", "--json"], check=False)\n',
}


def _bridge_setup_candidate(path: Path) -> tuple[str, str]:
    from scripts.release_scope import BRIDGE_SETUP_REQUIRED, _bridge_setup_mode

    for name, source in _SETUP_BASE_SHARED.items():
        _commit(path, name, source)
    base = _git(path, "rev-parse", "HEAD")
    for name in BRIDGE_SETUP_REQUIRED:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        source = _SETUP_BASE_SHARED.get(name, "# Reviewed bridge setup component\n")
        if name == "adminme/runtime/application.py":
            source = _SETUP_IMPORT + source.replace(
                "    return app", _SETUP_INCLUDE + "    return app"
            )
        elif name.endswith("member.js"):
            source = source.replace(
                "function bridgeSettings(data) { return data; }",
                "function bridgeSettings(data) { return prepareMac(data); }",
            )
        elif name in _SETUP_BASE_SHARED:
            source = _TAILSCALE_IMPORT + source.replace(
                '["tailscale", "status", "--json"]',
                '[tailscale_executable(), "status", "--json"]',
            ).replace(
                "check=False",
                'env={**os.environ, "TAILSCALE_BE_CLI": "1"}, check=False',
            )
        target.write_text(source)
        target.chmod(0o755 if _bridge_setup_mode(name) == "100755" else 0o644)
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "Prepare member bridge with bounded wiring")
    return base, _git(path, "rev-parse", "HEAD")


def test_bridge_setup_scope_uses_immutable_sources_and_complete_workers(repository):
    path, _ = repository
    base, head = _bridge_setup_candidate(path)
    (path / "adminme/runtime/application.py").write_text("MUTABLE = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "bridge-setup"
    assert len(proof["bridge_setup_dependency_sha256"]) == 64
    assert required_workers("bridge-setup", "linux") == (
        "release-scope",
        "bridge-development",
    )
    assert required_workers("bridge-setup", "macos") == (
        "release-scope",
        "bridge-development",
        "bridge-installed-runtime",
    )
    for platform in ("linux", "macos"):
        verify_gate(proof, _needs(proof, platform), platform=platform)


@pytest.mark.parametrize(
    "name",
    [
        *_SETUP_BASE_SHARED,
        "adminme/products/auth.py",
        "adminme/events/log.py",
        "poetry.lock",
        "bridge/poetry.lock",
        "adminme/bridge_enrollment.py",
        "adminme/settings_control.py",
        "tests/unrelated.py",
    ],
)
def test_bridge_setup_scope_freezes_shared_logic_and_unrelated_dependencies(
    repository, name
):
    path, _ = repository
    base, _ = _bridge_setup_candidate(path)
    target = path / name
    source = target.read_text() if target.exists() else ""
    head = _commit(path, name, source + "\nBROAD_CHANGE = True\n")
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_source",
        "deleted",
        "symlink",
        "unpaired_import",
        "missing_cli_environment",
        "misplaced_router",
        "other_js_function",
    ],
)
def test_bridge_setup_scope_rejects_incomplete_or_broader_setup(repository, fault):
    path, _ = repository
    base, _ = _bridge_setup_candidate(path)
    name = "adminme/products/member/bridge_setup.py"
    if fault in {"missing_source", "deleted"}:
        (path / name).unlink()
    elif fault == "symlink":
        (path / name).unlink()
        (path / name).symlink_to("../main.py")
    elif fault == "unpaired_import":
        target = path / "adminme/runtime/application.py"
        target.write_text(target.read_text().replace(_SETUP_IMPORT, ""))
    elif fault == "misplaced_router":
        target = path / "adminme/runtime/application.py"
        target.write_text(
            target.read_text().replace(_SETUP_INCLUDE, "")
            + "\ndef unrelated():\n"
            + _SETUP_INCLUDE
        )
    elif fault == "missing_cli_environment":
        target = path / "bootstrap/bridge_enrollment.py"
        target.write_text(
            target.read_text().replace(
                'env={**os.environ, "TAILSCALE_BE_CLI": "1"}, ', ""
            )
        )
    else:
        target = path / "adminme/products/member/assets/member.js"
        target.write_text(
            target.read_text().replace(
                "function privacySettings(data) { return data; }",
                "function privacySettings(data) { return other(data); }",
            )
        )
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "Incomplete or broader setup")
    head = _git(path, "rev-parse", "HEAD")
    if fault == "symlink":
        with pytest.raises(SourceIdentityError):
            select_scope(path, _event(base, head), checkout_sha=head)
        return
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize("drift", [False, True])
def test_bridge_setup_scope_checks_the_actual_test_merge(repository, drift):
    path, _ = repository
    base, head = _bridge_setup_candidate(path)
    if drift:
        (path / "adminme/runtime.py").write_text("UNREVIEWED_MERGE_CHANGE = True\n")
        _git(path, "add", "-A")
    merge = _git(
        path,
        "commit-tree",
        _git(path, "write-tree"),
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Synthetic exact-parent merge",
    )
    _git(path, "checkout", "--detach", merge)
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == ("system-wide" if drift else "bridge-setup")


@pytest.mark.parametrize("filename", [row[0] for row in RELEASE_JOBS])
def test_bridge_setup_worker_keeps_new_authentication_preparation_and_browser_checks(
    filename,
):
    job = _workflow(filename)["jobs"]["bridge-development"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "'bridge-setup'" in job["if"]
    for required in (
        "test_bridge_helper_distribution.py",
        "test_member_bridge_mac_preparation.py",
        "test_member_bridge_helper_app.py",
        "test_self_service_member_bridge_enrollment.py",
        "test_member_bridge_permission_recovery.py",
        "test_bridge_authorization.py",
        "test_runtime_application.py",
    ):
        assert required in commands
    if filename.startswith("macos"):
        assert "ADMINME_GATE7_BRIDGE_ENROLLMENT_ONLY" in json.dumps(job)
        assert "test:gate7" in commands
        assert "not test_publish_exact_native_helper" not in commands
    else:
        assert (
            "not test_publish_exact_native_helper_and_reuse_without_rebuilding"
            in commands
        )


def _cos_google_workspace_candidate(repository: Path) -> tuple[str, str]:
    base = _git(repository, "rev-parse", "HEAD")
    for relative in sorted(COS_GOOGLE_WORKSPACE_REQUIRED_PATHS):
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Synthetic Google workspace component\n")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "CoS Google workspace fixture")
    return base, _git(repository, "rev-parse", "HEAD")


def test_cos_google_workspace_scope_reuses_both_protected_google_workers(
    repository: tuple[Path, str],
) -> None:
    path, _ = repository
    base, head = _cos_google_workspace_candidate(path)
    (path / "adminme/runtime.py").write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "cos-google-workspace"
    assert len(proof["cos_google_workspace_dependency_sha256"]) == 64
    for platform in ("linux", "macos"):
        assert required_workers("cos-google-workspace", platform) == (
            "release-scope",
            "google-connections-validation",
        )
        verify_gate(proof, _needs(proof, platform), platform=platform)


@pytest.mark.parametrize(
    "extra",
    [
        "poetry.lock",
        "bootstrap/versions.json",
        "bootstrap/upgrade.py",
        "adminme/runtime/application.py",
        "adminme/events/log.py",
        "adminme/events/schemas/ingest.py",
        "adminme/service_identities.py",
        "bridge/adminme_bridge/daemon.py",
        "packs/adapters/messaging/gmail/adapter.py",
        "tests/unreviewed.py",
    ],
)
def test_cos_google_workspace_never_exempts_outside_contracts_or_tests(
    repository: tuple[Path, str], extra: str
) -> None:
    path, _ = repository
    base, _ = _cos_google_workspace_candidate(path)
    head = _commit(path, extra, "# Outside the tested acquisition component\n")
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "defect", ["none", "missing-proof", "mode", "unknown-test", "runtime", "symlink"]
)
def test_cos_google_workspace_checks_exact_merge_tree_and_required_proof(
    repository: tuple[Path, str], defect: str
) -> None:
    path, _ = repository
    base, head = _cos_google_workspace_candidate(path)
    target = path / "tests/unit/bootstrap/test_cos_google_workspace.py"
    if defect == "missing-proof":
        target.unlink()
    elif defect == "mode":
        target.chmod(0o755)
    elif defect == "unknown-test":
        (path / "tests/unreviewed.py").write_text("# unreviewed\n")
    elif defect == "runtime":
        (path / "adminme/runtime.py").write_text("MERGE_ONLY_CHANGE = True\n")
    elif defect == "symlink":
        target.unlink()
        target.symlink_to("test_google_oauth.py")
    _git(path, "add", "-A")
    merge = _git(
        path,
        "commit-tree",
        _git(path, "write-tree"),
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Exact-parent CoS Google workspace merge",
    )
    _git(path, "checkout", "--detach", merge)
    if defect == "symlink":
        with pytest.raises(SourceIdentityError, match="unsupported object or mode"):
            select_scope(path, _event(base, head), checkout_sha=merge)
        return
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == (
        "cos-google-workspace" if defect == "none" else "system-wide"
    )


@pytest.mark.parametrize(
    "status,modes", [("D", {"100644"}), ("T", {"100644", "120000"}), ("M", {"100755"})]
)
def test_cos_google_workspace_rejects_removed_or_retyped_component_sources(
    status: str, modes: set[str]
) -> None:
    path = "bootstrap/cos_google_workspace.py"
    assert (
        choose_scope(
            [(status, path)],
            interfaces_equal=False,
            cos_google_workspace_interfaces_equal=True,
            path_modes={path: modes},
        )
        == "system-wide"
    )


def test_cos_google_workspace_fingerprint_binds_every_other_byte_and_mode() -> None:
    from bootstrap.source_identity import GitTreeEntry

    base = (GitTreeEntry(path="poetry.lock", mode="100644", object_id="a" * 40),)
    changed = (GitTreeEntry(path="poetry.lock", mode="100644", object_id="b" * 40),)
    assert _cos_google_workspace_dependency_fingerprint(
        base
    ) != _cos_google_workspace_dependency_fingerprint(changed)
    for path in COS_GOOGLE_WORKSPACE_COMPONENT_PATHS:
        regular = GitTreeEntry(path=path, mode="100644", object_id="a" * 40)
        executable = GitTreeEntry(path=path, mode="100755", object_id="a" * 40)
        assert _cos_google_workspace_dependency_fingerprint(
            base
        ) == _cos_google_workspace_dependency_fingerprint((*base, regular))
        assert _cos_google_workspace_dependency_fingerprint(
            base
        ) != _cos_google_workspace_dependency_fingerprint((*base, executable))


def _cos_apple_reminders_candidate(repository: Path) -> tuple[str, str]:
    base = _git(repository, "rev-parse", "HEAD")
    for relative in sorted(COS_APPLE_REMINDERS_REQUIRED_PATHS):
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Synthetic Apple Reminders component\n")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "CoS Apple Reminders fixture")
    return base, _git(repository, "rev-parse", "HEAD")


def test_cos_apple_reminders_scope_reuses_both_protected_google_workers(
    repository: tuple[Path, str],
) -> None:
    path, _ = repository
    base, head = _cos_apple_reminders_candidate(path)
    (path / "adminme/runtime.py").write_text("UNCOMMITTED = True\n")
    proof = select_scope(path, _event(base, head), checkout_sha=head)
    assert proof["scope"] == "cos-apple-reminders"
    assert len(proof["cos_apple_reminders_dependency_sha256"]) == 64
    for platform in ("linux", "macos"):
        assert required_workers("cos-apple-reminders", platform) == (
            "release-scope",
            "google-connections-validation",
        )
        verify_gate(proof, _needs(proof, platform), platform=platform)


@pytest.mark.parametrize(
    "extra",
    [
        "poetry.lock",
        "bootstrap/versions.json",
        "bootstrap/upgrade.py",
        "adminme/runtime/application.py",
        "adminme/events/log.py",
        "adminme/events/schemas/ingest.py",
        "adminme/projections/lists/schema.sql",
        "adminme/products/openclaw/service_work.py",
        "adminme/service_identities.py",
        "bridge/adminme_bridge/daemon.py",
        "packs/adapters/messaging/gmail/adapter.py",
        "tests/unreviewed.py",
    ],
)
def test_cos_apple_reminders_never_exempts_outside_contracts_or_tests(
    repository: tuple[Path, str], extra: str
) -> None:
    path, _ = repository
    base, _ = _cos_apple_reminders_candidate(path)
    head = _commit(path, extra, "# Outside the tested acquisition component\n")
    assert (
        select_scope(path, _event(base, head), checkout_sha=head)["scope"]
        == "system-wide"
    )


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "missing-proof",
        "missing-context-proof",
        "missing-native-proof",
        "mode",
        "unknown-test",
        "runtime",
        "symlink",
    ],
)
def test_cos_apple_reminders_checks_exact_merge_tree_and_required_proof(
    repository: tuple[Path, str], defect: str
) -> None:
    path, _ = repository
    base, head = _cos_apple_reminders_candidate(path)
    target = path / "tests/unit/bootstrap/test_cos_apple_reminders.py"
    if defect == "missing-proof":
        target.unlink()
    elif defect == "missing-context-proof":
        (path / "tests/acceptance/test_cos_apple_reminders_context.py").unlink()
    elif defect == "missing-native-proof":
        (path / "tests/unit/bootstrap/test_cos_reminders_helper_macos.py").unlink()
    elif defect == "mode":
        target.chmod(0o755)
    elif defect == "unknown-test":
        (path / "tests/unreviewed.py").write_text("# unreviewed\n")
    elif defect == "runtime":
        (path / "adminme/runtime.py").write_text("MERGE_ONLY_CHANGE = True\n")
    elif defect == "symlink":
        target.unlink()
        target.symlink_to("test_google_oauth.py")
    _git(path, "add", "-A")
    merge = _git(
        path,
        "commit-tree",
        _git(path, "write-tree"),
        "-p",
        base,
        "-p",
        head,
        "-m",
        "Exact-parent CoS Apple Reminders merge",
    )
    _git(path, "checkout", "--detach", merge)
    if defect == "symlink":
        with pytest.raises(SourceIdentityError, match="unsupported object or mode"):
            select_scope(path, _event(base, head), checkout_sha=merge)
        return
    proof = select_scope(path, _event(base, head), checkout_sha=merge)
    assert proof["scope"] == (
        "cos-apple-reminders" if defect == "none" else "system-wide"
    )


@pytest.mark.parametrize(
    "status,modes", [("D", {"100644"}), ("T", {"100644", "120000"}), ("M", {"100755"})]
)
def test_cos_apple_reminders_rejects_removed_or_retyped_component_sources(
    status: str, modes: set[str]
) -> None:
    path = "bootstrap/cos_apple_reminders.py"
    assert (
        choose_scope(
            [(status, path)],
            interfaces_equal=False,
            cos_apple_reminders_interfaces_equal=True,
            path_modes={path: modes},
        )
        == "system-wide"
    )


def test_cos_apple_reminders_fingerprint_binds_every_other_byte_and_mode() -> None:
    from bootstrap.source_identity import GitTreeEntry

    base = (GitTreeEntry(path="poetry.lock", mode="100644", object_id="a" * 40),)
    changed = (GitTreeEntry(path="poetry.lock", mode="100644", object_id="b" * 40),)
    assert _cos_apple_reminders_dependency_fingerprint(
        base
    ) != _cos_apple_reminders_dependency_fingerprint(changed)
    for path in COS_APPLE_REMINDERS_COMPONENT_PATHS:
        regular = GitTreeEntry(path=path, mode="100644", object_id="a" * 40)
        executable = GitTreeEntry(path=path, mode="100755", object_id="a" * 40)
        assert _cos_apple_reminders_dependency_fingerprint(
            base
        ) == _cos_apple_reminders_dependency_fingerprint((*base, regular))
        assert _cos_apple_reminders_dependency_fingerprint(
            base
        ) != _cos_apple_reminders_dependency_fingerprint((*base, executable))


def test_cos_native_process_proof_is_required_only_on_macos_worker() -> None:
    for filename, *_ in RELEASE_JOBS:
        job = _workflow(filename)["jobs"]["google-connections-validation"]
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        for name in (
            "test_cos_reminders_helper_app_macos.py",
            "test_cos_reminders_helper_macos.py",
        ):
            assert (name in commands) == ("macos" in filename)
