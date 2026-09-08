"""Unit contracts for deterministic cross-source reconciliation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.events.schemas.reconciliation import (
    ReconciliationEvidenceRelationshipV1,
    ReconciliationPolicySnapshotV1,
    ReconciliationTypedFactSharedV1,
)
from adminme.reconciliation import (
    assessed_signals,
    choose_decision,
    evidence_profile,
    evidence_relationships,
    issuer_reference,
    priority_for,
    reference_digest,
)


TENANT = "tenant-reconciliation-unit"
MEMBER = "member-reconciliation-unit"


def _source(
    event_id: str,
    *,
    subject: str,
    body: str = "",
    native_message_id: str | None = None,
    thread_id: str | None = None,
    labels: list[str] | None = None,
    sender: str = "correspondent@example.invalid",
    event_type: str = "messaging.received",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "gmail",
        "provider_account_id": "fixture-account",
        "workspace": "personal",
        "member_id": MEMBER,
        "subject": subject,
        "body_text": body,
        "from_identifier": sender,
        "to_identifier": "member@example.invalid",
        "provider_labels": labels or [],
    }
    if native_message_id:
        payload["native_message_id"] = native_message_id
    if thread_id:
        payload["thread_id"] = thread_id
    payload.update(extra or {})
    return {
        "event_id": event_id,
        "tenant_id": TENANT,
        "type": event_type,
        "source_adapter": "gmail",
        "source_account_id": "fixture-account",
        "owner_scope": f"private:{MEMBER}",
        "visibility_scope": f"private:{MEMBER}",
        "payload": payload,
    }


def _proposal(
    proposal_id: str,
    source_event_id: str,
    title: str,
    *,
    target_kind: str = "task",
    candidate: dict[str, object] | None = None,
    confidence: float = 0.5,
) -> dict[str, object]:
    return {
        "event_id": f"event-{proposal_id}",
        "tenant_id": TENANT,
        "type": "triage.item.proposed",
        "owner_scope": f"private:{MEMBER}",
        "visibility_scope": f"private:{MEMBER}",
        "payload": {
            "proposal_id": proposal_id,
            "target_kind": target_kind,
            "owner_member_id": MEMBER,
            "title": title,
            "candidate": candidate or {},
            "source_event_id": source_event_id,
            "confidence": confidence,
        },
    }


def _policy(
    *,
    triage: str = "review",
    alert: str = "security",
    context: str = "typed_facts",
) -> ReconciliationPolicySnapshotV1:
    return ReconciliationPolicySnapshotV1.model_validate(
        {
            "policy_key": f"spv2_{'a' * 64}",
            "policy_revision": 4,
            "policy_origin": "exact_override",
            "triage_route": triage,
            "alert_route": alert,
            "context_route": context,
            "reason_codes": ["fixture_policy"],
        }
    )


def test_exact_relationships_are_typed_and_contradictions_require_review() -> None:
    first = _source(
        "source-first",
        subject="Appointment APT-8842 follow-up",
        native_message_id="provider-object-1",
        thread_id="conversation-1",
    )
    second = _source(
        "source-second",
        subject="Appointment APT-8842 follow-up",
        native_message_id="provider-object-1",
        thread_id="conversation-1",
    )
    first_proposal = _proposal(
        "proposal-first",
        "source-first",
        "Review appointment APT-8842",
        candidate={"due_at": "2026-09-10T14:00:00Z"},
    )
    second_proposal = _proposal(
        "proposal-second",
        "source-second",
        "Review appointment APT-8842",
        candidate={"due_at": "2026-09-11T14:00:00Z"},
        confidence=0.99,
    )

    current = evidence_profile(second, second_proposal)
    related = evidence_profile(first, first_proposal)
    relationships = evidence_relationships(current, related)
    types = {item.relationship_type for item in relationships}

    assert {
        "same_provider_object",
        "same_conversation",
        "exact_external_identifier",
        "exact_appointment_reference",
        "contradiction",
    } <= types
    conversation = next(
        item for item in relationships if item.relationship_type == "same_conversation"
    )
    assert conversation.automatic_effect is False
    assert (
        choose_decision(
            coordinate_exact=True,
            signals=assessed_signals(second, second_proposal),
            policy=_policy(),
            profile=current,
            relationships=relationships,
            target_kind="task",
        )
        == "conflict_review"
    )


def test_weak_similarity_is_review_only_and_cannot_merge_or_authorize() -> None:
    first = _source("source-first", subject="School enrollment packet submit")
    second = _source("source-second", subject="Submit packet school enrollment")
    first_profile = evidence_profile(
        first,
        _proposal("proposal-first", "source-first", "School enrollment packet submit"),
    )
    second_proposal = _proposal(
        "proposal-second", "source-second", "Submit packet school enrollment"
    )
    second_profile = evidence_profile(second, second_proposal)
    [relationship] = evidence_relationships(second_profile, first_profile)

    assert relationship.relationship_type == "weak_review_suggestion"
    assert relationship.authority == "review_suggestion"
    assert relationship.automatic_effect is False
    assert (
        choose_decision(
            coordinate_exact=True,
            signals=assessed_signals(second, second_proposal),
            policy=_policy(),
            profile=second_profile,
            relationships=[relationship],
            target_kind="task",
        )
        == "admit"
    )
    with pytest.raises(ValidationError, match="review-only suggestion"):
        ReconciliationEvidenceRelationshipV1.model_validate(
            {
                **relationship.model_dump(mode="json"),
                "automatic_effect": True,
            }
        )


def test_explicit_shared_typed_state_satisfies_without_raw_cross_member_content() -> (
    None
):
    source = _source(
        "source-mortgage-due",
        subject="Mortgage invoice MTG-2048 payment due",
    )
    proposal = _proposal(
        "proposal-mortgage",
        "source-mortgage-due",
        "Pay mortgage invoice MTG-2048",
        candidate={"due_at": "2026-09-08T21:00:00Z"},
    )
    digest = reference_digest(
        TENANT, "exact_payment_reference", issuer_reference(source, "MTG-2048")
    )
    shared = {
        "event_id": "source-shared-payment-state",
        "tenant_id": TENANT,
        "type": "context.reconciliation.typed_fact_shared",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": {
            "member_id": "member-source",
            "references": [{"kind": "payment_reference", "digest": digest}],
            "current_state": "satisfied",
        },
    }
    shared_profile = evidence_profile(shared)
    relationships = evidence_relationships(
        shared_profile, evidence_profile(source, proposal)
    )
    types = {item.relationship_type for item in relationships}

    assert "exact_payment_reference" in types
    assert "satisfaction_or_completion" in types
    assert all(
        item.authority == "explicitly_shared_typed_fact" for item in relationships
    )
    assert (
        choose_decision(
            coordinate_exact=True,
            signals=assessed_signals(source, proposal),
            policy=_policy(),
            profile=shared_profile,
            relationships=relationships,
            target_kind="task",
        )
        == "already_satisfied"
    )

    valid_share = {
        "fact_id": "fact-payment-state",
        "source_member_id": "member-source",
        "shared_with_member_ids": [MEMBER],
        "shared_by_member_id": "member-source",
        "share_grant_id": "grant-payment-state",
        "source_reference_digest": "b" * 64,
        "fact_kind": "payment_state",
        "references": [{"kind": "payment_reference", "digest": digest}],
        "current_state": "satisfied",
        "occurred_at": "2026-09-01T12:00:00Z",
        "raw_body_included": False,
        "owner_transfer": False,
        "action_authority": False,
    }
    ReconciliationTypedFactSharedV1.model_validate(valid_share)
    with pytest.raises(ValidationError):
        ReconciliationTypedFactSharedV1.model_validate(
            {**valid_share, "raw_body_included": True}
        )


def test_priority_uses_only_allowed_typed_evidence() -> None:
    known = _source(
        "source-known",
        subject="Quick question",
        body="Could you please send the signed form?",
        extra={"provider_urgency": "critical"},
    )
    known_signals = assessed_signals(known, prior_member_correspondence=True)
    deadline_signals = assessed_signals(
        _source(
            "source-deadline",
            subject="Provider notice",
            sender="alerts@example.invalid",
            extra={"provider_urgency": "critical"},
        ),
        _proposal(
            "proposal-deadline",
            "source-deadline",
            "Review provider notice",
            candidate={"deadline_at": "2026-09-02T12:00:00Z"},
            confidence=1.0,
        ),
    )
    promise_signals = assessed_signals(
        _source(
            "source-promise",
            subject="Re: form",
            body="I will send the signed form tomorrow.",
            event_type="messaging.sent",
        )
    )

    assert priority_for(promise_signals) == (10, ["exact_member_commitment"])
    assert priority_for(known_signals) == (20, ["known_human_actionable_request"])
    # A model/provider field alone does not prove a source deadline.
    assert priority_for(deadline_signals) == (60, ["standard_actionable_candidate"])
    assert known_signals.provider_or_sender_urgency_non_authoritative == "critical"


@pytest.mark.parametrize(
    ("source", "target_kind", "expected"),
    [
        (
            _source(
                "source-security",
                subject="New sign-in detected",
                sender="security-alerts@example.invalid",
            ),
            "task",
            "alert_only",
        ),
        (
            _source(
                "source-promotion",
                subject="Limited-time promotion",
                labels=["CATEGORY_PROMOTIONS"],
                sender="mailer@example.invalid",
            ),
            "task",
            "context_only",
        ),
        (
            _source(
                "source-party",
                subject="Incidental person mention",
                labels=["CATEGORY_UPDATES"],
                sender="updates@example.invalid",
            ),
            "party_candidate",
            "context_only",
        ),
    ],
)
def test_low_signal_and_security_evidence_do_not_become_ordinary_work(
    source: dict[str, object], target_kind: str, expected: str
) -> None:
    proposal = _proposal(
        f"proposal-{source['event_id']}",
        str(source["event_id"]),
        str(source["payload"]),
        target_kind=target_kind,
        confidence=1.0,
    )
    profile = evidence_profile(source, proposal)
    signals = assessed_signals(source, proposal)
    assert (
        choose_decision(
            coordinate_exact=True,
            signals=signals,
            policy=_policy(),
            profile=profile,
            relationships=[],
            target_kind=target_kind,
        )
        == expected
    )


def test_policy_suppression_and_unresolved_are_distinct_decisions() -> None:
    source = _source("source-unresolved", subject="Review this item")
    proposal = _proposal("proposal-unresolved", "source-unresolved", "Review this item")
    profile = evidence_profile(source, proposal)
    signals = assessed_signals(source, proposal)

    assert (
        choose_decision(
            coordinate_exact=False,
            signals=signals,
            policy=_policy(),
            profile=profile,
            relationships=[],
            target_kind="task",
        )
        == "unresolved"
    )
    assert (
        choose_decision(
            coordinate_exact=True,
            signals=signals,
            policy=_policy(triage="none", alert="none", context="none"),
            profile=profile,
            relationships=[],
            target_kind="task",
        )
        == "source_policy_suppression"
    )


def test_imperative_complete_language_is_not_completion_evidence() -> None:
    source = _source(
        "source-incomplete-request",
        subject="Fixture request",
        body="Please complete the fixture form.",
    )
    proposal = _proposal(
        "proposal-incomplete-request",
        "source-incomplete-request",
        "Complete the fixture form",
    )

    assert evidence_profile(source, proposal).current_state == "current"
