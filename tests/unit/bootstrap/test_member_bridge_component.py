"""Fixed component packaging and retained-runtime identity regression gates."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from bootstrap import member_bridge_helper_app
from bootstrap.member_bridge_component import (
    HELPER_COMPONENT_PATHS,
    HELPER_RUNTIME_COMPATIBILITY_CONTRACT,
    PERMISSION_RECOVERY_CENTRAL_PATHS,
    PERMISSION_RECOVERY_COMPATIBILITY,
    PERMISSION_RECOVERY_PROTOCOL,
    inspect_helper_component,
    is_runtime_input,
    runtime_interface_fingerprint,
    verify_runtime_archive,
)
from bootstrap.member_bridge_helper_app import build_member_bridge_helper_app
from bootstrap.source_identity import sanitized_git_environment


ROOT = Path(__file__).resolve().parents[3]


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        env=sanitized_git_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def _commit(repository: Path) -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", "Synthetic component fixture")
    return _git(repository, "rev-parse", "HEAD")


def _repository(directory: Path) -> tuple[Path, str]:
    repository = directory / "component source"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Component Fixture")
    _git(repository, "config", "user.email", "component@example.invalid")
    for relative in HELPER_COMPONENT_PATHS | {
        "bootstrap/__init__.py",
        "bootstrap/contracts.py",
        "bootstrap/source_identity.py",
    }:
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    for relative, content in {
        "adminme/runtime.py": "VALUE = 'retained'\n",
        "adminme/products/bridge/routers/ingest.py": "CENTRAL_PROTOCOL = 'retained'\n",
        "adminme/bridge_history.py": "CENTRAL_HISTORY = 'retained'\n",
        "README.md": "Retained package metadata\n",
        "bridge/README.md": "Retained bridge metadata\n",
        "poetry.lock": "locked central dependencies\n",
        "bridge/poetry.lock": "locked bridge dependencies\n",
        "bootstrap/README.md": "Runtime setup contract\n",
        "openclaw/README.md": "Runtime channel contract\n",
        "AGENTS.md": "Development policy\n",
    }.items():
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    return repository, _commit(repository)


def _component_revision(repository: Path) -> str:
    installer = repository / "bootstrap/install-member-bridge.sh"
    installer.write_bytes(
        installer.read_bytes() + b"\n# Component-only fixture revision.\n"
    )
    return _commit(repository)


def _archive(repository: Path, revision: str, destination: Path) -> str:
    _git(repository, "archive", "--format=tar", f"--output={destination}", revision)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def test_helper_only_revision_preserves_fixed_runtime_bytes_and_modes(
    tmp_path: Path,
) -> None:
    repository, runtime = _repository(tmp_path)
    component = _component_revision(repository)
    inputs = inspect_helper_component(repository, component, runtime)
    assert inputs.compatibility["contract"] == HELPER_RUNTIME_COMPATIBILITY_CONTRACT
    assert inputs.compatibility["runtime_commit"] == runtime
    assert inputs.compatibility["helper_component_commit"] == component
    assert inputs.compatibility["runtime_inputs_unchanged"] is True
    assert runtime_interface_fingerprint(
        repository, component
    ) == runtime_interface_fingerprint(repository, runtime)
    archive = tmp_path / "retained.tar"
    _archive(repository, runtime, archive)
    verify_runtime_archive(archive, inputs.runtime_inventory)


@pytest.mark.parametrize(
    "relative",
    [
        "adminme/runtime.py",
        "README.md",
        "bridge/README.md",
        "poetry.lock",
        "bridge/poetry.lock",
        "bootstrap/README.md",
        "openclaw/README.md",
        "docs/refactor/nested/runtime.md",
        "docs/refactor/runtime.json",
        "unknown/new-runtime-input.txt",
    ],
)
def test_dependency_interface_metadata_and_unknown_inputs_cannot_be_reused(
    tmp_path: Path, relative: str
) -> None:
    repository, runtime = _repository(tmp_path)
    changed = repository / relative
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("different runtime input\n")
    component = _commit(repository)
    assert is_runtime_input(relative)
    with pytest.raises(RuntimeError, match="changes retained runtime"):
        inspect_helper_component(repository, component, runtime)


def test_runtime_executable_mode_is_part_of_compatibility(tmp_path: Path) -> None:
    repository, runtime = _repository(tmp_path)
    (repository / "adminme/runtime.py").chmod(0o755)
    component = _commit(repository)
    with pytest.raises(RuntimeError, match="changes retained runtime"):
        inspect_helper_component(repository, component, runtime)


@pytest.mark.parametrize("relative", ["AGENTS.md", "docs/refactor/POLICY.md"])
def test_executable_prose_is_not_excluded_from_runtime(
    tmp_path: Path, relative: str
) -> None:
    repository, _ = _repository(tmp_path)
    prose = repository / relative
    prose.parent.mkdir(parents=True, exist_ok=True)
    prose.write_text("non-runtime prose\n")
    runtime = _commit(repository)
    prose.chmod(0o755)
    component = _commit(repository)
    assert is_runtime_input(relative, mode="100755")
    with pytest.raises(RuntimeError, match="changes retained runtime"):
        inspect_helper_component(repository, component, runtime)


@pytest.mark.parametrize(
    "relative",
    [
        "AGENTS.md",
        "docs/refactor/ACCEPTANCE-CONTRACT.md",
        "docs/refactor/CHANGE-ORDER-027-SELF-SERVICE-MEMBER-BRIDGE-ENROLLMENT.md",
        "tests/unit/synthetic_test.py",
        "bridge/tests/test_synthetic.py",
        ".github/workflows/synthetic.yml",
        "scripts/release_scope.py",
    ],
)
def test_explicit_non_runtime_prose_and_test_inputs_do_not_change_interface(
    tmp_path: Path, relative: str
) -> None:
    repository, runtime = _repository(tmp_path)
    changed = repository / relative
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("non-runtime fixture revision\n")
    component = _commit(repository)
    assert not is_runtime_input(relative)
    assert inspect_helper_component(repository, component, runtime)


@pytest.mark.parametrize(
    "relative",
    [
        "adminme/runtime.py",
        "bootstrap/install-member-bridge.sh",
        "bootstrap/member_bridge_permission_controller.py",
    ],
)
@pytest.mark.parametrize("hidden", [False, True])
def test_dirty_and_assume_unchanged_checkout_bytes_fail_closed(
    tmp_path: Path, relative: str, hidden: bool
) -> None:
    repository, runtime = _repository(tmp_path)
    if hidden:
        _git(repository, "update-index", "--assume-unchanged", relative)
    (repository / relative).write_text("tampered checkout bytes\n")
    with pytest.raises(RuntimeError, match="dirty|bytes or modes"):
        inspect_helper_component(repository, runtime, runtime)


def test_untracked_input_is_not_reviewed(tmp_path: Path) -> None:
    repository, runtime = _repository(tmp_path)
    (repository / "unreviewed.py").write_text("unreviewed input\n")
    with pytest.raises(RuntimeError, match="dirty"):
        inspect_helper_component(repository, runtime, runtime)


def test_nonancestor_and_missing_runtime_cannot_be_packaged(tmp_path: Path) -> None:
    repository, runtime = _repository(tmp_path)
    component = _component_revision(repository)
    _git(repository, "checkout", "-q", "--detach", runtime)
    with pytest.raises(RuntimeError, match="not retained"):
        inspect_helper_component(repository, runtime, component)
    with pytest.raises(RuntimeError):
        inspect_helper_component(repository, runtime, "0" * 40)


@pytest.mark.parametrize("attribute", ["export-ignore", "export-subst"])
def test_git_export_attributes_cannot_hide_or_rewrite_runtime_inputs(
    tmp_path: Path, attribute: str
) -> None:
    repository, _ = _repository(tmp_path)
    (repository / ".gitattributes").write_text(f"adminme/runtime.py {attribute}\n")
    (repository / "adminme/runtime.py").write_text("revision = '$Format:%H$'\n")
    commit = _commit(repository)
    inputs = inspect_helper_component(repository, commit, commit)
    archive = tmp_path / "exported.tar"
    _archive(repository, commit, archive)
    with pytest.raises(RuntimeError, match="omits|bytes differ"):
        verify_runtime_archive(archive, inputs.runtime_inventory)


@pytest.mark.parametrize(
    "defect",
    [
        "bytes",
        "mode",
        "world_writable",
        "special_mode",
        "symlink",
        "hardlink",
        "duplicate",
        "directory_duplicate",
        "directory_mode",
        "directory_missing",
        "directory_noncanonical",
    ],
)
def test_archive_inventory_type_content_and_mode_are_checked(
    tmp_path: Path, defect: str
) -> None:
    archive = tmp_path / "tampered.tar"
    payload = b"fixed runtime bytes\n"
    inventory = {
        "runtime/code.py": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "mode": "100644",
        }
    }
    with tarfile.open(archive, "w") as stream:
        directory = tarfile.TarInfo(
            "./runtime" if defect == "directory_noncanonical" else "runtime"
        )
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o777 if defect == "directory_mode" else 0o755
        if defect != "directory_missing":
            stream.addfile(directory)
        if defect == "directory_duplicate":
            stream.addfile(directory)
        member = tarfile.TarInfo("runtime/code.py")
        member.mode = {
            "mode": 0o755,
            "world_writable": 0o666,
            "special_mode": 0o4644,
        }.get(defect, 0o644)
        if defect in {"symlink", "hardlink"}:
            member.type = tarfile.SYMTYPE if defect == "symlink" else tarfile.LNKTYPE
            member.linkname = "other.py"
            stream.addfile(member)
        else:
            content = b"tampered bytes\n" if defect == "bytes" else payload
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
            if defect == "duplicate":
                stream.addfile(member, io.BytesIO(content))
    with pytest.raises(RuntimeError):
        verify_runtime_archive(archive, inventory)


def test_loaded_builder_is_verified_even_when_checkout_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, commit = _repository(tmp_path)
    different = tmp_path / "different-builder.py"
    different.write_text("not the committed builder\n")
    monkeypatch.setattr(member_bridge_helper_app, "__file__", str(different))
    with pytest.raises(RuntimeError, match="loaded helper build code differs"):
        build_member_bridge_helper_app(
            tmp_path / "Helper.app", root=repository, expected_commit=commit
        )
    assert not (tmp_path / "Helper.app").exists()


def test_distinct_runtime_requires_prior_exact_archive_proof(tmp_path: Path) -> None:
    repository, runtime = _repository(tmp_path)
    component = _component_revision(repository)
    with pytest.raises(RuntimeError, match="previously verified archive SHA-256"):
        build_member_bridge_helper_app(
            tmp_path / "Helper.app",
            root=repository,
            expected_commit=component,
            runtime_commit=runtime,
        )
    assert not (tmp_path / "Helper.app").exists()


@pytest.mark.skipif(
    sys.platform != "darwin", reason="sealed helper packaging requires hosted macOS"
)
def test_sealed_component_reuses_exact_old_runtime_and_preserves_rollback(
    tmp_path: Path,
) -> None:
    repository, runtime = _repository(tmp_path)
    previous = tmp_path / "Previous Helper.app"
    old_build = build_member_bridge_helper_app(
        previous, root=repository, expected_commit=runtime
    )
    old_resources = previous / "Contents/Resources"
    original = {
        path.relative_to(previous).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in previous.rglob("*")
        if path.is_file()
    }
    managed_state = tmp_path / "retained-state"
    managed_state.write_bytes(
        b"synthetic existing runtime, enrollment and provider state"
    )
    state_before = managed_state.read_bytes()
    component = _component_revision(repository)
    replacement = tmp_path / "Replacement Helper.app"
    result = build_member_bridge_helper_app(
        replacement,
        root=repository,
        expected_commit=component,
        runtime_commit=runtime,
        expected_runtime_archive_sha256=old_build.release_archive_sha256,
    )
    resources = replacement / "Contents/Resources"
    manifest = json.loads((resources / "helper-manifest.json").read_text())
    assert (
        result.release_commit
        == manifest["release_commit"]
        == manifest["runtime_commit"]
        == runtime
    )
    assert (
        result.helper_component_commit
        == manifest["helper_component_commit"]
        == component
    )
    assert manifest["runtime_compatibility"]["runtime_inputs_unchanged"] is True
    assert manifest["runtime_compatibility"]["runtime_commit"] == runtime
    assert manifest["release_archive_sha256"] == old_build.release_archive_sha256
    assert (resources / "administrateme-reviewed-release.tar").read_bytes() == (
        old_resources / "administrateme-reviewed-release.tar"
    ).read_bytes()
    assert (resources / "install-member-bridge.sh").read_bytes() == (
        repository / "bootstrap/install-member-bridge.sh"
    ).read_bytes()
    controller = resources / "member_bridge_permission_controller.py"
    assert (
        controller.read_bytes()
        == (
            repository / "bootstrap/member_bridge_permission_controller.py"
        ).read_bytes()
    )
    assert (
        manifest["permission_controller_sha256"]
        == hashlib.sha256(controller.read_bytes()).hexdigest()
    )
    assert manifest["installer_sha256"] != old_build.installer_sha256
    launcher = (resources / "bridge-helper-launcher.sh").read_text()
    assert f"EXPECTED_COMMIT={runtime}\n" in launcher
    assert f"HELPER_COMPONENT_COMMIT={component}\n" in launcher
    assert managed_state.read_bytes() == state_before
    assert original == {
        path.relative_to(previous).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in previous.rglob("*")
        if path.is_file()
    }
    for app in (previous, replacement):
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
            check=True,
        )
    with pytest.raises(RuntimeError, match="verified SHA-256"):
        build_member_bridge_helper_app(
            tmp_path / "Wrong Archive.app",
            root=repository,
            expected_commit=component,
            runtime_commit=runtime,
            expected_runtime_archive_sha256="0" * 64,
        )


def test_new_controller_is_exact_component_path_without_widening_runtime_exclusions(
    tmp_path: Path,
) -> None:
    repository, _ = _repository(tmp_path)
    controller = repository / "bootstrap/member_bridge_permission_controller.py"
    controller.unlink()
    retained = _commit(repository)
    shutil.copy2(ROOT / "bootstrap/member_bridge_permission_controller.py", controller)
    component = _commit(repository)
    inputs = inspect_helper_component(repository, component, retained)
    assert inputs.compatibility["runtime_inputs_unchanged"] is True
    assert (
        "bootstrap/member_bridge_permission_controller.py"
        not in inputs.runtime_inventory
    )
    assert (
        inputs.component_payloads["bootstrap/member_bridge_permission_controller.py"]
        == controller.read_bytes()
    )
    assert not is_runtime_input("bootstrap/member_bridge_permission_controller.py")
    for shared in (
        "bootstrap/member_bridge.py",
        "bootstrap/member_bridge_permissions.py",
        "bridge/adminme_bridge/enrollment.py",
        "bridge/adminme_bridge/enrollment_ui.py",
        "bridge/adminme_bridge/permissions.py",
    ):
        assert is_runtime_input(shared)


@pytest.mark.parametrize("central_path", sorted(PERMISSION_RECOVERY_CENTRAL_PATHS))
def test_explicit_retained_edge_contract_preserves_strict_default_and_ci_fingerprint(
    tmp_path: Path,
    central_path: str,
) -> None:
    repository, retained = _repository(tmp_path)
    original = (repository / central_path).read_bytes()
    (repository / central_path).write_bytes(
        original + b"# Versioned central recovery protocol.\n"
    )
    component = _commit(repository)
    assert is_runtime_input(central_path)
    assert runtime_interface_fingerprint(
        repository, retained
    ) != runtime_interface_fingerprint(repository, component)
    with pytest.raises(RuntimeError, match="changes retained runtime inputs"):
        inspect_helper_component(repository, component, retained)
    inspected = inspect_helper_component(
        repository,
        component,
        retained,
        compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
    )
    contract = inspected.compatibility
    assert contract["runtime_inputs_unchanged"] is False
    assert contract["retained_edge_inputs_unchanged"] is True
    assert contract["required_central_protocol"] == PERMISSION_RECOVERY_PROTOCOL
    assert contract["required_central_release_commit"] == component
    assert contract["runtime_commit"] == retained
    assert contract["helper_component_commit"] == component
    assert contract["allowed_central_difference_paths"] == sorted(
        PERMISSION_RECOVERY_CENTRAL_PATHS
    )
    assert contract["central_differences"] == [
        {
            "path": central_path,
            "retained": {
                "mode": "100644",
                "sha256": hashlib.sha256(original).hexdigest(),
            },
            "central_candidate": {
                "mode": "100644",
                "sha256": hashlib.sha256(
                    (repository / central_path).read_bytes()
                ).hexdigest(),
            },
        }
    ]
    archive = tmp_path / "retained-edge.tar"
    _archive(repository, retained, archive)
    verify_runtime_archive(archive, inspected.runtime_inventory)


@pytest.mark.parametrize(
    "relative",
    [
        "adminme/bridge_enrollment.py",
        "adminme/bridge_history.py",
        "adminme/settings_control.py",
        "adminme/products/bridge/routers/other.py",
        "adminme/events/log.py",
        "adminme/lib/governance.py",
        "bootstrap/bridge_enrollment.py",
        "bridge/adminme_bridge/enrollment_helper.py",
        "bridge/adminme_bridge/enrollment_ui.py",
        "bridge/adminme_bridge/ingest_client.py",
        "bootstrap/runtime-manifest.json",
        "bridge/poetry.lock",
        "poetry.lock",
        "README.md",
        "unknown-runtime.py",
    ],
)
def test_retained_edge_opt_in_does_not_exempt_other_central_edge_identity_or_dependency_inputs(
    tmp_path: Path,
    relative: str,
) -> None:
    repository, retained = _repository(tmp_path)
    changed = repository / relative
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("different protected runtime input\n")
    component = _commit(repository)
    with pytest.raises(RuntimeError, match="unsupported runtime inputs"):
        inspect_helper_component(
            repository,
            component,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


@pytest.mark.parametrize("defect", ["delete", "executable", "add"])
def test_retained_edge_contract_rejects_central_path_presence_or_mode_change(
    tmp_path: Path,
    defect: str,
) -> None:
    repository, retained = _repository(tmp_path)
    route = repository / "adminme/products/bridge/routers/ingest.py"
    if defect == "delete":
        route.unlink()
    elif defect == "executable":
        route.chmod(0o755)
    else:
        route.unlink()
        retained = _commit(repository)
        route.write_text("new central path\n")
    component = _commit(repository)
    with pytest.raises(RuntimeError, match="unsupported runtime inputs"):
        inspect_helper_component(
            repository,
            component,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


def test_unknown_retained_edge_contract_fails_closed(tmp_path: Path) -> None:
    repository, retained = _repository(tmp_path)
    with pytest.raises(
        RuntimeError, match="unsupported retained runtime compatibility"
    ):
        inspect_helper_component(
            repository, retained, retained, compatibility_contract="unreviewed-contract"
        )


@pytest.mark.skipif(
    sys.platform != "darwin", reason="sealed mixed packaging requires hosted macOS"
)
def test_sealed_mixed_release_keeps_exact_paired_archive_and_requires_central_negotiation(
    tmp_path: Path,
) -> None:
    repository, retained = _repository(tmp_path)
    archive = tmp_path / "paired.tar"
    archive_sha = _archive(repository, retained, archive)
    route = repository / "adminme/products/bridge/routers/ingest.py"
    route.write_bytes(route.read_bytes() + b"# Supported new central protocol.\n")
    component = _commit(repository)
    with pytest.raises(RuntimeError, match="changes retained runtime inputs"):
        build_member_bridge_helper_app(
            tmp_path / "Rejected.app",
            root=repository,
            expected_commit=component,
            runtime_commit=retained,
            expected_runtime_archive_sha256=archive_sha,
        )
    with pytest.raises(RuntimeError, match="previously verified archive SHA-256"):
        build_member_bridge_helper_app(
            tmp_path / "Missing Proof.app",
            root=repository,
            expected_commit=component,
            runtime_commit=retained,
            runtime_compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )
    app = tmp_path / "Mixed Helper.app"
    build_member_bridge_helper_app(
        app,
        root=repository,
        expected_commit=component,
        runtime_commit=retained,
        expected_runtime_archive_sha256=archive_sha,
        runtime_compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
    )
    manifest = json.loads((app / "Contents/Resources/helper-manifest.json").read_text())
    assert (app / manifest["release_archive_path"]).read_bytes() == archive.read_bytes()
    assert manifest["release_commit"] == manifest["runtime_commit"] == retained
    assert manifest["required_central_release_commit"] == component
    assert manifest["required_central_protocol"] == PERMISSION_RECOVERY_PROTOCOL
    assert manifest["runtime_compatibility"]["runtime_archive_sha256"] == archive_sha
    assert manifest["runtime_compatibility"]["runtime_inputs_unchanged"] is False
    launcher = (app / "Contents/Resources/bridge-helper-launcher.sh").read_text()
    assert '--expected-central-commit "$HELPER_COMPONENT_COMMIT"' in launcher
    assert '--expected-runtime-commit "$EXPECTED_COMMIT"' in launcher
    with pytest.raises(RuntimeError, match="verified SHA-256"):
        build_member_bridge_helper_app(
            tmp_path / "Wrong Proof.app",
            root=repository,
            expected_commit=component,
            runtime_commit=retained,
            expected_runtime_archive_sha256="0" * 64,
            runtime_compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )
