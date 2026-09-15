"""Loader-validates-this-pack canary + handler-direct unit cases.

Catches structural drift in pack.yaml, SKILL.md frontmatter, or either
schema, and exercises the defensive-default coercion handler directly
(no wrapper, no HTTP)."""

import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack
from adminme.triage_intent import CANDIDATE_FIELD_ALLOWLISTS

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


def test_declared_legacy_compatibility_preserves_validation_contract() -> None:
    """Prompt/schema annotations do not invalidate unchanged historical receipts."""
    preserved = {
        "handler.py": "19ec6cc927d8b41c3417a0f9db1f74a5d1a5a96270c6bdfdbcf5f53c2e83ff81",
        "schemas/input.schema.json": "5b003406118a7c3e94651e50e74e0d90032961207599e55405a82dc66f6dc9e0",
    }
    for name, digest in preserved.items():
        assert hashlib.sha256((PACK_ROOT / name).read_bytes()).hexdigest() == digest
    schema = json.loads((PACK_ROOT / "schemas/output.schema.json").read_text())
    item = schema["properties"]["candidates"]["items"]
    assessment = item["properties"]["obligation_assessment"]
    assert "blocking_question_indices" not in assessment["required"]
    question_scope = assessment["properties"].pop("blocking_question_indices")
    assert question_scope["uniqueItems"] is True
    assert question_scope["maxItems"] == 8
    # Every new conditional branch contains annotations only. Its true branch
    # must impose no new required fields, types or additional-property limits.
    for branch in item.pop("allOf"):
        assert set(branch) == {"if", "then"}
        assert set(branch["then"]) == {"properties"}
        assert set(branch["then"]["properties"]) == {"candidate"}
        candidate = branch["then"]["properties"]["candidate"]
        assert set(candidate) == {"description", "properties"}
        assert all(
            set(value) == {"description"} for value in candidate["properties"].values()
        )

    def remove_annotations(value):
        if isinstance(value, dict):
            return {
                key: remove_annotations(item)
                for key, item in value.items()
                if key != "description"
            }
        if isinstance(value, list):
            return [remove_annotations(item) for item in value]
        return value

    original_validation = json.dumps(remove_annotations(schema), indent=2) + "\n"
    assert hashlib.sha256(original_validation.encode()).hexdigest() == (
        "84f83092b29fe3d3e0cc4d69608165d7ea24154bfdf0977aeb23627b22b25c3c"
    )


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:classify_message_nature"
    assert loaded.version == "8.0.5"
    assert loaded.skill_frontmatter["compatible_success_versions"] == [
        "8.0.0",
        "8.0.1",
        "8.0.2",
        "8.0.3",
        "8.0.4",
    ]
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


def test_candidate_field_annotations_match_every_production_allowlist() -> None:
    loaded = load_pack(PACK_ROOT)
    item = loaded.output_schema["properties"]["candidates"]["items"]
    documented = {
        branch["if"]["properties"]["target_kind"]["const"]: set(
            branch["then"]["properties"]["candidate"]["properties"]
        )
        for branch in item["allOf"]
    }
    assert documented == {
        kind: set(fields) for kind, fields in CANDIDATE_FIELD_ALLOWLISTS.items()
    }
    # Historical successful payloads may contain arbitrary candidate fields.
    # They remain valid and immutable rather than causing a patch re-inference.
    assert loaded.handler_post_process is not None
    for kind in ALL_TRIAGE_TARGETS:
        raw = {
            "classification": "personal",
            "confidence": 0.9,
            "reasons": ["retained"],
            "candidates": [
                {
                    "target_kind": kind,
                    "title": "Retained fixture action",
                    "evidence_quote": "Retained fixture action",
                    "confidence": 0.9,
                    "reasons": ["retained"],
                    "candidate": {
                        "action": "Retained fixture action",
                        "deadline": "2031-04-09",
                        "legacy_extra": {"nested": True},
                    },
                }
            ],
        }
        output = loaded.handler_post_process(raw, {}, None)
        before = copy.deepcopy(output)
        Draft202012Validator(loaded.output_schema).validate(output)
        assert output == before


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
