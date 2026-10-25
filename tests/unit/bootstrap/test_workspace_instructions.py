from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from adminme.openclaw_roster import canonical_agent_rows
from adminme.openclaw_topology import member_agent_id
from bootstrap.contracts import DeploymentPaths
from bootstrap.workspace_instructions import (
    WorkspaceInstructionsError,
    apply_transition,
    compile_instructions,
    instruction_layout,
    prepare_transition,
    validate_transition,
)


REPO = Path(__file__).resolve().parents[3]
MEMBER = "member-fixture"
ADAPTERS = {
    "openclaw_actor_identities": {"imessage|fixture-account|fixture-actor": MEMBER}
}


def fixture_workspace(tmp_path: Path):
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    source, target = tmp_path / "source", tmp_path / "target"
    for root, content in ((source, "Prior policy.\n"), (target, "Updated policy.\n")):
        programs = root / "bootstrap/openclaw/programs"
        programs.mkdir(parents=True)
        (programs / "fixture.md").write_text(content)
    generated = compile_instructions(source)
    coordinates = ["workspace", f"member-workspaces/{member_agent_id(MEMBER)}"]
    files = []
    for coordinate in coordinates:
        directory = paths.instance_dir / "openclaw" / coordinate
        directory.mkdir(parents=True, mode=0o700)
        file = directory / "AGENTS.md"
        file.write_bytes(b"Private prefix.\n" + generated + b"\nPrivate suffix.\n")
        file.chmod(0o600)
        (directory / "SOUL.md").write_bytes(b"Persona overlay.\n")
        (directory / "USER.md").write_bytes(b"Member overlay.\n")
        files.append(file)
    return paths, source, target, files


def prepare(paths, source, target):
    return prepare_transition(
        paths, source_release=source, target_release=target, adapter_config=ADAPTERS
    )


def test_compiler_matches_the_packaged_node_compiler(tmp_path: Path) -> None:
    environment = {
        **os.environ,
        "ADMINME_WORKSPACE_OUTPUT": str(tmp_path),
        "ADMINME_PERSONA_ID": "quiet_assistant",
    }
    subprocess.run(
        ["node", str(REPO / "openclaw/administrateme/scripts/compile-workspace.mjs")],
        env=environment,
        check=True,
    )
    compiled = compile_instructions(REPO)
    assert (tmp_path / "AGENTS.md").read_bytes() == compiled
    assert len(compiled) < 14000  # Reserve at least 6 KB for retained private layers.
    assert compiled.index(b"Current authority") < compiled.index(
        b"Complete requested workflows"
    )
    layout = instruction_layout(REPO)
    assert layout is not None
    for name in layout["background"]:
        original = (REPO / "bootstrap/openclaw/programs" / name).read_bytes()
        assert (tmp_path / "skill-references" / name).read_bytes() == original
        assert (
            REPO
            / "openclaw/administrateme/skills/administrateme-household/programs"
            / name
        ).read_bytes() == original
        assert original not in compiled
        assert f"`programs/{name}`".encode() in compiled


def test_updates_all_instructions_and_preserves_persona_and_member_overlays(
    tmp_path: Path,
) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    original = [file.read_bytes() for file in files]
    plan = prepare(paths, source, target)
    assert [file.read_bytes() for file in files] == original
    apply_transition(paths, plan)
    apply_transition(paths, plan)  # A completed file can be retried after interruption.
    for file in files:
        assert (
            file.read_bytes()
            == b"Private prefix.\n"
            + compile_instructions(target)
            + b"\nPrivate suffix.\n"
        )
        assert file.stat().st_mode & 0o777 == 0o600
        assert (file.parent / "SOUL.md").read_bytes() == b"Persona overlay.\n"
        assert (file.parent / "USER.md").read_bytes() == b"Member overlay.\n"
    apply_transition(paths, plan, recover=True)
    assert [file.read_bytes() for file in files] == original
    apply_transition(paths, plan, recover=True)
    assert [file.read_bytes() for file in files] == original


def test_reverse_release_preparation_preserves_new_private_overlay(
    tmp_path: Path,
) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    apply_transition(paths, prepare(paths, source, target))
    files[1].write_bytes(files[1].read_bytes() + b"Later member preference.\n")
    apply_transition(paths, prepare(paths, target, source))
    assert compile_instructions(source) in files[1].read_bytes()
    assert files[1].read_bytes().endswith(b"Later member preference.\n")


