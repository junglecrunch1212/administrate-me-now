"""Content-free repository identity scan with fail-closed release canaries."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-](\d{3})[ .-](\d{4})(?!\d)"
)
HARDCODED_INSTANCE_RE = re.compile(
    r"(?:/Users/[^/]+/AdministrateMe|[A-Za-z]:\\Users\\[^\\]+\\AdministrateMe)",
    re.IGNORECASE,
)
REQUIRED_RELEASE_CATEGORIES = frozenset({"name", "email", "address"})
GENERIC_PHONE_REFERENCE_PREFIXES = ("docs/reference/",)
GENERIC_PHONE_REFERENCE_FILES = frozenset({"ADMINISTRATEME_CONSOLE_REFERENCE.html"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str


def default_private_patterns_file() -> Path:
    """Return the lexical owner-local path so symlink checks remain possible."""

    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "AdministrateMe"
            / "private-identity-patterns.txt"
        )
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    if not base.is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be an absolute path")
    return base / "administrateme" / "private-identity-patterns.txt"


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


def path_has_symlink_component(path: Path) -> bool:
    """Return true when the lexical file path or an existing parent is a link."""

    expanded = path.expanduser()
    lexical = expanded if expanded.is_absolute() else Path.cwd() / expanded
    return any(candidate.is_symlink() for candidate in (lexical, *lexical.parents))


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
    if path_has_symlink_component(expanded):
        if require_release_profile:
            raise ValueError("private identity release profile is not configured")
        raise ValueError("private identity pattern path must be a real file")
    resolved = expanded.resolve()
    if not resolved.is_file():
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


def _line_rules(
    line: str,
    private_patterns: tuple[str, ...],
    *,
    check_generic_phone: bool = True,
) -> set[str]:
    rules: set[str] = set()
    lowered = line.casefold()
    if any(pattern.casefold() in lowered for pattern in private_patterns):
        rules.add("owner_private_literal")
    if HARDCODED_INSTANCE_RE.search(line):
        rules.add("hardcoded_instance_path")
    if check_generic_phone:
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


def _fallback_repository_paths(root: Path) -> list[Path]:
    """Return every file-like path below a non-Git fixture, excluding Git internals."""

    paths: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink() or path.is_file():
            paths.append(path)
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def _repository_paths(root: Path) -> list[Path]:
    """Return tracked and untracked non-ignored paths for an exact Git worktree."""

    worktree = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
    )
    if worktree.returncode != 0:
        return _fallback_repository_paths(root)
    top_level = Path(os.fsdecode(worktree.stdout).strip()).resolve()
    if top_level != root.resolve():
        return _fallback_repository_paths(root)
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
        check=False,
        capture_output=True,
    )
    if listed.returncode != 0:
        return _fallback_repository_paths(root)
    return [
        root / os.fsdecode(encoded)
        for encoded in sorted(item for item in listed.stdout.split(b"\0") if item)
    ]


def scan_repository(
    root: Path,
    *,
    private_patterns: tuple[str, ...] = (),
) -> list[Violation]:
    """Scan the complete Git-eligible tree and return content-free coordinates."""

    violations: list[Violation] = []
    for path in _repository_paths(root):
        relative = path.relative_to(root).as_posix()
        private_path = any(
            pattern.casefold() in relative.casefold() for pattern in private_patterns
        )
        reported_path = "<owner-private-path>" if private_path else relative
        if private_path:
            violations.append(Violation(reported_path, 0, "owner_private_path"))
        check_generic_phone = not (
            relative in GENERIC_PHONE_REFERENCE_FILES
            or relative.startswith(GENERIC_PHONE_REFERENCE_PREFIXES)
        )
        try:
            if path.is_symlink():
                lines = os.readlink(path).splitlines()
            elif path.is_file():
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            else:
                # A tracked deletion or submodule has no candidate-tip file content.
                continue
        except OSError:
            violations.append(Violation(reported_path, 0, "unreadable_repository_file"))
            continue
        for line_number, line in enumerate(lines, start=1):
            for rule in sorted(
                _line_rules(
                    line,
                    private_patterns,
                    check_generic_phone=check_generic_phone,
                )
            ):
                violations.append(Violation(reported_path, line_number, rule))
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
    try:
        if configured is None and args.require_private_patterns:
            configured = default_private_patterns_file()
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
