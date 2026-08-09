from pathlib import Path

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


def test_pack_loads_and_rejects_ungrounded_shape() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:extract_note_semantics"
    assert loaded.version == "2.0.0"
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
