"""Retained background programs remain complete and loadable on their triggers."""

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
    assert found == EXPECTED_PROGRAMS | {
        "requested_workflows",
        "workstation_inventory",
        "workstation_work",
    }, f"expected exactly {sorted(EXPECTED_PROGRAMS)}; found {sorted(found)}"


def test_every_program_file_has_required_sections() -> None:
    for md_path in sorted(PROGRAMS_DIR / f"{name}.md" for name in EXPECTED_PROGRAMS):
        text = md_path.read_text()
        for header in REQUIRED_SECTIONS:
            assert header in text, (
                f"{md_path.name}: missing required section header {header!r}"
            )


FULLY_POPULATED_PROGRAMS = {
    "reward_dispatch",
    "morning_digest",
    "paralysis_detection",
    "reminder_dispatch",
    "crm_surface",
    "custody_brief",
}


def test_fully_populated_programs_have_no_todo_markers() -> None:
    """reward_dispatch (10c-i), morning_digest / paralysis_detection
    (10c-ii), and reminder_dispatch / crm_surface / custody_brief
    (10c-iii) are all fully populated. Their Execution steps section
    MUST NOT contain a TODO marker."""
    for name in sorted(FULLY_POPULATED_PROGRAMS):
        text = (PROGRAMS_DIR / f"{name}.md").read_text()
        match = re.search(r"## Execution steps\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
        assert match is not None, f"{name}.md: Execution steps not found"
        body = match.group(1)
        assert "TODO(" not in body, (
            f"{name}.md: Execution steps still contains TODO marker:\n{body}"
        )


def test_no_remaining_stub_programs() -> None:
    """After 10c-iii commit 1 there are no stub program files: every
    EXPECTED_PROGRAMS entry is in FULLY_POPULATED_PROGRAMS."""
    stubs = EXPECTED_PROGRAMS - FULLY_POPULATED_PROGRAMS
    assert stubs == set(), f"unexpected stub programs remain: {sorted(stubs)}"
