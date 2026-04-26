"""Loader-validates-this-pack canary + handler-direct unit cases.

Catches structural drift in pack.yaml, SKILL.md frontmatter, or either
schema, and exercises the urgency-coercion safety net handler directly
(no wrapper, no HTTP)."""

from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:classify_thank_you_candidate"
    assert loaded.version == "1.3.0"
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert (
        "anthropic/claude-haiku-4-5"
        in loaded.skill_frontmatter["provider_preferences"]
    )
    assert loaded.handler_post_process is not None
    assert "is_candidate" in loaded.output_schema["properties"]


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "is_candidate": True,
        "confidence": 0.92,
        "urgency": "this_week",
        "suggested_medium": "handwritten_card",
        "reasons": ["hosting hospitality"],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out == raw


def test_handler_coerces_when_urgency_missing() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "is_candidate": True,
        "confidence": 0.85,
        "reasons": ["hosting hospitality"],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out["is_candidate"] is False
    assert out["confidence"] == 0.85
    assert "missing_urgency" in out["reasons"]
    assert "hosting hospitality" in out["reasons"]


def test_handler_handles_non_dict_input_defensively() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    out = loaded.handler_post_process("oops not a dict", {}, None)  # type: ignore[arg-type]
    assert out["is_candidate"] is False
    assert out["confidence"] == 0.0
    assert "skill_post_process_non_dict_input" in out["reasons"]
