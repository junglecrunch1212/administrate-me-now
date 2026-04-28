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
    assert loaded.pack_id == "skill:extract_commitment_fields"
    assert loaded.version == "2.1.0"
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert loaded.skill_frontmatter["context_scopes_required"] == []
    assert loaded.handler_post_process is not None
    # Output shape must round-trip into CommitmentProposedV1 — the kind
    # enum here matches CommitmentProposedV1.kind's Literal in
    # adminme/events/schemas/domain.py.
    assert loaded.output_schema["properties"]["kind"]["enum"] == [
        "reply",
        "task",
        "appointment",
        "payment",
        "document_return",
        "visit",
        "other",
    ]
    assert loaded.output_schema["properties"]["urgency"]["enum"] == [
        "today",
        "this_week",
        "this_month",
        "no_rush",
    ]


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "kind": "reply",
        "owed_by_member_id": "member_alpha",
        "owed_to_party_id": "party_beta",
        "text_summary": "Confirm Saturday 2pm walk-through",
        "suggested_due": "2026-05-02T19:00:00Z",
        "urgency": "this_week",
        "confidence": 0.84,
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out == raw


def test_handler_coerces_unknown_kind_to_other() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "kind": "complain_to_friend",
        "confidence": 0.6,
        "reasons": ["model invented a new kind"],
    }
    out = loaded.handler_post_process(raw, {}, None)
    assert out["kind"] == "other"
    assert out["confidence"] == 0.0
    assert any("rejected_kind" in r for r in out["reasons"])
    assert "model invented a new kind" in out["reasons"]


def test_handler_handles_non_dict_input_defensively() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    out = loaded.handler_post_process(["not", "a", "dict"], {}, None)  # type: ignore[arg-type]
    assert out["kind"] == "other"
    assert out["confidence"] == 0.0
    assert "skill_post_process_non_dict_input" in out["reasons"]
