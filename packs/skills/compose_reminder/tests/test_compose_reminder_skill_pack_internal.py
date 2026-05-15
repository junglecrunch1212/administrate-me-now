"""Loader-validates-this-pack canary + handler-direct unit cases.

Catches structural drift in pack.yaml, SKILL.md frontmatter, or either
schema; exercises the defensive-default post-process handler directly
(no wrapper, no HTTP).

F-3 rename pattern: unique basename (`test_compose_reminder_skill_pack_internal.py`)
to avoid pytest's rootdir-relative-module-name collision across the
three new 10c-iii compose_* skill packs.
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
    assert loaded.skill_frontmatter["context_scopes_required"] == [
        "member_profile_read"
    ]
    assert loaded.skill_frontmatter["outbound_affecting"] is False
    # provider_preferences must use valid aliases (no 4-7) per POSTMORTEM-v3-to-v4.
    prefs = loaded.skill_frontmatter["provider_preferences"]
    assert prefs == [
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4-6",
    ]
    # Output shape is the round-trip into the pipeline's emit path.
    assert sorted(loaded.output_schema["required"]) == [
        "composed_text",
        "reasons",
        "suggested_channel",
        "validation_failed",
    ]
    # SMS-friendly clamp.
    assert loaded.output_schema["properties"]["composed_text"]["maxLength"] == 280
    # on_failure reflects defensive-default per [§7.7].
    on_failure = loaded.skill_frontmatter["on_failure"]
    assert on_failure["composed_text"] == ""
    assert on_failure["validation_failed"] is True
    assert "skill_failure_defensive_default" in on_failure["reasons"]
    assert loaded.handler_post_process is not None


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "composed_text": "Reminder: pick up milk by 6pm.",
        "suggested_channel": "imessage",
        "validation_failed": False,
        "reasons": [],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out == raw


def test_handler_handles_non_dict_input_defensively() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    out = loaded.handler_post_process(["not", "a", "dict"], {}, None)
    assert out["composed_text"] == ""
    assert out["suggested_channel"] == "imessage"
    assert out["validation_failed"] is True
    assert "skill_post_process_non_dict_input" in out["reasons"]


def test_handler_rejects_unknown_channel() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "composed_text": "Body",
        "suggested_channel": "carrier_pigeon",
        "validation_failed": False,
        "reasons": [],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out["validation_failed"] is True
    assert "skill_post_process_unknown_channel" in out["reasons"]


def test_handler_clips_overlong_text() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "composed_text": "x" * 500,
        "suggested_channel": "sms",
        "validation_failed": False,
        "reasons": [],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert len(out["composed_text"]) == 280
