"""Actual public shell/tooling-selection boundary in a synthetic filesystem."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from bootstrap.contracts import atomic_write
from bootstrap.prepared_runtime import runtime_artifact_fingerprint
from bootstrap.source_identity import (
    verify_release_source_identity,
    write_source_marker,
)

ROOT = Path(__file__).resolve().parents[3]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def fixture(tmp_path: Path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    git(candidate, "init", "-q")
    git(candidate, "config", "user.name", "Synthetic updater fixture")
    git(candidate, "config", "user.email", "updater@example.invalid")
    versions = {
        "python": {
            "formula": "python@3.14",
            "preferred": "3.14.0",
            "compatible": ">=3.14,<3.15",
        },
        "node": {
            "formula": "node@24",
            "preferred": "24.22.1",
            "compatible": ">=24,<25",
        },
        "openclaw": {
            "commit": "a" * 40,
            "version": "2026.10.7",
            "plugin_api": "2026.10.7",
        },
    }
    (candidate / "bootstrap").mkdir()
    (candidate / "bootstrap/versions.json").write_text(json.dumps(versions))
    git(candidate, "add", ".")
    git(candidate, "commit", "-qm", "Synthetic failed target")
    failed = git(candidate, "rev-parse", "HEAD")
    instance = tmp_path / "instance"
    release = instance / "app/releases" / failed
    (release / "bootstrap").mkdir(parents=True, mode=0o700)
    shutil.copyfile(
        candidate / "bootstrap/versions.json", release / "bootstrap/versions.json"
    )
    (candidate / "corrected.txt").write_text("Bounded correction\n")
    git(candidate, "add", ".")
    git(candidate, "commit", "-qm", "Synthetic corrected target")
    commit = git(candidate, "rev-parse", "HEAD")
    write_source_marker(release, failed)
    runtime = release / ".venv/bin"
    runtime.mkdir(parents=True, mode=0o700)
    atomic_write(runtime / "python", b"#!/bin/sh\nexit 99\n", mode=0o700)
    source = verify_release_source_identity(
        release,
        repository=candidate,
        commit=failed,
        allow_legacy_tree=False,
        require_tracked_tree=True,
    )
    metadata = release.stat()
    prepared = {
        "contract": "administrateme-release-preparation-v1",
        "phase": "consumed",
        "commit": failed,
        "candidate_controller_commit": failed,
        "instance_sha256": hashlib.sha256(str(instance).encode()).hexdigest(),
        "release_device": metadata.st_dev,
        "release_inode": metadata.st_ino,
        "source_manifest_sha256": source["tracked_source_manifest_sha256"],
        "runtime_artifact_sha256": runtime_artifact_fingerprint(release),
    }
    atomic_write(
        release.parent / f".release-state-{failed}.json", json.dumps(prepared).encode()
    )
    parent = {
        "contract": "administrateme-activation-transaction-v1",
        "operation": "activate",
        "phase": "native_forward_repair_required",
        "transaction_id": "b" * 32,
        "target_commit": failed,
        "target_path": str(release),
        "openclaw_migration": {"state": "contained"},
    }
    atomic_write(instance / "app/activation/active.json", json.dumps(parent).encode())
    now = datetime.now(timezone.utc)
    plan = {
        "contract": "administrateme-native-contained-recovery-v1",
        "candidate_commit": commit,
        "failed_target_commit": failed,
        "parent_transaction_id": parent["transaction_id"],
        "parent_journal_sha256": hashlib.sha256(
            json.dumps(parent, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "recorded_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "automatic_rollback_eligible": False,
    }
    plan_path = tmp_path / "plan.json"
    atomic_write(plan_path, json.dumps(plan).encode())
    # The active selector deliberately retains the old runtime manifest.
    old = instance / "app/releases" / ("c" * 40)
    (old / "bootstrap").mkdir(parents=True)
    old_versions = copy.deepcopy(versions)
    old_versions["node"]["preferred"] = "24.19.0"
    atomic_write(old / "bootstrap/versions.json", json.dumps(old_versions).encode())
    atomic_write(
        instance / "app/current-release.json",
        json.dumps({"path": str(old), "commit": "c" * 40}).encode(),
    )
    return instance, candidate, commit, plan_path, release


def public_selection(
    instance: Path,
    candidate: Path,
    commit: str,
    plan: Path,
    release: Path,
    *,
    approve: bool = False,
):
    source = (ROOT / "bootstrap/update.sh").read_text()
    start = source.index(
        'if [[ -n "$native_recovery_flag" ]]; then\n  [[ -z "$approve_flag"'
    )
    end = source.index("\ncontroller_tooling=", start)
    # Execute the exact public selection branch, stopping before any tooling
    # installation. Only the OS formula lookup is a credential-free fake.
    script = (
        """set -euo pipefail
