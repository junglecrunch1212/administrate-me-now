"""
Canary test: no hardcoded instance-directory paths in platform code.

Per SYSTEM_INVARIANTS.md §15 and DECISIONS.md §D15: no module under adminme/
(or bootstrap/, profiles/, personas/, integrations/) hardcodes ~/.adminme/ or
any subpath of it as a runtime string literal. All instance paths resolve
through InstanceConfig.

Docstrings that illustrate the conceptual layout using ~/.adminme/ as an
example are exempt — the regexes below operate on runtime-literal shapes
(quoted string literals) and the module-level pass strips triple-quoted
blocks before scanning.

Implemented in prompt 05, which is the first prompt where InstanceConfig
has enough behavior for platform code to route paths through it.
"""

from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN_PATTERNS = [
    re.compile(r"""['"]~/\.adminme"""),      # e.g. "~/.adminme/projections"
    re.compile(r"""['"]/\.adminme/"""),      # e.g. "/.adminme/"
    re.compile(r"os\.path\.expanduser\([^)]*\.adminme"),
]

ALLOWED_DIRS_TO_SCAN = ["adminme", "bootstrap", "packs"]


def test_no_hardcoded_instance_path_in_platform_code() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for dir_name in ALLOWED_DIRS_TO_SCAN:
        root = repo_root / dir_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".sh"}:
                continue
            text = path.read_text(encoding="utf-8")
            # Strip triple-quoted blocks (docstrings may reference the
            # conceptual layout). Runtime string literals inside code
            # remain visible to the regex sweep.
            stripped = re.sub(r'"""[\s\S]*?"""', "", text)
            stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
            for pat in FORBIDDEN_PATTERNS:
                for m in pat.finditer(stripped):
                    rel = path.relative_to(repo_root)
                    violations.append(f"{rel}: {m.group(0)!r}")
    assert not violations, (
        "Hardcoded instance-path literals found (see SYSTEM_INVARIANTS.md §15 / "
        "DECISIONS.md §D15):\n" + "\n".join(violations)
    )
