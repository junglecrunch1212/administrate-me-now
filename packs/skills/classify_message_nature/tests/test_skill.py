"""Loader-validates-this-pack canary + handler-direct unit cases.

Catches structural drift in pack.yaml, SKILL.md frontmatter, or either
schema, and exercises the defensive-default coercion handler directly
(no wrapper, no HTTP)."""

from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:classify_message_nature"
    assert loaded.version == "2.0.0"
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert (
        "anthropic/claude-haiku-4-5"
        in loaded.skill_frontmatter["provider_preferences"]
    )
    assert loaded.handler_post_process is not None
    assert loaded.output_schema["properties"]["classification"]["enum"] == [
        "noise",
        "transactional",
        "personal",
        "professional",
        "promotional",
    ]


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "classification": "transactional",
        "confidence": 0.93,
        "reasons": ["unsubscribe footer + receipt language"],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out == raw


def test_handler_coerces_when_classification_missing() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "confidence": 0.4,
        "reasons": ["model returned unknown bucket"],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out["classification"] == "personal"
    assert out["confidence"] == 0.0
    assert any("rejected_classification" in r for r in out["reasons"])
    assert "model returned unknown bucket" in out["reasons"]
