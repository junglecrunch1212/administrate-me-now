"""Loader-validates-this-pack canary + handler-direct unit cases.

Catches structural drift in pack.yaml, SKILL.md frontmatter, or either
schema; exercises the defensive-default post-process handler directly
(no wrapper, no HTTP). Mirrors
``packs/skills/compose_morning_digest/tests/test_skill.py``.
"""

from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:compose_reminder"
    assert loaded.version == "1.0.0"
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert loaded.skill_frontmatter["context_scopes_required"] == ["member_profile_read"]
    assert loaded.skill_frontmatter["max_tokens"] == 300
    assert sorted(loaded.output_schema["required"]) == [
        "composed_text",
        "suggested_channel",
    ]
    on_failure = loaded.skill_frontmatter["on_failure"]
    assert on_failure["composed_text"] == ""
    assert on_failure["suggested_channel"] == "imessage"
    assert on_failure["tone_note"] == "skill_failure_defensive_default"
    assert loaded.handler_post_process is not None


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "composed_text": "Heads-up: pick up Charlie at 3.",
        "suggested_channel": "imessage",
        "tone_note": "warm",
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out == raw


def test_handler_coerces_unknown_channel_to_imessage() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    out = loaded.handler_post_process(
        {"composed_text": "Reminder body.", "suggested_channel": "carrier-pigeon"},
        {},
        None,
    )
    assert out["composed_text"] == "Reminder body."
    assert out["suggested_channel"] == "imessage"
    assert "channel_coerced_from='carrier-pigeon'" in out["tone_note"]


def test_handler_handles_non_dict_input_defensively() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    out = loaded.handler_post_process(["not", "a", "dict"], {}, None)  # type: ignore[arg-type]
    assert out["composed_text"] == ""
    assert out["suggested_channel"] == "imessage"
    assert out["tone_note"] == "skill_post_process_non_dict_input"
