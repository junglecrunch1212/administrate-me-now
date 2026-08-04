"""Canary: tenant identity must never be embedded in platform code."""

from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOTS = (
    "adminme",
    "bootstrap",
    "profiles",
    "personas",
    "integrations",
)
SCANNED_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".sql", ".ts"})

# These values identify the original household from which the constitutional
# prohibition was derived. Keeping the terms in this canary (outside platform
# roots) makes the rule executable without putting them into shipped code.
FORBIDDEN_IDENTITY = re.compile(
    r"\b(?:james|laura|charlie|stice|morningside)\b", re.IGNORECASE
)
HARDCODED_INSTANCE = re.compile(
    r"(?:/Users/[^/]+/AdministrateMe|[A-Za-z]:\\Users\\[^\\]+\\AdministrateMe)",
    re.IGNORECASE,
)


def test_no_hardcoded_identity_in_platform_code() -> None:
    """Report every platform source line that embeds tenant-specific data."""
    violations: list[str] = []
    for root_name in PLATFORM_ROOTS:
        root = REPOSITORY_ROOT / root_name
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.suffix.lower() not in SCANNED_SUFFIXES:
                continue
            relative = path.relative_to(REPOSITORY_ROOT).as_posix()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                if "fixture:tenant_data:ok" in line:
                    continue
                if FORBIDDEN_IDENTITY.search(line) or HARDCODED_INSTANCE.search(line):
                    violations.append(f"{relative}:{line_number}: {line.strip()}")

    assert not violations, "tenant identity embedded in platform code:\n" + "\n".join(
        violations
    )
