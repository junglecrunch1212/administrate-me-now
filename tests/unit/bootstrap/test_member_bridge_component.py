"""Fixed component packaging and retained-runtime identity regression gates."""

from __future__ import annotations

import ast
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
    PERMISSION_RECOVERY_CANDIDATE_MODULE_SHA256,
    PERMISSION_RECOVERY_COMPATIBILITY,
    PERMISSION_RECOVERY_COS_CENTRAL_COMMIT,
    PERMISSION_RECOVERY_COS_OVERLAY,
    PERMISSION_RECOVERY_COS_OVERLAY_CONTRACT,
    PERMISSION_RECOVERY_COS_RETAINED_COMMIT,
    PERMISSION_RECOVERY_GATEWAY_PROBE_CANDIDATE_SHA256,
    PERMISSION_RECOVERY_GATEWAY_PROBE_PATH,
    PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256,
    PERMISSION_RECOVERY_GATEWAY_PROBE_RULE,
    PERMISSION_RECOVERY_METHOD_RULE,
    PERMISSION_RECOVERY_PROTOCOL,
    PERMISSION_RECOVERY_RETAINED_MODULE_SHA256,
    PERMISSION_RECOVERY_SHARED_MODULE,
    inspect_helper_component,
    is_runtime_input,
    runtime_interface_fingerprint,
    verify_runtime_archive,
)
from bootstrap.member_bridge_helper_app import build_member_bridge_helper_app
from bootstrap.source_identity import sanitized_git_environment


ROOT = Path(__file__).resolve().parents[3]


def _enrollment_revisions() -> tuple[bytes, bytes]:
    candidate = (ROOT / PERMISSION_RECOVERY_SHARED_MODULE).read_bytes()
    start = candidate.index(b"        # Configuring one source must not revoke")
    end = candidate.index(b"        record.update(\n", start)
    retained = (candidate[:start] + candidate[end:]).replace(
        b'"provider_capability_state": global_state,',
        b'"provider_capability_state": "not_attested",',
        1,
    )
    assert (
        hashlib.sha256(candidate).hexdigest()
        == PERMISSION_RECOVERY_CANDIDATE_MODULE_SHA256
    )
    assert (
        hashlib.sha256(retained).hexdigest()
        == PERMISSION_RECOVERY_RETAINED_MODULE_SHA256
    )
    return retained, candidate


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
    (repository / PERMISSION_RECOVERY_SHARED_MODULE).write_bytes(
        _enrollment_revisions()[0]
    )
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


def _cos_overlay_payload(path: str, *, candidate: bool) -> bytes | None:
    old_sha, new_sha = PERMISSION_RECOVERY_COS_OVERLAY[path]
    expected_sha = new_sha if candidate else old_sha
    if expected_sha is None:
        return None
    # This fixture proves a closed historical pair, not today's working tree.
    # Later unrelated changes must neither replace the reviewed bytes nor force
    # new production exemptions just to construct this synthetic repository.
    revision = (
        PERMISSION_RECOVERY_COS_CENTRAL_COMMIT
        if candidate
        else PERMISSION_RECOVERY_COS_RETAINED_COMMIT
    )
    payload = subprocess.run(
        ["/usr/bin/git", "-C", str(ROOT), "show", f"{revision}:{path}"],
        env=sanitized_git_environment(),
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    assert hashlib.sha256(payload).hexdigest() == expected_sha
    return payload


def _write_cos_overlay(repository: Path, *, candidate: bool) -> None:
    for path in PERMISSION_RECOVERY_COS_OVERLAY:
        target = repository / path
        payload = _cos_overlay_payload(path, candidate=candidate)
        if payload is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(0o644)


def _cos_overlay_repository(directory: Path) -> tuple[Path, str, str]:
    repository, _ = _repository(directory)
    _write_cos_overlay(repository, candidate=False)
    retained = _commit(repository)
    _write_cos_overlay(repository, candidate=True)
    return repository, retained, _commit(repository)


def _gateway_probe_revisions() -> tuple[bytes, bytes]:
    retained = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(ROOT),
            "show",
            f"{PERMISSION_RECOVERY_COS_RETAINED_COMMIT}:{PERMISSION_RECOVERY_GATEWAY_PROBE_PATH}",
        ],
        env=sanitized_git_environment(),
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    candidate = (ROOT / PERMISSION_RECOVERY_GATEWAY_PROBE_PATH).read_bytes()
    assert (
        hashlib.sha256(retained).hexdigest()
        == PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256
    )
    assert (
        hashlib.sha256(candidate).hexdigest()
        == PERMISSION_RECOVERY_GATEWAY_PROBE_CANDIDATE_SHA256
    )
    return retained, candidate


