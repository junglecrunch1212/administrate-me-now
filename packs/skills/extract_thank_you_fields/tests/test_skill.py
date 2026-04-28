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
    assert loaded.pack_id == "skill:extract_thank_you_fields"
    assert loaded.version == "1.0.0"
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert loaded.skill_frontmatter["context_scopes_required"] == []
    assert loaded.handler_post_process is not None
    # Output shape must round-trip into CommitmentProposedV1 — the
    # urgency enum here matches CommitmentProposedV1.urgency's Literal
    # in adminme/events/schemas/domain.py.
    assert loaded.output_schema["properties"]["urgency"]["enum"] == [
        "today",
        "this_week",
        "this_month",
        "no_rush",
    ]
    # Required fields lock the round-trip surface.
    assert sorted(loaded.output_schema["required"]) == [
        "confidence",
        "recipient_party_id",
        "suggested_text",
        "urgency",
    ]


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "recipient_party_id": "party_seed",
        "suggested_text": "Thanks so much for having us over Friday — the kids are still talking about the s'mores.",
        "urgency": "this_week",
        "confidence": 0.84,
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out == raw


def test_handler_coerces_unknown_urgency_to_no_rush() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "recipient_party_id": "party_seed",
        "suggested_text": "Thanks for the help.",
        "urgency": "asap",
        "confidence": 0.6,
        "reasons": ["model invented a new urgency"],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out["urgency"] == "no_rush"
    assert out["confidence"] == 0.0
    assert any("rejected_urgency" in r for r in out["reasons"])
    assert "model invented a new urgency" in out["reasons"]


def test_handler_handles_non_dict_input_defensively() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    out = loaded.handler_post_process(["not", "a", "dict"], {}, None)  # type: ignore[arg-type]
    assert out["urgency"] == "no_rush"
    assert out["confidence"] == 0.0
    assert "skill_post_process_non_dict_input" in out["reasons"]
