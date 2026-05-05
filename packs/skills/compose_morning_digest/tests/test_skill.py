"""Loader-validates-this-pack canary + handler-direct unit cases.

Catches structural drift in pack.yaml, SKILL.md frontmatter, or either
schema; exercises the defensive-default post-process handler directly
(no wrapper, no HTTP). Mirrors
``packs/skills/extract_thank_you_fields/tests/test_skill.py``.
"""

from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:compose_morning_digest"
    assert loaded.version == "3.0.0"
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert loaded.skill_frontmatter["context_scopes_required"] == []
    # Output round-trips into the validation guard via claimed_event_ids.
    assert sorted(loaded.output_schema["required"]) == [
        "body_text",
        "claimed_event_ids",
        "reasons",
        "validation_failed",
    ]
    # on_failure reflects defensive-default per [BUILD.md §1289].
    on_failure = loaded.skill_frontmatter["on_failure"]
    assert on_failure["validation_failed"] is True
    assert "skill_failure_defensive_default" in on_failure["reasons"]
    assert loaded.handler_post_process is not None


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "body_text": "Today: 1 event, 2 tasks.",
        "claimed_event_ids": ["evt-1", "task-1", "task-2"],
        "validation_failed": False,
        "reasons": [],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out == raw


def test_handler_handles_non_dict_input_defensively() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    out = loaded.handler_post_process(["not", "a", "dict"], {}, None)  # type: ignore[arg-type]
    assert out["body_text"] == ""
    assert out["claimed_event_ids"] == []
    assert out["validation_failed"] is True
    assert "skill_post_process_non_dict_input" in out["reasons"]
