"""Standing-order program-file canary.

Asserts every .md file in bootstrap/openclaw/programs/ has the six
required section headers per [cheatsheet Q3] (Scope / Triggers /
Approval gate / Escalation / Execution steps / What NOT to do). Per
[D1] Corollary, bootstrap §8 (prompt 16) concatenates these into
`~/Chief/AGENTS.md` — section-header presence is the contract.

Stub program files (morning_digest / paralysis_detection /
reminder_dispatch / crm_surface / custody_brief) carry the headers
plus TODO markers in Execution steps; reward_dispatch.md is fully
populated.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PROGRAMS_DIR = REPO_ROOT / "bootstrap" / "openclaw" / "programs"

REQUIRED_SECTIONS = [
    "## Scope",
    "## Triggers",
    "## Approval gate",
    "## Escalation",
    "## Execution steps",
    "## What NOT to do",
]

EXPECTED_PROGRAMS = {
    "reward_dispatch",
    "morning_digest",
    "paralysis_detection",
    "reminder_dispatch",
    "crm_surface",
    "custody_brief",
}


def test_all_six_program_files_exist() -> None:
    found = {p.stem for p in PROGRAMS_DIR.glob("*.md")}
    assert found == EXPECTED_PROGRAMS, (
        f"expected exactly {sorted(EXPECTED_PROGRAMS)}; found {sorted(found)}"
    )


def test_every_program_file_has_required_sections() -> None:
    for md_path in sorted(PROGRAMS_DIR.glob("*.md")):
        text = md_path.read_text()
        for header in REQUIRED_SECTIONS:
            assert header in text, (
                f"{md_path.name}: missing required section header {header!r}"
            )


def test_reward_dispatch_is_fully_populated() -> None:
    """reward_dispatch.md is the one fully-populated program in 10c-i.
    Its Execution steps section MUST NOT contain a TODO marker."""
    text = (PROGRAMS_DIR / "reward_dispatch.md").read_text()
    # Locate the Execution steps section and the next ## heading.
    match = re.search(
        r"## Execution steps\n(.*?)(?=\n## |\Z)", text, re.DOTALL
    )
    assert match is not None, "reward_dispatch.md: Execution steps not found"
    body = match.group(1)
    assert "TODO(" not in body, (
        f"reward_dispatch.md: Execution steps still contains TODO marker:\n{body}"
    )


def test_stub_programs_carry_todo_markers() -> None:
    """The five stub programs carry TODO(prompt-10c-ii) or
    TODO(prompt-10c-iii) markers in their Execution steps so a future
    reader knows they're stubs awaiting fill-in."""
    stubs = EXPECTED_PROGRAMS - {"reward_dispatch"}
    for name in sorted(stubs):
        text = (PROGRAMS_DIR / f"{name}.md").read_text()
        match = re.search(
            r"## Execution steps\n(.*?)(?=\n## |\Z)", text, re.DOTALL
        )
        assert match is not None, f"{name}.md: Execution steps not found"
        body = match.group(1)
        assert "TODO(prompt-10c-" in body, (
            f"{name}.md: Execution steps missing TODO(prompt-10c-*) stub marker"
        )
