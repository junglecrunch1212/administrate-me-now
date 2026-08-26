"""Tests for the ordinary content-free identity scan."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from scripts.identity_scan import load_private_patterns, scan_repository


def test_scan_reports_rules_without_echoing_sensitive_content(tmp_path: Path) -> None:
    private = "private" + "-canary"
    machine_path = "/Users/" + "example/Documents/AdministrateMe V2"
    phone = "212" + "-867-5309"
    email = "person" + "@private-domain.com"
    address = "742" + " Evergreen Road"
    (tmp_path / "candidate.txt").write_text(
        (
            f"{private}\n{machine_path}\n{phone}\n{email}\n{address}\n"
            "202-555-0100\nfixture@example.com\n123 Example Street\n"
        ),
        encoding="utf-8",
    )

    violations = scan_repository(
        tmp_path, private_patterns=(private,), scan_generic_all=True
    )

    assert {item.rule for item in violations} == {
        "owner_private_literal",
        "hardcoded_instance_path",
        "non_synthetic_phone",
        "non_synthetic_email",
        "non_synthetic_street_address",
    }
    assert all(private not in repr(item) for item in violations)
    assert all(machine_path not in repr(item) for item in violations)
    assert all(phone not in repr(item) for item in violations)
    assert all(email not in repr(item) for item in violations)
    assert all(address not in repr(item) for item in violations)


def test_optional_private_patterns_stay_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    inside = repository / "patterns.txt"
    inside.write_text("fixture-only\n", encoding="utf-8")
    inside.chmod(stat.S_IRUSR | stat.S_IWUSR)

    with pytest.raises(ValueError, match="outside Git"):
        load_private_patterns(inside, repository_root=repository)

    outside = tmp_path / "owner-patterns.txt"
    outside.write_text("fixture-only\n# comment\nfixture-only\n", encoding="utf-8")
    outside.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert load_private_patterns(outside, repository_root=repository) == (
        "fixture-only",
    )
