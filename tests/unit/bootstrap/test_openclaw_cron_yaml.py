"""cron.yaml sidecar canary.

Asserts the cron sidecar consumed by bootstrap §8 (prompt 16) parses
via yaml.safe_load, every entry has program_id + cron + message,
every program_id has a corresponding <id>.md under programs/, and
reward_dispatch is NOT present (it's reactive, not scheduled).
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CRON_YAML_PATH = REPO_ROOT / "bootstrap" / "openclaw" / "cron.yaml"
PROGRAMS_DIR = REPO_ROOT / "bootstrap" / "openclaw" / "programs"

EXPECTED_SCHEDULED = {
    "morning_digest",
    "paralysis_detection",
    "reminder_dispatch",
    "crm_surface",
    "custody_brief",
}


def _load_entries() -> list[dict]:
    with CRON_YAML_PATH.open() as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "cron.yaml: top-level must be a mapping"
    entries = data.get("entries")
    assert isinstance(entries, list), "cron.yaml: 'entries' must be a list"
    return entries


def test_cron_yaml_parses_and_has_expected_entries() -> None:
    entries = _load_entries()
    program_ids = {e["program_id"] for e in entries}
    assert program_ids == EXPECTED_SCHEDULED, (
        f"expected {sorted(EXPECTED_SCHEDULED)}; got {sorted(program_ids)}"
    )


def test_every_entry_has_required_keys() -> None:
    entries = _load_entries()
    for entry in entries:
        assert "program_id" in entry, f"entry missing program_id: {entry}"
        assert "cron" in entry, f"entry missing cron: {entry}"
        assert "message" in entry, f"entry missing message: {entry}"
        assert isinstance(entry["program_id"], str) and entry["program_id"]
        assert isinstance(entry["cron"], str) and entry["cron"]
        assert isinstance(entry["message"], str) and entry["message"]


def test_every_program_id_has_corresponding_md_file() -> None:
    entries = _load_entries()
    for entry in entries:
        md_path = PROGRAMS_DIR / f"{entry['program_id']}.md"
        assert md_path.exists(), (
            f"cron.yaml references program_id={entry['program_id']!r} but "
            f"{md_path.relative_to(REPO_ROOT)} does not exist"
        )


def test_reward_dispatch_is_not_scheduled() -> None:
    """reward_dispatch is reactive (has triggers.events). The
    PipelineRunner picks it up via the in-process bus; OpenClaw cron
    does NOT invoke it. Per [§7.1-7.2] and bootstrap/openclaw/README.md.
    """
    entries = _load_entries()
    program_ids = {e["program_id"] for e in entries}
    assert "reward_dispatch" not in program_ids, (
        "reward_dispatch must not appear in cron.yaml — it's a reactive "
        "pipeline picked up by PipelineRunner, not OpenClaw cron"
    )
