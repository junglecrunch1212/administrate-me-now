"""Content-free scan for identity leaks and machine-specific instance paths."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-](\d{3})[ .-](\d{4})(?!\d)"
)
EMAIL_RE = re.compile(
    r"(?<![\w.+-])([A-Z0-9._%+-]+)@([A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Z0-9.\'-]*(?:\s+[A-Z][A-Z0-9.\'-]*){0,4}\s+"
    r"(?:STREET|ST\.?|ROAD|RD\.?|AVENUE|AVE\.?|BOULEVARD|BLVD\.?|"
    r"LANE|LN\.?|DRIVE|DR\.?|COURT|CT\.?|WAY|PLACE|PL\.?)\b",
    re.IGNORECASE,
)
HARDCODED_INSTANCE_RE = re.compile(
    r"(?:/Users/[^/\s]+/(?:Documents/)?AdministrateMe(?:\s+V\d+)?|[A-Za-z]:\\Users\\[^\\\s]+\\AdministrateMe)",
    re.IGNORECASE,
)
PHONE_REFERENCE_PREFIXES = ("docs/reference/",)
PHONE_REFERENCE_FILES = frozenset({"ADMINISTRATEME_CONSOLE_REFERENCE.html"})
GENERIC_IDENTITY_PREFIXES = (
    "adminme/",
    "bootstrap/",
    "bridge/",
    "openclaw/",
    "packs/",
    "scripts/",
    "tests/",
)
SYNTHETIC_EMAIL_DOMAINS = frozenset(
    {"example.com", "example.net", "example.org", "example.test"}
)
SYNTHETIC_ADDRESS_MARKERS = frozenset(
    {"example", "fake", "fixture", "mock", "sample", "synthetic", "test"}
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str


def _has_symlink_component(path: Path) -> bool:
    lexical = path.expanduser()
    if not lexical.is_absolute():
        lexical = Path.cwd() / lexical
    return any(candidate.is_symlink() for candidate in (lexical, *lexical.parents))


def load_private_patterns(
    path: Path | None, *, repository_root: Path
) -> tuple[str, ...]:
    """Load an optional owner-local literal list without making it a release gate."""

    if path is None:
        return ()
    if _has_symlink_component(path):
        raise ValueError("private identity pattern path must not contain a symlink")
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("private identity pattern path must be a regular file")
    try:
        resolved.relative_to(repository_root.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("private identity pattern file must stay outside Git")
    metadata = resolved.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("private identity pattern file must use mode 0600")
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise ValueError("private identity pattern file must be owned by this user")

    patterns: list[str] = []
    seen: set[str] = set()
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if len(value) < 3:
            raise ValueError(
                "private identity patterns must contain at least 3 characters"
            )
        folded = value.casefold()
        if folded not in seen:
            seen.add(folded)
            patterns.append(value)
    return tuple(patterns)


def _repository_paths(root: Path) -> list[Path]:
    """Return tracked and untracked non-ignored paths, with a fixture fallback."""

    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
    )
    if (
        top.returncode == 0
        and Path(os.fsdecode(top.stdout).strip()).resolve() == root.resolve()
    ):
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            check=False,
            capture_output=True,
        )
        if listed.returncode == 0:
            return [
                root / os.fsdecode(item)
                for item in sorted(part for part in listed.stdout.split(b"\0") if part)
            ]
    return sorted(
        (
            path
            for path in root.rglob("*")
            if ".git" not in path.parts and (path.is_file() or path.is_symlink())
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _rules_for_line(
    line: str,
    private_patterns: tuple[str, ...],
    *,
    check_generic_identity: bool,
) -> set[str]:
    rules: set[str] = set()
    folded = line.casefold()
    if any(pattern.casefold() in folded for pattern in private_patterns):
        rules.add("owner_private_literal")
    if HARDCODED_INSTANCE_RE.search(line):
        rules.add("hardcoded_instance_path")
    if check_generic_identity:
        for match in PHONE_RE.finditer(line):
            if match.group(1) != "555" and match.group(2) != "555":
                rules.add("non_synthetic_phone")
        for match in EMAIL_RE.finditer(line):
            domain = match.group(2).casefold()
            if (
                domain not in SYNTHETIC_EMAIL_DOMAINS
                and not domain.startswith("example.")
                and not domain.endswith((".example", ".invalid", ".test"))
            ):
                rules.add("non_synthetic_email")
        if ADDRESS_RE.search(line) and not any(
            marker in folded for marker in SYNTHETIC_ADDRESS_MARKERS
        ):
            rules.add("non_synthetic_street_address")
    return rules


def scan_repository(
    root: Path,
    *,
    private_patterns: tuple[str, ...] = (),
    scan_generic_all: bool = False,
) -> list[Violation]:
    """Scan the Git-eligible tree and return only path, line, and rule."""

    root = root.resolve()
    violations: list[Violation] = []
    for path in _repository_paths(root):
        relative = path.relative_to(root).as_posix()
        private_path = any(
            pattern.casefold() in relative.casefold() for pattern in private_patterns
        )
        reported_path = "<owner-private-path>" if private_path else relative
        if private_path:
            violations.append(Violation(reported_path, 0, "owner_private_path"))
        check_generic_identity = (
            scan_generic_all or relative.startswith(GENERIC_IDENTITY_PREFIXES)
        ) and not (
            relative in PHONE_REFERENCE_FILES
            or relative.startswith(PHONE_REFERENCE_PREFIXES)
        )
        if not path.is_symlink() and not path.is_file():
            # Git lists tracked deletions in a working tree. They have no
            # candidate content to scan.
            continue
        try:
            raw = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        except OSError:
            violations.append(Violation(reported_path, 0, "unreadable_repository_file"))
            continue
        for number, line in enumerate(
            raw.decode("utf-8", errors="replace").splitlines(), 1
        ):
            for rule in sorted(
                _rules_for_line(
                    line,
                    private_patterns,
                    check_generic_identity=check_generic_identity,
                )
            ):
                violations.append(Violation(reported_path, number, rule))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--patterns-file", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    configured = args.patterns_file
    if configured is None and os.environ.get("ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE"):
        configured = Path(os.environ["ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE"])
    try:
        patterns = load_private_patterns(configured, repository_root=root)
    except ValueError as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, sort_keys=True))
        return 2
    violations = scan_repository(root, private_patterns=patterns)
    print(
        json.dumps(
            {
                "status": "PASS" if not violations else "FAIL",
                "private_patterns": "CONFIGURED" if patterns else "NOT_CONFIGURED",
                "violations": [asdict(violation) for violation in violations],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