def test_unchanged_sources_need_no_workspace_write(tmp_path: Path) -> None:
    paths, source, _, files = fixture_workspace(tmp_path)
    files[1].write_bytes(b"Unrelated custom policy.\n")
    assert prepare(paths, source, source) is None


@pytest.mark.parametrize(
    "damage",
    [
        "custom_generated",
        "symlink_file",
        "symlink_directory",
        "hardlink",
        "writable",
        "missing",
        "fifo",
    ],
)
def test_preflight_rejects_unsafe_or_custom_generated_content_without_any_write(
    tmp_path: Path, damage: str
) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    original_template = files[0].read_bytes()
    outside = tmp_path / "outside"
    outside.write_bytes(b"Outside remains untouched.\n")
    if damage == "custom_generated":
        files[1].write_bytes(
            files[1].read_bytes().replace(b"Prior policy.", b"Private edited policy.")
        )
    elif damage == "symlink_file":
        files[1].unlink()
        files[1].symlink_to(outside)
    elif damage == "symlink_directory":
        directory = files[1].parent
        directory.rename(directory.with_name("retained"))
        directory.symlink_to(tmp_path, target_is_directory=True)
    elif damage == "hardlink":
        files[1].unlink()
        os.link(outside, files[1])
    elif damage == "writable":
        files[1].chmod(0o666)
    elif damage == "missing":
        files[1].unlink()
    elif damage == "fifo":
        files[1].unlink()
        os.mkfifo(files[1], 0o600)
    with pytest.raises(WorkspaceInstructionsError):
        prepare(paths, source, target)
    assert files[0].read_bytes() == original_template
    assert outside.read_bytes() == b"Outside remains untouched.\n"


def test_changed_last_file_is_detected_before_any_write(tmp_path: Path) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    plan = prepare(paths, source, target)
    original_template = files[0].read_bytes()
    files[1].write_bytes(b"Concurrent edit.\n")
    with pytest.raises(WorkspaceInstructionsError, match="after preparation"):
        apply_transition(paths, plan)
    assert files[0].read_bytes() == original_template
    assert files[1].read_bytes() == b"Concurrent edit.\n"


def test_partial_write_failure_is_recoverable_from_serialized_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap import workspace_instructions as module

    paths, source, target, files = fixture_workspace(tmp_path)
    plan = json.loads(json.dumps(prepare(paths, source, target)))
    original = [file.read_bytes() for file in files]
    write = module._write
    calls = 0

    def interrupted(descriptor, payload, mode):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected interruption")
        write(descriptor, payload, mode)

    monkeypatch.setattr(module, "_write", interrupted)
    with pytest.raises(WorkspaceInstructionsError):
        apply_transition(paths, plan)
    assert files[0].read_bytes() != original[0]
    assert files[1].read_bytes() == original[1]
    monkeypatch.setattr(module, "_write", write)
    apply_transition(paths, plan, recover=True)
    assert [file.read_bytes() for file in files] == original


@pytest.mark.parametrize(
    "damage", ["coordinate", "digest", "duplicate", "mode", "template"]
)
def test_journal_transition_rejects_corruption(tmp_path: Path, damage: str) -> None:
    paths, source, target, _ = fixture_workspace(tmp_path)
    plan = copy.deepcopy(prepare(paths, source, target))
    assert plan is not None
    if damage == "coordinate":
        plan["files"][0]["coordinate"] = "../outside/AGENTS.md"
    elif damage == "digest":
        plan["files"][0]["before"]["sha256"] = "0" * 64
    elif damage == "duplicate":
        plan["files"].append(plan["files"][0])
    elif damage == "mode":
        plan["files"][0]["mode"] = 0o666
    elif damage == "template":
        plan["files"] = plan["files"][1:]
    with pytest.raises(WorkspaceInstructionsError):
        validate_transition(plan)


def test_distinct_member_overlays_are_never_copied_between_workspaces(
    tmp_path: Path,
) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    other = (
        paths.instance_dir
        / "openclaw/member-workspaces"
        / member_agent_id("other-fixture-member")
    )
    other.mkdir(mode=0o700)
    other_file = other / "AGENTS.md"
    other_file.write_bytes(
        b"Other member private prefix.\n" + compile_instructions(source)
    )
    other_file.chmod(0o600)
    adapters = {
        "openclaw_actor_identities": {"imessage|fixture-account|fixture-actor": MEMBER},
        "member_chat_enabled_members": ["other-fixture-member"],
    }
    plan = prepare_transition(
        paths, source_release=source, target_release=target, adapter_config=adapters
    )
    apply_transition(paths, plan)
    assert (
        other_file.read_bytes()
        == b"Other member private prefix.\n" + compile_instructions(target)
    )
    assert all(b"Other member" not in file.read_bytes() for file in files)
    assert b"Private suffix" not in other_file.read_bytes()


