"""Triage context is authorized, exact-source grounded, and non-generative."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adminme.triage_context import (
    add_triage_source_context,
    compose_triage_source_context,
)


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
            "provider_labels": ["INBOX", "CATEGORY_UPDATES", "Private fixture label"],
        },
    }
    context = compose_triage_source_context(item, source)
    assert context["channel"] == "gmail"
    assert context["sender"] == "sender@example.invalid"
    assert context["subject"] == "Water bill"
    assert context["direction"] == "inbound"
    assert context["provider_category"] == {
        "id": "updates",
        "label": "Updates",
        "provenance": "Gmail category",
        "authority": "provider_observation",
    }
    assert "Private fixture label" not in str(context)
    assert context["evidence"] == {
        "quote": "Please pay the water bill Friday.",
        "exactly_grounded": True,
    }
    assert context["correction_capabilities"] == {
        "unrelated_source_event_ids": [],
        "quoted_or_forwarded_history": False,
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
    assert context["correction_capabilities"] == {
        "unrelated_source_event_ids": [],
        "quoted_or_forwarded_history": False,
    }


def test_gmail_category_is_observation_only_and_non_gmail_labels_are_hidden() -> None:
    item = {
        "title": "Review account access",
        "target_kind": "task",
        "eligibility_rule_ids": ["candidate_policy_v1:adapter:gmail:review"],
    }
    gmail = compose_triage_source_context(
        item,
        {
            "event_id": "gmail-category",
            "type": "messaging.received",
            "source_adapter": "gmail-api",
            "payload": {
                "source_channel": "gmail",
                "provider_labels": ["SPAM", "CATEGORY_PROMOTIONS", "Family"],
            },
        },
    )
    assert gmail["provider_category"] == {
        "id": "spam",
        "label": "Spam",
        "provenance": "Gmail category",
        "authority": "provider_observation",
    }
    assert "Family" not in str(gmail)

    generic = compose_triage_source_context(
        item,
        {
            "event_id": "generic-category",
            "type": "messaging.received",
            "source_adapter": "generic-message",
            "payload": {
                "source_channel": "message",
                "provider_labels": ["CATEGORY_UPDATES"],
            },
        },
    )
    assert generic["provider_category"] is None


def test_context_corrections_are_exposed_only_when_server_can_apply_them() -> None:
    source = {
        "event_id": "message-current",
        "type": "messaging.received",
        "payload": {"body_text": "Please review the exact fixture transfer."},
    }
    related = {
        "event_id": "message-related",
        "type": "messaging.received",
        "payload": {"body_text": "A related fixture update."},
    }
    item = {
        "title": "Review the fixture transfer",
        "target_kind": "task",
        "interpretation": {
            "evidence_refs": [
                {
                    "source_event_id": "message-current",
                    "quote": "Please review the exact fixture transfer.",
                }
            ],
            "context_source_event_ids": ["message-current", "message-related"],
            "context_sources": [
                {
                    "source_event_id": "message-current",
                    "relation": "thread",
                    "relationship_source_event_ids": [],
                },
                {
                    "source_event_id": "message-related",
                    "relation": "related",
                    "relationship_source_event_ids": ["message-current"],
                },
            ],
        },
    }

    context = compose_triage_source_context(
        item,
        source,
        {"message-current": source, "message-related": related},
    )

    assert context["correction_capabilities"] == {
        "unrelated_source_event_ids": ["message-related"],
        "quoted_or_forwarded_history": True,
    }


@pytest.mark.asyncio
async def test_surface_hydrates_relationship_provenance_outside_display_window() -> (
    None
):
    def event(event_id: str, body: str) -> dict[str, object]:
        return {
            "event_id": event_id,
            "type": "messaging.received",
            "owner_scope": "private:member-fixture",
            "visibility_scope": "private:member-fixture",
            "sensitivity": "normal",
            "payload": {"body_text": body},
        }

    events = {
        "message-current": event("message-current", "Current fixture update."),
        "message-related": event("message-related", "Related fixture update."),
        "message-old-edge": event("message-old-edge", "Older admitting message."),
    }

    class EventLog:
        async def get(self, event_id: str):  # type: ignore[no-untyped-def]
            return events.get(event_id)

    surface = {
        "items": [
            {
                "source_event_id": "message-current",
                "title": "Current fixture update",
                "target_kind": "task",
                "interpretation": {
                    "context_source_event_ids": [
                        "message-current",
                        "message-related",
                    ],
                    "context_sources": [
                        {
                            "source_event_id": "message-current",
                            "relation": "thread",
                            "relationship_source_event_ids": [],
                        },
                        {
                            "source_event_id": "message-related",
                            "relation": "related",
                            "relationship_source_event_ids": ["message-old-edge"],
                        },
                    ],
                },
            }
        ]
    }
    session = SimpleNamespace(
        auth_role="principal",
        auth_member_id="member-fixture",
        allowed_scopes={"private:member-fixture"},
    )

    await add_triage_source_context(
        SimpleNamespace(event_log=EventLog()), session, surface
    )

    assert surface["items"][0]["source_context"]["correction_capabilities"] == {
        "unrelated_source_event_ids": ["message-related"],
        "quoted_or_forwarded_history": False,
    }
