"""Tenant-neutrality canary without repository-embedded household values."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import identity_scan
from scripts.configure_private_identity_patterns import _write_profile
from scripts.identity_scan import (
    default_private_patterns_file,
    load_private_patterns,
    main,
    scan_repository,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_no_hardcoded_identity_in_platform_code() -> None:
    configured = os.environ.get("ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE")
    patterns = (
        load_private_patterns(Path(configured), repository_root=REPOSITORY_ROOT)
        if configured
        else ()
    )

    violations = scan_repository(REPOSITORY_ROOT, private_patterns=patterns)

    assert not violations, "tenant identity coordinates: " + ", ".join(
        f"{item.path}:{item.line}:{item.rule}" for item in violations
    )


def test_scanner_detects_private_values_without_echoing_them(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan-root"
    platform = scan_root / "adminme"
    platform.mkdir(parents=True)
    private_phone = "212" + "-867-5309"
    private_literal = "HOUSEHOLD" + "-SECRET-CANARY"
    (platform / "bad.py").write_text(
        f"{private_phone}\n{private_literal}\n",
        encoding="utf-8",
    )
    patterns_file = tmp_path / "private-patterns.txt"
    patterns_file.write_text("name:" + private_literal + "\n", encoding="utf-8")
    patterns_file.chmod(0o600)

    violations = scan_repository(
        scan_root,
        private_patterns=load_private_patterns(
            patterns_file,
            repository_root=scan_root,
        ),
    )

    assert [item.rule for item in violations] == [
        "non_synthetic_phone",
        "owner_private_literal",
    ]
    assert all(private_literal not in repr(item) for item in violations)


def test_scanner_covers_every_git_eligible_path_and_suffix(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan-root"
    scan_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=scan_root, check=True)
    private_literal = "PRIVATE" + "-TREE-CANARY"
    paths = [
        scan_root / "docs/tracked.md",
        scan_root / "openclaw/tracked.ts",
        scan_root / "scripts/tracked.py",
        scan_root / "adminme/excluded.txt",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(private_literal + "\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "docs/tracked.md", "openclaw/tracked.ts"],
        cwd=scan_root,
        check=True,
    )

    violations = scan_repository(scan_root, private_patterns=(private_literal,))

    assert [item.path for item in violations] == [
        "adminme/excluded.txt",
        "docs/tracked.md",
        "openclaw/tracked.ts",
        "scripts/tracked.py",
    ]
    assert {item.rule for item in violations} == {"owner_private_literal"}
    assert all(private_literal not in repr(item) for item in violations)


def test_scanner_excludes_git_ignored_files(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan-root"
    scan_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=scan_root, check=True)
    (scan_root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    private_literal = "PRIVATE" + "-IGNORED-CANARY"
    (scan_root / "ignored.txt").write_text(private_literal, encoding="utf-8")

    assert not scan_repository(scan_root, private_patterns=(private_literal,))


def test_reference_phone_exemption_never_exempts_owner_private_literals(
    tmp_path: Path,
) -> None:
    scan_root = tmp_path / "scan-root"
    reference = scan_root / "docs/reference/vendor-example.txt"
    reference.parent.mkdir(parents=True)
    private_literal = "PRIVATE" + "-REFERENCE-CANARY"
    reference_phone = "1-415" + "-867-5309"
    reference.write_text(reference_phone + "\n" + private_literal, encoding="utf-8")

    violations = scan_repository(scan_root, private_patterns=(private_literal,))

    assert [(item.path, item.rule) for item in violations] == [
        ("docs/reference/vendor-example.txt", "owner_private_literal")
    ]


def test_scanner_redacts_owner_private_file_names(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan-root"
    scan_root.mkdir()
    private_literal = "PRIVATE" + "-PATH-CANARY"
    (scan_root / f"{private_literal}.txt").write_text("safe fixture", encoding="utf-8")

    violations = scan_repository(scan_root, private_patterns=(private_literal,))

    assert [(item.path, item.line, item.rule) for item in violations] == [
        ("<owner-private-path>", 0, "owner_private_path")
    ]
    assert private_literal not in repr(violations)


def test_release_profile_requires_name_email_and_address_categories(
    tmp_path: Path,
) -> None:
    patterns_file = tmp_path / "private-patterns.txt"
    patterns_file.write_text("name:private fixture\n", encoding="utf-8")
    patterns_file.chmod(0o600)

    with pytest.raises(ValueError, match="address, email"):
        load_private_patterns(patterns_file, require_release_profile=True)


def test_release_profile_rejects_empty_owner_only_file(tmp_path: Path) -> None:
    patterns_file = tmp_path / "private-patterns.txt"
    patterns_file.write_text("# no configured private values\n", encoding="utf-8")
    patterns_file.chmod(0o600)

    with pytest.raises(ValueError, match="address, email, name"):
        load_private_patterns(patterns_file, require_release_profile=True)


def test_release_profile_accepts_categorized_owner_only_file(tmp_path: Path) -> None:
    patterns_file = tmp_path / "private-patterns.txt"
    patterns_file.write_text(
        "name:private fixture\n"
        "email:fixture@example.invalid\n"
        "address:100 Fixture Way\n",
        encoding="utf-8",
    )
    patterns_file.chmod(0o600)

    patterns = load_private_patterns(patterns_file, require_release_profile=True)

    assert patterns == (
        "private fixture",
        "fixture@example.invalid",
        "100 Fixture Way",
    )


def test_release_scan_fails_closed_without_private_profile(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "adminme").mkdir()

    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "--patterns-file",
                str(tmp_path / "missing-private-profile.txt"),
                "--require-private-patterns",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert '"status": "ERROR"' in output
    assert "release profile" in output


def test_default_release_profile_path_preserves_and_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_home = tmp_path / "owner-home"
    monkeypatch.setattr(identity_scan.sys, "platform", "darwin")
    monkeypatch.setattr(
        identity_scan.Path,
        "home",
        classmethod(lambda _cls: fake_home),
    )
    canonical = default_private_patterns_file()
    canonical.parent.mkdir(parents=True)
    target = tmp_path / "private-patterns-target.txt"
    target.write_text(
        "name:private fixture\n"
        "email:fixture@example.invalid\n"
        "address:100 Fixture Way\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    canonical.symlink_to(target)
    repository = tmp_path / "repository"
    (repository / "adminme").mkdir(parents=True)

    assert canonical.is_symlink()
    assert main(["--root", str(repository), "--require-private-patterns"]) == 2
    output = capsys.readouterr().out
    assert '"status": "ERROR"' in output
    assert "release profile" in output
    assert "private fixture" not in output


def test_release_profile_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    patterns_file = real_parent / "private-patterns.txt"
    patterns_file.write_text(
        "name:private fixture\n"
        "email:fixture@example.invalid\n"
        "address:100 Fixture Way\n",
        encoding="utf-8",
    )
    patterns_file.chmod(0o600)

    with pytest.raises(ValueError, match="real file"):
        load_private_patterns(linked_parent / patterns_file.name)


def test_private_pattern_file_requires_exact_0600_mode(tmp_path: Path) -> None:
    patterns_file = tmp_path / "private-patterns.txt"
    patterns_file.write_text("private literal\n", encoding="utf-8")
    patterns_file.chmod(0o640)

    with pytest.raises(ValueError, match="mode 0600"):
        load_private_patterns(patterns_file)


def test_configurator_writes_atomic_owner_only_release_profile(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    destination = tmp_path / "private-identity-patterns.txt"
    values = {
        "name": ["private fixture"],
        "email": ["fixture@example.invalid"],
        "address": ["100 Fixture Way"],
    }

    _write_profile(
        destination,
        values,
        replace=False,
        repository_root=repository,
    )

    assert destination.stat().st_mode & 0o777 == 0o600
    assert load_private_patterns(destination, require_release_profile=True) == (
        "100 Fixture Way",
        "fixture@example.invalid",
        "private fixture",
    )
    with pytest.raises(ValueError, match="already exists"):
        _write_profile(
            destination,
            values,
            replace=False,
            repository_root=repository,
        )


def test_configurator_refuses_repository_or_arbitrary_filename(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    values = {
        "name": ["private fixture"],
        "email": ["fixture@example.invalid"],
        "address": ["100 Fixture Way"],
    }

    with pytest.raises(ValueError, match="outside Git"):
        _write_profile(
            repository / "private-identity-patterns.txt",
            values,
            replace=False,
            repository_root=repository,
        )
    with pytest.raises(ValueError, match="filename"):
        _write_profile(
            tmp_path / "unrelated.txt",
            values,
            replace=True,
            repository_root=repository,
        )


def test_private_pattern_file_must_stay_outside_scanned_repository(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    patterns_file = repository / "private-patterns.txt"
    patterns_file.write_text("private literal\n", encoding="utf-8")
    patterns_file.chmod(0o600)

    with pytest.raises(ValueError, match="outside Git"):
        load_private_patterns(patterns_file, repository_root=repository)