fail() { echo "$*" >&2; exit 1; }
resolve_formula_node() { [[ "$1" == node@24 && "$2" == 24.22.1 ]] || exit 1; echo /synthetic/node/bin/node; }
"""
        + source[start:end]
        + '\nprintf "%s\\n%s\\n" "$controller_python" "$reviewed_node"\n'
    )
    environment = {
        **os.environ,
        "native_recovery_flag": f"--native-recovery-plan={plan}",
        "approve_flag": "--approve-runtime-migration" if approve else "",
        "source_repair_flag": "",
        "provider_binding_flag": "",
        "effect_context_repair_flag": "",
        "submission_repair_flag": "",
        "test_reset_flag": "",
        "manifest_parser": sys.executable,
        "fixed_controller_root": str(ROOT),
        "ADMINME_INSTANCE_ROOT": str(instance),
        "candidate": str(candidate),
        "candidate_commit": commit,
        "node_formula": "node@24",
        "node_preferred": "24.22.1",
    }
    # dirname/cd in the public branch requires a real directory; the fake Node
    # file is never executed.
    node_dir = release.parent / "synthetic-node"
    node_dir.mkdir(exist_ok=True)
    script = script.replace("/synthetic/node/bin/node", str(node_dir / "node"))
    return subprocess.run(
        ["bash", "-c", script],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_public_entry_uses_exact_consumed_target_without_moving_old_pointer(
    tmp_path: Path,
) -> None:
    instance, candidate, commit, plan, release = fixture(tmp_path)
    pointer = (instance / "app/current-release.json").read_bytes()
    result = public_selection(instance, candidate, commit, plan, release)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == str(release / ".venv/bin/python")
    assert (instance / "app/current-release.json").read_bytes() == pointer
    assert not (instance / "app/tooling").exists()


@pytest.mark.parametrize(
    "mutation", ["plan", "journal", "runtime", "manifest", "unconsumed", "new_approval"]
)
def test_public_entry_refuses_unbound_or_new_runtime_authority(
    tmp_path: Path, mutation: str
) -> None:
    instance, candidate, commit, plan, release = fixture(tmp_path)
    if mutation in {"plan", "journal"}:
        path = plan if mutation == "plan" else instance / "app/activation/active.json"
        value = json.loads(path.read_bytes())
        value["candidate_commit" if mutation == "plan" else "target_commit"] = "d" * 40
        atomic_write(path, json.dumps(value).encode())
    elif mutation == "runtime":
        atomic_write(release / ".venv/bin/python", b"changed interpreter", mode=0o700)
    elif mutation == "manifest":
        # Even another clean corrected commit cannot request a new Node patch.
        path = candidate / "bootstrap/versions.json"
        value = json.loads(path.read_bytes())
        value["node"]["preferred"] = "24.23.0"
        path.write_text(json.dumps(value))
        git(candidate, "add", ".")
        git(candidate, "commit", "-qm", "Unapproved extra transition")
        commit = git(candidate, "rev-parse", "HEAD")
        value = json.loads(plan.read_bytes())
        value["candidate_commit"] = commit
        atomic_write(plan, json.dumps(value).encode())
    elif mutation == "unconsumed":
        path = release.parent / f".release-state-{release.name}.json"
        value = json.loads(path.read_bytes())
        value["phase"] = "complete"
        atomic_write(path, json.dumps(value).encode())
    result = public_selection(
        instance, candidate, commit, plan, release, approve=mutation == "new_approval"
    )
    assert result.returncode != 0
    assert not result.stdout.strip()


@pytest.mark.parametrize("changed_ancestor", [False, True])
def test_public_entry_retains_consumed_ancestor_after_preconsume_correction_failure(
    tmp_path: Path, changed_ancestor: bool
) -> None:
    instance, candidate, failed_correction, plan, consumed = fixture(tmp_path)
    active = instance / "app/activation/active.json"
    parent = json.loads(active.read_bytes())
    archive = instance / "app/activation/history" / f"{parent['transaction_id']}.json"
    atomic_write(archive, json.dumps(parent).encode())
    release = consumed.parent / failed_correction
    shutil.copytree(consumed, release)
    (release / "corrected.txt").write_bytes((candidate / "corrected.txt").read_bytes())
    write_source_marker(release, failed_correction)
    source = verify_release_source_identity(
        release,
        repository=candidate,
        commit=failed_correction,
        allow_legacy_tree=False,
        require_tracked_tree=True,
    )
    prepared = json.loads(
        (consumed.parent / f".release-state-{consumed.name}.json").read_bytes()
    )
    prepared.update(
        phase="complete",
        commit=failed_correction,
        candidate_controller_commit=failed_correction,
        release_device=release.stat().st_dev,
        release_inode=release.stat().st_ino,
        source_manifest_sha256=source["tracked_source_manifest_sha256"],
        runtime_artifact_sha256=runtime_artifact_fingerprint(release),
    )
    atomic_write(
        consumed.parent / f".release-state-{failed_correction}.json",
        json.dumps(prepared).encode(),
    )
    child = {
        **parent,
        "transaction_id": "e" * 32,
        "target_commit": failed_correction,
        "target_path": str(release),
        "native_recovery": {
            "contract": "administrateme-native-contained-recovery-v1",
            "candidate_commit": failed_correction,
            "failed_target_commit": consumed.name,
            "parent_transaction_id": parent["transaction_id"],
            "parent_journal_sha256": hashlib.sha256(
                json.dumps(parent, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
    }
    child.pop("openclaw_migration")
    atomic_write(active, json.dumps(child).encode())
    (candidate / "next-correction.txt").write_text("Next bounded correction\n")
    git(candidate, "add", ".")
    git(candidate, "commit", "-qm", "Correct early failed correction")
    commit = git(candidate, "rev-parse", "HEAD")
    value = json.loads(plan.read_bytes())
    value.update(
        candidate_commit=commit,
        failed_target_commit=failed_correction,
        parent_transaction_id=child["transaction_id"],
        parent_journal_sha256=hashlib.sha256(
            json.dumps(child, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    atomic_write(plan, json.dumps(value).encode())
    if changed_ancestor:
        atomic_write(archive, json.dumps({**parent, "phase": "committed"}).encode())
    result = public_selection(instance, candidate, commit, plan, release)
    if changed_ancestor:
        assert result.returncode != 0
        assert not result.stdout.strip()
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[0] == str(consumed / ".venv/bin/python")
        assert json.loads(active.read_bytes()) == child


def test_reviewed_launcher_and_fixed_supervisor_preserve_explicit_recovery_flag(
    tmp_path: Path,
) -> None:
    from tests.unit.bootstrap.test_source_identity import (
        _commit,
        _supervisor_instance,
        _supervisor_repository,
    )

    repository, _ = _supervisor_repository(tmp_path)
    script = repository / "bootstrap/update.sh"
    script.write_text(
        "#!/bin/bash -p\nset -Eeuo pipefail\n"
        '[[ "${ADMINME_UPDATE_SUPERVISED:-}" == 1 ]]\n'
        '[[ -n "${ADMINME_UPGRADE_OPERATION_LOCK_FD:-}" ]]\n'
        'printf "%s\\n" "$ADMINME_SUPERVISED_CANDIDATE_COMMIT" "$@" >"$ADMINME_SUPERVISOR_REPORT"\n'
    )
    script.chmod(0o755)
    commit = _commit(repository, "Record exact fixed controller arguments")
    paths, _ = _supervisor_instance(tmp_path)
    report = tmp_path / "arguments"
    plan = tmp_path / "native-plan.json"
    atomic_write(
        plan, b'{"synthetic":"argument only; controller admission is separate"}'
    )
    arguments = [
        "--pending-attestation-id=" + "d" * 32,
        f"--native-recovery-plan={plan}",
        str(repository),
    ]
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-I",
            str(ROOT / "bootstrap/reviewed_launcher.py"),
            "update",
            str(paths.root),
            str(repository),
            *arguments,
        ],
        env={**os.environ, "ADMINME_SUPERVISOR_REPORT": str(report)},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert report.read_text().splitlines() == [commit, *arguments]
    assert (paths.app_dir / ".upgrade-operation.lock").is_file()
    assert not list((paths.app_dir / "tooling").glob(".upgrade-controller-source-*"))
