"""Canonical synthetic household facts for the credential-free demo."""

from __future__ import annotations

from adminme.events.envelope import EventEnvelope

FIXTURE_MEMBER_ID = "member_fixture_principal"
FIXTURE_SENDER_ID = "party_fixture_sender"
FIXTURE_SENDER_EMAIL = "sender@fixture.invalid"
FIXTURE_MEMBER_EMAIL = "principal@fixture.invalid"
FIXTURE_NOW = "2026-07-30T12:00:00Z"


def _fixture_event(
    tenant_id: str,
    event_type: str,
    payload: dict,
    key: str,
    *,
    private: bool = False,
) -> EventEnvelope:
    scope = f"private:{FIXTURE_MEMBER_ID}" if private else "shared:household"
    return EventEnvelope(
        event_at_ms=1_775_131_200_000,
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=FIXTURE_NOW,
        source_adapter="fixture-bootstrap",
        source_account_id="fixture-household",
        owner_scope=scope,
        visibility_scope=scope,
        sensitivity="normal",
        actor_identity=FIXTURE_MEMBER_ID,
        idempotency_key=f"fixture-seed:{key}",
        payload=payload,
    )


def canonical_seed_events(tenant_id: str) -> list[EventEnvelope]:
    def _event(
        event_type: str, payload: dict, key: str, *, private: bool = False
    ) -> EventEnvelope:
        return _fixture_event(tenant_id, event_type, payload, key, private=private)

    events = [
        _event(
            "party.created",
            {
                "party_id": "household_fixture",
                "kind": "household",
                "display_name": "Fixture Household",
                "sort_name": "fixture household",
                "attributes": {},
            },
            "household",
        ),
        _event(
            "party.created",
            {
                "party_id": FIXTURE_MEMBER_ID,
                "kind": "person",
                "display_name": "Fixture Principal",
                "sort_name": "fixture principal",
                "attributes": {"synthetic": True},
            },
            "member",
        ),
        _event(
            "membership.added",
            {
                "membership_id": "membership_fixture_principal",
                "party_id": FIXTURE_MEMBER_ID,
                "parent_party_id": "household_fixture",
                "role": "principal",
                "started_at": FIXTURE_NOW,
                "attributes": {},
            },
            "membership",
        ),
        _event(
            "identifier.added",
            {
                "identifier_id": "identifier_fixture_principal",
                "party_id": FIXTURE_MEMBER_ID,
                "kind": "email",
                "value": FIXTURE_MEMBER_EMAIL,
                "value_normalized": FIXTURE_MEMBER_EMAIL,
                "verified": True,
                "primary_for_kind": True,
            },
            "member-identifier",
        ),
        _event(
            "party.created",
            {
                "party_id": FIXTURE_SENDER_ID,
                "kind": "person",
                "display_name": "Fixture Sender",
                "sort_name": "fixture sender",
                "attributes": {"synthetic": True},
            },
            "sender",
        ),
        _event(
            "identifier.added",
            {
                "identifier_id": "identifier_fixture_sender",
                "party_id": FIXTURE_SENDER_ID,
                "kind": "email",
                "value": FIXTURE_SENDER_EMAIL,
                "value_normalized": FIXTURE_SENDER_EMAIL,
                "verified": True,
                "primary_for_kind": True,
            },
            "sender-identifier",
        ),
        _event(
            "task.created",
            {
                "task_id": "task_fixture_secondary",
                "title": "Review the household checklist",
                "description": "Synthetic fallback for what-now ranking.",
                "owner_member_id": FIXTURE_MEMBER_ID,
                "due": "2026-08-15",
                "energy": "low",
                "effort_min": 10,
                "source_commitment_id": None,
            },
            "secondary-task",
            private=True,
        ),
    ]
    return events


__all__ = [
    "FIXTURE_MEMBER_EMAIL",
    "FIXTURE_MEMBER_ID",
    "FIXTURE_NOW",
    "FIXTURE_SENDER_EMAIL",
    "FIXTURE_SENDER_ID",
    "canonical_seed_events",
]
