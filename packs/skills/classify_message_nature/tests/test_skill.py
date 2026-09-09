"""Loader-validates-this-pack canary + handler-direct unit cases.

Catches structural drift in pack.yaml, SKILL.md frontmatter, or either
schema, and exercises the defensive-default coercion handler directly
(no wrapper, no HTTP)."""

import hashlib
from pathlib import Path

from jsonschema import Draft202012Validator

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack

PACK_ROOT = Path(__file__).resolve().parents[1]
ALL_TRIAGE_TARGETS = {
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "list",
    "list_item",
    "recurrence",
    "party_identifier",
    "party_candidate",
    "party_relationship",
    "relationship_fact",
}


def test_declared_8_0_0_compatibility_preserves_the_exact_result_contract() -> None:
    """Only execution timeout/version may differ from the reviewed 8.0.0 pack."""
    preserved = {
        "prompt.jinja2": "f3b35ec4601805bb07bc9ab46ea85c835bebf14008b3db7c51b8890c86ca8d92",
        "handler.py": "19ec6cc927d8b41c3417a0f9db1f74a5d1a5a96270c6bdfdbcf5f53c2e83ff81",
        "schemas/input.schema.json": "5b003406118a7c3e94651e50e74e0d90032961207599e55405a82dc66f6dc9e0",
        "schemas/output.schema.json": "84f83092b29fe3d3e0cc4d69608165d7ea24154bfdf0977aeb23627b22b25c3c",
    }
    for name, digest in preserved.items():
        assert hashlib.sha256((PACK_ROOT / name).read_bytes()).hexdigest() == digest
    previous_metadata = (
        (PACK_ROOT / "SKILL.md")
        .read_text()
        .replace(
            "version: 8.0.1\ncompatible_success_versions: [8.0.0]",
            "version: 8.0.0",
        )
        .replace("timeout_seconds: 120", "timeout_seconds: 60")
    )
    assert hashlib.sha256(previous_metadata.encode()).hexdigest() == (
        "047f80f7ad69fcee1b7edfb98b882aa1b1f0a032e5f65787be72c8b09de90b24"
    )


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:classify_message_nature"
    assert loaded.version == "8.0.1"
    assert loaded.skill_frontmatter["compatible_success_versions"] == ["8.0.0"]
    assert loaded.skill_frontmatter["timeout_seconds"] == 120
    assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
    assert (
        "anthropic/claude-haiku-4-5" in loaded.skill_frontmatter["provider_preferences"]
    )
    assert loaded.handler_post_process is not None
    assert loaded.output_schema["properties"]["classification"]["enum"] == [
        "noise",
        "transactional",
        "personal",
        "professional",
        "promotional",
    ]
    candidate_enum = loaded.output_schema["properties"]["candidates"]["items"][
        "properties"
    ]["target_kind"]["enum"]
    assert set(candidate_enum) == ALL_TRIAGE_TARGETS


def test_input_schema_accepts_strong_rfc_edge_with_weak_corroboration() -> None:
    """The fixed-code context vocabulary and pinned skill schema stay atomic."""
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    inputs = {
        "body_text": "Fixture source body.",
        "source_channel": "gmail",
        "from_identifier": "service@example.invalid",
        "context_pack": {
            "contract": "message-context-pack-v2",
            "thread_key": "a" * 64,
            "context_fingerprint": "b" * 64,
            "current_source_id": "m01",
            "messages": [
                {
                    "source_id": "m01",
                    "relation": "related",
                    "relation_reason_codes": [
                        "rfc_reference",
                        "participant_overlap",
                        "participant_reference_overlap",
                    ],
                    "direction": "inbound",
                    "occurred_at": "2026-08-10T12:00:00Z",
                    "source_channel": "gmail",
                    "sender": "service@example.invalid",
                    "recipients": ["member@example.invalid"],
                    "subject": "Fixture notice",
                    "body_text": "Fixture source body.",
                    "attachment_names": [],
                }
            ],
        },
    }

    Draft202012Validator(loaded.input_schema).validate(inputs)


def test_handler_passes_through_well_formed_output() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "assessment_contract": "personal-obligation-assessment-v1",
        "classification": "transactional",
        "confidence": 0.93,
        "reasons": ["unsubscribe footer + receipt language"],
        "reply_signal": "not_required",
        "automated": True,
        "transactional_due": False,
        "security_code": False,
        "person_to_person": False,
        "deadline": None,
        "candidates": [],
    }
    out = loaded.handler_post_process(raw, {}, None)
    Draft202012Validator(loaded.output_schema).validate(out)
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


def test_handler_admits_every_grounded_triage_target_kind() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    raw = {
        "classification": "personal",
        "confidence": 0.9,
        "reasons": ["direct message"],
        "reply_signal": "unknown",
        "automated": False,
        "transactional_due": False,
        "security_code": False,
        "person_to_person": True,
        "deadline": None,
        "candidates": [
            {
                "target_kind": kind,
                "title": kind,
                "evidence_quote": kind,
                "candidate": {},
                "confidence": 0.8,
                "reasons": ["explicit"],
            }
            for kind in sorted(ALL_TRIAGE_TARGETS)
        ],
    }
    output = loaded.handler_post_process(raw, {}, None)
    assert {item["target_kind"] for item in output["candidates"]} == ALL_TRIAGE_TARGETS