def test_recovery_preserves_unknown_later_edits_and_refuses_to_claim_success(
    tmp_path: Path,
) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    plan = prepare(paths, source, target)
    apply_transition(paths, plan)
    selected_template = files[0].read_bytes()
    files[1].write_bytes(b"Unexpected later content.\n")
    with pytest.raises(WorkspaceInstructionsError, match="after preparation"):
        apply_transition(paths, plan, recover=True)
    assert files[0].read_bytes() == selected_template
    assert files[1].read_bytes() == b"Unexpected later content.\n"


def _migrated_member_roster(paths: DeploymentPaths) -> None:
    # This target-writer fixture starts after native Doctor materialized ownership;
    # the retained-state acceptance separately exercises the real native migration.
    root = paths.instance_dir / "openclaw"
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    (runtime / "openclaw.json").write_text(
        json.dumps(
            {
                "agents": {
                    "ownership": "explicit",
                    "entries": {
                        member_agent_id(MEMBER): {
                            "workspace": str(
                                root / "member-workspaces" / member_agent_id(MEMBER)
                            )
                        }
                    },
                }
            }
        )
    )


def test_updater_covers_workspaces_selected_by_production_member_topology(
    tmp_path: Path,
) -> None:
    from bootstrap.openclaw_activation import reconcile_member_topology

    paths, source, target, _ = fixture_workspace(tmp_path)
    adapters = {**ADAPTERS, "member_chat_enabled_members": ["portal-fixture-member"]}
    configuration = paths.instance_dir / "config"
    configuration.mkdir(mode=0o700)
    (configuration / "adapters.json").write_text(json.dumps(adapters))
    _migrated_member_roster(paths)
    topology = reconcile_member_topology(paths)
    assert topology["member_agent_count"] == 2
    gateway = json.loads(
        (paths.instance_dir / "openclaw/runtime/openclaw.json").read_text()
    )
    actual_workspaces = [
        Path(row["workspace"]) for row in canonical_agent_rows(gateway)
    ]
    plan = prepare_transition(
        paths, source_release=source, target_release=target, adapter_config=adapters
    )
    apply_transition(paths, plan)
    for workspace in actual_workspaces:
        assert compile_instructions(target) in (workspace / "AGENTS.md").read_bytes()
        assert (
            compile_instructions(source) not in (workspace / "AGENTS.md").read_bytes()
        )


def test_full_legacy_member_files_keep_workflow_guidance_inside_native_budget(
    tmp_path: Path,
) -> None:
    from bootstrap.openclaw_activation import reconcile_member_topology

    paths, source, _, files = fixture_workspace(tmp_path)
    programs = source / "bootstrap/openclaw/programs"
    (programs / "fixture.md").unlink()
    for program in (REPO / "bootstrap/openclaw/programs").glob("*.md"):
        (programs / program.name).write_bytes(program.read_bytes())
    # A predecessor without the layout marker injects every complete program.
    legacy = compile_instructions(source)
    assert len(legacy) > 20000
    files[0].write_bytes(b"Private template policy.\n" + legacy)
    adapters = {**ADAPTERS, "member_chat_enabled_members": ["portal-fixture-member"]}
    configuration = paths.instance_dir / "config"
    configuration.mkdir(mode=0o700)
    (configuration / "adapters.json").write_text(json.dumps(adapters))
    _migrated_member_roster(paths)
    assert reconcile_member_topology(paths)["member_agent_count"] == 2
    gateway = json.loads(
        (paths.instance_dir / "openclaw/runtime/openclaw.json").read_text()
    )
    # Exercise the paths the native agent actually loads, with distinct private
    # layers and the entire six-kilobyte reserved overlay budget in each file.
    overlays = {}
    for row in canonical_agent_rows(gateway):
        file = Path(row["workspace"]) / "AGENTS.md"
        marker = f"Private policy for {row['id']}.\n".encode()
        prefix = marker + b"p" * (3000 - len(marker))
        suffix = b"\nPrivate retained suffix.\n".ljust(3000, b"s")
        file.write_bytes(prefix + legacy + suffix)
        overlays[file] = (prefix, suffix)
        assert file.read_bytes().index(b"minimal concise title") > 20000
    all_files = [files[0], *overlays]
    originals = {file: file.read_bytes() for file in all_files}
    personas = {
        file.parent / name: (file.parent / name).read_bytes()
        for file in all_files
        for name in ("SOUL.md", "USER.md")
    }
    transition = prepare_transition(
        paths, source_release=source, target_release=REPO, adapter_config=adapters
    )
    assert {file: file.read_bytes() for file in all_files} == originals
    apply_transition(paths, transition)
    compact = compile_instructions(REPO)
    for file, (prefix, suffix) in overlays.items():
        rendered = file.read_bytes()
        assert rendered == prefix + compact + suffix
        assert len(rendered) <= 20000
        assert legacy not in rendered
        for guidance in (
            b"Current authority and workstation identity",
            b"minimal concise title",
            b"administrateme_confirm_cos_apple_reminder",
            b"administrateme_confirm_requested_email",
            b"preserve the no-automatic-send restriction",
            b"For a current request to read an authorized CoS browser resource",
        ):
            assert guidance in rendered[:20000]
    assert all(file.read_bytes() == content for file, content in personas.items())
    apply_transition(paths, transition, recover=True)
    assert {file: file.read_bytes() for file in all_files} == originals


