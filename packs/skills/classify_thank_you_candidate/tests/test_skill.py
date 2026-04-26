"""Loader-validates-this-pack canary. Catches structural drift in
pack.yaml, SKILL.md frontmatter, or either schema."""

from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    pack_root = Path(__file__).resolve().parents[1]
    loaded = load_pack(pack_root)
    assert loaded.pack_id == "skill:classify_thank_you_candidate"
    assert loaded.version == "1.3.0"
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert (
        "anthropic/claude-haiku-4-5"
        in loaded.skill_frontmatter["provider_preferences"]
    )
    # Commit 2 adds handler.py; in Commit 1 there's no handler yet.
    assert loaded.handler_post_process is None
    assert "is_candidate" in loaded.output_schema["properties"]
