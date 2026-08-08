"""Deterministic intent eligibility covers each distinct Triage kind."""

from __future__ import annotations

from datetime import datetime, timezone

from adminme.triage_intent import extract_intent_candidates


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