def modern_target(target: Path) -> None:
    (target / "bootstrap/openclaw/workspace-layout.json").write_text(
        json.dumps(
            {
                "version": 1,
                "max_composed_bytes": 20000,
                "priority": ["fixture.md"],
                "background": [],
            }
        )
    )


def test_budget_measures_each_full_composed_member_file_without_discarding_layers(
    tmp_path: Path,
) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    modern_target(target)
    files[1].write_bytes(files[1].read_bytes() + b"Private retained layer. " * 1000)
    originals = [file.read_bytes() for file in files]
    with pytest.raises(
        WorkspaceInstructionsError, match="composed workspace instructions"
    ):
        prepare(paths, source, target)
    assert [file.read_bytes() for file in files] == originals


def test_modern_compilation_recovers_exact_legacy_bytes_and_private_layers(
    tmp_path: Path,
) -> None:
    paths, source, target, files = fixture_workspace(tmp_path)
    modern_target(target)
    originals = [file.read_bytes() for file in files]
    transition = prepare(paths, source, target)
    apply_transition(paths, transition)
    assert all(len(file.read_bytes()) <= 20000 for file in files)
    apply_transition(paths, transition, recover=True)
    assert [file.read_bytes() for file in files] == originals
    assert instruction_layout(source) is None


def test_layout_cannot_silently_omit_a_program(tmp_path: Path) -> None:
    _, _, target, _ = fixture_workspace(tmp_path)
    modern_target(target)
    (target / "bootstrap/openclaw/programs/new_program.md").write_text(
        "Retain this program.\n"
    )
    with pytest.raises(WorkspaceInstructionsError, match="omits or repeats"):
        compile_instructions(target)


def test_native_continuation_composes_exact_partial_selection_and_keeps_private_overlays(
    tmp_path: Path,
) -> None:
    paths, source, failed, files = fixture_workspace(tmp_path)
    corrected = tmp_path / "corrected"
    programs = corrected / "bootstrap/openclaw/programs"
    programs.mkdir(parents=True)
    (programs / "fixture.md").write_text("Corrected instructions.\n")
    # A process interruption left one file on each exact known source version.
    files[0].write_bytes(
        files[0]
        .read_bytes()
        .replace(compile_instructions(source), compile_instructions(failed))
    )
    with pytest.raises(WorkspaceInstructionsError, match="differ"):
        prepare_transition(
            paths,
            source_release=source,
            target_release=corrected,
            adapter_config=ADAPTERS,
        )
    transition = prepare_transition(
        paths,
        source_release=source,
        contained_source_release=failed,
        target_release=corrected,
        adapter_config=ADAPTERS,
    )
    apply_transition(paths, transition)
    for file in files:
        assert (
            file.read_bytes()
            == b"Private prefix.\n"
            + compile_instructions(corrected)
            + b"\nPrivate suffix.\n"
        )
        assert (file.parent / "SOUL.md").read_bytes() == b"Persona overlay.\n"
    files[-1].write_bytes(b"Unknown instructions.\n")
    with pytest.raises(WorkspaceInstructionsError, match="differ"):
        prepare_transition(
            paths,
            source_release=source,
            contained_source_release=failed,
            target_release=corrected,
            adapter_config=ADAPTERS,
        )
