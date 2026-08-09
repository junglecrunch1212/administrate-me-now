"""Triage context is authorized, exact-source grounded, and non-generative."""

from __future__ import annotations

from adminme.triage_context import compose_triage_source_context


def test_message_context_exposes_decision_cues_and_only_exact_quote() -> None:
    item = {
        "title": "Pay the water bill Friday",
        "target_kind": "task",
        "candidate": {
            "due": "2026-08-14",
            "source_message_preview": "Please pay the water bill Friday.",
            "not_allowlisted": "hidden",
        },
        "eligibility_rule_ids": [
            "explicit_pay_action_v2",
            "grounded_language_exact_quote_v1",
            "candidate_policy_v1:adapter:gmail:review",
        ],
    }
    source = {
        "event_id": "message-1",
        "type": "messaging.received",
        "source_adapter": "gmail-api",
        "source_account_id": "account-redacted",
        "occurred_at": "2026-08-09T12:00:00Z",
        "payload": {
            "source_channel": "gmail",
            "from_identifier": "sender@example.invalid",
            "subject": "Water bill",
            "body_text": "Please pay the water bill Friday.",
        },
    }
    context = compose_triage_source_context(item, source)
    assert context["channel"] == "gmail"
    assert context["sender"] == "sender@example.invalid"
    assert context["subject"] == "Water bill"
    assert context["direction"] == "inbound"
    assert context["evidence"] == {
        "quote": "Please pay the water bill Friday.",
        "exactly_grounded": True,
    }
    assert context["meaning"]["extracted_fields"] == [
        {"field": "due", "label": "Due", "value": "2026-08-14"}
    ]
    assert [method["id"] for method in context["methods"]] == [
        "raw",
        "rules",
        "language",
    ]


def test_nonliteral_candidate_text_is_never_presented_as_source_evidence() -> None:
    context = compose_triage_source_context(
        {
            "title": "Model-written paraphrase",
            "target_kind": "calendar_event",
            "candidate": {"evidence_quote": "Words not present"},
            "eligibility_rule_ids": ["candidate_policy_v1:adapter:gmail:review"],
        },
        {
            "event_id": "message-2",
            "type": "messaging.received",
            "payload": {"body_text": "The actual immutable source text."},
        },
    )
    assert context["evidence"] == {"quote": None, "exactly_grounded": False}
