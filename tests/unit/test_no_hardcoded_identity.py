"""Tenant-neutrality canary without repository-embedded household values."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.identity_scan import load_private_patterns, scan_repository


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
    patterns_file.write_text(private_literal + "\n", encoding="utf-8")
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
