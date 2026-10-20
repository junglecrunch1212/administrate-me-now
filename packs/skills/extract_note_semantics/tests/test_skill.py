from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack
from adminme.lib.skill_runner.wrapper import _build_request_body


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


def test_pack_loads_and_rejects_ungrounded_shape() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:extract_note_semantics"
    assert loaded.version == "2.1.1"
    assert loaded.handler_post_process is not None
    output = loaded.handler_post_process(
        {
            "candidates": [
                {
                    "target_kind": "task",
                    "title": "Call the school",
                    "candidate": {},
                    "confidence": 0.9,
                    "reasons": ["explicit request"],
                }
            ],
            "reasons": [],
        },
        {},
        None,
    )
    assert output["candidates"] == []


def test_pack_and_handler_admit_every_grounded_triage_target_kind() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    candidate_enum = loaded.output_schema["properties"]["candidates"]["items"][
        "properties"
    ]["target_kind"]["enum"]
    assert set(candidate_enum) == ALL_TRIAGE_TARGETS
    assert loaded.handler_post_process is not None
    output = loaded.handler_post_process(
        {
            "candidates": [
                {
                    "target_kind": kind,
                    "title": kind,
                    "candidate": {},
                    "evidence_quote": kind,
                    "confidence": 0.8,
                    "reasons": ["explicit"],
                }
                for kind in sorted(ALL_TRIAGE_TARGETS)
            ],
            "reasons": [],
        },
        {},
        None,
    )
    assert {item["target_kind"] for item in output["candidates"]} == ALL_TRIAGE_TARGETS


def test_forwarded_submission_prompt_includes_evidence_after_old_display_limit() -> (
    None
):
    loaded = load_pack(PACK_ROOT)
    body = "Retained source details. " * 1200 + "Final selected obligation."
    request = _build_request_body(
        pack=loaded,
        inputs={
            "source_kind": "member_submission",
            "body_text": body,
            "body_format": "plain",
        },
        session_key="fixture-submission",
    )
    assert request["args"]["input"]["body_text"] == body
    assert request["args"]["input"]["body_text"].endswith("Final selected obligation.")
    assert (
        "A forwarded\nsender is not the submitting member" in request["args"]["prompt"]
    )
    assert "UNTRUSTED DATA" in request["args"]["prompt"]
    assert "{{" not in request["args"]["prompt"]


def test_full_evidence_budget_keeps_the_existing_extraction_contract() -> None:
    loaded = load_pack(PACK_ROOT)
    assert loaded.skill_frontmatter["compatible_success_versions"] == ["2.1.0"]
    assert loaded.skill_frontmatter["timeout_seconds"] == 120
    request = _build_request_body(
        pack=loaded,
        inputs={
            "source_kind": "member_submission",
            "body_text": "Call the school office.",
            "body_format": "plain",
        },
        session_key="fixture-submission-budget",
    )
    assert request["tool"] == "llm-task"
    assert request["args"]["timeoutMs"] == 120_000
    assert request["args"]["maxTokens"] == 1200
    assert request["args"]["temperature"] == 0.0
    assert "model" not in request["args"]
    assert "provider" not in request["args"]
