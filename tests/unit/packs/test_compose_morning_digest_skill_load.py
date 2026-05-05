"""Pack-load canary for compose_morning_digest skill at v3.0.0.

Mirrors 09b's pattern (e.g. classify_thank_you_candidate) but lives
under ``tests/unit/packs/`` so the standard ``tests/`` testpath picks
it up. The pack-internal test (``packs/skills/compose_morning_digest/
tests/test_skill.py``) covers handler-direct paths; this file asserts
top-level structural contracts: registered version, frontmatter
shape, schema fields.
"""

from __future__ import annotations

from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack

PACK_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packs"
    / "skills"
    / "compose_morning_digest"
)


def test_compose_morning_digest_pack_loads() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:compose_morning_digest"
    # [BUILD.md §2242] catalog name is `compose_morning_digest@v3` —
    # the pack ships at SemVer 3.0.0.
    assert loaded.version == "3.0.0"
    assert loaded.skill_frontmatter["name"] == "compose_morning_digest"
    assert loaded.skill_frontmatter["namespace"] == "adminme"


def test_compose_morning_digest_input_schema_carries_profile_format() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    profile_format = loaded.input_schema["properties"]["profile_format"]
    assert profile_format["enum"] == [
        "fog_aware",
        "compressed",
        "carousel",
        "child",
        "none",
    ]
