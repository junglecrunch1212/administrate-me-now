"""Generate the small deterministic reference block shared by harness skills."""

from __future__ import annotations

from pathlib import Path

from .core import repository_root

BEGIN = "<!-- BEGIN GENERATED:ADMINME-HARNESS -->"
END = "<!-- END GENERATED:ADMINME-HARNESS -->"


def generated_block(skill_name: str) -> str:
    artifact = {
        "adminme-outcome": "outcome-contract.json",
        "adminme-change": "preservation-manifest.json",
        "adminme-review": "review-report.json",
        "adminme-frontend-qa": "verification-report.json",
        "adminme-evidence": "owner-handoff.metadata.json",
    }[skill_name]
    return "\n".join(
        (
            BEGIN,
            "## Generated harness reference",
            "",
            f"Primary artifact: `{artifact}`.",
            "Validate artifacts with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli validate-run <run-dir>`.",
            "Inspect current inventories with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli inventory`.",
            "Use `UNDETERMINED` for UNKNOWN, TRACE_BREAK, missing, or unproved behavior.",
            END,
        )
    )


def replace_generated_block(content: str, skill_name: str) -> str:
    expected = generated_block(skill_name)
    if BEGIN not in content or END not in content:
        return content.rstrip() + "\n\n" + expected + "\n"
    prefix, remainder = content.split(BEGIN, 1)
    _, suffix = remainder.split(END, 1)
    return prefix.rstrip() + "\n\n" + expected + suffix


def skill_paths(root: Path | None = None) -> list[Path]:
    base = root or repository_root()
    skills = base / ".agents" / "skills"
    return [
        skills / name / "SKILL.md"
        for name in (
            "adminme-outcome",
            "adminme-change",
            "adminme-review",
            "adminme-frontend-qa",
            "adminme-evidence",
        )
    ]


def update_skill_references(root: Path | None = None) -> list[Path]:
    updated: list[Path] = []
    for path in skill_paths(root):
        if not path.is_file():
            continue
        content = path.read_text("utf-8")
        replacement = replace_generated_block(content, path.parent.name)
        if replacement != content:
            path.write_text(replacement, encoding="utf-8")
            updated.append(path)
    return updated


def check_skill_references(root: Path | None = None) -> list[str]:
    stale: list[str] = []
    base = root or repository_root()
    for path in skill_paths(base):
        if not path.is_file():
            stale.append(path.relative_to(base).as_posix())
            continue
        content = path.read_text("utf-8")
        if replace_generated_block(content, path.parent.name) != content:
            stale.append(path.relative_to(base).as_posix())
    return stale
