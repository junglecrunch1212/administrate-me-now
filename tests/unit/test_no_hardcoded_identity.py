"""Canary: repository files must not contain identity or instance leaks."""

from pathlib import Path

from scripts.identity_scan import scan_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_no_hardcoded_identity_in_repository() -> None:
    violations = scan_repository(REPOSITORY_ROOT)
    assert not violations, "identity scan failed: " + ", ".join(
        f"{item.path}:{item.line}:{item.rule}" for item in violations
    )
