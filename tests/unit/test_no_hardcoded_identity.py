"""Canary: tenant identity must never be embedded in platform code."""

from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOTS = (
    "adminme",
    "bootstrap",
    "bridge",
    "packs",
    "tests",
)
SCANNED_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".sql", ".ts"})


def _secret_term(*codepoints: int) -> str:
    """Keep the executable canary from embedding the protected text itself."""

    return "".join(chr(value) for value in codepoints)


_PROTECTED_TERMS = (
    _secret_term(106, 97, 109, 101, 115),
    _secret_term(106, 105, 109, 109, 121),
    _secret_term(108, 97, 117, 114, 97),
    _secret_term(99, 104, 97, 114, 108, 105, 101),
    _secret_term(101, 115, 116, 104, 101, 114),
    _secret_term(115, 116, 105, 99, 101),
    _secret_term(109, 111, 114, 110, 105, 110, 103, 115, 105, 100, 101),
    _secret_term(104, 111, 108, 108, 97, 110, 100),
    _secret_term(106, 114, 115, 116, 105, 99, 101),
    _secret_term(108, 99, 104, 111, 108, 108, 97, 110, 100, 49, 50),
)
FORBIDDEN_IDENTITY = re.compile(
    r"\b(?:" + "|".join(re.escape(value) for value in _PROTECTED_TERMS) + r")\b",
    re.IGNORECASE,
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
            if relative == "tests/unit/test_no_hardcoded_identity.py":
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                if FORBIDDEN_IDENTITY.search(line) or HARDCODED_INSTANCE.search(line):
                    violations.append(f"{relative}:{line_number}: {line.strip()}")

    assert not violations, "tenant identity embedded in platform code:\n" + "\n".join(
        violations
    )
