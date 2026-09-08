"""Contrasting action evidence and conservative cross-source identity."""

from datetime import datetime, timezone

import pytest

from adminme.obligation_assessment import (
    date_is_supported,
    ground_obligation_assessment,
)
from adminme.reconciliation import evidence_profile, evidence_relationships


def _assessment(**overrides):
    quote = "Please complete the form by September 12, 2026."
    raw = {
        "purpose": "preparation",
        "actionability": "required",
        "condition_state": "not_applicable",
        "date_role": "deadline",
        "qualifying_evidence": [{"source_id": "m01", "quote": quote, "role": "date"}],
        **overrides,
    }
    return ground_obligation_assessment(
        raw,
        source_texts={
            "m01": ("source-form", quote + " Only if attending. Already completed.")
        },
        primary_alias="m01",
        primary_quote=quote,
        action_owner="member",
        fields={"due": "2026-09-12T17:00:00Z"},
    )


def test_required_action_keeps_verified_day_separate_from_model_authority():
    result = _assessment()
    assert result is not None
    assert result["independent_obligation"] is True
    assert result["verified_date_fields"] == ["due"]
    assert result["evidence"][0]["source_event_id"] == "source-form"
    assert (
        _assessment(
            qualifying_evidence=[
                {
                    "source_id": "foreign",
                    "quote": "Only if attending.",
                    "role": "condition",
                }
            ]
        )
        is None
    )
    assert (
        _assessment(
            qualifying_evidence=[
                {
                    "source_id": "m01",
                    "quote": "Invented qualification",
                    "role": "condition",
                }
            ]
        )
        is None
    )


@pytest.mark.parametrize("role", ["negation", "completion"])
def test_qualifiers_prevent_independent_obligation_override(role):
    result = _assessment(
        qualifying_evidence=[
            {"source_id": "m01", "quote": "Already completed.", "role": role}
        ]
    )
    assert result is not None
    assert result["independent_obligation"] is False
    assert result["actionability"] == "none"


def test_unknown_condition_is_a_possibility_and_reporting_is_not_recurrence():
    result = _assessment(
        condition="Only if attending",
        condition_state="unknown",
        qualifying_evidence=[
            {"source_id": "m01", "quote": "Only if attending.", "role": "condition"}
        ],
    )
    assert result is not None
    assert result["actionability"] == "conditional"
    assert result["independent_obligation"] is False
    assert _assessment(purpose="status_update")["recurrence_grounded"] is False


@pytest.mark.parametrize(
    "quote", ["September 12", "tomorrow", "every September", "2025-09-12"]
)
def test_relative_or_ambiguous_dates_do_not_inherit_current_year(quote):
    assert not date_is_supported("2026-09-12T17:00:00Z", [quote])
    assert not date_is_supported(datetime.now(timezone.utc), [quote])


def _source(key, body, sender="issuer-a@example.invalid"):
    return {
        "event_id": key,
        "type": "messaging.received",
        "tenant_id": "fixture-tenant",
        "owner_scope": "private:fixture-member",
        "source_adapter": "gmail",
        "source_account_id": "fixture-account",
        "payload": {
            "from_identifier": sender,
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "native_message_id": key,
            "body_text": body,
        },
    }


def _proposal(title, fields):
    return {"payload": {"title": title, "target_kind": "task", "candidate": fields}}


def test_account_cycles_and_different_issuers_cannot_merge_automatically():
    first = evidence_profile(
        _source("first", "Account 1234 payment due"),
        _proposal("Pay September bill", {"due": "2026-09-12"}),
    )
    second = evidence_profile(
        _source("second", "Account 1234 payment due"),
        _proposal("Pay October bill", {"due": "2026-10-12"}),
    )
    relations = evidence_relationships(first, second)
    assert any(
        item.relationship_type == "exact_account_reference" for item in relations
    )
    assert not any(item.automatic_effect for item in relations)
    first = evidence_profile(
        _source("first", "Invoice 1234 payment due"),
        _proposal("Pay invoice 1234", {"due": "2026-09-12"}),
    )
    second = evidence_profile(
        _source(
            "second", "Invoice 1234 payment due", sender="issuer-b@example.invalid"
        ),
        _proposal("Pay invoice 1234", {"due": "2026-09-12"}),
    )
    assert not any(
        item.automatic_effect for item in evidence_relationships(first, second)
    )


def test_paid_text_does_not_complete_an_unrelated_action_in_same_message():
    source = _source(
        "concert", "Payment received. Your purchased concert is September 12, 2026."
    )
    profile = evidence_profile(
        source,
        {
            "payload": {
                "title": "Attend purchased concert",
                "target_kind": "calendar_event",
                "candidate": {"start": "2026-09-12T19:00:00Z"},
            }
        },
    )
    assert profile.current_state == "current"
