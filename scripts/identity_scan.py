"""Content-free repository identity scan with optional owner-private canaries."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
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
@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str


def load_private_patterns(
    path: Path | None,
    *,
    repository_root: Path | None = None,
) -> tuple[str, ...]:
    """Load literal canaries from an owner-only file outside the repository."""

    if path is None:
        return ()
    resolved = path.expanduser().resolve()
    if path.is_symlink() or not resolved.is_file():
        raise ValueError("private identity pattern path must be a real file")
    if repository_root is not None:
        try:
            resolved.relative_to(repository_root.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("private identity pattern file must stay outside Git")
    if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise ValueError("private identity pattern file must be owner-only (mode 0600)")
    patterns = tuple(
        line.strip()
        for line in resolved.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if any(len(pattern) < 3 for pattern in patterns):
        raise ValueError("private identity patterns must contain at least 3 characters")
    return patterns


def _line_rules(line: str, private_patterns: tuple[str, ...]) -> set[str]:
    rules: set[str] = set()
    lowered = line.casefold()
    if any(pattern.casefold() in lowered for pattern in private_patterns):
        rules.add("owner_private_literal")
    if HARDCODED_INSTANCE_RE.search(line):
        rules.add("hardcoded_instance_path")
    for match in PHONE_RE.finditer(line):
        digits = "".join(character for character in match.group(0) if character.isdigit())
        national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
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
    args = parser.parse_args(argv)
    configured = args.patterns_file
    if configured is None and os.environ.get("ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE"):
        configured = Path(os.environ["ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE"])
    try:
        patterns = load_private_patterns(configured, repository_root=args.root)
    except ValueError as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, sort_keys=True))
        return 2
    violations = scan_repository(args.root.resolve(), private_patterns=patterns)
    print(
        json.dumps(
            {
                "status": "PASS" if not violations else "FAIL",
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
