"""Content-free repository identity scan with fail-closed release canaries."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


PLATFORM_ROOTS = ("adminme", "bootstrap", "bridge", "packs", "tests")
SCANNED_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
}
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-](\d{3})[ .-](\d{4})(?!\d)"
)
HARDCODED_INSTANCE_RE = re.compile(
    r"(?:/Users/[^/]+/AdministrateMe|[A-Za-z]:\\Users\\[^\\]+\\AdministrateMe)",
    re.IGNORECASE,
)
REQUIRED_RELEASE_CATEGORIES = frozenset({"name", "email", "address"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str


def default_private_patterns_file() -> Path:
    """Return the owner-local canary path without creating or reading it."""

    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "AdministrateMe"
            / "private-identity-patterns.txt"
        ).resolve()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return (base / "administrateme" / "private-identity-patterns.txt").resolve()


def _categorized_patterns(lines: list[str]) -> tuple[tuple[str, ...], frozenset[str]]:
    patterns: list[str] = []
    categories: set[str] = set()
    seen: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        category = "literal"
        value = line
        prefix, separator, remainder = line.partition(":")
        normalized_prefix = prefix.strip().casefold()
        if separator and normalized_prefix in REQUIRED_RELEASE_CATEGORIES:
            category = normalized_prefix
            value = remainder.strip()
        if len(value) < 3:
            raise ValueError(
                "private identity patterns must contain at least 3 characters"
            )
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        patterns.append(value)
        categories.add(category)
    return tuple(patterns), frozenset(categories)


def load_private_patterns(
    path: Path | None,
    *,
    repository_root: Path | None = None,
    require_release_profile: bool = False,
) -> tuple[str, ...]:
    """Load owner-only canaries without exposing their values."""

    if path is None:
        if require_release_profile:
            raise ValueError("private identity release profile is not configured")
        return ()
    expanded = path.expanduser()
    resolved = expanded.resolve()
    if expanded.is_symlink() or not resolved.is_file():
        if require_release_profile:
            raise ValueError("private identity release profile is not configured")
        raise ValueError("private identity pattern path must be a real file")
    if repository_root is not None:
        try:
            resolved.relative_to(repository_root.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("private identity pattern file must stay outside Git")
    file_stat = resolved.stat()
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise ValueError("private identity pattern file must be owner-only (mode 0600)")
    if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
        raise ValueError("private identity pattern file must be owned by this user")
    patterns, categories = _categorized_patterns(
        resolved.read_text(encoding="utf-8").splitlines()
    )
    if require_release_profile:
        missing = sorted(REQUIRED_RELEASE_CATEGORIES - categories)
        if missing:
            raise ValueError(
                "private identity release profile is missing required categories: "
                + ", ".join(missing)
            )
    return patterns


def _line_rules(line: str, private_patterns: tuple[str, ...]) -> set[str]:
    rules: set[str] = set()
    lowered = line.casefold()
    if any(pattern.casefold() in lowered for pattern in private_patterns):
        rules.add("owner_private_literal")
    if HARDCODED_INSTANCE_RE.search(line):
        rules.add("hardcoded_instance_path")
    for match in PHONE_RE.finditer(line):
        digits = "".join(
            character for character in match.group(0) if character.isdigit()
        )
        national = (
            digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
        )
        if not national.startswith("555"):
            rules.add("non_synthetic_phone")
    return rules


def scan_repository(
    root: Path,
    *,
    private_patterns: tuple[str, ...] = (),
) -> list[Violation]:
    """Return only content-free coordinates; never echo a matched value."""

    violations: list[Violation] = []
    for root_name in PLATFORM_ROOTS:
        source_root = root / root_name
        if not source_root.exists():
            continue
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            if path.suffix.lower() not in SCANNED_SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                for rule in sorted(_line_rules(line, private_patterns)):
                    violations.append(Violation(relative, line_number, rule))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--patterns-file", type=Path)
    parser.add_argument("--require-private-patterns", action="store_true")
    args = parser.parse_args(argv)
    configured = args.patterns_file
    if configured is None and os.environ.get("ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE"):
        configured = Path(os.environ["ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE"])
    if configured is None and args.require_private_patterns:
        configured = default_private_patterns_file()
    try:
        patterns = load_private_patterns(
            configured,
            repository_root=args.root,
            require_release_profile=args.require_private_patterns,
        )
    except ValueError as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, sort_keys=True))
        return 2
    violations = scan_repository(args.root.resolve(), private_patterns=patterns)
    print(
        json.dumps(
            {
                "status": "PASS" if not violations else "FAIL",
                "private_release_profile": (
                    "CONFIGURED" if args.require_private_patterns else "NOT_REQUIRED"
                ),
                "private_pattern_count": len(patterns),
                "violations": [asdict(violation) for violation in violations],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