def _gateway_probe_repository(
    directory: Path, *, with_cos: bool = False
) -> tuple[Path, str, str]:
    if with_cos:
        repository, retained, _ = _cos_overlay_repository(directory)
        _git(repository, "checkout", "--detach", retained)
    else:
        repository, _ = _repository(directory)
    old, new = _gateway_probe_revisions()
    target = repository / PERMISSION_RECOVERY_GATEWAY_PROBE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(old)
    retained = _commit(repository)
    if with_cos:
        _write_cos_overlay(repository, candidate=True)
    target.write_bytes(new)
    return repository, retained, _commit(repository)


@pytest.mark.parametrize("with_cos", [False, True])
def test_exact_hosted_gateway_probe_pair_is_independent_and_retains_old_archive(
    tmp_path: Path,
    with_cos: bool,
) -> None:
    repository, retained, candidate = _gateway_probe_repository(
        tmp_path, with_cos=with_cos
    )
    assert is_runtime_input(PERMISSION_RECOVERY_GATEWAY_PROBE_PATH)
    assert PERMISSION_RECOVERY_GATEWAY_PROBE_PATH not in PERMISSION_RECOVERY_COS_OVERLAY
    assert runtime_interface_fingerprint(
        repository, retained
    ) != runtime_interface_fingerprint(repository, candidate)
    with pytest.raises(RuntimeError, match="changes retained runtime inputs"):
        inspect_helper_component(repository, candidate, retained)
    inspected = inspect_helper_component(
        repository,
        candidate,
        retained,
        compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
    )
    assert inspected.compatibility["reviewed_hosted_gateway_probe"] == {
        "contract": PERMISSION_RECOVERY_GATEWAY_PROBE_RULE,
        "path": PERMISSION_RECOVERY_GATEWAY_PROBE_PATH,
        "retained_sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256,
        "central_candidate_sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_CANDIDATE_SHA256,
    }
    differences = inspected.compatibility["central_differences"]
    assert isinstance(differences, list)
    assert [
        row
        for row in differences
        if row["path"] == PERMISSION_RECOVERY_GATEWAY_PROBE_PATH
    ] == [
        {
            "path": PERMISSION_RECOVERY_GATEWAY_PROBE_PATH,
            "retained": {
                "mode": "100644",
                "sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256,
            },
            "central_candidate": {
                "mode": "100644",
                "sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_CANDIDATE_SHA256,
            },
            "compatibility_rule": PERMISSION_RECOVERY_GATEWAY_PROBE_RULE,
        }
    ]
    assert ("reviewed_central_overlay" in inspected.compatibility) is with_cos
    assert len(differences) == (32 if with_cos else 1)
    assert inspected.compatibility["required_central_release_commit"] == candidate
    assert (
        inspected.compatibility["required_central_protocol"]
        == PERMISSION_RECOVERY_PROTOCOL
    )
    archive = tmp_path / "exact-retained-probe.tar"
    _archive(repository, retained, archive)
    verify_runtime_archive(archive, inspected.runtime_inventory)
    with tarfile.open(archive) as package:
        stream = package.extractfile(PERMISSION_RECOVERY_GATEWAY_PROBE_PATH)
        assert stream is not None
        assert (
            hashlib.sha256(stream.read()).hexdigest()
            == PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256
        )


