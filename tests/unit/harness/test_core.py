"""Unit coverage for deterministic harness primitives."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from scripts.harness.checks import (
    CheckSpec,
    _browser_summary,
    compare_browser_evidence,
    focused_checks,
    run_browser,
    run_tier,
)
from scripts.harness.baseline_navigation import BASELINE_TEST
from scripts.harness.core import (
    HarnessError,
    _validate_browser_evidence,
    applicable_instructions,
    binding,
    candidate_base_sha,
    candidate_branch,
    clean_environment,
    ensure_ephemeral_run_dir,
    executable_inventory,
    normalize_undetermined,
    repository_root,
    task_diff_bytes,
    validate_artifact,
    validate_artifact_value,
    validate_schema_file,
)


def _bind_browser_run(run_dir: Path, *directories: Path) -> None:
    current = binding(repository_root(), [])
    (run_dir / "outcome-contract.json").write_text(
        json.dumps({"binding": current}), encoding="utf-8"
    )
    for directory in directories:
        (directory / "harness-binding.json").write_text(
            json.dumps(current), encoding="utf-8"
        )


def _fixture_png(value: int = 0) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes((0, value, value, value))))
        + chunk(b"IEND", b"")
    )


def _complete_visual_report(primary_name: str) -> dict[str, object]:
    names = [primary_name]
    names.extend(
        f"fixture-{index:02d}-{'narrow' if index == 0 else 'desktop'}.png"
        for index in range(38)
    )
    return {
        "schema_version": 1,
        "generated_at": "fixture",
        "status": "PASS",
        "browser": "Microsoft Edge; engine 151.0.7922.110",
        "viewports": ["1440x1000", "1280x900", "1024x768", "820x1180", "390x844"],
        "states": [
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
        ],
        "results": [
            {"name": f"assertion-{index}", "status": "PASS", "detail": "same"}
            for index in range(260)
        ],
        "screenshots": {name: {"sha256": "0" * 64} for name in names},
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
    }


def _write_visual_report(
    directory: Path,
    report: dict[str, object],
    *,
    pixels: dict[str, bytes] | None = None,
) -> None:
    supplied = pixels or {}
    screenshots = report["screenshots"]
    assert isinstance(screenshots, dict)
    for name, metadata in screenshots.items():
        payload = supplied.get(name, _fixture_png())
        (directory / name).write_bytes(payload)
        assert isinstance(metadata, dict)
        metadata["sha256"] = hashlib.sha256(payload).hexdigest()
    (directory / "visual-results.json").write_text(json.dumps(report), "utf-8")


def test_all_harness_schemas_are_valid() -> None:
    root = repository_root()
    for path in sorted((root / "schemas" / "harness").glob("*.schema.json")):
        validate_schema_file(path)


def test_structured_output_properties_have_explicit_types() -> None:
    root = repository_root()
    for path in sorted((root / "schemas" / "harness").glob("*.schema.json")):
        schema = json.loads(path.read_text("utf-8"))
        for name, definition in schema["properties"].items():
            assert "type" in definition or "$ref" in definition, (path.name, name)

    review_schema = json.loads(
        (root / "schemas/harness/review-report.schema.json").read_text("utf-8")
    )
    assert "uniqueItems" not in json.dumps(review_schema)


def test_unknown_markers_are_normalized_recursively() -> None:
    assert normalize_undetermined(
        {"top": "UNKNOWN", "items": ["TRACE_BREAK", {"kept": "PASS"}]}
    ) == {"top": "UNDETERMINED", "items": ["UNDETERMINED", {"kept": "PASS"}]}


def test_ephemeral_runs_refuse_repository_paths() -> None:
    root = repository_root()
    with pytest.raises(HarnessError, match="system temp"):
        ensure_ephemeral_run_dir(root / "administrateme-harness-invalid", root)


def test_binding_base_is_the_candidate_merge_base_not_a_moving_remote_tip() -> None:
    root = repository_root()
    expected = subprocess.check_output(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=root,
        text=True,
    ).strip()

    assert binding(root, ["scripts/harness"])["base_sha"] == expected


def test_detached_checkout_binding_has_exact_nonempty_identity(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness fixture"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "fixture.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    head_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", head_sha],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "checkout", "--detach", "-q"], cwd=tmp_path, check=True)

    assert candidate_branch(tmp_path) == f"detached@{head_sha}"
    assert binding(tmp_path, ["fixture.txt"])["branch"] == f"detached@{head_sha}"


def test_committed_candidate_diff_binds_content_and_declared_path_scope(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness fixture"],
        cwd=tmp_path,
        check=True,
    )
    candidate = tmp_path / "candidate.txt"
    prerequisite = tmp_path / "prerequisite.txt"
    candidate.write_text("baseline\n", "utf-8")
    prerequisite.write_text("must remain bound\n", "utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    candidate.write_text("candidate\n", "utf-8")
    subprocess.run(["git", "add", "candidate.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=tmp_path, check=True)

    complete = task_diff_bytes(
        tmp_path,
        ["candidate.txt", "prerequisite.txt"],
        base_sha=base,
    )
    incomplete = task_diff_bytes(tmp_path, ["candidate.txt"], base_sha=base)

    assert b"-baseline" in complete
    assert b"+candidate" in complete
    assert complete != incomplete


def test_candidate_base_and_navigation_baseline_select_merge_base(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness fixture"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "fixture.txt").write_text("base\n", "utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", base],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "fixture.txt").write_text("candidate\n", "utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=tmp_path, check=True)

    assert candidate_base_sha(tmp_path) == base
    assert "test_ten_surfaces_three_modes" in BASELINE_TEST


def test_clean_environment_removes_live_authority_even_from_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe = {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_STATE_DIR",
        "ADMINME_OPENCLAW_GATEWAY_TOKEN",
        "ADMINME_OPENCLAW_SHARED_SECRET",
        "ADMINME_EVENT_LOG_KEY_HEX",
        "ADMINME_INSTANCE_ROOT",
        "ADMINME_INSTANCE_DIR",
    }
    for name in unsafe:
        monkeypatch.setenv(name, "ambient-live-value")

    environment = clean_environment(
        {
            **{name: "override-live-value" for name in unsafe},
            "ADMINME_GATE7_EVIDENCE_DIR": "/tmp/fixture-evidence",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    assert unsafe.isdisjoint(environment)
    assert environment["ADMINME_GATE7_EVIDENCE_DIR"] == "/tmp/fixture-evidence"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_task_diff_excludes_ignored_runtime_residue(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness fixture"],
        cwd=tmp_path,
        check=True,
    )
    scripts = tmp_path / "scripts/harness/__pycache__"
    scripts.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text("__pycache__/\n", "utf-8")
    (tmp_path / "tracked.txt").write_text("baseline\n", "utf-8")
    subprocess.run(
        ["git", "add", ".gitignore", "tracked.txt"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    (tmp_path / "scripts/harness/core.py").write_text("wanted\n", "utf-8")
    (scripts / "core.pyc").write_bytes(b"ignored residue")

    diff = task_diff_bytes(tmp_path, ["scripts/harness"])

    assert b"UNTRACKED:scripts/harness/core.py" in diff
    assert b"wanted" in diff
    assert b"core.pyc" not in diff
    assert b"ignored residue" not in diff


def test_task_diff_binds_untracked_mode_and_symlink_target(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness fixture"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "tracked.txt").write_text("baseline\n", "utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)

    candidate = tmp_path / "candidate.sh"
    candidate.write_text("exit 0\n", "utf-8")
    candidate.chmod(0o644)
    regular = task_diff_bytes(tmp_path, ["candidate.sh"])
    candidate.chmod(0o755)
    executable = task_diff_bytes(tmp_path, ["candidate.sh"])
    assert regular != executable

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("same\n", "utf-8")
    second.write_text("same\n", "utf-8")
    link = tmp_path / "candidate-link"
    link.symlink_to(first.name)
    first_target = task_diff_bytes(tmp_path, ["candidate-link"])
    link.unlink()
    link.symlink_to(second.name)
    second_target = task_diff_bytes(tmp_path, ["candidate-link"])
    assert first_target != second_target


def test_inventory_matches_all_constitutional_floors() -> None:
    inventory = executable_inventory()

    assert len(inventory["surfaces"]) == 10
    assert len(inventory["projections"]) == 15
    assert len(inventory["adapter_categories"]) == 5
    assert len(inventory["pack_kinds"]) == 6
    assert len(inventory["profiles"]) == 5
    assert len(inventory["personas"]) == 4


def test_instruction_discovery_returns_root_agents_for_harness_paths() -> None:
    assert applicable_instructions(repository_root(), ["scripts/harness/cli.py"]) == [
        "AGENTS.md"
    ]


def test_path_mapping_selects_navigation_and_harness_proof() -> None:
    checks = focused_checks(
        ["adminme/products/member/navigation.py", "scripts/harness/core.py"]
    )
    ids = {check.check_id for check in checks}

    assert ids == {
        "harness-focused",
        "member-navigation-characterization",
        "member-navigation-base-baseline",
        "member-navigation-acceptance",
    }


def test_path_mapping_accepts_task_owned_directory_roots() -> None:
    checks = focused_checks(["scripts/harness", ".github/workflows"])

    assert {check.check_id for check in checks} == {
        "harness-focused",
        "constitutional-consistency",
    }


def test_schema_invalid_artifact_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "outcome-contract.json"
    artifact.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    with pytest.raises(HarnessError, match="artifact outcome-contract.json is invalid"):
        validate_artifact(artifact)


def test_review_schema_requires_every_scope_exactly_once() -> None:
    binding_value = {
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "diff_sha256": "c" * 64,
        "branch": "codex/fixture",
        "evidence_class": "fixture",
    }
    review = {
        "schema_version": 1,
        "run_id": "fixture-review",
        "binding": binding_value,
        "reviewer": "fixture",
        "read_only": True,
        "scope": ["correctness"] * 8,
        "findings": [],
        "unresolved_evidence": [],
        "conclusion": "APPROVE",
    }

    with pytest.raises(HarnessError, match="artifact review-report.json is invalid"):
        validate_artifact_value("review-report.json", review)


def test_browser_equivalence_ignores_only_report_timestamp(tmp_path: Path) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-compare"
    before_dir = run_dir / "browser"
    after_dir = run_dir / "browser-after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    _bind_browser_run(run_dir, before_dir, after_dir)
    report = _complete_visual_report("surface-today-narrow.png")
    report["generated_at"] = "before"
    _write_visual_report(before_dir, json.loads(json.dumps(report)))
    report["generated_at"] = "after"
    _write_visual_report(after_dir, report)

    receipt = compare_browser_evidence(repository_root(), run_dir)

    assert receipt["status"] == "PASS"
    assert receipt["exit_code"] == 0


def test_required_browser_evidence_is_rehashed_and_decoded_on_validation(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-required"
    evidence_dir = run_dir / "browser"
    evidence_dir.mkdir(parents=True)
    _bind_browser_run(run_dir, evidence_dir)
    report = _complete_visual_report("surface-today-narrow.png")
    _write_visual_report(evidence_dir, report)
    report_path = evidence_dir / "visual-results.json"
    summary = _browser_summary(evidence_dir, report_path, report)
    current = binding(repository_root(), [])

    _validate_browser_evidence(run_dir, current, summary)
    (evidence_dir / "surface-today-narrow.png").write_bytes(_fixture_png(9))

    with pytest.raises(HarnessError, match="hash mismatch"):
        _validate_browser_evidence(run_dir, current, summary)


def test_browser_runner_rejects_unsupported_node_before_execution(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-node-version"
    run_dir.mkdir()
    _bind_browser_run(run_dir)
    fake_node = tmp_path / "node"
    fake_node.write_text("#!/bin/sh\necho v22.23.2\n", "utf-8")
    fake_node.chmod(0o755)

    with pytest.raises(HarnessError, match="requires Node >=24.15.0"):
        run_browser(repository_root(), run_dir, node_bin=str(fake_node))


def test_openclaw_breadth_uses_and_attests_supported_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository_root()
    run_dir = tmp_path / "administrateme-harness-openclaw-toolchain"
    run_dir.mkdir()
    _bind_browser_run(run_dir)
    runtime_bin = tmp_path / "runtime" / "bin"
    runtime_bin.mkdir(parents=True)
    fake_node = runtime_bin / "node"
    fake_node.write_text("#!/bin/sh\necho v24.19.0\n", "utf-8")
    fake_node.chmod(0o755)
    fake_npm = runtime_bin / "npm"
    fake_npm.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo 11.17.0; '
        "else node --version; fi\n",
        "utf-8",
    )
    fake_npm.chmod(0o755)
    spec = CheckSpec(
        "openclaw-fixture",
        "breadth",
        "npm --prefix openclaw/administrateme test",
    )
    monkeypatch.setattr(
        "scripts.harness.checks.tier_checks",
        lambda _tier, _paths: [spec],
    )

    receipts = run_tier(
        root,
        run_dir,
        "breadth",
        node_bin=str(fake_node),
    )

    assert receipts[0]["status"] == "PASS"
    receipt = json.loads((run_dir / receipts[0]["receipt"]).read_text(encoding="utf-8"))
    assert receipt["toolchain"]["node_version"] == "v24.19.0"
    assert receipt["toolchain"]["npm_version"] == "11.17.0"
    log = (run_dir / receipt["log"]["path"]).read_text(encoding="utf-8")
    assert '"node_version": "v24.19.0"' in log
    assert "v24.19.0" in log


def test_openclaw_breadth_rejects_unsupported_npm_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "administrateme-harness-openclaw-npm-version"
    run_dir.mkdir()
    _bind_browser_run(run_dir)
    runtime_bin = tmp_path / "runtime" / "bin"
    runtime_bin.mkdir(parents=True)
    fake_node = runtime_bin / "node"
    fake_node.write_text("#!/bin/sh\necho v24.19.0\n", "utf-8")
    fake_node.chmod(0o755)
    fake_npm = runtime_bin / "npm"
    fake_npm.write_text("#!/bin/sh\necho 10.9.8\n", "utf-8")
    fake_npm.chmod(0o755)
    monkeypatch.setattr(
        "scripts.harness.checks.tier_checks",
        lambda _tier, _paths: [
            CheckSpec(
                "openclaw-fixture",
                "breadth",
                "npm --prefix openclaw/administrateme test",
            )
        ],
    )

    with pytest.raises(HarnessError, match="requires npm >=11.6.0 <12"):
        run_tier(
            repository_root(),
            run_dir,
            "breadth",
            node_bin=str(fake_node),
        )


def test_browser_equivalence_fails_on_same_size_pixel_change(tmp_path: Path) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-pixel-change"
    before_dir = run_dir / "browser"
    after_dir = run_dir / "browser-after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    _bind_browser_run(run_dir, before_dir, after_dir)
    base_report = _complete_visual_report("surface.png")
    base_report["generated_at"] = "before"
    after_report = json.loads(json.dumps(base_report))
    after_report["generated_at"] = "after"
    # Identical declared metadata must not prevent decoding the changed files.
    after_report["screenshots"]["surface.png"]["sha256"] = "a" * 64
    _write_visual_report(
        before_dir,
        base_report,
        pixels={"surface.png": _fixture_png(0)},
    )
    _write_visual_report(
        after_dir,
        after_report,
        pixels={"surface.png": _fixture_png(255)},
    )

    receipt = compare_browser_evidence(repository_root(), run_dir)

    assert receipt["status"] == "FAIL"
    comparison = json.loads((run_dir / "browser-comparison.json").read_text("utf-8"))
    assert "screenshot_pixels:surface.png" in comparison["mismatches"]


def test_browser_equivalence_fails_on_one_low_delta_pixel(tmp_path: Path) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-low-delta"
    before_dir = run_dir / "browser"
    after_dir = run_dir / "browser-after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    _bind_browser_run(run_dir, before_dir, after_dir)
    report = _complete_visual_report("surface.png")
    changed = json.loads(json.dumps(report))
    changed["screenshots"]["surface.png"]["sha256"] = "a" * 64
    _write_visual_report(
        before_dir,
        report,
        pixels={"surface.png": _fixture_png(100)},
    )
    _write_visual_report(
        after_dir,
        changed,
        pixels={"surface.png": _fixture_png(101)},
    )

    receipt = compare_browser_evidence(repository_root(), run_dir)

    assert receipt["status"] == "FAIL"
    comparison = json.loads((run_dir / "browser-comparison.json").read_text("utf-8"))
    assert comparison["pixel_comparisons"]["surface.png"]["max_channel_delta"] == 1
    assert "screenshot_pixels:surface.png" in comparison["mismatches"]


def test_browser_equivalence_rejects_two_identically_failed_runs(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-failed-sources"
    before_dir = run_dir / "browser"
    after_dir = run_dir / "browser-after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    _bind_browser_run(run_dir, before_dir, after_dir)
    report = {
        "status": "FAIL",
        "viewports": [],
        "states": [],
        "results": [],
        "screenshots": {},
        "console_errors": ["fixture failure"],
        "page_errors": [],
        "request_failures": [],
    }
    for directory in (before_dir, after_dir):
        (directory / "visual-results.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    receipt = compare_browser_evidence(repository_root(), run_dir)

    assert receipt["status"] == "FAIL"
    comparison = json.loads((run_dir / "browser-comparison.json").read_text("utf-8"))
    assert "current_status_not_pass" in comparison["mismatches"]
    assert "after_status_not_pass" in comparison["mismatches"]


def test_browser_equivalence_rejects_two_empty_pass_reports(tmp_path: Path) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-empty-pass"
    before_dir = run_dir / "browser"
    after_dir = run_dir / "browser-after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    _bind_browser_run(run_dir, before_dir, after_dir)
    report = {
        "schema_version": 1,
        "status": "PASS",
        "browser": "Microsoft Edge; engine fixture",
        "viewports": [],
        "states": [],
        "results": [],
        "screenshots": {},
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
    }
    for directory in (before_dir, after_dir):
        (directory / "visual-results.json").write_text(json.dumps(report), "utf-8")

    receipt = compare_browser_evidence(repository_root(), run_dir)

    assert receipt["status"] == "FAIL"
    comparison = json.loads((run_dir / "browser-comparison.json").read_text("utf-8"))
    assert any("_incomplete:" in item for item in comparison["mismatches"])


def test_browser_equivalence_rejects_stale_binding(tmp_path: Path) -> None:
    run_dir = tmp_path / "administrateme-harness-browser-stale"
    before_dir = run_dir / "browser"
    after_dir = run_dir / "browser-after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    _bind_browser_run(run_dir, before_dir, after_dir)
    stale = json.loads((before_dir / "harness-binding.json").read_text("utf-8"))
    stale["diff_sha256"] = "f" * 64
    (before_dir / "harness-binding.json").write_text(
        json.dumps(stale), encoding="utf-8"
    )
    for directory in (before_dir, after_dir):
        (directory / "visual-results.json").write_text("{}", encoding="utf-8")

    with pytest.raises(HarnessError, match="stale"):
        compare_browser_evidence(repository_root(), run_dir)
