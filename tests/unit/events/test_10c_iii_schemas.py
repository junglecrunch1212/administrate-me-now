"""ReminderDispatchedV1 / CrmSurfacedV1 / CustodyBriefComposedV1
schema-registration canaries.

Asserts the three event types added in prompt 10c-iii commit 1
register at v1 per [D7], the Pydantic models enforce the
required-field contracts from [BUILD.md §L4] (reminder_dispatch /
crm_surface / custody_brief), and ``extra="forbid"`` rejects unknown
payload keys — matching the shape established by
``test_digest_paralysis_schemas.py`` (10c-ii).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.events.envelope import EventEnvelope
from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.domain import (
    CrmSurfacedV1,
    CustodyBriefComposedV1,
    ReminderDispatchedV1,
)


def test_reminder_dispatched_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("reminder.dispatched", 1)
    assert model is ReminderDispatchedV1
    assert registry.latest_version("reminder.dispatched") == 1


def test_crm_surfaced_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("crm.surfaced", 1)
    assert model is CrmSurfacedV1
    assert registry.latest_version("crm.surfaced") == 1


def test_custody_brief_composed_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("custody_brief.composed", 1)
    assert model is CustodyBriefComposedV1
    assert registry.latest_version("custody_brief.composed") == 1


def test_reminder_dispatched_minimum_payload_validates() -> None:
    instance = ReminderDispatchedV1(
        commitment_id="commit-1",
        owner_member_id="member-a",
        recipient_party_id="party-b",
        composed_text="Don't forget: dentist at 3pm.",
        channel="imessage",
        due_at=datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
        delivered=True,
        validation_failed=False,
    )
    assert instance.channel == "imessage"
    assert instance.scheduled_send_at is None
    assert instance.validation_failed is False


def test_reminder_dispatched_sentinel_path_validates() -> None:
    """Sentinel path mirrors DigestComposedV1: composed_text="",
    validation_failed=True, delivered=False (no outbound)."""
    instance = ReminderDispatchedV1(
        commitment_id="commit-1",
        owner_member_id="member-a",
        recipient_party_id="party-b",
        composed_text="",
        channel="sms",
        due_at=datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
        delivered=False,
        validation_failed=True,
    )
    assert instance.composed_text == ""
    assert instance.delivered is False
    assert instance.validation_failed is True


def test_reminder_dispatched_rejects_too_long_composed_text() -> None:
    with pytest.raises(ValidationError):
        ReminderDispatchedV1(
            commitment_id="commit-1",
            owner_member_id="member-a",
            recipient_party_id="party-b",
            composed_text="x" * 281,
            channel="imessage",
            due_at=datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
            delivered=True,
            validation_failed=False,
        )


def test_reminder_dispatched_rejects_invalid_channel() -> None:
    with pytest.raises(ValidationError):
        ReminderDispatchedV1(
            commitment_id="commit-1",
            owner_member_id="member-a",
            recipient_party_id="party-b",
            composed_text="hi",
            channel="carrier-pigeon",  # type: ignore[arg-type]
            due_at=datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
            delivered=True,
            validation_failed=False,
        )


def test_reminder_dispatched_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        ReminderDispatchedV1(
            commitment_id="commit-1",
            owner_member_id="member-a",
            recipient_party_id="party-b",
            composed_text="hi",
            channel="imessage",
            due_at=datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
            delivered=True,
            validation_failed=False,
            mystery_field="nope",  # type: ignore[call-arg]
        )


def test_crm_surfaced_minimum_payload_validates() -> None:
    instance = CrmSurfacedV1(
        member_id="member-a",
        surfaced_party_ids=["party-b", "party-c"],
        gap_kind="stale_contact",
        composed_text="3 friends you haven't heard from in 90+ days.",
        channel="email",
        week_of_date="2026-05-11",
        delivered=True,
        validation_failed=False,
    )
    assert instance.gap_kind == "stale_contact"
    assert len(instance.surfaced_party_ids) == 2


def test_crm_surfaced_rejects_empty_surfaced_party_ids() -> None:
    with pytest.raises(ValidationError):
        CrmSurfacedV1(
            member_id="member-a",
            surfaced_party_ids=[],
            gap_kind="stale_contact",
            composed_text="x",
            channel="email",
            week_of_date="2026-05-11",
            delivered=True,
            validation_failed=False,
        )


def test_crm_surfaced_rejects_invalid_gap_kind() -> None:
    with pytest.raises(ValidationError):
        CrmSurfacedV1(
            member_id="member-a",
            surfaced_party_ids=["party-b"],
            gap_kind="vibes",  # type: ignore[arg-type]
            composed_text="x",
            channel="email",
            week_of_date="2026-05-11",
            delivered=True,
            validation_failed=False,
        )


def test_crm_surfaced_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        CrmSurfacedV1(
            member_id="member-a",
            surfaced_party_ids=["party-b"],
            gap_kind="upcoming_birthday",
            composed_text="x",
            channel="email",
            week_of_date="2026-05-11",
            delivered=True,
            validation_failed=False,
            unknown="nope",  # type: ignore[call-arg]
        )


def test_custody_brief_composed_minimum_payload_validates() -> None:
    instance = CustodyBriefComposedV1(
        member_id="member-a",
        coparent_member_id="member-b",
        child_member_ids=["member-c", "member-d"],
        composed_text="Tomorrow handoff 5pm. Bring inhaler.",
        channel="imessage",
        brief_date="2026-05-16",
        delivered=True,
        validation_failed=False,
    )
    assert instance.coparent_member_id == "member-b"
    assert len(instance.child_member_ids) == 2


def test_custody_brief_composed_rejects_empty_child_member_ids() -> None:
    with pytest.raises(ValidationError):
        CustodyBriefComposedV1(
            member_id="member-a",
            coparent_member_id="member-b",
            child_member_ids=[],
            composed_text="x",
            channel="imessage",
            brief_date="2026-05-16",
            delivered=True,
            validation_failed=False,
        )


def test_custody_brief_composed_rejects_too_long_composed_text() -> None:
    with pytest.raises(ValidationError):
        CustodyBriefComposedV1(
            member_id="member-a",
            coparent_member_id="member-b",
            child_member_ids=["member-c"],
            composed_text="x" * 1501,
            channel="imessage",
            brief_date="2026-05-16",
            delivered=True,
            validation_failed=False,
        )


def test_custody_brief_composed_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        CustodyBriefComposedV1(
            member_id="member-a",
            coparent_member_id="member-b",
            child_member_ids=["member-c"],
            composed_text="x",
            channel="imessage",
            brief_date="2026-05-16",
            delivered=True,
            validation_failed=False,
            unknown="nope",  # type: ignore[call-arg]
        )


def test_reminder_dispatched_round_trips_through_envelope() -> None:
    envelope = EventEnvelope(
        event_at_ms=1700000000000,
        tenant_id="tenant-test",
        type="reminder.dispatched",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="reminder_dispatch",
        source_account_id="pipeline",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload={
            "commitment_id": "commit-1",
            "owner_member_id": "member-a",
            "recipient_party_id": "party-b",
            "composed_text": "Reminder: dentist 3pm.",
            "channel": "imessage",
            "due_at": "2026-05-15T15:00:00+00:00",
            "delivered": True,
            "validation_failed": False,
        },
    )
    validated = registry.validate("reminder.dispatched", 1, envelope.payload)
    assert isinstance(validated, ReminderDispatchedV1)


def test_crm_surfaced_round_trips_through_envelope() -> None:
    envelope = EventEnvelope(
        event_at_ms=1700000000000,
        tenant_id="tenant-test",
        type="crm.surfaced",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="crm_surface",
        source_account_id="pipeline",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload={
            "member_id": "member-a",
            "surfaced_party_ids": ["party-b", "party-c"],
            "gap_kind": "stale_contact",
            "composed_text": "3 stale contacts.",
            "channel": "email",
            "week_of_date": "2026-05-11",
            "delivered": True,
            "validation_failed": False,
        },
    )
    validated = registry.validate("crm.surfaced", 1, envelope.payload)
    assert isinstance(validated, CrmSurfacedV1)


def test_custody_brief_composed_round_trips_through_envelope() -> None:
    envelope = EventEnvelope(
        event_at_ms=1700000000000,
        tenant_id="tenant-test",
        type="custody_brief.composed",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="custody_brief",
        source_account_id="pipeline",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload={
            "member_id": "member-a",
            "coparent_member_id": "member-b",
            "child_member_ids": ["member-c"],
            "composed_text": "Tomorrow handoff 5pm.",
            "channel": "imessage",
            "brief_date": "2026-05-16",
            "delivered": True,
            "validation_failed": False,
        },
    )
    validated = registry.validate("custody_brief.composed", 1, envelope.payload)
    assert isinstance(validated, CustodyBriefComposedV1)