@pytest.mark.parametrize("side", ["candidate", "retained"])
@pytest.mark.parametrize("defect", ["bytes", "mode", "missing"])
def test_hosted_gateway_probe_requires_both_exact_regular_file_coordinates(
    tmp_path: Path,
    side: str,
    defect: str,
) -> None:
    repository, retained, _ = _gateway_probe_repository(tmp_path)
    if side == "retained":
        _git(repository, "checkout", "--detach", retained)
    target = repository / PERMISSION_RECOVERY_GATEWAY_PROBE_PATH
    if defect == "bytes":
        target.write_bytes(target.read_bytes() + b"\n// unknown\n")
    elif defect == "mode":
        target.chmod(0o755)
    else:
        target.unlink()
    candidate = _commit(repository)
    if side == "retained":
        retained = candidate
        target.write_bytes(_gateway_probe_revisions()[1])
        target.chmod(0o644)
        candidate = _commit(repository)
    with pytest.raises(RuntimeError, match="reviewed hosted Gateway probe changed"):
        inspect_helper_component(
            repository,
            candidate,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


def test_hosted_gateway_probe_pair_cannot_be_reversed(tmp_path: Path) -> None:
    repository, _, retained = _gateway_probe_repository(tmp_path)
    (repository / PERMISSION_RECOVERY_GATEWAY_PROBE_PATH).write_bytes(
        _gateway_probe_revisions()[0]
    )
    candidate = _commit(repository)
    with pytest.raises(RuntimeError, match="reviewed hosted Gateway probe changed"):
        inspect_helper_component(
            repository,
            candidate,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


@pytest.mark.parametrize(
    "path", ["openclaw/administrateme/scripts/unknown-probe.mjs", "bridge/poetry.lock"]
)
def test_reviewed_probe_does_not_exempt_nearby_files_or_dependencies(
    tmp_path: Path, path: str
) -> None:
    repository, retained, _ = _gateway_probe_repository(tmp_path)
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unreviewed\n")
    candidate = _commit(repository)
    with pytest.raises(RuntimeError, match="unsupported runtime inputs"):
        inspect_helper_component(
            repository,
            candidate,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


def test_complete_reviewed_cos_overlay_preserves_exact_retained_archive(
    tmp_path: Path,
) -> None:
    repository, retained, candidate = _cos_overlay_repository(tmp_path)
    assert len(PERMISSION_RECOVERY_COS_OVERLAY) == 32
    assert sum(old is None for old, _ in PERMISSION_RECOVERY_COS_OVERLAY.values()) == 8
    assert all(is_runtime_input(path) for path in PERMISSION_RECOVERY_COS_OVERLAY)
    assert runtime_interface_fingerprint(
        repository, candidate
    ) != runtime_interface_fingerprint(repository, retained)
    with pytest.raises(RuntimeError, match="changes retained runtime inputs"):
        inspect_helper_component(repository, candidate, retained)
    inspected = inspect_helper_component(
        repository,
        candidate,
        retained,
        compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
    )
    expected = [
        {
            "path": path,
            "retained": {"mode": "100644", "sha256": old} if old else None,
            "central_candidate": {"mode": "100644", "sha256": new},
            "compatibility_rule": PERMISSION_RECOVERY_COS_OVERLAY_CONTRACT,
        }
        for path, (old, new) in sorted(PERMISSION_RECOVERY_COS_OVERLAY.items())
    ]
    assert inspected.compatibility["central_differences"] == expected
    assert inspected.compatibility["reviewed_central_overlay"] == {
        "contract": PERMISSION_RECOVERY_COS_OVERLAY_CONTRACT,
        "retained_reference_commit": PERMISSION_RECOVERY_COS_RETAINED_COMMIT,
        "central_reference_commit": PERMISSION_RECOVERY_COS_CENTRAL_COMMIT,
        "paths": sorted(PERMISSION_RECOVERY_COS_OVERLAY),
        "complete_group_verified": True,
    }
    assert inspected.compatibility["runtime_inputs_unchanged"] is False
    assert inspected.compatibility["retained_edge_inputs_unchanged"] is True
    assert inspected.compatibility["required_central_release_commit"] == candidate
    assert (
        inspected.compatibility["required_central_protocol"]
        == PERMISSION_RECOVERY_PROTOCOL
    )
    archive = tmp_path / "retained-with-old-cos.tar"
    _archive(repository, retained, archive)
    verify_runtime_archive(archive, inspected.runtime_inventory)
    with tarfile.open(archive) as package:
        for path, (old, _) in PERMISSION_RECOVERY_COS_OVERLAY.items():
            if old is None:
                assert path not in package.getnames()
            else:
                stream = package.extractfile(path)
                assert stream is not None
                assert hashlib.sha256(stream.read()).hexdigest() == old


@pytest.mark.parametrize("path", sorted(PERMISSION_RECOVERY_COS_OVERLAY))
@pytest.mark.parametrize("side", ["retained", "candidate"])
def test_reviewed_cos_overlay_rejects_unknown_bytes_in_every_source_pair(
    tmp_path: Path,
    path: str,
    side: str,
) -> None:
    repository, retained, _ = _cos_overlay_repository(tmp_path)
    if side == "retained":
        _git(repository, "checkout", "--detach", retained)
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        (target.read_bytes() if target.exists() else b"") + b"\nunknown\n"
    )
    changed = _commit(repository)
    if side == "retained":
        retained = changed
        _write_cos_overlay(repository, candidate=True)
        changed = _commit(repository)
    with pytest.raises(
        RuntimeError, match="reviewed central overlay is incomplete or changed"
    ):
        inspect_helper_component(
            repository,
            changed,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


@pytest.mark.parametrize(
    "path",
    [
        "adminme/cos_channel_operation.py",
        "adminme/cos_self_replies.py",
        "bootstrap/cos_channel_activation.py",
        "bootstrap/cos_reply_hooks.py",
        "adminme/adapters/member_google_connections.py",
        "adminme/settings_control.py",
        "bootstrap/versions.json",
        "scripts/google_connection_wiring.json",
        "openclaw/administrateme/scripts/check-cos-imessage-text.mjs",
    ],
)
@pytest.mark.parametrize("defect", ["missing", "mode", "old_coordinate"])
def test_reviewed_cos_overlay_rejects_partial_group_and_candidate_mode_drift(
    tmp_path: Path,
    path: str,
    defect: str,
) -> None:
    repository, retained, _ = _cos_overlay_repository(tmp_path)
    target = repository / path
    if defect == "missing":
        target.unlink()
    elif defect == "mode":
        target.chmod(0o755)
    elif PERMISSION_RECOVERY_COS_OVERLAY[path][0] is None:
        target.unlink()
    else:
        _git(repository, "checkout", retained, "--", path)
    candidate = _commit(repository)
    with pytest.raises(
        RuntimeError, match="reviewed central overlay is incomplete or changed"
    ):
        inspect_helper_component(
            repository,
            candidate,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


@pytest.mark.parametrize("defect", ["mode", "missing", "new_coordinate"])
def test_reviewed_cos_overlay_requires_the_exact_retained_group(
    tmp_path: Path,
    defect: str,
) -> None:
    repository, retained, _ = _cos_overlay_repository(tmp_path)
    _git(repository, "checkout", "--detach", retained)
    path = "adminme/cos_channel_operation.py"
    target = repository / path
    if defect == "mode":
        target.chmod(0o755)
    elif defect == "missing":
        target.unlink()
    else:
        payload = _cos_overlay_payload(path, candidate=True)
        assert payload is not None
        target.write_bytes(payload)
    retained = _commit(repository)
    _write_cos_overlay(repository, candidate=True)
    candidate = _commit(repository)
    with pytest.raises(
        RuntimeError, match="reviewed central overlay is incomplete or changed"
    ):
        inspect_helper_component(
            repository,
            candidate,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


def test_reviewed_cos_overlay_cannot_be_reversed_to_downgrade_central(
    tmp_path: Path,
) -> None:
    repository, _, retained = _cos_overlay_repository(tmp_path)
    _write_cos_overlay(repository, candidate=False)
    candidate = _commit(repository)
    with pytest.raises(
        RuntimeError, match="reviewed central overlay is incomplete or changed"
    ):
        inspect_helper_component(
            repository,
            candidate,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


@pytest.mark.parametrize(
    "path",
    [
        "bridge/poetry.lock",
        "adminme/bridge_history.py",
        "adminme/events/envelope.py",
        "bridge/adminme_bridge/enrollment_helper.py",
        "adminme/bridge_authorization.py",
    ],
)
def test_verified_cos_overlay_does_not_exempt_any_other_dependency_or_edge_contract(
    tmp_path: Path,
    path: str,
) -> None:
    repository, retained, _ = _cos_overlay_repository(tmp_path)
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unreviewed edge or dependency change\n")
    candidate = _commit(repository)
    with pytest.raises(RuntimeError, match="unsupported runtime inputs"):
        inspect_helper_component(
            repository,
            candidate,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


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


def test_exact_central_coordinator_fix_preserves_all_other_shared_module_ast(
    tmp_path: Path,
) -> None:
    repository, retained = _repository(tmp_path)
    old, new = _enrollment_revisions()
    shared = repository / PERMISSION_RECOVERY_SHARED_MODULE

    def protected_ast(payload: bytes) -> str:
        module = ast.parse(payload)
        coordinator = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "BridgeEnrollmentCoordinator"
        )
        method = next(
            node
            for node in coordinator.body
            if isinstance(node, ast.FunctionDef) and node.name == "configure_capability"
        )
        method.body = [ast.Pass()]
        return ast.dump(module, include_attributes=False)

    assert protected_ast(old) == protected_ast(new)
    shared.write_bytes(new)
    component = _commit(repository)
    assert is_runtime_input(PERMISSION_RECOVERY_SHARED_MODULE)
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
    assert inspected.compatibility["central_differences"] == [
        {
            "path": PERMISSION_RECOVERY_SHARED_MODULE,
            "retained": {"mode": "100644", "sha256": hashlib.sha256(old).hexdigest()},
            "central_candidate": {
                "mode": "100644",
                "sha256": hashlib.sha256(new).hexdigest(),
            },
            "compatibility_rule": PERMISSION_RECOVERY_METHOD_RULE,
            "reviewed_central_method_body": "BridgeEnrollmentCoordinator.configure_capability",
        }
    ]
    assert inspected.compatibility["allowed_shared_module_hash_pair"] == {
        "path": PERMISSION_RECOVERY_SHARED_MODULE,
        "retained_sha256": PERMISSION_RECOVERY_RETAINED_MODULE_SHA256,
        "central_candidate_sha256": PERMISSION_RECOVERY_CANDIDATE_MODULE_SHA256,
    }
    archive = tmp_path / "exact-retained-coordinator.tar"
    _archive(repository, retained, archive)
    verify_runtime_archive(archive, inspected.runtime_inventory)
    with tarfile.open(archive) as stream:
        archived = stream.extractfile(PERMISSION_RECOVERY_SHARED_MODULE)
        assert archived is not None and archived.read() == old


@pytest.mark.parametrize(
    "mutation",
    [
        "import",
        "constant",
        "function",
        "other_class",
        "class_base",
        "class_decorator",
        "class_statement",
        "other_method",
        "method_decorator",
        "method_default",
        "method_annotation",
        "method_async",
        "duplicate_class",
        "duplicate_method",
        "unknown_body",
        "comment_only",
        "syntax_error",
    ],
)
def test_shared_module_compatibility_rejects_every_unreviewed_byte_change(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository, retained = _repository(tmp_path)
    shared = repository / PERMISSION_RECOVERY_SHARED_MODULE
    _, candidate = _enrollment_revisions()
    tree = ast.parse(candidate)
    coordinator = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BridgeEnrollmentCoordinator"
    )
    method = next(
        node
        for node in coordinator.body
        if isinstance(node, ast.FunctionDef) and node.name == "configure_capability"
    )
    if mutation == "import":
        tree.body.append(ast.Import(names=[ast.alias(name="fixture_unknown")]))
    elif mutation == "constant":
        tree.body.append(
            ast.Assign(
                targets=[ast.Name(id="UNKNOWN", ctx=ast.Store())],
                value=ast.Constant(value=True),
            )
        )
    elif mutation == "function":
        tree.body.extend(ast.parse("def unknown():\n    return True\n").body)
    elif mutation == "other_class":
        tree.body.extend(ast.parse("class Unknown:\n    pass\n").body)
    elif mutation == "class_base":
        coordinator.bases.append(ast.Name(id="Unknown", ctx=ast.Load()))
    elif mutation == "class_decorator":
        coordinator.decorator_list.append(ast.Name(id="unknown", ctx=ast.Load()))
    elif mutation == "class_statement":
        coordinator.body.append(
            ast.Expr(
                value=ast.Call(
                    func=ast.Name(id="unknown", ctx=ast.Load()), args=[], keywords=[]
                )
            )
        )
    elif mutation == "other_method":
        coordinator.body.extend(ast.parse("def unknown(self):\n    return True\n").body)
    elif mutation == "method_decorator":
        method.decorator_list.append(ast.Name(id="unknown", ctx=ast.Load()))
    elif mutation == "method_default":
        method.args.defaults.append(
            ast.Call(func=ast.Name(id="unknown", ctx=ast.Load()), args=[], keywords=[])
        )
    elif mutation == "method_annotation":
        method.returns = ast.Name(id="Unknown", ctx=ast.Load())
    elif mutation == "method_async":
        coordinator.body[coordinator.body.index(method)] = ast.AsyncFunctionDef(
            **{field: getattr(method, field) for field in method._fields}
        )
    elif mutation == "duplicate_class":
        tree.body.append(coordinator)
    elif mutation == "duplicate_method":
        coordinator.body.append(method)
    elif mutation == "unknown_body":
        method.body.append(
            ast.Expr(
                value=ast.Call(
                    func=ast.Name(id="unknown", ctx=ast.Load()), args=[], keywords=[]
                )
            )
        )
    changed = ast.unparse(ast.fix_missing_locations(tree)).encode()
    if mutation == "comment_only":
        changed = candidate + b"\n# Unknown revision is not an approved hash.\n"
    elif mutation == "syntax_error":
        changed = b"invalid source (\n"
    shared.write_bytes(changed)
    component = _commit(repository)
    with pytest.raises(RuntimeError, match="unsupported runtime inputs"):
        inspect_helper_component(
            repository,
            component,
            retained,
            compatibility_contract=PERMISSION_RECOVERY_COMPATIBILITY,
        )


@pytest.mark.parametrize("defect", ["delete", "executable", "add"])
@pytest.mark.parametrize(
    "central_path",
    ["adminme/products/bridge/routers/ingest.py", PERMISSION_RECOVERY_SHARED_MODULE],
)
def test_retained_edge_contract_rejects_central_path_presence_or_mode_change(
    tmp_path: Path,
    defect: str,
    central_path: str,
) -> None:
    repository, retained = _repository(tmp_path)
    route = repository / central_path
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
    (repository / PERMISSION_RECOVERY_SHARED_MODULE).write_bytes(
        _enrollment_revisions()[1]
    )
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
