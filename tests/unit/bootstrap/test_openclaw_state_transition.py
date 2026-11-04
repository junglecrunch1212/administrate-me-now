"""Native generation recovery never swaps a lone executable over migrated state."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import stat
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.openclaw_state_transition import (
    NativeStateError,
    PREDECESSOR,
    SUCCESSOR,
    capture_generation,
    declared_transition,
    inspect_coverage,
    native_source_fingerprint,
    native_source_inventory,
    restore_generation,
    validate_migrated_authority,
)
from adminme.openclaw_topology import runtime_agent_rows, replace_runtime_agent_rows


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@pytest.fixture
def native(tmp_path):
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.create()
    root = paths.instance_dir / "openclaw"
    runtime = root / "runtime"
    runtime.mkdir(parents=True, mode=0o700)
    rows = []
    for index, agent in enumerate(
        ("adminme-member-a", "adminme-member-b", "am-service-cos")
    ):
        workspace = root / "workspaces" / agent
        workspace.mkdir(parents=True, mode=0o700)
        row = {
            "id": agent,
            "workspace": str(workspace),
            "tools": {"allow": ["authorized_read"]},
        }
        if index == 0:
            row["default"] = True
        rows.append(row)
    config = {
        "agents": {"defaults": {"workspace": str(root / "workspace")}, "list": rows},
        "channels": {
            "imessage": {
                "enabled": True,
                "accounts": {"cos": {"allowFrom": ["private-member"]}},
            }
        },
        "bindings": [
            {
                "agentId": rows[0]["id"],
                "match": {
                    "channel": "imessage",
                    "accountId": "cos",
                    "peer": {"id": "private-member"},
                },
            }
        ],
        "plugins": {
            "entries": {
                "administrateme": {
                    "enabled": True,
                    "config": {"exactGrant": "retained"},
                }
            }
        },
    }
    write_json(runtime / "openclaw.json", config)
    write_json(runtime / "auth.json", {"synthetic": "fixture-only-secret"})
    write_json(paths.instance_dir / "events" / "sentinel.json", {"append_only": [1, 2]})
    return paths, config


def test_only_exact_declared_schema_transition_is_allowed():
    new = load_versions()["openclaw"]
    old = new["predecessor"]
    assert declared_transition(old, new)
    for key in ("commit", "source_archive_sha256", "package_manager"):
        forged = copy.deepcopy(new)
        forged["predecessor"][key] = "forged"
        assert not declared_transition(old, forged)
    assert not declared_transition(new, old)


def test_complete_recovery_retains_failed_generation_and_central_history(native):
    paths, config = native
    root = paths.instance_dir / "openclaw"
    alias = root / "auth-alias"
    alias.symlink_to("runtime/auth.json")
    # Both immutable managed plugin locations found on production are legitimate.
    skill = (
        paths.app_dir
        / "openclaw"
        / PREDECESSOR[1]
        / "dist/extensions/canvas/skills/canvas"
    )
    skill.mkdir(parents=True, mode=0o700)
    release_skill = (
        paths.app_dir
        / "releases"
        / ("a" * 40)
        / "openclaw/administrateme/skills/administrateme-household"
    )
    release_skill.mkdir(parents=True, mode=0o700)
    links = root / "runtime/plugin-skills"
    links.mkdir(mode=0o700)
    (links / "canvas").symlink_to(skill)
    (links / "administrateme-household").symlink_to(release_skill)
    tx = "b" * 32
    record = capture_generation(paths, tx)
    write_json(root / "runtime/openclaw.json", {"new_generation": True})
    write_json(root / "runtime/new-history.json", {"failed_attempt": "retained"})
    central = paths.instance_dir / "events/sentinel.json"
    original_events = central.read_bytes()
    restore_generation(paths, record, tx)
    assert json.loads((root / "runtime/openclaw.json").read_text()) == config
    assert (
        alias.is_symlink()
        and alias.read_bytes() == (root / "runtime/auth.json").read_bytes()
    )
    assert (
        paths.rollback_dir / f"openclaw-{tx}/failed/runtime/new-history.json"
    ).is_file()
    assert central.read_bytes() == original_events
    # Recovery can finish controller reconciliation without restoring again.
    write_json(root / "runtime/reconciliation.json", {"completed": True})
    restore_generation(paths, record, tx)
    assert (root / "runtime/reconciliation.json").is_file()


def test_interrupted_rename_resumes_without_losing_either_generation(
    native, monkeypatch
):
    paths, _ = native
    tx = "c" * 32
    record = capture_generation(paths, tx)
    root = paths.instance_dir / "openclaw"
    write_json(root / "runtime/failed.json", {"native": "new"})
    original = os.rename

    def interrupted(source, target):
        if Path(target) == root:
            raise OSError("simulated interruption after failed generation retained")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "rename", interrupted)
        with pytest.raises(OSError, match="simulated interruption"):
            restore_generation(paths, record, tx)
    assert not root.exists()
    restore_generation(paths, record, tx)
    assert (root / "runtime/openclaw.json").is_file()
    assert (paths.rollback_dir / f"openclaw-{tx}/failed/runtime/failed.json").is_file()


@pytest.mark.parametrize(
    "mutation",
    [
        "external_workspace",
        "include",
        "oauth",
        "codex_home",
        "dotenv",
        "link",
        "writable",
        "hardlink",
    ],
)
def test_unsupported_paths_fail_before_native_cli_or_capture(
    native, tmp_path, mutation
):
    paths, config = native
    root = paths.instance_dir / "openclaw"
    if mutation == "external_workspace":
        config["agents"]["list"][0]["workspace"] = str(tmp_path / "outside")
    elif mutation == "include":
        config["$include"] = "outside.json"
    elif mutation == "oauth":
        config["env"] = {"OPENCLAW_OAUTH_DIR": str(tmp_path / "outside")}
    elif mutation == "codex_home":
        config["plugins"]["entries"]["codex"] = {
            "config": {"appServer": {"homeScope": "user"}}
        }
    elif mutation == "dotenv":
        (root / "runtime/.env").touch(mode=0o600)
    elif mutation == "link":
        (root / "outside").symlink_to(tmp_path)
    elif mutation == "writable":
        (root / "runtime/auth.json").chmod(0o666)
    elif mutation == "hardlink":
        os.link(root / "runtime/auth.json", root / "hardlink")
    write_json(root / "runtime/openclaw.json", config)
    with pytest.raises(NativeStateError):
        inspect_coverage(paths)
    with pytest.raises(NativeStateError):
        capture_generation(paths, "d" * 32)
    assert not (paths.rollback_dir / ("openclaw-" + "d" * 32) / "receipt.json").exists()


def test_restore_rejects_tampered_or_replayed_receipt(native):
    paths, _ = native
    tx = "e" * 32
    record = capture_generation(paths, tx)
    with pytest.raises(NativeStateError):
        restore_generation(paths, record, "f" * 32)
    before = paths.rollback_dir / f"openclaw-{tx}/before/runtime/auth.json"
    before.write_text("forged")
    with pytest.raises(NativeStateError, match="changed"):
        restore_generation(paths, record, tx)


def canonical_migration(config):
    after = copy.deepcopy(config)
    owner = after["agents"]["list"][0]["id"]
    after["agents"]["entries"] = {
        row["id"]: {
            key: value for key, value in row.items() if key not in {"id", "default"}
        }
        for row in after["agents"].pop("list")
    }
    after["agents"]["ownership"] = "explicit"
    for key in ("systemAgent", "sessionStore", "authInheritance", "heartbeat"):
        after["agents"]["defaults"][key] = {"agentId": owner}
    after["talk"] = {"agentId": owner}
    after["bindings"].append(
        {"agentId": owner, "match": {"channel": "imessage", "accountId": "*"}}
    )
    return after


def test_native_roster_and_existing_default_roles_preserve_authority(native, tmp_path):
    paths, before = native
    after = canonical_migration(before)
    target = tmp_path / "migrated.json"
    write_json(target, after)
    validate_migrated_authority(
        paths.instance_dir / "openclaw/runtime/openclaw.json", target
    )
    rows = runtime_agent_rows(after)
    replace_runtime_agent_rows(after, rows)
    assert "list" not in after["agents"]
    assert after["agents"]["defaults"]["systemAgent"] == {"agentId": rows[0]["id"]}


@pytest.mark.parametrize(
    "mutation", ["owner", "grant", "workspace", "binding", "plugin", "default"]
)
def test_doctor_success_cannot_hide_authority_changes(native, tmp_path, mutation):
    paths, before = native
    after = canonical_migration(before)
    first = next(iter(after["agents"]["entries"].values()))
    if mutation == "owner":
        after["agents"]["defaults"]["systemAgent"] = {"agentId": "am-service-cos"}
    elif mutation == "grant":
        first["tools"]["allow"].append("exec")
    elif mutation == "workspace":
        first["workspace"] = str(tmp_path / "different")
    elif mutation == "binding":
        after["bindings"][0]["agentId"] = "am-service-cos"
    elif mutation == "plugin":
        after["plugins"]["entries"]["administrateme"]["config"]["exactGrant"] = (
            "widened"
        )
    else:
        first["default"] = True
    target = tmp_path / "migrated.json"
    write_json(target, after)
    with pytest.raises(NativeStateError):
        validate_migrated_authority(
            paths.instance_dir / "openclaw/runtime/openclaw.json", target
        )


def test_snapshot_syncs_nested_directories_before_receipt(native, monkeypatch):
    import bootstrap.openclaw_state_transition as transition

    paths, _ = native
    observed = []
    original = transition._sync

    def sync(path):
        observed.append(Path(path))
        return original(path)

    monkeypatch.setattr(transition, "_sync", sync)
    capture_generation(paths, "a" * 32)
    before = paths.rollback_dir / ("openclaw-" + "a" * 32) / "before"
    assert observed.index(before / "runtime") < observed.index(before)
    assert observed.index(before) < observed.index(before.parent)


@pytest.mark.parametrize("copy_umask", [0o022, 0o077])
def test_snapshot_and_recovery_preserve_directory_modes_across_umasks(
    native, copy_umask
):
    paths, _ = native
    root = paths.instance_dir / "openclaw"
    directory = root / "runtime" / "readable"
    directory.mkdir(mode=0o700)
    directory.chmod(0o755)
    nested = directory / "private"
    nested.mkdir(mode=0o700)
    nested.chmod(0o700)
    write_json(nested / "sentinel.json", {"retained": True})
    link = root / "mode-link"
    link.symlink_to("runtime/readable/private/sentinel.json")
    if sys.platform == "darwin":
        os.chmod(link, 0o755, follow_symlinks=False)
    original_link_mode = stat.S_IMODE(link.lstat().st_mode)
    prior_umask = os.umask(copy_umask)
    try:
        tx = "1" * 32
        record = capture_generation(paths, tx)
        directory.chmod(0o700)
        restore_generation(paths, record, tx)
    finally:
        os.umask(prior_umask)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert stat.S_IMODE(nested.stat().st_mode) == 0o700
    assert stat.S_IMODE(link.lstat().st_mode) == original_link_mode
    assert json.loads(link.read_text()) == {"retained": True}


def test_recovery_refuses_unsettled_native_migration_worker(native, tmp_path):
    from bootstrap.upgrade import UpgradeError, UpgradeManager

    paths, _ = native
    manager = object.__new__(UpgradeManager)
    manager.paths = paths
    release = tmp_path / ("2" * 40)
    activity = release.parent / manager._preparation_activity_name(release.name)
    activity.write_bytes(b"")
    activity.chmod(0o600)
    descriptor = os.open(activity, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        with pytest.raises(UpgradeError, match="terminality is unproven"):
            manager._require_native_migration_settled(release)
    finally:
        os.close(descriptor)
    manager._require_native_migration_settled(release)


@pytest.mark.parametrize("phase", ["native_state_migrating", "native_state_migrated"])
def test_followup_controls_require_completed_native_migration(
    native, monkeypatch, phase
):
    from bootstrap.upgrade import UpgradeError, UpgradeManager

    paths, _ = native
    manager = object.__new__(UpgradeManager)
    manager.paths = paths
    transitions = []

    def transition(journal, next_phase):
        transitions.append(next_phase)
        return {**journal, "phase": next_phase}

    manager.activation_journal = SimpleNamespace(transition=transition)
    manager._test_crash_after_journal_phase = lambda _: None
    monkeypatch.setattr(
        "bootstrap.upgrade.apply_workspace_instructions", lambda *_: None
    )
    monkeypatch.setattr("bootstrap.cos_guard_release.COORDINATES", {})
    monkeypatch.setattr("bootstrap.cos_guard_release.snapshot_bytes", lambda _: b"{}")
    monkeypatch.setattr(
        "bootstrap.cos_imessage.verify_guard_files", lambda *_, **__: None
    )
    journal = {
        "phase": phase,
        "workspace_instructions": {},
        "cos_imessage_rebind": {"guard": {"before": {}, "after": {}}},
        "predecessor_path": "/synthetic/old",
        "target_path": "/synthetic/new",
    }
    for operation in (
        manager._select_workspace_instructions,
        manager._select_cos_guard_rebind,
    ):
        if phase == "native_state_migrating":
            with pytest.raises(UpgradeError, match="requires stopped"):
                operation(journal)
        else:
            operation(journal)
    assert bool(transitions) is (phase == "native_state_migrated")


def test_native_source_seal_inventory_is_exact_and_matches_preparation_fingerprint(
    tmp_path,
):
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    source = paths.app_dir / "openclaw" / SUCCESSOR[1]
    source.mkdir(parents=True, mode=0o700)
    (source / "openclaw.mjs").write_bytes(b"synthetic entry")
    manifest = load_versions()["openclaw"]
    inventory = native_source_inventory(paths, manifest)
    assert inventory["profile"] == "native-full-tree-metadata-v1"
    assert inventory["record_count"] == 2
    assert inventory["content_bytes"] == len(b"synthetic entry")
    assert native_source_fingerprint(paths, manifest) == inventory["artifact_sha256"]
    with pytest.raises(NativeStateError, match="outside the reviewed transition"):
        native_source_inventory(paths, {**manifest, "commit": PREDECESSOR[1]})
    source.rename(source.with_name("aliased"))
    source.symlink_to(source.with_name("aliased"), target_is_directory=True)
    with pytest.raises(NativeStateError):
        native_source_inventory(paths, manifest)
