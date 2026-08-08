"""Deterministic intent eligibility covers each distinct Triage kind."""

from __future__ import annotations

from datetime import datetime, timezone

from adminme.triage_intent import (
    extract_grounded_nlp_candidates,
    extract_intent_candidates,
    merge_intent_candidates,
)


NOW = datetime(2026, 8, 8, 16, tzinfo=timezone.utc)


def test_multi_candidate_text_preserves_types_spans_and_no_confidence_gate() -> None:
    text = "\n".join(
        (
            "Call the school office",
            "Appointment on 2026-08-10T18:00:00-04:00 until 2026-08-10T19:00:00-04:00",
            "Remind me to pack the form",
            "Create a school supplies list",
            "Add poster board to school list",
            "Every Friday return the school form",
            "I promised to send the update",
            "Their birthday is 2026-09-03",
        )
    )
    candidates = extract_intent_candidates(text, submitted_at=NOW)

    assert [candidate.target_kind for candidate in candidates] == [
        "task",
        "calendar_event",
        "reminder",
        "list",
        "list_item",
        "recurrence",
        "commitment",
        "relationship_fact",
    ]
    assert all(candidate.confidence is None for candidate in candidates)
    for candidate in candidates:
        [span] = candidate.evidence_spans
        assert text[span["start"] : span["end"]] == candidate.title
        assert candidate.eligibility_rule_ids
    recurrence = candidates[5]
    assert recurrence.fields["rrule"] == "FREQ=WEEKLY;BYDAY=FR"
    assert recurrence.fields["next_occurrence"].startswith("2026-08-14")


def test_ambiguous_text_remains_without_an_invented_candidate() -> None:
    assert (
        extract_intent_candidates("Maybe something about that", submitted_at=NOW) == []
    )


def test_natural_dictation_splits_only_at_explicit_intent_starts() -> None:
    text = (
        "Tomorrow at 9am schedule a dentist appointment, remind me to bring the "
        "insurance card, and every Friday review the weekly plan. I promised to "
        "send the form."
    )

    candidates = extract_intent_candidates(text, submitted_at=NOW)

    assert [candidate.target_kind for candidate in candidates] == [
        "calendar_event",
        "reminder",
        "recurrence",
        "commitment",
    ]
    assert [candidate.title for candidate in candidates] == [
        "Tomorrow at 9am schedule a dentist appointment",
        "remind me to bring the insurance card",
        "every Friday review the weekly plan",
        "I promised to send the form",
    ]
    for candidate in candidates:
        [span] = candidate.evidence_spans
        assert text[span["start"] : span["end"]] == candidate.title


def test_grounded_nlp_admission_requires_exact_quote_and_allowlisted_fields() -> None:
    text = "Your utility payment is due August 15 and repeats monthly."
    candidates = extract_grounded_nlp_candidates(
        text,
        [
            {
                "target_kind": "task",
                "title": "Pay utilities",
                "evidence_quote": text,
                "candidate": {
                    "description": "model paraphrase",
                    "due": "2026-08-15T17:00:00Z",
                    "provider_write": True,
                },
                "confidence": 0.97,
                "reasons": ["explicit due action"],
            },
            {
                "target_kind": "calendar_event",
                "title": "Invented appointment",
                "evidence_quote": "Appointment tomorrow at noon",
                "candidate": {"start": "2026-08-09T12:00:00Z"},
                "confidence": 0.99,
                "reasons": ["not actually present"],
            },
        ],
    )

    [task] = candidates
    assert task.title == text
    assert task.fields == {
        "description": text,
        "due": "2026-08-15T17:00:00Z",
    }
    assert task.confidence == 0.97
    assert task.eligibility_rule_ids == [
        "private_member_message_v1",
        "exact_source_quote_grounded_v1",
        "candidate_fields_task_v1",
    ]


def test_model_fields_enrich_overlapping_deterministic_candidate_without_duplicate() -> (
    None
):
    text = "Every month pay the utility bill"
    deterministic = extract_intent_candidates(text, submitted_at=NOW)
    grounded = extract_grounded_nlp_candidates(
        text,
        [
            {
                "target_kind": "recurrence",
                "title": text,
                "evidence_quote": text,
                "candidate": {
                    "rrule": "FREQ=MONTHLY",
                    "kind": "bill_payment",
                    "trackable": True,
                },
                "confidence": 0.94,
                "reasons": ["explicit monthly cadence"],
            }
        ],
    )

    [merged] = merge_intent_candidates(deterministic, grounded)
    assert merged.target_kind == "recurrence"
    assert merged.fields["rrule"] == "FREQ=MONTHLY"
    assert merged.fields["kind"] == "bill_payment"
    assert merged.fields["notes"] == text
    assert "explicit_recurrence_language_v1" in merged.eligibility_rule_ids
    assert "exact_source_quote_grounded_v1" in merged.eligibility_rule_ids
